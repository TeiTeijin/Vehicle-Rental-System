"""Final-audit golden path: customer booking -> staff -> customer."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from app.models import Branch
from app.services import (
    booking_service,
    dashboard_service,
    payment_service,
    receipt_service,
    report_service,
)


@pytest.fixture
def branch(session):
    row = Branch(
        name="Main Branch",
        address="1 Main Street",
        open_time=time(7, 0),
        close_time=time(21, 0),
        is_active=True,
        created_at=datetime(2026, 1, 1, 8, 0),
    )
    session.add(row)
    session.flush()
    return row


def test_online_booking_reaches_staff_and_completes(
    session, customer, staff, vehicle, branch
):
    pickup = datetime(2026, 10, 1, 10, 0)
    return_dt = datetime(2026, 10, 3, 10, 0)

    booking = booking_service.create_online_booking(
        session,
        user=customer,
        vehicle=vehicle,
        pickup_dt=pickup,
        return_dt=return_dt,
        pickup_branch=branch,
        return_branch=branch,
        payment_method="cash",
    )
    assert booking.status == "pending"
    assert booking.channel == "online"
    assert booking.pickup_dt == pickup
    assert booking.return_dt == return_dt
    assert booking.reference_code

    booking_service.confirm_booking(session, booking)
    assert booking.status == "confirmed"

    booking_service.check_in(session, booking, staff, 1_000, "full")
    assert booking.status == "ongoing"
    assert vehicle.status == "rented"

    rental = booking.total_cost
    payment_service.record_payment(
        session, booking, rental, "cash", recorded_by=staff.user_id
    )
    deposit = booking_service.deposit_for_vehicle(vehicle)
    payment_service.record_payment(
        session,
        booking,
        deposit,
        "cash",
        kind="deposit",
        recorded_by=staff.user_id,
    )

    quote = booking_service.checkout_quote(
        session, booking, actual_return_date=date(2026, 10, 3)
    )
    assert quote.balance_due == 0

    booking_service.check_out(
        session, booking, staff, date(2026, 10, 3), 1_200, "full"
    )
    assert booking.status == "completed"
    assert vehicle.status == "available"

    customer_payments = payment_service.payments_for_user(session, customer.user_id)
    assert {row.kind for row in customer_payments} >= {"rental", "deposit"}

    receipt_text = "\n".join(
        row.text for row in receipt_service.receipt_rows(session, booking)
    )
    assert booking.reference_code in receipt_text
    assert f"Booking ID: {booking.booking_id}" in receipt_text

    today = date.today()
    revenue = report_service.revenue_report(session, today, today)
    assert revenue.rental == rental

    figures = dashboard_service.collect(
        session, today=today, weeks=0, recent=0, monthly=False
    )
    assert figures.revenue_today == rental
