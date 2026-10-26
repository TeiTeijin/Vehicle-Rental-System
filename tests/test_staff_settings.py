"""Settings tab: shared policy validation, staff accounts, prefs plumbing."""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.database import Base  # noqa: E402
from app.services import settings_service, staff_service  # noqa: E402
from app.services.errors import StateError, ValidationError  # noqa: E402
from app.utils.money import money  # noqa: E402
from app.utils.security import hash_password  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def session(engine):
    from sqlalchemy.orm import Session
    with Session(engine, future=True) as session:
        yield session
        session.rollback()


@pytest.fixture
def branch(tmp_path, qapp):
    """StaffContext over a temp file DB with an admin + counter + customer."""
    from app.models import Users
    from app.staff.context import DatabaseSelection, DatabaseTarget, StaffContext, StaffUser
    path = tmp_path / "settings_branch.db"
    eng = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(eng)
    eng.dispose()
    context = StaffContext(DatabaseSelection(
        url=f"sqlite:///{path}", target=DatabaseTarget.LIVE, label="Test"))
    with context.session() as s:
        admin = Users(full_name="Branch Admin", email="admin@example.com",
                      password_hash=hash_password("x"), address="1 Test Street",
                      license_number="LIC0000001", license_expiry=date(2030, 1, 1),
                      role="admin", is_active=True,
                      created_at=datetime(2026, 1, 1, 9, 0))
        counter = Users(full_name="Counter Staff", email="counter@example.com",
                        password_hash=hash_password("x"), address="1 Test Street",
                        license_number="LIC0000002", license_expiry=date(2030, 1, 1),
                        role="staff", is_active=True,
                        created_at=datetime(2026, 1, 1, 9, 0))
        s.add_all([admin, counter])
        s.flush()
        admin_id, counter_id = admin.user_id, counter.user_id
    context.sign_in(StaffUser(admin_id, "Branch Admin", "admin@example.com", "admin"))

    class Built:
        pass
    built = Built()
    built.admin_id = admin_id
    built.counter_id = counter_id
    yield built, context
    context.dispose()


def _staff(session, **overrides):
    args = {"full_name": "Sam Counter", "email": "sam@example.com",
            "role": "staff", "temp_password": "temp-pass-1"}
    args.update(overrides)
    return staff_service.create_staff(session, **args)


class TestSharedPolicy:
    def test_defaults_load_without_rows(self, session):
        assert settings_service.get_raw(session, "scheduling_fee") == "100.00"
        assert settings_service.get_raw(session, "deposit_suv") == "3000.00"

    def test_money_round_trip(self, session):
        settings_service.set_value(session, "scheduling_fee", "250")
        assert settings_service.get_money(session, "scheduling_fee") == money("250.00")

    def test_negative_fee_rejected(self, session):
        with pytest.raises(ValidationError):
            settings_service.set_value(session, "scheduling_fee", "-5")

    def test_scheduling_fee_above_total_is_rejected(self):
        with pytest.raises(ValidationError, match="higher than the rental total"):
            settings_service.validate_scheduling_fee(
                Decimal("300.00"), Decimal("200.00")
            )
        # Equal is the boundary: the whole rental can be paid as the fee.
        settings_service.validate_scheduling_fee(
            Decimal("200.00"), Decimal("200.00")
        )

    def test_bad_choice_rejected(self, session):
        with pytest.raises(ValidationError):
            settings_service.set_value(session, "default_payment_method", "bitcoin")

    def test_bool_coercion(self, session):
        settings_service.set_value(session, "gcash_ref_required", "off")
        assert settings_service.get_bool(session, "gcash_ref_required") is False

    def test_unknown_key_rejected(self, session):
        with pytest.raises(ValidationError):
            settings_service.get_raw(session, "nope")

    def test_effective_fee_falls_back_without_table(self, session):
        session.execute(text("DROP TABLE APP_SETTINGS"))
        assert settings_service.scheduling_fee(session) == money("100.00")

    def test_int_floor(self, session):
        with pytest.raises(ValidationError):
            settings_service.set_value(session, "long_term_days", "1")


