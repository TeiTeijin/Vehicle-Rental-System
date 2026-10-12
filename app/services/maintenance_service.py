"""The workshop lifecycle: one start/complete/cancel path for Fleet and
Maintenance alike (same idea as the shared payment function).

The rule that keeps the tabs honest: a vehicle's shop status is *derived*
from these rows. A vehicle is in the shop while it holds a record that is
``in_progress``, or ``scheduled`` with a start date that has arrived.
`sync_vehicle_status` is the only writer of that derivation.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models import Booking, Maintenance_Record, Vehicle
from app.services.errors import StateError, ValidationError
from app.utils.money import ZERO, money

#: Display order matches the spec: Scheduled, In Progress, Completed, Cancelled.
STATUSES = ("scheduled", "in_progress", "completed", "cancelled")

#: Records that keep a vehicle off the road.
BLOCKING_STATUSES = ("scheduled", "in_progress")

#: Terminal states: history, never rewritten.
TERMINAL_STATUSES = ("completed", "cancelled")

TYPES: tuple[str, ...] = (
    "routine",
    "repair",
    "tire_brake",
    "inspection_registration",
    "cleaning",
    "damage_repair",
)

TYPE_LABELS: dict[str, str] = {
    "routine": "Routine service",
    "repair": "Repair",
    "tire_brake": "Tire / brake",
    "inspection_registration": "Inspection / registration",
    "cleaning": "Cleaning / detailing",
    "damage_repair": "Damage repair",
}

#: A due date this close counts as "due soon".
DUE_SOON_DAYS = 14

#: Odometer room before a mileage due date counts as "due soon".
DUE_SOON_KM = 500


def type_label(kind: str | None) -> str:
    return TYPE_LABELS.get((kind or "").strip().lower(), (kind or "—"))


def _blocking(record: Maintenance_Record, on_date: date) -> bool:
    """Whether one row keeps its vehicle in the shop on a date."""
    if record.status == "in_progress":
        return True
    if record.status == "scheduled" and record.start_date <= on_date:
        # A scheduled job whose start has arrived holds the vehicle until
        # it is started, completed, or cancelled.
        return True
    return False


def is_in_shop(vehicle: Vehicle, on_date: date | None = None) -> bool:
    day = on_date or date.today()
    return any(_blocking(r, day) for r in vehicle.maintenance_records)


def sync_vehicle_status(
    session: Session, vehicle: Vehicle, on_date: date | None = None
) -> Vehicle:
    """Derive the Fleet status from the service log. The only writer of it.

    Never touches `rented` (a car out on a booking stays out) or `retired`.
    Everything else follows the log: blocking rows mean `maintenance`,
    otherwise back to `available`.
    """
    day = on_date or date.today()
    if vehicle.status in ("rented", "retired"):
        return vehicle
    vehicle.status = (
        "maintenance" if is_in_shop(vehicle, day) else "available"
    )
    session.flush()
    return vehicle


def booking_conflicts(
    session: Session, vehicle: Vehicle, start_date: date, end_date: date | None
) -> list[Booking]:
    """Active bookings overlapping a planned shop window, for the clash
    message. An open-ended window (no end date yet) runs to `end_date`."""
    from app.domain.availability import AvailabilityChecker

    window_end = end_date or date.max
    clashes = []
    for booking in active_bookings(session, vehicle):
        if AvailabilityChecker.overlaps(
            start_date, window_end, booking.start_date, booking.end_date
        ):
            clashes.append(booking)
    return clashes


def active_bookings(session: Session, vehicle: Vehicle) -> list[Booking]:
    return list(
        session.execute(
            select(Booking).where(
                Booking.vehicle_id == vehicle.vehicle_id,
                Booking.status.in_(("pending", "confirmed", "ongoing")),
            )
        ).scalars()
    )


def start_maintenance(
    session: Session,
    vehicle: Vehicle,
    *,
    description: str,
    maintenance_type: str = "routine",
    start_date: date | None = None,
    end_date: date | None = None,
    estimate_cost=None,
    odometer: int | None = None,
    shop: str | None = None,
    booking_id: int | None = None,
    logged_by: int | None = None,
    status: str = "scheduled",
) -> Maintenance_Record:
    """Log a shop stay. `in_progress` (or a start date of today) puts the
    vehicle straight into Maintenance in Fleet.

    Overlapping active bookings do NOT block the entry: a car is often sent
    to the shop after it was booked (the handover then refuses with the
    workshop conflict). Callers that want the clash list up front -- the
    Maintenance add form does -- use `booking_conflicts()` and show it as
    a warning so staff can reassign first."""
    if not (description or "").strip():
        raise ValidationError("Describe the work first.", field="description")
    kind = (maintenance_type or "routine").strip().lower()
    if kind not in TYPES:
        raise ValidationError(
            f"Unknown maintenance type: {maintenance_type}. Choose one of "
            f"{', '.join(TYPES)}.",
            field="maintenance_type",
        )
    if status not in ("scheduled", "in_progress"):
        raise ValidationError(
            f"A new record starts as scheduled or in_progress, not {status}.",
            field="status",
        )
    day = start_date or date.today()
    if end_date is not None and end_date < day:
        raise ValidationError(
            "Maintenance end date must not be before its start date.",
            field="end_date",
        )
    if odometer is not None and odometer < 0:
        raise ValidationError("Odometer cannot be negative.", field="odometer")

    record = Maintenance_Record(        vehicle_id=vehicle.vehicle_id,
        description=description.strip(),
        maintenance_type=kind,
        cost=None,
        estimate_cost=(
            money(estimate_cost, field="estimate_cost")
            if estimate_cost is not None
            else None
        ),
        start_date=day,
        end_date=end_date,
        status=status,
        odometer=odometer,
        shop=(shop or "").strip() or None,
        booking_id=booking_id,
        logged_by=logged_by,
        created_at=datetime.now(),
    )
    session.add(record)
    session.flush()
    session.refresh(vehicle)
    # Derived against right now, not the record's start: a future schedule
    # leaves the car rentable until its start date arrives.
    sync_vehicle_status(session, vehicle)
    return record


def complete_maintenance(
    session: Session,
    record: Maintenance_Record,
    *,
    end_date: date | None = None,
    cost=None,
    odometer: int | None = None,
    next_due_date: date | None = None,
    next_due_odometer: int | None = None,
) -> Maintenance_Record:
    """The one button staff mustn't forget: close the job, release the car.

    Sets the end date, final cost, and odometer; optionally stamps the next
    due date/odometer. Releasing follows the log: back to Available only
    when no other blocking row remains.
    """
    if record.status in TERMINAL_STATUSES:
        raise StateError(f"A {record.status} record cannot be completed.")
    done = end_date or date.today()
    if done < record.start_date:
        raise ValidationError(
            "The completion date cannot be before the start date.",
            field="end_date",
        )
    if cost is None:
        raise ValidationError("Enter the final cost to complete.", field="cost")
    value = money(cost, field="cost")
    if value < ZERO:
        raise ValidationError("Cost cannot be negative.", field="cost")
    if odometer is not None and odometer < 0:
        raise ValidationError("Odometer cannot be negative.", field="odometer")

    record.status = "completed"
    record.end_date = done
    record.cost = value
    if odometer is not None:
        record.odometer = odometer
    record.next_due_date = next_due_date
    record.next_due_odometer = next_due_odometer
    session.flush()

    vehicle = record.vehicle
    if odometer is not None and odometer > (vehicle.mileage or 0):
        vehicle.mileage = odometer
    sync_vehicle_status(session, vehicle)
    return record


def cancel_maintenance(
    session: Session, record: Maintenance_Record, *, reason: str | None = None
) -> Maintenance_Record:
    """Cancel instead of delete, so the log stays honest."""
    if record.status in TERMINAL_STATUSES:
        raise StateError(f"A {record.status} record cannot be cancelled.")
    record.status = "cancelled"
    record.cancel_reason = (reason or "").strip() or "No reason given"
    session.flush()
    sync_vehicle_status(session, record.vehicle)
    return record


def due_soon(
    session: Session,
    *,
    on_date: date | None = None,
    within_days: int = DUE_SOON_DAYS,
    within_km: int = DUE_SOON_KM,
) -> list[tuple[Vehicle, Maintenance_Record, str]]:
    """Vehicles whose next service is due: (vehicle, record, note).

    Overdue first. A record counts when its next-due date is within the
    window (or past), or its next-due odometer is within threshold of the
    vehicle's current mileage (or already passed). Only the latest
    completed record per vehicle is read.
    """
    day = on_date or date.today()
    rows = (
        session.execute(
            select(Maintenance_Record)
            .options(joinedload(Maintenance_Record.vehicle))
            .where(Maintenance_Record.status == "completed")
            .order_by(
                Maintenance_Record.vehicle_id,
                Maintenance_Record.end_date.desc(),
            )
        )
        .scalars()
        .all()
    )
    latest: dict[int, Maintenance_Record] = {}
    for record in rows:
        latest.setdefault(record.vehicle_id, record)

    due: list[tuple[int, Vehicle, Maintenance_Record, str]] = []
    for record in latest.values():
        vehicle = record.vehicle
        if vehicle is None:
            continue
        if record.next_due_date is not None:
            delta = (record.next_due_date - day).days
            if delta <= within_days:
                note = (
                    f"OVERDUE {abs(delta)} day(s)"
                    if delta < 0
                    else f"due in {delta} day(s)"
                )
                due.append((delta, vehicle, record, note))
                continue
        if record.next_due_odometer is not None and vehicle.mileage is not None:
            gap = record.next_due_odometer - vehicle.mileage
            if gap <= within_km:
                note = (
                    f"OVERDUE {abs(gap):,} km"
                    if gap < 0
                    else f"due in {gap:,} km"
                )
                due.append((gap, vehicle, record, note))
    due.sort(key=lambda item: item[0])
    return [(vehicle, record, note) for _, vehicle, record, note in due]


def spend_summary(
    session: Session,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
) -> tuple[Decimal, dict[str, Decimal], dict[str, Decimal]]:
    """(total, by_type, by_vehicle_plate) of completed-job spend, optionally
    clipped to records starting inside a period. Estimates never count:
    only final costs."""
    stmt = select(Maintenance_Record).options(
        joinedload(Maintenance_Record.vehicle)
    )
    if from_date is not None:
        stmt = stmt.where(Maintenance_Record.start_date >= from_date)
    if to_date is not None:
        stmt = stmt.where(Maintenance_Record.start_date <= to_date)
    records = session.execute(stmt).scalars().all()

    total = ZERO
    by_type: dict[str, Decimal] = {}
    by_vehicle: dict[str, Decimal] = {}
    for record in records:
        if record.status != "completed" or record.cost is None:
            continue
        value = money(record.cost, field="cost")
        total += value
        by_type[record.maintenance_type] = (
            by_type.get(record.maintenance_type, ZERO) + value
        )
        plate = record.vehicle.plate_number if record.vehicle else "—"
        by_vehicle[plate] = by_vehicle.get(plate, ZERO) + value
    return money(total, field="total"), by_type, by_vehicle


def history_for(session: Session, vehicle: Vehicle) -> tuple[list[Maintenance_Record], Decimal]:
    """Every record for one vehicle, newest first, plus total final spend."""
    records = list(
        session.execute(
            select(Maintenance_Record)
            .where(Maintenance_Record.vehicle_id == vehicle.vehicle_id)
            .order_by(Maintenance_Record.start_date.desc())
        ).scalars()
    )
    spent = money(
        sum(
            (money(r.cost, field="cost") for r in records if r.cost is not None),
            ZERO,
        ),
        field="spent",
    )
    return records, spent


def period_cost(
    session: Session, vehicle_id: int, from_date: date, to_date: date
) -> Decimal:
    total = session.execute(
        select(func.coalesce(func.sum(Maintenance_Record.cost), 0)).where(
            Maintenance_Record.vehicle_id == vehicle_id,
            Maintenance_Record.status == "completed",
            Maintenance_Record.start_date >= from_date,
            Maintenance_Record.start_date <= to_date,
        )
    ).scalar_one()
    return money(total or 0, field="maintenance_cost")
