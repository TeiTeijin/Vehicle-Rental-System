"""Customer-flow guards found during the end-to-end account audit."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from app.models import Branch
from app.services import (
    auth_service,
    booking_service,
    fleet_service,
    notification_service,
    payment_service,
    profile_service,
)
from app.services.errors import ConflictError, PermissionDenied

PASSWORD = "correct horse battery staple"
PICKUP = datetime(2026, 9, 1, 10, 0)
RETURN = datetime(2026, 9, 3, 10, 0)


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


def _book(session, customer, vehicle, branch, **overrides):
    fields = {
        "user": customer,
        "vehicle": vehicle,
        "pickup_dt": PICKUP,
        "return_dt": RETURN,
        "pickup_branch": branch,
        "payment_method": "cash",
    }
    fields.update(overrides)
    return booking_service.create_online_booking(session, **fields)


def test_an_active_customer_can_sign_in(session, customer):
    user = auth_service.sign_in_customer(session, customer.email, PASSWORD)
    assert user.user_id == customer.user_id


def test_a_wrong_password_stays_generic(session, customer):
    with pytest.raises(auth_service.InvalidCredentials, match="Incorrect email"):
        auth_service.sign_in_customer(session, customer.email, "not-the-password")


def test_a_deactivated_customer_cannot_sign_in(session, customer):
    customer.is_active = False
    session.flush()

    with pytest.raises(auth_service.InactiveAccount, match="inactive"):
        auth_service.sign_in_customer(session, customer.email, PASSWORD)


def test_a_staff_account_cannot_use_the_customer_sign_in(session, staff):
    with pytest.raises(auth_service.NotACustomer, match="staff sign-in"):
        auth_service.sign_in_customer(session, staff.email, PASSWORD)


def test_the_shared_credential_check_still_lets_staff_in(session, staff):
    """The staff login wraps `sign_in` with its own role/active policy."""
    user = auth_service.sign_in(session, staff.email, PASSWORD)
    assert user.user_id == staff.user_id


def test_online_booking_works_for_an_eligible_customer(
    session, customer, vehicle, branch
):
    booking = _book(session, customer, vehicle, branch)

    assert booking.status == "pending"
    assert booking.channel == "online"
    assert booking.user_id == customer.user_id
    assert booking.pickup_branch_id == branch.branch_id


def test_signed_in_customer_sees_the_booking_receipt_and_profile_counts(
    session, customer, vehicle, branch
):
    user = auth_service.sign_in_customer(session, customer.email, PASSWORD)
    booking = _book(
        session,
        user,
        vehicle,
        branch,
        payment_method="gcash",
        payment_reference="AUDIT-GCASH-1",
    )

    bookings = booking_service.bookings_for(session, user)
    receipts = payment_service.payments_for_user(session, user.user_id)
    profile = profile_service.profile_for(session, user.user_id)

    assert booking.status == "confirmed"
    assert [row.booking_id for row in bookings] == [booking.booking_id]
    assert len(receipts) == 1
    assert receipts[0].amount == booking.total
    assert profile is not None
    assert profile.booking_count == 1
    assert profile.active_booking_count == 1
    assert profile.payment_count == 1
    assert profile.status_label == "Active Renter"


def test_one_customer_never_sees_another_customers_records(
    session, customer_factory, vehicle, second_vehicle, branch
):
    """Every customer-scoped read must be keyed by the signed-in identity.

    This is the service-level IDOR guard behind the Bookings, Payments,
    Notifications and Profile screens.
    """
    ana = customer_factory(
        full_name="Ana Santos", email="ana@example.com", phone="09170000001"
    )
    ben = customer_factory(
        full_name="Ben Cruz", email="ben@example.com", phone="09170000002"
    )
    ana_booking = _book(
        session,
        ana,
        vehicle,
        branch,
        payment_method="gcash",
        payment_reference="AUDIT-ANA-1",
    )
    ben_booking = _book(
        session,
        ben,
        second_vehicle,
        branch,
        payment_method="gcash",
        payment_reference="AUDIT-BEN-1",
    )

    assert [b.booking_id for b in booking_service.bookings_for(session, ana)] == [
        ana_booking.booking_id
    ]
    assert [b.booking_id for b in booking_service.bookings_for(session, ben)] == [
        ben_booking.booking_id
    ]

    ana_payments = payment_service.payments_for_user(session, ana.user_id)
    ben_payments = payment_service.payments_for_user(session, ben.user_id)
    assert [p.booking_id for p in ana_payments] == [ana_booking.booking_id]
    assert [p.booking_id for p in ben_payments] == [ben_booking.booking_id]

    ana_notes = notification_service.notifications_for(session, ana.user_id)
    ben_notes = notification_service.notifications_for(session, ben.user_id)
    assert ana_notes and ben_notes
    assert {n.booking_id for n in ana_notes} == {ana_booking.booking_id}
    assert {n.booking_id for n in ben_notes} == {ben_booking.booking_id}

    ana_profile = profile_service.profile_for(session, ana.user_id)
    ben_profile = profile_service.profile_for(session, ben.user_id)
    assert ana_profile is not None and ben_profile is not None
    assert ana_profile.user_id == ana.user_id
    assert ben_profile.user_id == ben.user_id
    assert ana_profile.booking_count == ben_profile.booking_count == 1
    assert ana_profile.payment_count == ben_profile.payment_count == 1


def test_online_booking_refuses_a_deactivated_customer(
    session, customer, vehicle, branch
):
    customer.is_active = False
    session.flush()

    with pytest.raises(ConflictError, match="deactivated"):
        _book(session, customer, vehicle, branch)


def test_online_booking_refuses_an_expired_licence(
    session, customer, vehicle, branch
):
    customer.license_expiry = date(2026, 8, 1)
    session.flush()

    with pytest.raises(ConflictError, match="Licence expired"):
        _book(session, customer, vehicle, branch)


def test_online_booking_refuses_a_staff_account(
    session, staff, vehicle, branch
):
    with pytest.raises(PermissionDenied, match="customer accounts"):
        _book(session, staff, vehicle, branch)


def test_online_booking_refuses_a_vehicle_in_the_workshop(
    session, customer, vehicle, branch
):
    fleet_service.schedule_maintenance(
        session,
        vehicle,
        "Brake pads",
        date(2026, 8, 30),
        date(2026, 9, 2),
        4500,
    )

    with pytest.raises(ConflictError, match="workshop"):
        _book(session, customer, vehicle, branch)
