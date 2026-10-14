from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.availability import AvailabilityChecker
from app.domain.rental_calculator import RentalCalculator
from app.domain.vehicle_types import Car, Motorcycle
from app.models import Booking, Inspection_Report, Penalty, Vehicle
from app.services import fleet_service, payment_service
from app.services.errors import ConflictError, StateError, ValidationError
from app.utils.money import ZERO, money

#: Statuses that still hold the vehicle for their dates.
BLOCKING_STATUSES = ("pending", "confirmed", "ongoing")

#: How a rental reached the branch (walk-in or online).
CHANNELS = ("walk_in", "online")

#: Statuses a rental may be checked in from.
CHECK_IN_STATUSES = ("pending", "confirmed")

#: Statuses that permit a cancellation.
CANCELLABLE_STATUSES = ("pending", "confirmed")

#: Default late fee. Per day, per booking.
LATE_PENALTY_PER_DAY = money(1000.00, field="late_penalty_per_day")

#: Non-refundable scheduling fee, in pesos. Part of the rental total: the
#: minimum first payment that locks the dates. Forfeited on cancellation
#: (rental-kind payments are never auto-refunded).
SCHEDULING_FEE = money(100.00, field="scheduling_fee")

#: Refundable security deposit by vehicle class. One tunable dict: the
#: counter always collects the deposit upfront, even on the fee-only path.
DEPOSIT_BY_CLASS: dict[str, Decimal] = {
    "motorcycle": money(1500.00, field="deposit"),
    "small": money(2000.00, field="deposit"),
    "medium": money(2500.00, field="deposit"),
    "suv": money(3000.00, field="deposit"),
    "van": money(3500.00, field="deposit"),
    "pickup": money(4000.00, field="deposit"),
    "truck": money(5000.00, field="deposit"),
}

#: Deposit when the vehicle has no class on file.
DEFAULT_DEPOSIT = money(2500.00, field="deposit")


def deposit_for_vehicle(vehicle: Vehicle) -> Decimal:
    """Refundable deposit for one vehicle, by its class."""
    return DEPOSIT_BY_CLASS.get(
        (getattr(vehicle, "vehicle_class", None) or "").strip().lower(),
        DEFAULT_DEPOSIT,
    )


#: Booking states an extension may start from. Completed/cancelled history
#: is never rewritten; shortening is not an extension (cancel + rebook).
EXTENDABLE_STATUSES = ("pending", "confirmed", "ongoing")


def extension_quote(
    session: Session, booking: Booking, new_end_date: date
) -> tuple[Decimal, Decimal]:
    """Validate an extension and price it, without writing anything.

    Returns (old_total, new_total); the difference is simply new balance.
    """
    if booking.status not in EXTENDABLE_STATUSES:
        raise StateError(
            f"A {booking.status} booking cannot be extended."
        )
    if new_end_date <= booking.end_date:
        raise ValidationError(
            "The new return date must be after the current one.",
            field="new_end_date",
        )
    vehicle = booking.vehicle
    others = [
        row
        for row in active_bookings(session, vehicle)
        if row.booking_id != booking.booking_id
    ]
    if AvailabilityChecker.find_conflict(booking.end_date, new_end_date, others):
        raise ConflictError(
            f"{vehicle.make} {vehicle.model} is already booked inside "
            f"{booking.end_date:%d %b} to {new_end_date:%d %b %Y}."
        )
    workshop = fleet_service.maintenance_conflict(
        session, vehicle, booking.end_date, new_end_date
    )
    if workshop is not None:
        raise ConflictError(
            f"{vehicle.make} {vehicle.model} enters the workshop "
            f"({workshop.start_date:%d %b} to {workshop.end_date:%d %b %Y}): "
            f"{workshop.description}"
        )
    old_total = money(booking.total_cost, field="total_cost")
    new_total = money(
        RentalCalculator.total_cost(
            _to_domain_vehicle(vehicle), booking.start_date, new_end_date
        ),
        field="total_cost",
    )
    return old_total, new_total


def extend_booking(
    session: Session, booking: Booking, new_end_date: date
) -> tuple[Booking, Decimal, Decimal]:
    """Push the return date forward, repriced over the whole range.

    The total is recomputed with the one pricing function so discount tiers
    stay consistent. Returns (booking, old_total, new_total) for the confirm
    preview; the difference simply becomes balance, payable normally.
    """
    old_total, new_total = extension_quote(session, booking, new_end_date)
    booking.end_date = new_end_date
    booking.total_cost = new_total
    session.flush()
    return booking, old_total, new_total


def quote_for(session: Session, vehicle: Vehicle, start_date: date, end_date: date) -> tuple[int, Decimal]:
    """Live price preview for the counter: (days, total) through the same
    calculator `create_booking` charges with. Raises like it does."""
    assert_available(session, vehicle, start_date, end_date)
    days = RentalCalculator.rental_days(start_date, end_date)
    total = money(
        RentalCalculator.total_cost(_to_domain_vehicle(vehicle), start_date, end_date),
        field="total_cost",
    )
    return days, total


