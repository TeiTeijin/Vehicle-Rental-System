"""Regression tests for the Check Details gallery, 3D preview, and checker.

The staff process must never create a QWebEngineView: opening 3D hands the
model to a separate preview process. These tests pin that architecture and
the gallery/cache/availability fixes around it.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from PySide6.QtCore import QBuffer, QIODevice, QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

from app.database import Base
from app.models import Booking, Users, Vehicle, Vehicle_Category
from app.services.vehicle_photos import VehicleRef
from app.staff.context import (
    DatabaseSelection,
    DatabaseTarget,
    StaffContext,
    StaffUser,
)
from app.staff.shell import StaffShell
from app.utils.security import hash_password


@pytest.fixture
def details_branch(tmp_path):
    """A tiny signed-in branch: one free car and one car with a booking."""
    path = tmp_path / "details-branch.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    engine.dispose()
    context = StaffContext(
        DatabaseSelection(
            url=f"sqlite:///{path}", target=DatabaseTarget.LIVE, label="Test"
        )
    )
    today = date.today()
    with context.session() as session:
        category = Vehicle_Category(
            category_name="Car", base_rate_multiplier=1.0
        )
        session.add(category)
        session.flush()
        customer = Users(
            full_name="Ana Reyes",
            email="ana-details@example.com",
            phone="09170000001",
            password_hash=hash_password("secret"),
            address="1 Test Street",
            license_number="LIC0000001",
            license_expiry=today + timedelta(days=365),
            role="customer",
            created_at=datetime(2026, 1, 1, 9, 0),
        )
        session.add(customer)
        session.flush()
        free_car = Vehicle(
            category_id=category.category_id,
            make="Toyota",
            model="Vios",
            year=2022,
            plate_number="FREE-001",
            daily_rate=Decimal("2500.00"),
            mileage=10_000,
            status="available",
            created_at=datetime(2026, 1, 1, 9, 0),
        )
        promised_car = Vehicle(
            category_id=category.category_id,
            make="Honda",
            model="City",
            year=2021,
            plate_number="PROM-001",
            daily_rate=Decimal("2500.00"),
            mileage=12_000,
            status="available",
            created_at=datetime(2026, 1, 1, 9, 0),
        )
        session.add_all([free_car, promised_car])
        session.flush()
        session.add(
            Booking(
                user_id=customer.user_id,
                vehicle_id=promised_car.vehicle_id,
                start_date=today + timedelta(days=2),
                end_date=today + timedelta(days=4),
                total_cost=Decimal("7500.00"),
                status="confirmed",
                created_at=datetime(2026, 9, 1, 10, 0),
            )
        )
        session.flush()
        branch = SimpleNamespace(
            context=context,
            free_car_id=free_car.vehicle_id,
            promised_car_id=promised_car.vehicle_id,
        )
    context.sign_in(
        StaffUser(1, "Branch Admin", "admin-details@example.com", "admin")
    )
    try:
        yield branch
    finally:
        context.dispose()


@pytest.fixture
def details_shell(qapp, details_branch):
    shell = StaffShell(details_branch.context)
    try:
        yield shell, details_branch
    finally:
        shell.deleteLater()
        qapp.processEvents()


def _png_bytes() -> bytes:
    image = QImage(32, 24, QImage.Format.Format_RGB32)
    image.fill(0x336699)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    return bytes(buf.data())


def _pump(qapp, predicate=None, timeout_ms: int = 3000) -> None:
    """Let queued worker results land without a fixed sleep."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate is not None and predicate():
            return
        time.sleep(0.005)


def _click_check_and_wait(qapp, dialog, timeout_ms: int = 3000) -> list:
    """Run one checker click and wait for its worker signal to land."""
    finished: list = []

    def _landed(*args) -> None:
        finished.append(args)

    panel = dialog.panel
    panel._check_signals.finished.connect(_landed)
    try:
        dialog.check_button.click()
        _pump(qapp, lambda: bool(finished), timeout_ms)
    finally:
        try:
            panel._check_signals.finished.disconnect(_landed)
        except (RuntimeError, TypeError):
            pass
    assert finished, "availability check never finished"
    return finished


class _DummyPool:
    def __init__(self) -> None:
        self.jobs = []

    def start(self, job) -> None:
        self.jobs.append(job)


