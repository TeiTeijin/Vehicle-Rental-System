from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.availability import AvailabilityChecker
from app.domain.rental_calculator import RentalCalculator
from app.domain.vehicle_types import Car, Motorcycle
from app.models import Booking, Inspection_Report, Penalty, Vehicle
from app.services import fleet_service, payment_service
from app.services.errors import (
    ConflictError,
    StateError,
    ValidationError,
)
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
    fee_floor: Decimal | None = None,
    deposit: Decimal | None = None,
) -> tuple[Booking, bool]:
    """The wizard's Pay button: booking + payments + confirm-if-settled.

    `rental_amount` is the rental-kind payment: anything from the scheduling
    fee (minimum that locks the dates) up to the full total. The configured
    deposit is collected in full alongside it; a zero deposit records no
    deposit row. Returns (booking, confirmed).
    The caller commits: any raise rolls everything back, nothing half-written.

    `fee_floor` and `deposit` are the exact values the wizard displayed, from
    `counter_service.quote`. When omitted, the current branch settings are
    read so non-wizard callers keep the old behaviour.
    """
    if fee_floor is not None:
        fee_floor = money(fee_floor, field="fee_floor")
        if fee_floor < ZERO:
            raise ValidationError(
                "The scheduling fee cannot be negative.", field="fee_floor"
            )
    if deposit is not None:
        deposit = money(deposit, field="deposit")
        if deposit < ZERO:
            raise ValidationError(
                "The security deposit cannot be negative.", field="deposit"
            )
    # Price before writing anything: a fee the rental total can never cover
    # is a configuration error the clerk must see before any payment.
    _days, total = quote_for(session, vehicle, start_date, end_date)
    try:
        from app.services import settings_service as _settings
        if fee_floor is None:
            fee_floor = _settings.scheduling_fee(session)
        gcash_required = _settings.get_bool(session, "gcash_ref_required")
    except Exception:
        if fee_floor is None:
            fee_floor = SCHEDULING_FEE
        gcash_required = True
    from app.services import settings_service as _settings_check
    _settings_check.validate_scheduling_fee(fee_floor, total)

    booking = create_booking(
        session, user, vehicle, start_date, end_date,
        created_by=staff_id, channel=channel,
    )
    if (flag_override_note or "").strip():
        from datetime import datetime as _datetime

        booking.flag_override_note = flag_override_note.strip()
        booking.flag_override_by = flag_override_by
        booking.flag_override_at = _datetime.now()
    pay = money(rental_amount, field="rental_amount")
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
    if deposit is None:
        try:
            from app.services import settings_service as _settings2
            deposit = _settings2.deposit_for_class(
                session, getattr(vehicle, "vehicle_class", None))
        except Exception:
            deposit = deposit_for_vehicle(vehicle)
    if deposit > ZERO:
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


def _active_bookings_stmt(vehicle_id: int, *, for_update: bool = False):
    """Active bookings for one vehicle, optionally as a locking read.

    Under InnoDB's default REPEATABLE READ, a transaction's first plain SELECT
    fixes its snapshot. A counter that already read the vehicle (the wizard's
    pre-lock `session.get`) keeps that stale view even after it waits for the
    vehicle row lock, so a plain overlap query can miss the booking the other
    counter just committed. `FOR UPDATE` makes this a current read, which sees
    the latest committed rows.

    SQLite ignores the clause, so the suite can assert the compiled MySQL
    statement and the non-locking behaviour but cannot prove the race is
    closed. Required MySQL staging check, with a pre-lock read in each
    session: two sessions read the same vehicle and window without locking,
    then call `create_booking` concurrently. Exactly one must commit; the
    other must fail with the overlap conflict instead of inserting.
    """
    stmt = select(Booking).where(
        Booking.vehicle_id == vehicle_id,
        Booking.status.in_(BLOCKING_STATUSES),
    )
    if for_update:
        stmt = stmt.with_for_update()
    return stmt


