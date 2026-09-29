"""Fleet: vehicle records, their status, and their maintenance.

`VEHICLE.status` is the single most consequential field in the schema and
nothing in the original codebase ever wrote it -- the only assignment in the
whole project was the literal "available" in the seed script. A car that was
physically out on rent still read `available`, which the customer-facing hero
carousel filtered on, so out-on-rent vehicles were shown as bookable.

The status rules live here and nowhere else, so the booking, check-in and
check-out flows cannot each invent their own idea of what a vehicle's status
should be.

A note on `reserved`
--------------------
The column's enum has four values, but only three describe what the car is
doing *now*: available, rented, maintenance. "Reserved" is a fact about the
future -- the car is free today and booked on Thursday -- and one column cannot
hold both. Reserving is therefore derived from the booking table
(`is_reserved_between`) and the `reserved` value is never written. The enum
member is left in place rather than dropped so no existing row breaks.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Maintenance_Record, Vehicle, Vehicle_Category
from app.services.errors import ConflictError, NotFoundError, StateError, ValidationError
from app.utils.money import money

#: Every value the column's enum accepts.
VEHICLE_STATUSES = ("available", "reserved", "rented", "maintenance")

#: Statuses a new rental may start from. `reserved` is absent on purpose: it is
#: derived from bookings, never stored.
RENTABLE_STATUSES = ("available",)

#: Which statuses a vehicle may move to, keyed by where it is now. A vehicle in
#: the workshop cannot jump straight to `rented` without coming out of it, and
#: an available vehicle cannot be marked `rented` by hand -- that happens only
#: on check-in.
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "available": frozenset({"maintenance"}),
    "reserved": frozenset({"available", "maintenance"}),
    "rented": frozenset({"available", "maintenance"}),
    "maintenance": frozenset({"available", "rented"}),
}

#: Maintenance statuses that mean the vehicle is in the shop.
BLOCKING_MAINTENANCE_STATUSES = ("scheduled", "ongoing")


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------


def set_status(session: Session, vehicle: Vehicle, new_status: str) -> Vehicle:
    """Move a vehicle to a new status, refusing illegal jumps.

    `force` is for the flows that legitimately cross the state machine --
    check-in marks a car `rented` even though `available -> rented` is not a
    legal hand-off -- and is not exposed to the Fleet screen's status picker.
    """
    if new_status not in VEHICLE_STATUSES:
        raise ValidationError(
            f"Unknown vehicle status: {new_status}", field="status"
        )

    current = vehicle.status
    if new_status == current:
        return vehicle

    if new_status not in LEGAL_TRANSITIONS.get(current, frozenset()):
        raise StateError(
            f"Cannot change a {current} vehicle to {new_status}."
        )

    vehicle.status = new_status
    return vehicle


def mark_rented(session: Session, vehicle: Vehicle) -> Vehicle:
    """Called from check-in. Bypasses the state machine deliberately."""
    vehicle.status = "rented"
    return vehicle


def mark_available(session: Session, vehicle: Vehicle) -> Vehicle:
    """Called from check-out.

    Refuses to return a vehicle to the pool while it has open maintenance --
    otherwise a car that was sent to the workshop on the way back would
    immediately read `available` and be offered to the next customer.
    """
    if has_open_maintenance(session, vehicle):
        vehicle.status = "maintenance"
    else:
        vehicle.status = "available"
    return vehicle


def mark_maintenance(session: Session, vehicle: Vehicle) -> Vehicle:
    vehicle.status = "maintenance"
    return vehicle


def is_rentable(vehicle: Vehicle) -> bool:
    return vehicle.status in RENTABLE_STATUSES


# --------------------------------------------------------------------------
# Maintenance
# --------------------------------------------------------------------------


def has_open_maintenance(session: Session, vehicle: Vehicle) -> bool:
    """True when the vehicle has maintenance that is scheduled or under way.

    Scheduled counts: a car booked into the workshop on Friday is not rentable
    today either, because the booking will take it away.
    """
    return any(
        record.status in BLOCKING_MAINTENANCE_STATUSES
        for record in vehicle.maintenance_records
    )


def maintenance_conflict(
    session: Session,
    vehicle: Vehicle,
    start_date: date,
    end_date: date,
) -> Maintenance_Record | None:
    """Open maintenance overlapping a half-open [start_date, end_date) range."""
    for record in vehicle.maintenance_records:
        if record.status not in BLOCKING_MAINTENANCE_STATUSES:
            continue
        if start_date < record.end_date and record.start_date < end_date:
            return record
    return None


def schedule_maintenance(
    session: Session,
    vehicle: Vehicle,
    description: str,
    start_date: date,
    end_date: date,
    cost,
    status: str = "scheduled",
) -> Maintenance_Record:
    """Put a vehicle into the workshop.

    Sets the vehicle's status immediately even for future-dated work, so the
    fleet screen shows what the diary says. `mark_available` puts it back once
    the record is completed.
    """
    if end_date < start_date:
        raise ValidationError(
            "Maintenance end date must not be before its start date.",
            field="end_date",
        )
    if status not in ("scheduled", "ongoing", "completed"):
        raise ValidationError(
            f"Unknown maintenance status: {status}", field="status"
        )

    record = Maintenance_Record(
        vehicle_id=vehicle.vehicle_id,
        description=description,
        cost=money(cost, field="cost"),
        start_date=start_date,
        end_date=end_date,
        status=status,
    )
    session.add(record)
    session.flush()
    session.refresh(vehicle)

    if status != "completed":
        mark_maintenance(session, vehicle)
    return record


def complete_maintenance(session: Session, record: Maintenance_Record) -> Maintenance_Record:
    """Finish a maintenance job and free the vehicle if nothing else holds it."""
    record.status = "completed"
    record.end_date = max(record.end_date, date.today())
    session.flush()

    vehicle = record.vehicle
    if not has_open_maintenance(session, vehicle):
        vehicle.status = "available"
    return record


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------


def category_id_for(session: Session, category_name: str) -> int:
    category = session.execute(
        select(Vehicle_Category).where(
            Vehicle_Category.category_name == category_name
        )
    ).scalar_one_or_none()
    if category is None:
        raise NotFoundError(f"No '{category_name}' category exists.")
    return category.category_id


def add_vehicle(
    session: Session,
    *,
    category_name: str,
    make: str,
    model: str,
    year: int,
    plate_number: str,
    daily_rate,
    mileage: int = 0,
    seats: int | None = None,
    transmission: str | None = None,
    fuel_type: str | None = None,
    body_style: str | None = None,
) -> Vehicle:
    """Create a vehicle, rejecting a plate that is already on the fleet.

    The plate is checked here rather than left to the unique constraint, so the
    counter clerk gets "Plate ABC-123 is already assigned to a Toyota Vios"
    instead of an IntegrityError.
    """
    plate = (plate_number or "").strip().upper()
    if not plate:
        raise ValidationError("Plate number is required.", field="plate_number")
    if len(plate) > 7:
        raise ValidationError(
            "Plate number must be 7 characters or fewer.", field="plate_number"
        )
    if not make.strip() or not model.strip():
        raise ValidationError("Make and model are required.", field="make")

    existing = session.execute(
        select(Vehicle).where(Vehicle.plate_number == plate)
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            f"Plate {plate} is already assigned to a "
            f"{existing.make} {existing.model}."
        )

    vehicle = Vehicle(
        category_id=category_id_for(session, category_name),
        make=make.strip(),
        model=model.strip(),
        year=year,
        plate_number=plate,
        daily_rate=money(daily_rate, field="daily_rate"),
        mileage=mileage,
        seats=seats,
        transmission=transmission,
        fuel_type=fuel_type,
        body_style=body_style,
        status="available",
        created_at=datetime.now(),
    )
    session.add(vehicle)
    session.flush()
    return vehicle


def update_vehicle(session: Session, vehicle: Vehicle, **changes) -> Vehicle:
    """Apply field changes, validating the ones with rules."""
    if "plate_number" in changes:
        plate = (changes["plate_number"] or "").strip().upper()
        clash = session.execute(
            select(Vehicle).where(
                Vehicle.plate_number == plate, Vehicle.vehicle_id != vehicle.vehicle_id
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise ConflictError(
                f"Plate {plate} is already assigned to a "
                f"{clash.make} {clash.model}."
            )
        changes["plate_number"] = plate

    if "daily_rate" in changes:
        changes["daily_rate"] = money(changes["daily_rate"], field="daily_rate")

    for field, value in changes.items():
        if not hasattr(vehicle, field):
            raise ValidationError(f"Unknown vehicle field: {field}", field=field)
        setattr(vehicle, field, value)

    session.flush()
    return vehicle


def record_mileage(session: Session, vehicle: Vehicle, mileage: int) -> Vehicle:
    """Set the odometer, refusing to move it backwards.

    A decreasing odometer means a typo at the counter, and it quietly
    corrupts every fuel-economy and service-interval calculation afterwards.
    """
    if mileage < vehicle.mileage:
        raise ValidationError(
            f"Mileage cannot go backwards: it is currently "
            f"{vehicle.mileage:,} km.",
            field="mileage",
        )
    vehicle.mileage = mileage
    return vehicle


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------


def list_vehicles(
    session: Session,
    *,
    statuses: tuple[str, ...] | None = None,
    category_name: str | None = None,
    search: str | None = None,
) -> list[Vehicle]:
    """Vehicles newest-first, with their category and maintenance preloaded.

    joinedload keeps this to one query; without it the fleet screen's status
    column and the open-maintenance check both trigger a query per row.
    """
    stmt = (
        select(Vehicle)
        .join(Vehicle_Category, Vehicle.category_id == Vehicle_Category.category_id)
        .options(joinedload(Vehicle.category), joinedload(Vehicle.maintenance_records))
    )
    if statuses:
        stmt = stmt.where(Vehicle.status.in_(statuses))
    if category_name:
        stmt = stmt.where(Vehicle_Category.category_name == category_name)
    if search:
        needle = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            Vehicle.plate_number.ilike(needle)
            | Vehicle.make.ilike(needle)
            | Vehicle.model.ilike(needle)
        )
    stmt = stmt.order_by(Vehicle.status, Vehicle.plate_number)
    return list(session.execute(stmt).scalars().unique())


def get_vehicle(session: Session, vehicle_id: int) -> Vehicle:
    vehicle = session.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise NotFoundError(f"Vehicle #{vehicle_id} does not exist.")
    return vehicle