def _gallery(ref=None, *, autoload: bool = False):
    from app.staff.vehicle_details import Gallery

    ref = ref or VehicleRef(make="Honda", model="Civic", year=2022)
    gallery = Gallery(
        ref, ref.make, ref.model, ref.year, autoload=autoload
    )
    return gallery, ref


class TestSpinner:
    def test_start_stop_and_animated_arc(self, qapp):
        from app.widgets.spinner import Spinner

        spinner = Spinner()
        assert spinner.is_running() is False
        assert spinner.isHidden() is True

        spinner.set_running(True)
        assert spinner.is_running() is True
        assert spinner._timer.isActive() is True
        spinner.show()
        qapp.processEvents()
        first = spinner._angle
        QTest.qWait(60)
        qapp.processEvents()
        assert spinner._angle != first
        assert spinner.grab().toImage().isNull() is False

        spinner.stop()
        assert spinner.is_running() is False
        assert spinner._timer.isActive() is False

    def test_set_running_false_stops_the_timer(self, qapp):
        from app.widgets.spinner import Spinner

        spinner = Spinner()
        spinner.start()
        assert spinner._timer.isActive() is True
        spinner.set_running(False)
        assert spinner.is_running() is False
        assert spinner._timer.isActive() is False


class TestGalleryCache:
    def _gallery(self, monkeypatch):
        gallery, _ref = _gallery()
        return gallery

    def test_the_exact_view_wins_before_any_fallback(self, monkeypatch):
        from app.services import image_cache

        gallery = self._gallery(monkeypatch)
        seen = []
        png = _png_bytes()

        def _load(make, model, year, variant=""):
            seen.append(variant)
            if variant == "left":
                return SimpleNamespace(data=png)
            if variant == "front":
                return SimpleNamespace(data=b"front-bytes")
            return None

        monkeypatch.setattr(image_cache, "load", _load)
        assert gallery._cached_bytes("left") == png
        assert seen == ["left"]

    def test_only_front_falls_back_to_front34_and_default(self, monkeypatch):
        from app.services import image_cache

        gallery = self._gallery(monkeypatch)
        png = _png_bytes()
        seen = []
        available = {"front34": png}

        def _load(make, model, year, variant=""):
            seen.append(variant)
            return SimpleNamespace(data=available[variant]) if variant in available else None

        monkeypatch.setattr(image_cache, "load", _load)
        assert gallery._cached_bytes("front") == png
        assert seen == ["front", "front34"]

        seen.clear()
        available.clear()
        available["default"] = png
        assert gallery._cached_bytes("front") == png
        assert seen == ["front", "front34", "default"]

    def test_side_views_never_use_the_front_fallback(self, monkeypatch):
        from app.services import image_cache

        gallery = self._gallery(monkeypatch)
        png = _png_bytes()

        def _load(make, model, year, variant=""):
            if variant in ("front", "front34", "default"):
                return SimpleNamespace(data=png)
            return None

        monkeypatch.setattr(image_cache, "load", _load)
        assert gallery._cached_bytes("left") is None
        assert gallery._cached_bytes("right") is None


class TestGalleryViews:
    def test_motorcycles_show_only_the_front_and_no_3d_tab(self, qapp):
        ref = VehicleRef(
            make="Honda", model="Click 125i", year=2021, motorcycle=True
        )
        gallery, _ = _gallery(ref)
        assert gallery.views == ("front",)
        assert "3d" not in gallery.view_buttons
        assert list(gallery.view_buttons) == ["front"]

    def test_cars_keep_all_photo_views_and_the_3d_tab(self, qapp):
        gallery, _ = _gallery()
        assert gallery.views == ("front", "left", "right", "3d")
        assert gallery.view_buttons["3d"].text() == "3D"

    def test_the_gallery_has_no_webengine_member(self, qapp):
        gallery, _ = _gallery()
        assert getattr(gallery, "_web_view", None) is None
        assert not hasattr(gallery, "_ensure_viewer")


class TestNoWebEngineInMainProcess:
    def test_staff_modules_do_not_import_webengine(self):
        root = Path(__file__).resolve().parents[1]
        code = (
            "import sys\n"
            "import app.staff.vehicle_details\n"
            "import app.staff.fleet_panels\n"
            "assert 'PySide6.QtWebEngineWidgets' not in sys.modules\n"
            "assert 'PySide6.QtWebEngineCore' not in sys.modules\n"
        )
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr

    def test_the_details_module_never_constructs_a_view(self):
        from app.staff import vehicle_details

        source = inspect.getsource(vehicle_details)
        assert "QWebEngineView(" not in source
        assert "QWebEngineSettings" not in source