def active_bookings(
    session: Session, vehicle: Vehicle, *, for_update: bool = False
) -> list[Booking]:
    return list(
        session.execute(
            _active_bookings_stmt(vehicle.vehicle_id, for_update=for_update)
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
    session: Session,
    vehicle: Vehicle,
    start_date: date,
    end_date: date,
    *,
    for_update: bool = False,
) -> None:
    """Reject a window the vehicle cannot take.

    `for_update=True` makes the active-bookings read a locking/current read.
    `create_booking` uses it after locking the vehicle row so a second counter
    sees the first counter's committed booking; quote and preview callers keep
    the plain read.
    """
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

    if vehicle.status in fleet_service.ALWAYS_BLOCKING_STATUSES:
        raise ConflictError(
            f"{vehicle.make} {vehicle.model} is currently {vehicle.status} and "
            "cannot be booked."
        )

    clash = AvailabilityChecker.find_conflict(
        start_date,
        end_date,
        active_bookings(session, vehicle, for_update=for_update),
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
    # Serialize per vehicle and make both availability reads current reads.
    # The lock blocks the other counter; the locking follow-up selects (the
    # refresh plus the booking read) see through this transaction's earlier
    # snapshot, so a committed booking from the first counter is visible.
    # Flush first: refresh would otherwise discard a pending status change.
    session.flush()
    session.refresh(vehicle, with_for_update=True)
    assert_available(session, vehicle, start_date, end_date, for_update=True)
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
    require_settlement: bool = False,
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

    if require_settlement:
        balance = payment_service.booking_balance(session, booking)
        if balance.balance > ZERO:
            raise ConflictError(
                f"Booking #{booking.booking_id} still has "
                f"{balance.balance} outstanding. Collect the pickup balance "
                "before releasing the vehicle."
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


@dataclass(frozen=True)
class CheckoutQuote:
    """What a return costs and how much of it the deposit covers."""

    late_fee: Decimal
    damage_charge: Decimal
    deposit_applied: Decimal
    deposit_refund: Decimal
    balance_due: Decimal


def _late_fee_per_day(session: Session, per_day=None) -> Decimal:
    """The configured daily late fee, unless the caller pinned one."""
    if per_day is None:
        per_day = LATE_PENALTY_PER_DAY
    value = money(per_day, field="late_penalty_per_day")
    if per_day == LATE_PENALTY_PER_DAY:
        try:
            from app.services import settings_service as _settings

            value = money(
                _settings.late_fee_per_day(session), field="late_penalty_per_day"
            )
        except Exception:  # noqa: BLE001 - fall back to the code constant
            pass
    return value


def checkout_quote(
    session: Session,
    booking: Booking,
    *,
    actual_return_date: date,
    damage_charge=None,
    late_penalty_per_day=None,
) -> CheckoutQuote:
    """Price a return without writing anything.

    The held deposit is a liability: it covers penalties up to its value,
    the rest is handed back, and any penalty shortfall stays due. An
    overpaid rental offsets penalties before the deposit is touched.
    """
    if actual_return_date < booking.start_date:
        raise ValidationError(
            f"Return date {actual_return_date:%d %b %Y} is before the pick-up "
            f"date {booking.start_date:%d %b %Y}.",
            field="actual_return_date",
        )

    per_day = _late_fee_per_day(session, late_penalty_per_day)
    late_fee = ZERO
    if actual_return_date > booking.end_date:
        late_fee = money(
            per_day * (actual_return_date - booking.end_date).days,
            field="late_fee",
        )

    damage = ZERO
    if damage_charge is not None:
        damage = money(damage_charge, field="damage_charge")
    if damage < ZERO:
        raise ValidationError(
            "A damage charge cannot be negative.", field="damage_charge"
        )

    balance = payment_service.booking_balance(session, booking)
    rental_paid = money(
        sum(
            (
                payment.amount
                for payment in booking.payments
                if payment.kind == "rental"
                and payment.status in payment_service.SETTLED_STATUSES
            ),
            ZERO,
        ),
        field="rental_paid",
    )
    overpaid_rental = max(ZERO, rental_paid - balance.rental_cost)
    penalties = money(
        balance.penalty_total + late_fee + damage, field="penalty_total"
    )
    penalties_outstanding = max(ZERO, penalties - overpaid_rental)
    applied = money(
        min(balance.deposit_held, penalties_outstanding),
        field="deposit_applied",
    )
    refund = money(balance.deposit_held - applied, field="deposit_refund")
    due = money(
        balance.balance + late_fee + damage - applied, field="balance_due"
    )
    return CheckoutQuote(
        late_fee=late_fee,
        damage_charge=damage,
        deposit_applied=applied,
        deposit_refund=refund,
        balance_due=max(ZERO, due),
    )


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
    settlement_amount=None,
    settlement_method: str | None = None,
    settlement_reference: str | None = None,
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
    if mileage_reading < vehicle.mileage:
        raise ValidationError(
            f"Return odometer reading of {mileage_reading:,} km is below the "
            f"{vehicle.mileage:,} km recorded at pick-up.",
            field="mileage_reading",
        )

    quote = checkout_quote(
        session,
        booking,
        actual_return_date=actual_return_date,
        damage_charge=damage_charge,
        late_penalty_per_day=late_penalty_per_day,
    )
    settlement = None
    if settlement_amount is not None:
        settlement = money(settlement_amount, field="settlement_amount")
        if settlement != quote.balance_due:
            raise ValidationError(
                f"The settlement must be exactly {quote.balance_due}, "
                f"not {settlement}.",
                field="settlement_amount",
            )
        if settlement > ZERO and not (settlement_method or "").strip():
            raise ValidationError(
                "Choose a settlement method.", field="settlement_method"
            )
    elif require_settlement and quote.balance_due > ZERO:
        raise ConflictError(
            f"Booking #{booking.booking_id} still has {quote.balance_due} "
            "outstanding. Collect the balance before closing the rental."
        )

    fleet_service.mark_available(session, vehicle)
    vehicle.mileage = mileage_reading

    if quote.late_fee > ZERO:
        days_late = (actual_return_date - booking.end_date).days
        apply_penalty(
            session,
            booking,
            penalty_type="late_return",
            amount=quote.late_fee,
            description=(
                f"Returned {days_late} day(s) after the scheduled return date."
            ),
        )

    if quote.damage_charge > ZERO:
        apply_penalty(
            session,
            booking,
            penalty_type="damage",
            amount=quote.damage_charge,
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

    # Deposit first: penalties consume it, the remainder is returned, and only
    # then is the settlement verdict read.
    payment_service.settle_deposit(session, booking)

    balance = payment_service.booking_balance(session, booking)
    if settlement is not None:
        if settlement != balance.balance:
            raise ValidationError(
                f"The balance changed to {balance.balance}; review the "
                "checkout quote before taking payment.",
                field="settlement_amount",
            )
        if settlement > ZERO:
            payment_service.record_payment(
                session,
                booking,
                settlement,
                settlement_method,
                recorded_by=inspector.user_id,
                reference_no=settlement_reference,
                note="Checkout settlement",
            )
            balance = payment_service.booking_balance(session, booking)

    if require_settlement and balance.balance > ZERO:
        raise ConflictError(
            f"Booking #{booking.booking_id} still has {balance.balance} "
            "outstanding after settlement."
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
