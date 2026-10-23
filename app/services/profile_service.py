"""The signed-in customer's account summary.

The profile page needs the customer row plus a few numbers that belong to
other tables (bookings and receipts). Keeping that aggregation here means the
page never has to reach for a database row behind a closed session, and the
same payload can be reused by any future account screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Booking, Payment, Users
from app.services import booking_service


@dataclass(frozen=True)
class CustomerProfile:
    """One customer, reduced to the fields the account page draws."""

    user_id: int
    full_name: str
    email: str
    phone: str | None
    address: str
    birthdate: date | None
    license_number: str
    license_expiry: date | None
    emergency_contact: str | None
    created_at: datetime | None
    is_active: bool
    booking_count: int
    active_booking_count: int
    payment_count: int

    @property
    def first_name(self) -> str:
        parts = (self.full_name or "").split(maxsplit=1)
        return parts[0] if parts else ""

    @property
    def last_name(self) -> str:
        parts = (self.full_name or "").split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""

    @property
    def status_label(self) -> str:
        if not self.is_active:
            return "Inactive"
        if self.active_booking_count:
            return "Active Renter"
        if self.booking_count:
            return "Returning Customer"
        return "New Customer"

    @property
    def status_tone(self) -> str:
        if not self.is_active:
            return "red"
        if self.active_booking_count:
            return "green"
        if self.booking_count:
            return "muted"
        return "amber"


def profile_for(session: Session, user_id: int) -> CustomerProfile | None:
    """Return the account payload for one customer, or None if it is gone."""
    user = session.get(Users, user_id)
    if user is None:
        return None

    booking_count = session.scalar(
        select(func.count(Booking.booking_id)).where(
            Booking.user_id == user.user_id
        )
    ) or 0
    active_booking_count = session.scalar(
        select(func.count(Booking.booking_id)).where(
            Booking.user_id == user.user_id,
            Booking.status.in_(booking_service.BLOCKING_STATUSES),
        )
    ) or 0
    payment_count = session.scalar(
        select(func.count(Payment.payment_id))
        .join(Booking, Payment.booking_id == Booking.booking_id)
        .where(Booking.user_id == user.user_id)
    ) or 0

    return CustomerProfile(
        user_id=user.user_id,
        full_name=(user.full_name or "").strip(),
        email=(user.email or "").strip(),
        phone=(user.phone or "").strip() or None,
        address=(user.address or "").strip(),
        birthdate=user.birthdate,
        license_number=(user.license_number or "").strip(),
        license_expiry=user.license_expiry,
        emergency_contact=(user.emergency_contact or "").strip() or None,
        created_at=user.created_at,
        is_active=bool(user.is_active),
        booking_count=int(booking_count),
        active_booking_count=int(active_booking_count),
        payment_count=int(payment_count),
    )


__all__ = ["CustomerProfile", "profile_for"]