class TestExternalPreviewLaunch:
    def test_launch_starts_the_child_module_with_path_and_label(
        self, monkeypatch, tmp_path
    ):
        from app.config import BASE_DIR
        from app.staff import model_preview

        glb = tmp_path / "car model.glb"
        glb.write_bytes(b"glTF")
        calls = []

        class _Process:
            @staticmethod
            def startDetached(program, args, *rest):
                calls.append((program, list(args), rest))
                return True, 4321

        monkeypatch.setattr(model_preview, "QProcess", _Process)
        assert model_preview.launch_model_preview(glb, "Kia Soluto") is True
        program, args, rest = calls[0]
        assert program == sys.executable
        assert args[:2] == ["-m", "app.staff.model_preview_window"]
        assert args[2] == str(glb)
        assert args[3] == "Kia Soluto"
        assert rest and Path(rest[0]) == BASE_DIR

    def test_launch_returns_false_when_the_process_cannot_start(
        self, monkeypatch, tmp_path
    ):
        from app.staff import model_preview

        glb = tmp_path / "car.glb"
        glb.write_bytes(b"glTF")

        class _Process:
            @staticmethod
            def startDetached(program, args, *rest):
                return False, -1

        monkeypatch.setattr(model_preview, "QProcess", _Process)
        assert model_preview.launch_model_preview(glb, "Kia Soluto") is False

    def test_launch_refuses_a_missing_glb(self, monkeypatch, tmp_path):
        from app.staff import model_preview

        called = []
        monkeypatch.setattr(
            model_preview,
            "QProcess",
            SimpleNamespace(startDetached=lambda *a, **k: called.append(a)),
        )
        assert (
            model_preview.launch_model_preview(tmp_path / "missing.glb", "X")
            is False
        )
        assert called == []


class TestGallery3D:
    def test_3d_tab_shows_a_ready_card_and_opens_the_preview(
        self, qapp, monkeypatch, tmp_path
    ):
        from app.staff import vehicle_details as vd

        glb = tmp_path / "car.glb"
        glb.write_bytes(b"glTF")
        gallery, _ = _gallery()
        gallery._model_path = str(glb)
        gallery.set_view("3d")
        qapp.processEvents()

        assert gallery._stack.currentWidget() is gallery.model_card
        assert gallery.open_3d_button.text() == "Open 3D preview"
        assert gallery.auto_button.isHidden() is True
        assert gallery.reset_button.isHidden() is True

        calls = []
        monkeypatch.setattr(
            vd.model_preview,
            "launch_model_preview",
            lambda path, label: calls.append((Path(path), label)) or True,
        )
        gallery.open_3d_button.click()
        assert calls == [(glb, "Honda Civic 2022")]

    def test_a_failed_launch_tells_the_user(self, qapp, monkeypatch, tmp_path):
        from app.staff import vehicle_details as vd

        glb = tmp_path / "car.glb"
        glb.write_bytes(b"glTF")
        gallery, _ = _gallery()
        gallery._model_path = str(glb)
        gallery.set_view("3d")
        monkeypatch.setattr(vd.model_preview, "launch_model_preview", lambda *a: False)
        gallery.open_3d_button.click()
        assert "could not open" in gallery.model_note.text().lower()

    def test_no_model_shows_a_clear_message(self, qapp):
        gallery, _ = _gallery()
        gallery._model_requested = True
        gallery.set_view("3d")
        gallery._on_model_failed()
        qapp.processEvents()
        assert gallery._stack.currentWidget() is gallery.image_label
        assert "No 3D model" in gallery.image_label.text()

    def test_a_late_model_ready_does_not_switch_back_to_3d(self, qapp, tmp_path):
        glb = tmp_path / "car.glb"
        glb.write_bytes(b"glTF")
        gallery, _ = _gallery()
        image = QImage(8, 8, QImage.Format.Format_RGB32)
        image.fill(0x112233)
        gallery._photos["front"] = image
        gallery._model_requested = True
        gallery.set_view("3d")
        gallery.set_view("front")
        gallery._on_model_ready(str(glb))
        qapp.processEvents()
        assert gallery._current == "front"
        assert gallery._stack.currentWidget() is gallery.image_label
        assert gallery.spinner.is_running() is False

    def test_model_failure_after_a_tab_switch_keeps_the_photo(self, qapp):
        gallery, _ = _gallery()
        gallery._model_requested = True
        gallery.set_view("3d")
        gallery.set_view("left")
        gallery._on_model_failed()
        qapp.processEvents()
        assert gallery._current == "left"
        assert gallery._stack.currentWidget() is gallery.image_label


