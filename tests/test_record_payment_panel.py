"""Record-payment sheet: the amount due must be obvious.

The counter reported that the prefilled amount did not say what it was for.
These tests pin the visible amount-due caption, the rental-balance prefill,
the separate security-deposit copy, the zero-balance state, and the page
deep link re-reading the live balance.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QLabel, QWidget  # noqa: E402

from app.models import Booking  # noqa: E402
from app.services import payment_service  # noqa: E402
from app.staff.booking_panels import PaymentPanel  # noqa: E402
from app.staff.pages.bookings import BookingsPage  # noqa: E402
from app.utils.money import pesos  # noqa: E402


class _StubContext:
    """Only the read side the page deep-link needs, backed by the test session."""

    def __init__(self, session) -> None:
        self._session = session

    @contextmanager
    def reading(self):
        yield self._session


class _StubPage(QWidget):
    def __init__(self, session) -> None:
        super().__init__()
        self.context = _StubContext(session)
        self._panel = None

    def focus_booking(self, booking_id: int) -> bool:
        return True

    def _overlay_panel(self, panel) -> None:
        if self._panel is not None:
            self._panel.deleteLater()
        self._panel = panel

    def _hide_overlay(self) -> None:
        pass


def test_amount_due_is_visible_and_prefills_the_rental_balance(qapp):
    panel = PaymentPanel(Decimal("1234.56"))
    try:
        assert panel.amount_edit.text() == "1,234.56"
        assert panel.amount_due_label.text() == pesos(Decimal("1234.56"))

        labels = [label.text() for label in panel.findChildren(QLabel)]
        assert "Amount due" in labels

        copy = " ".join(labels).lower()
        assert "rental balance" in copy
        assert "security deposit" in copy
        assert "separate" in copy

        assert panel.go_button.isEnabled() is True
    finally:
        panel.deleteLater()


def test_zero_outstanding_is_shown_and_blocks_the_payment(qapp):
    panel = PaymentPanel(Decimal("0.00"))
    seen: list[dict] = []
    panel.submitted.connect(seen.append)
    try:
        assert panel.amount_edit.text() == "0.00"
        assert panel.amount_due_label.text() == pesos(Decimal("0.00"))
        assert panel.go_button.isEnabled() is False

        panel._on_go()
        assert seen == []

        # Nothing is owed, so even a manually typed amount must not turn the
        # record-payment action back on.
        panel.amount_edit.setText("100.00")
        assert panel.go_button.isEnabled() is False
        panel._on_go()
        assert seen == []
    finally:
        panel.deleteLater()


def test_zero_rental_outstanding_still_allows_a_security_deposit(qapp):
    panel = PaymentPanel(Decimal("0.00"), show_kind=True)
    seen: list[dict] = []
    panel.submitted.connect(seen.append)
    try:
        assert panel.go_button.isEnabled() is False
        panel.amount_edit.setText("2000.00")
        assert panel.go_button.isEnabled() is False

        panel.kind_chips.buttons["deposit"].click()
        assert panel.kind_combo.currentData() == "deposit"
        assert panel.go_button.isEnabled() is True
        assert panel.go_button.text() == "Record ₱2,000.00"

        panel._on_go()
        assert seen[-1]["amount"] == Decimal("2000.00")
        assert seen[-1]["kind"] == "deposit"

        panel.kind_chips.buttons["rental"].click()
        assert panel.go_button.isEnabled() is False
    finally:
        panel.deleteLater()


def test_open_payment_for_rereads_the_live_balance(
    qapp, session, customer, vehicle, staff
):
    booking = Booking(
        user_id=customer.user_id,
        vehicle_id=vehicle.vehicle_id,
        start_date=date.today(),
        end_date=date.today() + timedelta(days=2),
        total_cost=Decimal("7500.00"),
        status="confirmed",
        created_at=datetime(2026, 10, 20, 10, 0),
    )
    session.add(booking)
    session.flush()
    payment_service.record_payment(
        session,
        booking,
        Decimal("1000.00"),
        "cash",
        recorded_by=staff.user_id,
    )
    session.flush()

    page = _StubPage(session)
    try:
        assert BookingsPage.open_payment_for(page, booking.booking_id) is True
        panel = page._panel
        assert isinstance(panel, PaymentPanel)
        assert panel.amount_due_label.text() == "₱6,500.00"
        assert panel.amount_edit.text() == "6,500.00"

        # A payment taken while the page is open must be visible next time.
        payment_service.record_payment(
            session,
            booking,
            Decimal("500.00"),
            "cash",
            recorded_by=staff.user_id,
        )
        session.flush()

        assert BookingsPage.open_payment_for(page, booking.booking_id) is True
        panel = page._panel
        assert panel.amount_due_label.text() == "₱6,000.00"
        assert panel.amount_edit.text() == "6,000.00"
    finally:
        page.deleteLater()