class TestStaffAccounts:
    def test_create_staff_must_change(self, session):
        row = _staff(session)
        assert row.must_change_password is True
        assert row.role == "staff"

    def test_duplicate_email_refused(self, session):
        _staff(session, email="dup@example.com")
        with pytest.raises(Exception):
            _staff(session, email="dup@example.com")

    def test_short_password_refused(self, session):
        with pytest.raises(ValidationError):
            _staff(session, temp_password="short")

    def test_change_password_clears_flag(self, session):
        row = _staff(session, temp_password="temp-pass-1")
        staff_service.change_password(
            session, row, current_password="temp-pass-1", new_password="brand-new-99")
        assert row.must_change_password is False

    def test_wrong_current_refused(self, session):
        row = _staff(session)
        with pytest.raises(ValidationError):
            staff_service.change_password(
                session, row, current_password="wrong", new_password="brand-new-99")

    def test_last_admin_cannot_deactivate(self, session):
        admin = _staff(session, email="a1@example.com", role="admin")
        with pytest.raises(StateError):
            staff_service.set_active(session, admin, False)
        _staff(session, email="a2@example.com", role="admin")
        staff_service.set_active(session, admin, False)
        assert admin.is_active is False

    def test_first_run_only_when_empty(self, session):
        admin = staff_service.ensure_first_admin(
            session, full_name="Root", email="root@example.com", password="root-pass-1")
        assert admin.role == "admin"
        with pytest.raises(StateError):
            staff_service.ensure_first_admin(
                session, full_name="Other", email="o@example.com", password="other-pass-1")


class TestPrefs:
    def test_local_prefs_round_trip(self, monkeypatch):
        import app.staff.prefs as prefs
        store: dict[str, object] = {}
        monkeypatch.setattr(prefs, "get", lambda k: store.get(k, prefs.DEFAULTS[k]))
        monkeypatch.setattr(prefs, "set", lambda k, v: store.__setitem__(k, v))
        prefs.set("behavior/rows_per_page", 25)
        assert prefs.get("behavior/rows_per_page") == 25
        assert prefs.rows_limit(500) == 25

    def test_rows_limit_clamped(self, monkeypatch):
        import app.staff.prefs as prefs
        monkeypatch.setattr(prefs, "get", lambda k: 99999 if "rows" in k else prefs.DEFAULTS[k])
        assert prefs.rows_limit(500) == 500


class TestSettingsPage:
    def test_page_builds_and_refreshes_for_admin(self, branch):
        from app.staff.pages.settings import SettingsPage
        from app.staff.shell import StaffShell
        _built, context = branch
        window = StaffShell(context)
        try:
            page = SettingsPage(window)
            page.refresh()
            assert page.save_shared_btn.isEnabled() is True
            assert page.users_table.row_count() >= 0
        finally:
            window.deleteLater()

    def test_shared_save_blocked_for_counter(self, branch, monkeypatch):
        from app.staff.context import StaffUser
        from app.staff.pages.settings import SettingsPage
        from app.staff.shell import StaffShell
        import app.staff.shell as shell_mod
        monkeypatch.setattr(shell_mod, "toast", lambda *a, **k: None)
        built, context = branch
        context.sign_in(StaffUser(built.counter_id, "Counter", "counter@example.com", "staff"))
        window = StaffShell(context)
        try:
            page = SettingsPage(window)
            page.refresh()
            assert page.save_shared_btn.isEnabled() is False
            assert page._on_save_shared() is None
        finally:
            window.deleteLater()

    def test_staff_may_open_settings_via_shell(self, branch):
        from app.staff.shell import StaffShell
        _built, context = branch
        window = StaffShell(context)
        try:
            window._on_signed_in(context.user)
            assert window.show_page("settings") is True
            assert window.current_page_key == "settings"
        finally:
            window.deleteLater()

    def test_backup_button_writes_only_a_sanitized_snapshot(
        self, branch, tmp_path, monkeypatch
    ):
        import stat

        from sqlalchemy import create_engine, select
        from sqlalchemy.engine import make_url
        from sqlalchemy.orm import Session

        import app.staff.pages.settings as settings_mod
        from app.models import Vehicle, Vehicle_Category, Vehicle_Media
        from app.staff.pages.settings import SettingsPage
        from app.staff.shell import StaffShell

        _built, context = branch
        source = Path(make_url(context.selection.url).database)
        with context.session() as session:
            category = Vehicle_Category(
                category_name="Car", base_rate_multiplier="1.00"
            )
            session.add(category)
            session.flush()
            vehicle = Vehicle(
                category_id=category.category_id,
                make="Ford",
                model="Explorer",
                year=2024,
                plate_number="BAK1234",
                daily_rate="50.00",
                mileage=1200,
                status="available",
                created_at=datetime(2026, 1, 2, 9, 0),
            )
            session.add(vehicle)
            session.flush()
            session.add(
                Vehicle_Media(
                    vehicle_id=vehicle.vehicle_id,
                    source="carimagesapi",
                    view_angle="front34",
                    image_url=(
                        "https://carimagesapi.com/image?sig=abc&api_key=secret"
                    ),
                    model_3d_url=(
                        "https://carimagesapi.com/model?sig=def&api_key=secret"
                    ),
                    cached_at=datetime(2026, 1, 2, 9, 0),
                )
            )

        before = source.read_bytes()
        monkeypatch.setattr(
            settings_mod.prefs, "export_folder", lambda default: tmp_path
        )
        monkeypatch.setattr(settings_mod, "toast", lambda *args, **kwargs: None)

        window = StaffShell(context)
        try:
            page = SettingsPage(window)
            page._on_backup()
        finally:
            window.deleteLater()

        backups = list(tmp_path.glob("backup_*.db"))
        assert len(backups) == 1
        snapshot = backups[0]
        assert b"api_key=" not in snapshot.read_bytes()
        assert b"secret" not in snapshot.read_bytes()
        assert source.read_bytes() == before
        if os.name != "nt":
            assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600

        target = create_engine(f"sqlite:///{snapshot}")
        with Session(target) as session:
            media = session.execute(select(Vehicle_Media)).scalar_one()
            assert media.image_url == ""
            assert media.model_3d_url == ""
            assert media.source == "carimagesapi"
        target.dispose()


