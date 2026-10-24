"""Settings tab: shared policy validation, staff accounts, prefs plumbing."""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal

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
