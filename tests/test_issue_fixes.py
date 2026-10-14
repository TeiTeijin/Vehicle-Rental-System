"""Regression tests for the four counter-reported fixes.

1. Reference no. is only editable for GCash (PaymentPanel + wizard Step 4).
2. Fleet / Customers / staff lists are newest-first.
3. Toasts fire above an open frosted overlay, not underneath it.
4. Retiring a gone vehicle reports "no longer exists", never a crash.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.database import Base  # noqa: E402
from app.services.errors import NotFoundError  # noqa: E402
from app.staff.feedback import describe  # noqa: E402 (kind check)


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def engine():
    from sqlalchemy import create_engine
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


class TestReferenceGating:
    def test_payment_panel_ref_locked_until_gcash(self, qapp):
        from decimal import Decimal
        from app.staff.booking_panels import PaymentPanel
        panel = PaymentPanel(Decimal("100.00"))
        try:
            assert panel.method_combo.currentText() == "cash"
            assert panel.ref_edit.isEnabled() is False
            panel.method_combo.setCurrentText("gcash")
            assert panel.ref_edit.isEnabled() is True
            panel.ref_edit.setText("GC-1")
            panel.method_combo.setCurrentText("cash")
            assert panel.ref_edit.isEnabled() is False
            assert panel.ref_edit.text() == ""
        finally:
            panel.deleteLater()

    def test_wizard_ref_locked_until_gcash(self, qapp):
        from decimal import Decimal
        from PySide6.QtCore import QDate
        from app.staff.rental_wizard import PaymentPanel as WizardPay, WizardState
        from app.utils.money import money
        state = WizardState(vehicle_id=1, start=QDate.currentDate(),
                            end=QDate.currentDate().addDays(1),
                            days=1, total=money("2500.00"),
                            deposit=money("2000.00"))
        step = WizardPay(None, state)
        try:
            assert step.ref_edit.isEnabled() is False
            step.method_buttons["gcash"].click()
            assert step.ref_edit.isEnabled() is True
            step.ref_edit.setText("GC-1")
            step.method_buttons["cash"].click()
            assert step.ref_edit.isEnabled() is False
            assert step.ref_edit.text() == ""
        finally:
            step.deleteLater()


class TestNewestFirst:
    def _users(self, session, role="customer"):
        from app.models import Users
        from app.utils.security import hash_password
        out = []
        for i, day in enumerate((1, 5, 3)):
            row = Users(full_name=f"N{i}", email=f"nf{i}{role}@x.com",
                        password_hash=hash_password("x"), address="a",
                        license_number=f"LICNF{i}{role[:2]}",
                        license_expiry=date(2030, 1, 1), role=role,
                        is_active=True,
                        created_at=datetime(2026, 1, day, 9, 0))
            session.add(row)
            session.flush()
            out.append(row)
        return out

    def test_customers_newest_first(self, session):
        from app.staff.pages.customers import _customer_rows
        from app.staff.context import DatabaseSelection, DatabaseTarget, StaffContext
        from sqlalchemy import create_engine as _create
        import tempfile
        from pathlib import Path
        path = Path(tempfile.mkdtemp()) / "c.db"
        eng = _create(f"sqlite:///{path}", future=True)
        Base.metadata.create_all(eng)
        eng.dispose()
        context = StaffContext(DatabaseSelection(
            url=f"sqlite:///{path}", target=DatabaseTarget.LIVE, label="Test"))
        try:
            with context.session() as s:
                self._users(s)
            from app.staff.pages.customers import CustomerFilter
            rows = _customer_rows(context, CustomerFilter())
            assert [r[11] for r in rows] == ["05 Jan 2026", "03 Jan 2026", "01 Jan 2026"]
        finally:
            context.dispose()

    def test_staff_newest_first(self, session):
        from app.services import staff_service
        a = staff_service.create_staff(session, full_name="Old", email="old@x.com",
                                       role="staff", temp_password="temp-pass-1")
        a.created_at = datetime(2026, 1, 1, 9, 0)
        b = staff_service.create_staff(session, full_name="New", email="new@x.com",
                                       role="staff", temp_password="temp-pass-1")
        b.created_at = datetime(2026, 2, 1, 9, 0)
        session.flush()
        assert [u.email for u in staff_service.list_staff(session)] == [
            "new@x.com", "old@x.com"]


class TestToastAboveOverlay:
    def test_overlay_toast_parents_to_overlay(self, qapp):
        from PySide6.QtWidgets import QWidget, QVBoxLayout
        from app.staff.overlay import FrostedOverlay
        from app.staff.widgets import toast, _visible_overlay
        window = QWidget()
        window.resize(800, 600)
        QVBoxLayout(window)
        overlay = FrostedOverlay(window)
        window.show()
        qapp.processEvents()
        try:
            plain = toast(window, "behind")
            assert plain.parentWidget() is window
            overlay.show_overlay()
            qapp.processEvents()
            assert _visible_overlay(window) is overlay
            over = toast(window, "overlay failure", "error")
            qapp.processEvents()
            assert over.parentWidget() is overlay
            assert over.isVisible() is True
        finally:
            window.deleteLater()
            qapp.processEvents()


class TestRetireGoneRow:
    def test_retire_none_is_not_found(self, session):
        from app.services import fleet_service
        with pytest.raises(NotFoundError):
            fleet_service.retire_vehicle(session, None)
        with pytest.raises(NotFoundError):
            fleet_service.unretire_vehicle(session, None)

    def test_gone_row_describes_as_warning(self):
        message, _field, kind = describe(NotFoundError("That vehicle no longer exists."))
        assert "no longer exists" in message
        assert kind == "warn"
