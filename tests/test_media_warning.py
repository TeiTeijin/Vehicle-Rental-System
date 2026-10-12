"""Warn-and-allow media spelling checks: probe classification, form hint,
save-never-blocked, and actionable Details errors. The API is always mocked;
no test touches the network."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.database import Base  # noqa: E402
from app.services import carimages_client as ci  # noqa: E402
from app.services import media_service  # noqa: E402
from app.staff.context import (  # noqa: E402
    DatabaseSelection,
    DatabaseTarget,
    StaffContext,
)
from app.staff.fleet_panels import (  # noqa: E402
    VehicleFormPanel,
    _media_failure_message,
)


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def context(tmp_path):
    path = tmp_path / "media_warn.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    engine.dispose()
    ctx = StaffContext(
        DatabaseSelection(
            url=f"sqlite:///{path}", target=DatabaseTarget.LIVE, label="Test"
        )
    )
    yield ctx
    ctx.dispose()


def _pump(turns: int = 30) -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    for _ in range(turns):
        app.processEvents()


class TestProbeMedia:
    def test_ok_when_photo_resolves(self, monkeypatch):
        monkeypatch.setattr(
            ci, "get_signed_image_url", lambda *a, **k: "https://cdn/x.webp"
        )
        assert media_service.probe_media("Toyota", "Vios", 2022, "Car") == "ok"

    def test_misspell_is_not_found(self, monkeypatch):
        def _boom(*a, **k):
            raise ci.CarImagesError("HTTP 404")

        monkeypatch.setattr(ci, "get_signed_image_url", _boom)
        assert media_service.probe_media("Toyta", "Vios", 2022, "Car") == "not_found"

    def test_auth_failure_is_unreachable_not_not_found(self, monkeypatch):
        def _boom(*a, **k):
            raise ci._RESTKeyError("bad key")

        monkeypatch.setattr(ci, "get_signed_image_url", _boom)
        assert media_service.probe_media("Toyota", "Vios", 2022, "Car") == "unreachable"

    def test_timeout_is_unreachable(self, monkeypatch):
        import requests

        def _boom(*a, **k):
            raise requests.ConnectTimeout("down")

        monkeypatch.setattr(ci, "get_signed_image_url", _boom)
        assert media_service.probe_media("Toyota", "Vios", 2022, "Car") == "unreachable"

    def test_moto_falls_back_to_signed_url(self, monkeypatch):
        monkeypatch.setattr(ci, "resolve_moto_generation_slug", lambda *a: None)
        monkeypatch.setattr(
            ci, "get_signed_image_url", lambda *a, **k: "https://cdn/m.webp"
        )
        assert media_service.probe_media("Honda", "Click 125i", 2023, "Motorcycle") == "ok"

    def test_empty_make_never_calls_the_api(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            ci, "get_signed_image_url", lambda *a, **k: called.append(1)
        )
        assert media_service.probe_media("", "Vios", 2022, "Car") == "not_found"
        assert called == []


class TestVehicleFormHint:
    def _panel(self, context):
        return VehicleFormPanel(context, None, parent=None)

    def test_misspell_warns_but_save_still_fires(self, qapp, context, monkeypatch):
        monkeypatch.setattr(media_service, "probe_media", lambda *a: "not_found")
        panel = self._panel(context)
        panel._on_probe_done(panel._probe_gen, "not_found", "Toyta", "Vios")
        assert "check spelling" in panel._media_hint.text()
        assert "still save" in panel._media_hint.text()

        panel.make_edit.setText("Toyta")
        panel.model_edit.setText("Vios")
        panel.plate_edit.setText("TST-001")
        panel.rate_edit.setText("2500.00")
        seen = []
        panel.submitted.connect(seen.append)
        panel._on_go()
        assert len(seen) == 1, "a spelling miss must never block the save"
        assert seen[0]["make"] == "Toyta"
        panel.deleteLater()

    def test_unreachable_warns_differently_and_still_saves(self, qapp, context):
        panel = self._panel(context)
        panel._on_probe_done(panel._probe_gen, "unreachable", "Toyota", "Vios")
        assert "Couldn't reach CarImages" in panel._media_hint.text()

        panel.make_edit.setText("Toyota")
        panel.model_edit.setText("Vios")
        panel.plate_edit.setText("TST-002")
        panel.rate_edit.setText("2500.00")
        seen = []
        panel.submitted.connect(seen.append)
        panel._on_go()
        assert len(seen) == 1
        panel.deleteLater()

    def test_background_probe_clears_hint_on_match(self, qapp, context, monkeypatch):
        monkeypatch.setattr(media_service, "probe_media", lambda *a: "ok")
        panel = self._panel(context)
        panel.make_edit.setText("Toyota")
        panel.model_edit.setText("Vios")
        panel._run_probe()
        _pump()
        assert panel._media_hint.text() == ""
        panel.deleteLater()

    def test_stale_probe_results_are_dropped(self, qapp, context):
        panel = self._panel(context)
        panel._probe_gen = 5
        panel._on_probe_done(4, "not_found", "Toyta", "Vios")
        assert panel._media_hint.text() == ""
        panel.deleteLater()


class TestDetailsErrorMessage:
    def test_unknown_model_points_at_edit_and_refresh(self):
        message = _media_failure_message(ci.CarImagesError("HTTP 404"))
        assert "check the spelling" in message
        assert "Refresh photos" in message

    def test_network_failure_points_at_connection_and_retry(self):
        import requests

        message = _media_failure_message(requests.ConnectTimeout("down"))
        assert "Refresh photos to retry" in message
        assert "check the spelling" not in message

    def test_non_error_falls_back(self):
        assert _media_failure_message(object()) == (
            "Media refresh failed. Refresh photos to retry."
        )


class _FakeRefresh:
    """Stand-in for `_MediaRefresh`: records force, answers synchronously."""

    calls: list = []

    def __init__(self, context, vehicle_id, signals, *, force=False):
        self._signals = signals
        type(self).calls.append((vehicle_id, force))
        self._rows = [{"view_angle": "front34"}]

    def run(self) -> None:
        self._signals.done.emit(self._rows)


class _FailingRefresh(_FakeRefresh):
    def run(self) -> None:
        self._signals.failed.emit(RuntimeError("down"))


class _SyncPool:
    def start(self, runnable) -> None:
        runnable.run()


def _fleet_page(context):
    from app.staff.pages.fleet import FleetPage

    class _Shell:
        def __init__(self, ctx):
            self.context = ctx

    return FleetPage(_Shell(context))


def _vehicle(context, **overrides):
    from datetime import datetime

    from app.models import Vehicle, Vehicle_Category

    with context.session() as session:
        category = Vehicle_Category(category_name="Car", base_rate_multiplier=1.0)
        session.add(category)
        session.flush()
        fields = {
            "category_id": category.category_id,
            "make": "Toyota",
            "model": "Vios",
            "year": 2022,
            "plate_number": "TST-100",
            "daily_rate": "2500.00",
            "mileage": 10_000,
            "status": "available",
            "created_at": datetime(2026, 1, 1, 9, 0),
        }
        fields.update(overrides)
        row = Vehicle(**fields)
        session.add(row)
        session.flush()
        return row.vehicle_id


def _edit_values(**overrides):
    values = {
        "plate_number": "TST-100",
        "make": "Toyota",
        "model": "Vios",
        "year": 2022,
        "color": None,
        "category_name": "Car",
        "transmission": "Automatic",
        "fuel_type": "Petrol",
        "seats": 5,
        "vehicle_class": None,
        "engine_cc": None,
        "daily_rate": "2500.00",
        "mileage": 10_000,
        "registration_expiry": None,
        "insurance_expiry": None,
    }
    values.update(overrides)
    return values


class TestWarmMedia:
    def test_save_warms_photos_and_3d(self, qapp, context, monkeypatch):
        import app.staff.fleet_panels as panels
        import app.staff.pages.fleet as fleet_mod

        _FakeRefresh.calls = []
        monkeypatch.setattr(panels, "_MediaRefresh", _FakeRefresh)
        toasts = []
        monkeypatch.setattr(fleet_mod, "toast", lambda *a, **k: toasts.append(a[1]))
        page = _fleet_page(context)
        monkeypatch.setattr(page, "_media_pool", _SyncPool())

        page._warm_media(7, "ABC-1234")

        assert _FakeRefresh.calls == [(7, False)]
        assert toasts == ["Media ready for ABC-1234 (1)."]
        page.deleteLater()

    def test_failed_warm_stays_quiet(self, qapp, context, monkeypatch):
        import app.staff.fleet_panels as panels
        import app.staff.pages.fleet as fleet_mod

        monkeypatch.setattr(panels, "_MediaRefresh", _FailingRefresh)
        toasts = []
        monkeypatch.setattr(fleet_mod, "toast", lambda *a, **k: toasts.append(a[1]))
        page = _fleet_page(context)
        monkeypatch.setattr(page, "_media_pool", _SyncPool())

        page._warm_media(7, "ABC-1234")

        assert toasts == []
        page.deleteLater()

    def test_edit_rename_forces_refresh(self, qapp, context, monkeypatch):
        import app.staff.fleet_panels as panels
        import app.staff.pages.fleet as fleet_mod

        vid = _vehicle(context)
        _FakeRefresh.calls = []
        monkeypatch.setattr(panels, "_MediaRefresh", _FakeRefresh)
        monkeypatch.setattr(fleet_mod, "toast", lambda *a, **k: None)
        page = _fleet_page(context)
        monkeypatch.setattr(page, "_media_pool", _SyncPool())

        page._on_edit_submit(vid, _edit_values(make="Honda", model="Civic"))
        _pump()

        assert (vid, True) in _FakeRefresh.calls
        page.deleteLater()

    def test_edit_without_rename_does_not_refresh(self, qapp, context, monkeypatch):
        import app.staff.fleet_panels as panels
        import app.staff.pages.fleet as fleet_mod

        vid = _vehicle(context)
        _FakeRefresh.calls = []
        monkeypatch.setattr(panels, "_MediaRefresh", _FakeRefresh)
        monkeypatch.setattr(fleet_mod, "toast", lambda *a, **k: None)
        page = _fleet_page(context)
        monkeypatch.setattr(page, "_media_pool", _SyncPool())

        page._on_edit_submit(vid, _edit_values())
        _pump()

        assert _FakeRefresh.calls == []
        page.deleteLater()