class TestWarmStaffModals:
    @staticmethod
    def _select(page, email: str) -> None:
        for row in range(page.users_table.row_count()):
            if page.users_table.cell_text(row, 1) == email:
                page.users_table.table.selectRow(row)
                return
        raise AssertionError(f"{email} not in staff table")

    @staticmethod
    def _window_page(branch):
        from app.staff.pages.settings import SettingsPage
        from app.staff.shell import StaffShell

        _built, context = branch
        window = StaffShell(context)
        page = SettingsPage(window)
        page.refresh()
        return window, page

    def test_settings_cards_use_warm_panels_and_rail_tags(self, branch):
        from PySide6.QtWidgets import QFrame, QLabel

        window, page = self._window_page(branch)
        try:
            cards = [
                frame
                for frame in page.findChildren(QFrame)
                if frame.objectName() == "pagePanel"
                and frame.property("variant") == "warm"
            ]
            assert len(cards) >= 9
            tags = {
                label.text()
                for label in page.findChildren(QLabel)
                if label.objectName() == "settingsTag"
            }
            assert tags == {"This PC only", "Shared", "Admin"}
            assert page._tools_panel.property("variant") == "warmHost"
        finally:
            window.deleteLater()

    def test_reset_password_panel_is_warm_and_validates(self, qapp):
        from PySide6.QtWidgets import QLineEdit

        from app.staff.settings_panels import ResetPwPanel

        panel = ResetPwPanel("counter@example.com")
        try:
            assert panel.card.width() == 540
            assert panel.email_label.text() == "counter@example.com"
            assert panel.temp.echoMode() == QLineEdit.EchoMode.Password

            panel.temp_show.click()
            assert panel.temp.echoMode() == QLineEdit.EchoMode.Normal
            assert panel.temp_show.text() == "Hide"

            panel.generate.click()
            assert len(panel.temp.text()) >= 8
            assert panel.temp.echoMode() == QLineEdit.EchoMode.Normal

            seen: list[dict] = []
            panel.submitted.connect(seen.append)
            panel.temp.clear()
            panel._on_save()
            assert "Enter a temporary password." in panel.error.text()
            assert panel.error.isHidden() is False

            panel.temp.setText("short")
            panel._on_save()
            assert "at least 8" in panel.error.text()
            assert seen == []

            panel.temp.setText("temp-pass-9")
            panel._on_save()
            assert seen == [{"temp_password": "temp-pass-9"}]
            assert panel.error.isHidden() is True
        finally:
            panel.deleteLater()

    def test_settings_reset_password_opens_warm_overlay(self, branch):
        from app.staff.settings_panels import ResetPwPanel

        window, page = self._window_page(branch)
        try:
            self._select(page, "counter@example.com")
            assert page.open_reset_pw() is True
            overlay = page._overlay
            panel = overlay.findChild(ResetPwPanel)
            assert panel is not None
            assert overlay.isHidden() is False
            assert overlay.scroll.width() == 540
            assert panel.card.width() == 540
            assert panel.email_label.text() == "counter@example.com"
            assert panel.objectName() == "bookingModal"
            assert panel.property("variant") == "settings"
        finally:
            page._hide_overlay()
            window.deleteLater()

    def test_add_staff_opens_warm_900_overlay(self, branch):
        from app.staff.settings_panels import StaffFormPanel

        window, page = self._window_page(branch)
        try:
            assert page.open_add_staff() is True
            overlay = page._overlay
            panel = overlay.findChild(StaffFormPanel)
            assert panel is not None
            assert overlay.scroll.width() == 900
            assert panel.card.width() == 900
            assert panel.role_chips.buttons["staff"].isChecked() is True
            panel.role.setCurrentText("admin")
            assert panel.role_chips.buttons["admin"].isChecked() is True
        finally:
            page._hide_overlay()
            window.deleteLater()

    def test_accent_panel_is_warm_and_submits_hex(self, qapp):
        from app.staff.settings_panels import AccentPanel

        panel = AccentPanel("#D97757")
        try:
            assert panel.card.width() == 540
            assert panel.hex_edit.text().lower() == "#d97757"
            panel.select_colour("#2563EB")
            assert panel.hex_edit.text().lower() == "#2563eb"

            seen: list[dict] = []
            panel.submitted.connect(seen.append)
            panel.hex_edit.setText("#12ab34")
            panel._on_save()
            assert seen == [{"accent": "#12ab34"}]

            panel.hex_edit.setText("not-a-colour")
            panel._on_save()
            assert "hex colour" in panel.error.text()
            assert seen == [{"accent": "#12ab34"}]
        finally:
            panel.deleteLater()

    def test_deactivate_confirmation_uses_warm_modal_arguments(
        self, branch, monkeypatch
    ):
        import app.staff.pages.settings as settings_mod
        import app.staff.widgets as widgets

        captured: dict = {}

        def fake_confirm(parent, question, **kwargs):
            captured["parent"] = parent
            captured["question"] = question
            captured.update(kwargs)
            return False

        monkeypatch.setattr(widgets, "confirm_modal", fake_confirm)
        monkeypatch.setattr(settings_mod, "toast", lambda *a, **k: None)
        window, page = self._window_page(branch)
        try:
            self._select(page, "counter@example.com")
            page._on_toggle_active()
            assert captured["parent"] is page
            assert captured["question"] == "Deactivate counter@example.com?"
            assert captured["destructive"] is True
            assert captured["confirm_text"] == "Deactivate"
            assert captured["pref"] == "behavior/confirm_deactivate"
            with page.context.reading() as session:
                from app.models import Users
                row = session.query(Users).filter_by(
                    email="counter@example.com").one()
                assert row.is_active is True
        finally:
            window.deleteLater()

    def test_reactivate_confirmation_is_not_destructive(self, branch, monkeypatch):
        import app.staff.pages.settings as settings_mod
        import app.staff.widgets as widgets
        from app.models import Users

        _built, context = branch
        with context.session() as session:
            row = session.query(Users).filter_by(
                email="counter@example.com").one()
            staff_service.set_active(session, row, False)

        captured: dict = {}

        def fake_confirm(parent, question, **kwargs):
            captured["question"] = question
            captured.update(kwargs)
            return True

        monkeypatch.setattr(widgets, "confirm_modal", fake_confirm)
        monkeypatch.setattr(settings_mod, "toast", lambda *a, **k: None)
        window, page = self._window_page(branch)
        try:
            self._select(page, "counter@example.com")
            page._on_toggle_active()
            assert captured["question"] == "Reactivate counter@example.com?"
            assert captured["destructive"] is False
            assert captured["confirm_text"] == "Reactivate"
            assert captured["pref"] == "behavior/confirm_deactivate"
            with page.context.reading() as session:
                row = session.query(Users).filter_by(
                    email="counter@example.com").one()
                assert row.is_active is True
        finally:
            window.deleteLater()

    def test_last_active_admin_guard_survives_warm_confirmation(
        self, branch, monkeypatch
    ):
        import app.staff.pages.settings as settings_mod
        import app.staff.widgets as widgets
        from app.models import Users

        seen: list[tuple[str, str]] = []

        monkeypatch.setattr(
            widgets, "confirm_modal", lambda *a, **k: True
        )
        monkeypatch.setattr(
            settings_mod, "toast", lambda _p, message, kind="ok": seen.append(
                (message, kind))
        )
        window, page = self._window_page(branch)
        try:
            self._select(page, "admin@example.com")
            page._on_toggle_active()
            assert any(
                "Cannot deactivate the last active admin." in message
                for message, _kind in seen
            )
            with page.context.reading() as session:
                row = session.query(Users).filter_by(
                    email="admin@example.com").one()
                assert row.is_active is True
        finally:
            window.deleteLater()
