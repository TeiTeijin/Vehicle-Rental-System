"""Booking updates for the customer's notification bell.

The app does not keep a separate notifications table: every update can be
derived from the booking it belongs to. That keeps the bell honest -- if the
counter marks a rental completed, the customer sees it on their next refresh
without a second write path to keep in step.

One row produces at most one notification, chosen by how urgently it needs the
customer's attention:

* pickup day today -> a reminder
* completed       -> a thank-you / receipt update
* confirmed       -> booking success
* pending         -> booking received (pay at the counter)
* ongoing         -> rental in progress

Cancelled bookings are deliberately absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Booking
from app.services import booking_service, vehicle_service

#: At most this many updates are kept. The popup is a quick glance, not an
#: archive; older completed rentals drop off first.
MAX_NOTIFICATIONS = 6

#: Keys are stable for the life of one booking state, so the UI can remember
#: what the customer has already opened without persisting a parallel table.
KIND_PICKUP = "pickup"
KIND_SUCCESS = "success"
KIND_PENDING = "pending"
KIND_COMPLETED = "completed"
KIND_ONGOING = "ongoing"


@dataclass(frozen=True)
class Notification:
    """One booking update, ready for the bell popup."""

    key: str
    kind: str
    title: str
    detail: str
    meta: str
    when: date
    booking_id: int
    vehicle_id: int | None = None


def _join(*parts: str | None) -> str:
    return " \u00b7 ".join(part for part in parts if part)


def _date_text(value: date) -> str:
    return value.strftime("%d %b %Y")


def _branch_name(booking: Booking) -> str:
    branch = booking.pickup_branch
    return (branch.name or "").strip() if branch is not None else ""


def _notification(booking: Booking, *, today: date) -> Notification | None:
    """Pick the single most useful update for one booking."""
    status = booking.status or "pending"
    if status == "cancelled":
        return None

    vehicle = booking.vehicle
    vehicle_name = (
        vehicle_service.display_name(vehicle)
        if vehicle is not None
        else "Vehicle no longer in the fleet"
    )
    reference = booking_service.booking_reference(booking)
    detail = _join(vehicle_name, reference)
    branch = _branch_name(booking)
    vehicle_id = vehicle.vehicle_id if vehicle is not None else None
    base = {
        "booking_id": booking.booking_id,
        "vehicle_id": vehicle_id,
    }

    if status in ("pending", "confirmed") and booking.start_date == today:
        return Notification(
            key=f"booking:{booking.booking_id}:pickup",
            kind=KIND_PICKUP,
            title="Pickup today",
            detail=detail,
            meta=_join("Today", branch, "Bring your licence and booking reference"),
            when=today,
            **base,
        )

    if status == "completed":
        returned = booking.actual_return_date or booking.end_date
        return Notification(
            key=f"booking:{booking.booking_id}:completed",
            kind=KIND_COMPLETED,
            title="Booking completed",
            detail=detail,
            meta=_join(f"Returned {_date_text(returned)}", branch),
            when=returned,
            **base,
        )

    if status == "confirmed":
        return Notification(
            key=f"booking:{booking.booking_id}:confirmed",
            kind=KIND_SUCCESS,
            title="Booking confirmed",
            detail=detail,
            meta=_join(f"Pickup {_date_text(booking.start_date)}", branch),
            when=booking.start_date,
            **base,
        )

    if status == "pending":
        return Notification(
            key=f"booking:{booking.booking_id}:pending",
            kind=KIND_PENDING,
            title="Booking received",
            detail=detail,
            meta=_join(
                f"Pickup {_date_text(booking.start_date)}",
                branch,
                "Pay at the counter",
            ),
            when=booking.start_date,
            **base,
        )

    if status == "ongoing":
        return Notification(
            key=f"booking:{booking.booking_id}:ongoing",
            kind=KIND_ONGOING,
            title="Rental in progress",
            detail=detail,
            meta=_join(f"Return due {_date_text(booking.end_date)}", branch),
            when=booking.end_date,
            **base,
        )

    return None


def _order(item: Notification) -> tuple[int, int]:
    """Pickups first, then things still ahead, then recent history."""
    if item.kind == KIND_PICKUP:
        return (0, item.when.toordinal())
    if item.kind in (KIND_SUCCESS, KIND_PENDING):
        return (1, item.when.toordinal())
    if item.kind == KIND_ONGOING:
        return (2, item.when.toordinal())
    return (3, -item.when.toordinal())


def notifications_for(
    session: Session,
    user_id: int | None,
    *,
    today: date | None = None,
    limit: int = MAX_NOTIFICATIONS,
) -> list[Notification]:
    """A customer's booking updates, most useful first.

    ``today`` is injectable so a test can stand on a pickup day without
    touching the machine clock.
    """
    if user_id is None:
        return []

    today = today or date.today()
    stmt = (
        select(Booking)
        .options(joinedload(Booking.vehicle), joinedload(Booking.pickup_branch))
        .where(Booking.user_id == user_id)
        .where(Booking.status != "cancelled")
        .order_by(Booking.start_date.desc(), Booking.booking_id.desc())
        .limit(50)
    )
    bookings = list(session.execute(stmt).scalars().unique())

    items = [
        item
        for item in (_notification(booking, today=today) for booking in bookings)
        if item is not None
    ]
    items.sort(key=_order)
    return items[: max(0, limit)]


__all__ = [
    "KIND_COMPLETED",
    "KIND_ONGOING",
    "KIND_PENDING",
    "KIND_PICKUP",
    "KIND_SUCCESS",
    "MAX_NOTIFICATIONS",
    "Notification",
    "notifications_for",
]
