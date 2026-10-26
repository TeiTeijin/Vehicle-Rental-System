"""The customer's payment history, and the empty tab that draws no cars."""

from __future__ import annotations

import os
from contextlib import nullcontext
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.services import booking_service, payment_service
from app.ui import payments as payments_ui
from app.ui import theme
from app.ui.session import Identity
from app.ui.vehicle_photo import VehiclePhoto


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous_style = app.styleSheet()
    theme.apply_theme(app)
    yield app
    app.processEvents()
    app.setStyleSheet(previous_style)
    app.processEvents()


def test_history_is_scoped_to_the_customer(
    session, customer, customer_factory, vehicle, booking_factory
):
    other = customer_factory()
    mine = booking_factory(customer, vehicle)
    theirs = booking_factory(other, vehicle)
    payment_service.record_payment(
        session, mine, "1200.00", "gcash", reference_no="MINE-ONLY"
    )
    payment_service.record_payment(
        session, theirs, "900.00", "cash", reference_no="THEIRS"
    )

    rows = payment_service.payments_for_user(session, customer.user_id)

    assert [row.reference_no for row in rows] == ["MINE-ONLY"]
    assert rows[0].booking.user_id == customer.user_id


def test_customer_payment_history_labels_applied_deposit(
    session, customer, vehicle, booking_factory
):
    booking = booking_factory(customer, vehicle)
    payment_service.record_payment(
        session, booking, "2000.00", "cash", kind="deposit"
    )
    booking_service.apply_penalty(
        session,
        booking,
        penalty_type="damage",
        amount=Decimal("500.00"),
        description="Scratch",
    )
    payment_service.settle_deposit(session, booking)

    applied = next(
        payment for payment in booking.payments if payment.kind == "deposit_applied"
    )
    row = payments_ui._row_for(applied)

    assert "Deposit applied" in row.details
    assert "Cash at the counter" not in row.details
    assert "no cash" in row.note.lower()
    assert row.when.startswith("Applied")


def test_a_customer_without_payments_has_an_empty_history(
    session, customer
):
    assert payment_service.payments_for_user(session, customer.user_id) == []


def test_an_empty_payments_tab_shows_no_cars(
    qapp, monkeypatch, session, customer
):
    monkeypatch.setattr(payments_ui, "SessionLocal", lambda: nullcontext(session))
    page = payments_ui.PaymentsPage()
    try:
        page.set_user(Identity.of(customer))

        assert page.list_layout.count() == 0
        assert page.findChildren(VehiclePhoto) == []
        assert page.summary.text() == "0 payments"
        assert "No payments recorded yet" in page.empty.text()
        assert page.empty.isHidden() is False
    finally:
        page.deleteLater()


def test_a_payment_draws_its_rented_vehicle(
    qapp, monkeypatch, session, customer, vehicle, booking_factory
):
    booking = booking_factory(customer, vehicle)
    payment_service.record_payment(
        session, booking, "7500.00", "gcash", reference_no="GCASH-TEST"
    )
    monkeypatch.setattr(payments_ui, "SessionLocal", lambda: nullcontext(session))
    # The photo loader is not what this test is about, and tests must not hit
    # the image API.
    monkeypatch.setattr(VehiclePhoto, "set_vehicle", lambda self, ref: None)

    page = payments_ui.PaymentsPage()
    try:
        page.set_user(Identity.of(customer))

        assert len(page._cards) == 1
        assert page.summary.text() == "1 payment"
        assert page.findChildren(VehiclePhoto)
        assert page.empty.isHidden() is True
        assert page._cards[0].row.vehicle_id == vehicle.vehicle_id
    finally:
        page.deleteLater()
