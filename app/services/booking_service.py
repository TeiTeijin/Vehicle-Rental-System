"""Bookings: the rental lifecycle from reservation to return.

The original version of this file had four defects that a counter clerk
would hit within a day of use:

    1. Nothing ever wrote `VEHICLE.status`. A car that was out on rent still
       read `available`, and the customer-facing hero carousel filtered on
       that, so out-on-rent vehicles were advertised as bookable. Status
       transitions now live in `fleet_service` so booking and fleet cannot
       drift apart.

    2. Booking a vehicle that was in the workshop was allowed. The availability
       check looked only at other bookings and never at maintenance, so a car
       booked in for a brake job could be promised to a customer for the same
       afternoon.

    3. Licence expiry was never checked. `Users.license_expiry` is NOT NULL
       and clearly meant to matter, but nothing read it.

    4. `check_out` wrote a `damage` penalty with a hardcoded `amount=0.0`.
       Damage was described in free text and charged at nothing.

Plus the date-range and money handling, which now come from the shared domain
helpers rather than being reinvented per call site.

The lifecycle is:

    pending --check_in--> ongoing --check_out--> completed
       |                                          ^
       +--confirm--> confirmed --check_in-------->+
       |
       +--cancel--> cancelled
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.availability import AvailabilityChecker
from app.domain.rental_calculator import RentalCalculator
from app.domain.vehicle_types import Car, Motorcycle
from app.models import Booking, Inspection_Report, Penalty, Vehicle
from app.services import fleet_service, payment_service
from app.services.errors import ConflictError, StateError, ValidationError
from app.utils.money import ZERO, money

#: Statuses that still hold the vehicle for their dates. A completed or
#: cancelled booking releases the vehicle.
BLOCKING_STATUSES = ("pending", "confirmed", "ongoing")

#: How a rental reached the branch. Walk-in means somebody came to the counter;
#: online means they started without talking to anyone.
CHANNELS = ("walk_in", "online")

#: Statuses a rental may be checked in from. `confirmed` is allowed because a
#: walk-in is often taken straight from enquiry to keys.
CHECK_IN_STATUSES = ("pending", "confirmed")

#: Statuses that permit a cancellation.
CANCELLABLE_STATUSES = ("pending", "confirmed")

#: Default late fee. Per day, per booking.
LATE_PENALTY_PER_DAY = money(1000.00, field="late_penalty_per_day")

FUEL_LEVELS = ("empty", "quarter", "half", "three_quarter", "full")

PENALTY_TYPES = ("late_return", "damage", "cleaning", "other")


def _to_domain_vehicle(vehicle: Vehicle) -> Car | Motorcycle:
    category = getattr(vehicle, "category", None)
    if category and category.category_name == "Motorcycle":
        return Motorcycle(vehicle.make, vehicle.model, vehicle.year, vehicle.daily_rate)
    return Car(vehicle.make, vehicle.model, vehicle.year, vehicle.daily_rate)


def active_bookings(session: Session, vehicle: Vehicle) -> list[Booking]:
    """Bookings that still hold this vehicle for their dates."""
    return list(
        session.execute(
            select(Booking).where(
                Booking.vehicle_id == vehicle.vehicle_id,
                Booking.status.in_(BLOCKING_STATUSES),
            )
        ).scalars()
    )


def is_reserved_between(
    session: Session, vehicle: Vehicle, start_date: date, end_date: date
) -> bool:
    """True when the vehicle is committed to someone for part of the range.

    This is what `reserved` means, and it is computed rather than stored. See
    the note in `fleet_service` about why the enum value is never written.
    """
    return (
        AvailabilityChecker.find_conflict(
            start_date, end_date, active_bookings(session, vehicle)
        )
        is not None
    )


def check_licence(session: Session, user, on_date: date) -> None:
    """Refuse a rental if the driver's licence has lapsed by that date.

    Checked against the booking's start date rather than today, so a licence
    that expires mid-rental does not get rejected for a booking that begins
    while it is still valid.
    """
    expiry = getattr(user, "license_expiry", None)
    if expiry is None:
        raise ValidationError(
            "This customer has no licence expiry on file.", field="license_expiry"
        )
    if expiry < on_date:
        raise ConflictError(
            f"Licence expired on {expiry:%d %b %Y}, before the requested start "
            f"date of {on_date:%d %b %Y}."
        )


def assert_available(
    session: Session, vehicle: Vehicle, start_date: date, end_date: date
) -> None:
    """Everything that must be true before a vehicle can be promised.

    Checked in order of how specific the resulting message is: the maintenance
    check runs before the generic status check, because "in the workshop for a
    brake job until the 5th" tells the counter clerk what to tell the customer,
    where "is currently maintenance" only restates the column.
    """
    if end_date <= start_date:
        raise ValidationError(
            "The return date must be after the pick-up date.", field="end_date"
        )

    workshop = fleet_service.maintenance_conflict(session, vehicle, start_date, end_date)
    if workshop is not None:
        raise ConflictError(
            f"{vehicle.make} {vehicle.model} is in the workshop "
            f"({workshop.start_date:%d %b} to {workshop.end_date:%d %b %Y}): "
            f"{workshop.description}"
        )

    if not fleet_service.is_rentable(vehicle):
        raise ConflictError(
            f"{vehicle.make} {vehicle.model} is currently {vehicle.status} and "
            "cannot be booked."
        )

    clash = AvailabilityChecker.find_conflict(
        start_date, end_date, active_bookings(session, vehicle)
    )
    if clash is not None:
        raise ConflictError(
            f"{vehicle.make} {vehicle.model} is already booked from "
            f"{clash.start_date:%d %b} to {clash.end_date:%d %b %Y}."
        )


def create_booking(
    session: Session,
    user,
    vehicle: Vehicle,
    start_date: date,
    end_date: date,
    *,
    created_by: int | None = None,
    channel: str | None = None,
) -> Booking:
    """Reserve a vehicle for a customer.

    `created_by` records which member of staff took the booking. The customer
    app leaves it None; the staff app passes the signed-in user.

    `channel` records where the customer found us -- walked in at the counter,
    or taken online. It is optional because the column is nullable and because
    a caller with no reason to know the answer (a migration, a test) should not
    be made to invent one.
    """
    if channel is not None and channel not in CHANNELS:
        raise ValidationError(
            f"Unknown channel: {channel}. Choose one of {', '.join(CHANNELS)}.",
            field="channel",
        )
    assert_available(session, vehicle, start_date, end_date)
    check_licence(session, user, start_date)

    total_cost = money(
        RentalCalculator.total_cost(_to_domain_vehicle(vehicle), start_date, end_date),
        field="total_cost",
    )

    booking = Booking(
        user_id=user.user_id,
        vehicle_id=vehicle.vehicle_id,
        start_date=start_date,
        end_date=end_date,
        actual_return_date=None,
        total_cost=total_cost,
        status="pending",
        created_at=datetime.now(),
        created_by=created_by,
        channel=channel,
    )
    session.add(booking)
    session.flush()

    # The vehicle's `status` column is deliberately untouched. The car may be
    # free today and booked next week, and the column cannot say both. What it
    # is doing right now is unchanged; `is_reserved_between` answers the
    # question the status column cannot.
    return booking


def confirm_booking(session: Session, booking: Booking) -> Booking:
    """Acknowledge a reservation that is ready to collect."""
    if booking.status != "pending":
        raise StateError(
            f"Only a pending booking can be confirmed (this one is "
            f"{booking.status})."
        )
    booking.status = "confirmed"
    return booking


def cancel_booking(
    session: Session, booking: Booking, *, reason: str | None = None
) -> Booking:
    """Cancel a reservation and release the vehicle.

    A rental that has already been collected cannot be cancelled -- it has to
    be returned, which is a different action with different consequences for
    the balance.
    """
    if booking.status not in CANCELLABLE_STATUSES:
        raise StateError(
            f"A {booking.status} booking cannot be cancelled. Check the vehicle "
            "in and out instead."
        )
    booking.status = "cancelled"
    booking.cancel_reason = (reason or "").strip() or "No reason given"
    session.flush()

    # `VEHICLE.status` is deliberately left alone: a cancelled booking did not
    # change what the car is doing today, only released future dates.
    return booking


def check_in(
    session: Session,
    booking: Booking,
    inspector,
    mileage_reading: int,
    fuel_level: str = "full",
    *,
    photo_url: str | None = None,
    damage_notes: str | None = None,
) -> Inspection_Report:
    """Hand the keys over: booking becomes ongoing, vehicle becomes rented.

    Both status writes happen here and nowhere else. A rental that starts
    without setting `VEHICLE.status` is what let out-on-rent cars look
    available in the first place.
    """
    if booking.status not in CHECK_IN_STATUSES:
        raise StateError(
            f"A {booking.status} booking cannot be checked in."
        )
    if fuel_level not in FUEL_LEVELS:
        raise ValidationError(
            f"Unknown fuel level: {fuel_level}. Choose one of "
            f"{', '.join(FUEL_LEVELS)}.",
            field="fuel_level",
        )

    vehicle = booking.vehicle
    if vehicle.status == "maintenance":
        raise ConflictError(
            f"{vehicle.make} {vehicle.model} is in the workshop and cannot be "
            "handed over."
        )

    if mileage_reading < vehicle.mileage:
        raise ValidationError(
            f"Odometer reading of {mileage_reading:,} km is below the "
            f"{vehicle.mileage:,} km already on record. Check the reading.",
            field="mileage_reading",
        )

    fleet_service.mark_rented(session, vehicle)
    vehicle.mileage = mileage_reading
    booking.status = "ongoing"

    report = Inspection_Report(
        booking=booking,
        inspected_by=inspector.user_id,
        inspection_type="pre-rental",
        mileage_reading=mileage_reading,
        fuel_level=fuel_level,
        damage_notes=(damage_notes or "").strip() or None,
        photo_url=(photo_url or "").strip() or None,
        inspected_at=datetime.now(),
    )
    session.add(report)
    session.flush()
    return report


def check_out(
    session: Session,
    booking: Booking,
    inspector,
    actual_return_date: date,
    mileage_reading: int,
    fuel_level: str,
    damage_notes: str | None = None,
    photo_url: str | None = None,
    *,
    damage_charge=None,
    late_penalty_per_day=LATE_PENALTY_PER_DAY,
    require_settlement: bool = True,
) -> Inspection_Report:
    """Take the vehicle back and close the rental.

    `damage_charge` replaces the hardcoded `amount=0.0` the old version
    passed, which meant damage was always described and never charged. It is
    optional: noting damage without charging for it is a normal thing to do.

    `require_settlement` refuses to complete a rental with money outstanding
    unless the caller opts out, which is the case for a walk-in being sent off
    to settle later.
    """
    if booking.status != "ongoing":
        raise StateError(
            f"Only an ongoing booking can be checked out (this one is "
            f"{booking.status})."
        )
    if fuel_level not in FUEL_LEVELS:
        raise ValidationError(
            f"Unknown fuel level: {fuel_level}.", field="fuel_level"
        )
    if actual_return_date < booking.start_date:
        raise ValidationError(
            f"Return date {actual_return_date:%d %b %Y} is before the pick-up "
            f"date {booking.start_date:%d %b %Y}.",
            field="actual_return_date",
        )

    vehicle = booking.vehicle

    fleet_service.mark_available(session, vehicle)
    if mileage_reading >= vehicle.mileage:
        vehicle.mileage = mileage_reading
    else:
        raise ValidationError(
            f"Return odometer reading of {mileage_reading:,} km is below the "
            f"{vehicle.mileage:,} km recorded at pick-up.",
            field="mileage_reading",
        )

    if actual_return_date > booking.end_date:
        days_late = (actual_return_date - booking.end_date).days
        apply_penalty(
            session,
            booking,
            penalty_type="late_return",
            amount=money(late_penalty_per_day, field="late_penalty_per_day")
            * days_late,
            description=(
                f"Returned {days_late} day(s) after the scheduled return date."
            ),
        )

    if damage_charge is not None and money(damage_charge, field="damage_charge") > ZERO:
        apply_penalty(
            session,
            booking,
            penalty_type="damage",
            amount=damage_charge,
            description=damage_notes or "Damage charge applied at return.",
        )
    elif damage_notes:
        # Noted but not charged -- still worth recording.
        apply_penalty(
            session,
            booking,
            penalty_type="damage",
            amount=ZERO,
            description=f"{damage_notes} (no charge applied)",
        )

    booking.actual_return_date = actual_return_date
    booking.status = "completed"

    report = Inspection_Report(
        booking=booking,
        inspected_by=inspector.user_id,
        inspection_type="post-rental",
        mileage_reading=mileage_reading,
        fuel_level=fuel_level,
        damage_notes=(damage_notes or "").strip() or None,
        photo_url=(photo_url or "").strip() or None,
        inspected_at=datetime.now(),
    )
    session.add(report)
    session.flush()

    if require_settlement:
        balance = payment_service.booking_balance(session, booking)
        if balance.balance > ZERO:
            raise ConflictError(
                f"Booking #{booking.booking_id} still has "
                f"{balance.balance} outstanding. Record the balance before "
                "closing the rental, or pass require_settlement=False."
            )

    return report


def apply_penalty(
    session: Session,
    booking: Booking,
    penalty_type: str,
    amount,
    description: str,
) -> Penalty:
    """Charge the customer for something.

    A zero amount is allowed only for `damage`, where staff may note a
    scratch without charging for it. Every other type must be a real figure,
    because a zero-value late fee is indistinguishable from no late fee.
    """
    if penalty_type not in PENALTY_TYPES:
        raise ValidationError(
            f"Unknown penalty type: {penalty_type}. Choose one of "
            f"{', '.join(PENALTY_TYPES)}.",
            field="penalty_type",
        )
    value = money(amount, field="amount")
    if value < ZERO:
        raise ValidationError(
            "A penalty cannot be negative.", field="amount"
        )
    if value == ZERO and penalty_type != "damage":
        raise ValidationError(
            f"A {penalty_type} penalty needs an amount.", field="amount"
        )

    penalty = Penalty(
        booking=booking,
        penalty_type=penalty_type,
        amount=value,
        description=description,
        created_at=datetime.now(),
    )
    session.add(penalty)
    session.flush()
    return penalty


def late_fee_for(
    booking: Booking, actual_return_date: date, *, per_day=LATE_PENALTY_PER_DAY
):
    """The late fee a return would incur, without applying it.

    Shown on the check-out form so the customer is told before they are
    charged, rather than discovering it on the receipt.
    """
    if actual_return_date <= booking.end_date:
        return ZERO
    days_late = (actual_return_date - booking.end_date).days
    return money(per_day, field="per_day") * days_late


def expected_vehicles_today(session: Session, on_date: date | None = None) -> list[Booking]:
    """Bookings that should be out on the road today.

    A booking from today onward counts: half-open, so `start <= today` and
    `end > today`.
    """
    today = on_date or date.today()
    return list(
        session.execute(
            select(Booking)
            .where(
                Booking.status.in_(("confirmed", "ongoing")),
                Booking.start_date <= today,
                Booking.end_date > today,
            )
            .order_by(Booking.end_date)
        ).scalars()
    )


def overdue_returns(session: Session, today: date | None = None) -> list[Booking]:
    """Rentals that were due back and have not been returned."""
    today = today or date.today()
    return list(
        session.execute(
            select(Booking)
            .where(
                Booking.status == "ongoing",
                Booking.end_date < today,
            )
            .order_by(Booking.end_date)
        ).scalars()
    )