def complete_counter_rental(
    session: Session,
    *,
    user,
    vehicle: Vehicle,
    start_date: date,
    end_date: date,
    rental_amount,
    method: str,
    staff_id: int | None,
    reference_no: str | None = None,
    channel: str = "walk_in",
    flag_override_note: str | None = None,
    flag_override_by: int | None = None,
) -> tuple[Booking, bool]:
    """The wizard's Pay button: booking + payments + confirm-if-settled.

    `rental_amount` is the rental-kind payment: anything from SCHEDULING_FEE
    (minimum that locks the dates) up to the full total. The deposit is
    always collected in full alongside it. Returns (booking, confirmed).
    The caller commits: any raise rolls everything back, nothing half-written.
    """
    booking = create_booking(
        session, user, vehicle, start_date, end_date,
        created_by=staff_id, channel=channel,
    )
    if (flag_override_note or "").strip():
        from datetime import datetime as _datetime

        booking.flag_override_note = flag_override_note.strip()
        booking.flag_override_by = flag_override_by
        booking.flag_override_at = _datetime.now()
    total = money(booking.total_cost, field="total_cost")
    pay = money(rental_amount, field="rental_amount")
    try:
        from app.services import settings_service as _settings
        fee_floor = _settings.scheduling_fee(session)
        gcash_required = _settings.get_bool(session, "gcash_ref_required")
    except Exception:
        fee_floor, gcash_required = SCHEDULING_FEE, True
    if pay < fee_floor:
        raise ValidationError(
            f"At least the {fee_floor} scheduling fee is due today.",
            field="rental_amount",
        )
    if pay > total:
        raise ValidationError(
            "That is more than the rental total.", field="rental_amount"
        )
    if method == "gcash" and gcash_required and not (reference_no or "").strip():
        raise ValidationError(
            "A GCash reference number is required.", field="reference_no"
        )
    payment_service.record_payment(
        session, booking, pay, method,
        recorded_by=staff_id,
        reference_no=reference_no,
        note="Counter payment",
    )
    try:
        from app.services import settings_service as _settings2
        deposit = _settings2.deposit_for_class(
            session, getattr(vehicle, "vehicle_class", None))
    except Exception:
        deposit = deposit_for_vehicle(vehicle)
    payment_service.record_payment(
        session, booking, deposit, method,
        kind="deposit",
        recorded_by=staff_id,
        reference_no=reference_no if method == "gcash" else None,
        note="Refundable security deposit",
    )
    # Walk-ins are verified in person, so they skip the pending review that
    # online reservations wait in. Payment state lives separately in the
    # balance: a fee-only walk-in is confirmed with OUTSTANDING due.
    confirm_booking(session, booking)
    return booking, True

FUEL_LEVELS = ("empty", "quarter", "half", "three_quarter", "full")

PENALTY_TYPES = ("late_return", "damage", "cleaning", "other")


def _to_domain_vehicle(vehicle: Vehicle) -> Car | Motorcycle:
    category = getattr(vehicle, "category", None)
    if category and category.category_name == "Motorcycle":
        return Motorcycle(vehicle.make, vehicle.model, vehicle.year, vehicle.daily_rate)
    return Car(vehicle.make, vehicle.model, vehicle.year, vehicle.daily_rate)


def active_bookings(session: Session, vehicle: Vehicle) -> list[Booking]:
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
    return (
        AvailabilityChecker.find_conflict(
            start_date, end_date, active_bookings(session, vehicle)
        )
        is not None
    )


def check_licence(session: Session, user, on_date: date) -> None:
    if not getattr(user, "is_active", True):
        raise ConflictError("This customer is deactivated.")
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
    if end_date < start_date:
        raise ValidationError(
            "The return date cannot be before the pick-up date.", field="end_date"
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

    return booking


def confirm_booking(session: Session, booking: Booking) -> Booking:
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
    if booking.status not in CANCELLABLE_STATUSES:
        raise StateError(
            f"A {booking.status} booking cannot be cancelled. Check the vehicle "
            "in and out instead."
        )
    booking.status = "cancelled"
    booking.cancel_reason = (reason or "").strip() or "No reason given"
    # The deposit always comes back on cancellation; rental-kind payments
    # (including the non-refundable scheduling fee) stay recorded.
    payment_service.settle_deposit(session, booking)
    session.flush()

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
        if late_penalty_per_day == LATE_PENALTY_PER_DAY:
            try:
                from app.services import settings_service as _settings
                late_penalty_per_day = _settings.late_fee_per_day(session)
            except Exception:
                pass
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

    # Deposit first: penalties consume it, the remainder is returned, and only
    # then is the settlement verdict read.
    payment_service.settle_deposit(session, booking)

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
    if actual_return_date <= booking.end_date:
        return ZERO
    days_late = (actual_return_date - booking.end_date).days
    return money(per_day, field="per_day") * days_late


def expected_vehicles_today(session: Session, on_date: date | None = None) -> list[Booking]:
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