class TestGallerySpinner:
    def test_photo_loading_starts_and_stops_the_spinner(
        self, qapp, monkeypatch
    ):
        from app.staff import vehicle_details as vd

        pool = _DummyPool()
        monkeypatch.setattr(vd, "media_pool", lambda: pool)
        gallery, _ = _gallery()
        gallery.set_view("left")
        assert pool.jobs
        assert gallery.spinner.is_running() is True
        gallery._on_photo_failed("left")
        assert gallery.spinner.is_running() is False

    def test_photo_success_stops_the_spinner(self, qapp, monkeypatch):
        from app.staff import vehicle_details as vd

        pool = _DummyPool()
        monkeypatch.setattr(vd, "media_pool", lambda: pool)
        gallery, _ = _gallery()
        gallery.set_view("left")
        gallery._on_photo_ready("left", _png_bytes())
        assert gallery.spinner.is_running() is False
        assert gallery._current == "left"

    def test_3d_loading_starts_and_stops_the_spinner(self, qapp, monkeypatch):
        from app.staff import vehicle_details as vd

        pool = _DummyPool()
        monkeypatch.setattr(vd, "media_pool", lambda: pool)
        gallery, _ = _gallery()
        gallery.set_view("3d")
        assert pool.jobs
        assert gallery.spinner.is_running() is True
        gallery._on_model_failed()
        assert gallery.spinner.is_running() is False


class TestAvailability:
    def test_the_check_runs_off_the_gui_thread_and_disables_the_button(
        self, qapp, details_branch, monkeypatch
    ):
        from app.services import booking_service
        from app.staff.vehicle_details import VehicleDetailsDialog

        built = details_branch
        context = built.context
        seen = {}
        gate = threading.Event()

        def _assert_available(*args, **kwargs):
            seen["thread"] = threading.get_ident()
            gate.wait(2)

        monkeypatch.setattr(booking_service, "assert_available", _assert_available)
        dialog = VehicleDetailsDialog(context, built.free_car_id)
        today = dialog.start_edit.date()
        dialog.start_edit.setDate(today.addDays(10))
        dialog.end_edit.setDate(today.addDays(11))
        finished = []

        def _landed(*args) -> None:
            finished.append(args)

        dialog.panel._check_signals.finished.connect(_landed)
        started_at = time.monotonic()
        dialog.check_button.click()
        elapsed = time.monotonic() - started_at

        # The click must return while the worker is still gated: no nested
        # event loop may hold the GUI for the checker.
        assert elapsed < 0.15
        assert dialog.panel._checking is True
        assert dialog.check_button.isEnabled() is False
        assert dialog.check_spinner.is_running() is True
        gate.set()
        _pump(qapp, lambda: bool(finished))

        assert finished
        assert seen["thread"] != threading.get_ident()
        assert dialog.check_button.isEnabled() is True
        assert dialog.check_spinner.is_running() is False
        assert dialog.checked_window is not None
        assert dialog.rent_button.isEnabled() is True
        dialog.deleteLater()

    def test_a_conflict_disables_rent_until_the_next_successful_check(
        self, qapp, details_branch
    ):
        from app.staff.vehicle_details import VehicleDetailsDialog

        built = details_branch
        context = built.context
        dialog = VehicleDetailsDialog(context, built.promised_car_id)
        today = dialog.start_edit.date()
        dialog.start_edit.setDate(today.addDays(2))
        dialog.end_edit.setDate(today.addDays(3))
        _click_check_and_wait(qapp, dialog)

        assert "Not available" in dialog.check_result.text()
        assert dialog.checked_window is None
        assert dialog.rent_button.isEnabled() is False
        seen = []
        dialog.rent_requested.connect(seen.append)
        dialog.rent_button.click()
        assert seen == []

        dialog.start_edit.setDate(today.addDays(10))
        dialog.end_edit.setDate(today.addDays(11))
        # A failed check keeps Rent gated until a later check succeeds.
        assert dialog.rent_button.isEnabled() is False
        _click_check_and_wait(qapp, dialog)
        assert dialog.checked_window is not None
        assert dialog.rent_button.isEnabled() is True
        dialog.deleteLater()

    def test_a_check_error_disables_rent(self, qapp, details_branch, monkeypatch):
        from app.services import booking_service
        from app.staff.vehicle_details import VehicleDetailsDialog

        built = details_branch
        context = built.context
        monkeypatch.setattr(
            booking_service,
            "assert_available",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database offline")),
        )
        dialog = VehicleDetailsDialog(context, built.free_car_id)
        today = dialog.start_edit.date()
        dialog.start_edit.setDate(today.addDays(10))
        dialog.end_edit.setDate(today.addDays(11))
        _click_check_and_wait(qapp, dialog)

        assert dialog.check_error.isHidden() is False
        assert "database offline" in dialog.check_error.text()
        assert dialog.checked_window is None
        assert dialog.rent_button.isEnabled() is False
        dialog.deleteLater()


