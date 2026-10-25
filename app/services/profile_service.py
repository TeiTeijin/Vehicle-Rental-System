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
from app.services import auth_service, booking_service
from app.services.errors import ConflictError, NotFoundError, ValidationError


@dataclass(frozen=True)
class CustomerProfile:
    """One customer, reduced to the fields the account page draws."""

    user_id: int
    full_name: str
    email: str
    phone: str | None
    address: str
    birthdate: date | None
    license_number: str | None
    license_expiry: date | None
    guarantor_first_name: str | None
    guarantor_last_name: str | None
    guarantor_mobile: str | None
    #: Legacy single-line fallback for rows created before the guarantor
    #: columns existed.
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
    def guarantor_name(self) -> str:
        return " ".join(
            part
            for part in (
                self.guarantor_first_name,
                self.guarantor_last_name,
            )
            if part
        ).strip()

    @property
    def has_guarantor(self) -> bool:
        return bool(self.guarantor_name or self.guarantor_mobile)

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
        license_number=(user.license_number or "").strip() or None,
        license_expiry=user.license_expiry,
        guarantor_first_name=(user.guarantor_first_name or "").strip() or None,
        guarantor_last_name=(user.guarantor_last_name or "").strip() or None,
        guarantor_mobile=(user.guarantor_mobile or "").strip() or None,
        emergency_contact=(user.emergency_contact or "").strip() or None,
        created_at=user.created_at,
        is_active=bool(user.is_active),
        booking_count=int(booking_count),
        active_booking_count=int(active_booking_count),
        payment_count=int(payment_count),
    )


def update_profile(
    session: Session,
    user_id: int,
    *,
    first_name: str,
    last_name: str,
    email: str,
    phone: str | None,
    birthdate: date | None,
    address: str,
    guarantor_first_name: str = "",
    guarantor_last_name: str = "",
    guarantor_mobile: str = "",
) -> Users:
    """Apply the customer's own edits and return the refreshed row.

    This is deliberately narrower than the staff customer editor: identity,
    contact, address and guarantor only. The licence belongs to the branch's
    verification flow and cannot be changed here.
    """
    user = session.get(Users, user_id)
    if user is None:
        raise NotFoundError("That account no longer exists.")

    first = " ".join((first_name or "").split())
    last = " ".join((last_name or "").split())
    if not first:
        raise ValidationError("Enter your first name.", field="first_name")
    if not last:
        raise ValidationError("Enter your last name.", field="last_name")
    full_name = f"{first} {last}"
    if len(full_name) > 255:
        raise ValidationError("That name is too long.", field="full_name")

    mail = (email or "").strip().lower()
    if not auth_service.EMAIL_RE.match(mail):
        raise ValidationError("Enter a valid email address.", field="email")
    clash = (
        session.query(Users)
        .filter(Users.email == mail, Users.user_id != user.user_id)
        .one_or_none()
    )
    if clash is not None:
        raise ConflictError("That email belongs to another account.")

    number = (phone or "").strip()
    if not auth_service.is_valid_mobile(number):
        raise ValidationError(
            "Enter an 11-digit mobile number starting with 09.", field="phone"
        )

    location = " ".join((address or "").split())
    if not location:
        raise ValidationError("Enter your home address.", field="address")
    if len(location) > 255:
        raise ValidationError("That address is too long.", field="address")

    if birthdate is not None and birthdate > date.today():
        raise ValidationError(
            "Date of birth cannot be in the future.", field="birthdate"
        )

    guarantee_first, guarantee_last, guarantee_number = (
        auth_service.validate_guarantor(
            guarantor_first_name,
            guarantor_last_name,
            guarantor_mobile,
            required=False,
        )
    )

    user.full_name = full_name
    user.email = mail
    user.phone = number
    user.address = location
    user.birthdate = birthdate
    user.guarantor_first_name = guarantee_first or None
    user.guarantor_last_name = guarantee_last or None
    user.guarantor_mobile = guarantee_number or None
    user.emergency_contact = (
        f"{guarantee_first} {guarantee_last} - {guarantee_number}"
        if guarantee_first
        else None
    )
    session.flush()
    return user


__all__ = ["CustomerProfile", "profile_for", "update_profile"]