class TestDetailsOverlayWidth:
    def test_the_card_fits_a_900px_window(self, qapp, details_shell):
        from app.staff.vehicle_details import VehicleDetailsOverlay

        shell, built = details_shell
        context = built.context
        shell.show()
        shell.resize(900, 700)
        qapp.processEvents()
        overlay = VehicleDetailsOverlay(
            context, built.free_car_id, shell, autoload=False
        )
        try:
            overlay.show_overlay()
            qapp.processEvents()
            assert overlay.scroll.width() == 900 - 48
            assert overlay.scroll.width() < 900
        finally:
            overlay.hide_overlay()
            overlay.deleteLater()

    def test_the_card_grows_with_the_window_but_caps_at_900(
        self, qapp, details_shell
    ):
        from app.staff.vehicle_details import VehicleDetailsOverlay

        shell, built = details_shell
        context = built.context
        shell.show()
        shell.resize(900, 700)
        qapp.processEvents()
        overlay = VehicleDetailsOverlay(
            context, built.free_car_id, shell, autoload=False
        )
        try:
            overlay.show_overlay()
            qapp.processEvents()
            shell.resize(1200, 800)
            qapp.processEvents()
            assert overlay.scroll.width() == 900
        finally:
            overlay.hide_overlay()
            overlay.deleteLater()


class TestCardBodyClick:
    def test_clicking_the_body_opens_the_same_details_action(self, qapp):
        from app.staff.vehicle_cards import RentalVehicle, VehicleCard

        card = VehicleCard(
            RentalVehicle(
                vehicle_id=42,
                make="Honda",
                model="Civic",
                year=2022,
                daily_rate=Decimal("2500"),
                seats=5,
            )
        )
        seen = []
        card.details_requested.connect(seen.append)
        card.resize(300, card.height())
        card.show()
        qapp.processEvents()

        QTest.mouseClick(
            card,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(card.width() // 2, 20),
        )
        qapp.processEvents()
        assert seen == [42]
        card.deleteLater()


class TestFleetExternalPreview:
    def test_fleet_details_offers_the_separate_preview(
        self, qapp, details_shell, monkeypatch, tmp_path
    ):
        from app.staff import fleet_panels
        from app.staff.fleet_panels import FleetDetailsPanel

        glb = tmp_path / "fleet.glb"
        glb.write_bytes(b"glTF")
        monkeypatch.setattr(
            fleet_panels.media_service, "get_or_fetch_media", lambda *a, **k: []
        )
        shell, built = details_shell
        context = built.context
        panel = FleetDetailsPanel(context, built.free_car_id, shell)
        panel._pool.waitForDone(15000)
        qapp.processEvents()
        panel._on_media_done(
            [
                {
                    "view_angle": "front34",
                    "image_url": "http://x/1.webp",
                    "model_3d_url": "https://cdn.test/car.glb",
                    "model_glb": str(glb),
                    "is_watermarked": False,
                    "image_data": None,
                }
            ]
        )
        qapp.processEvents()
        assert panel.open_3d_button.text() == "Open 3D preview"

        calls = []
        monkeypatch.setattr(
            fleet_panels.model_preview,
            "launch_model_preview",
            lambda path, label: calls.append((Path(path), label)) or True,
        )
        panel.open_3d_button.click()
        assert calls == [(glb, "Toyota Vios")]
        assert not hasattr(panel, "_web_view")
        panel.deleteLater()
