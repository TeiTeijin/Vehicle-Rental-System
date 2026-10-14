from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Maintenance_Record, Vehicle, Vehicle_Category
from app.services.errors import ConflictError, NotFoundError, StateError, ValidationError
from app.utils.money import money

#: Every value the column's enum accepts. `retired` is terminal storage:
#: old bookings keep their vehicle_id FK, but a retired vehicle can never
#: book again until returned to service.
VEHICLE_STATUSES = ("available", "reserved", "rented", "maintenance", "retired")

#: Statuses a new rental may start from (`reserved` is never stored).
RENTABLE_STATUSES = ("available",)

#: Legal status moves, keyed by the current status. Retiring out of `rented`
#: is refused (check the vehicle in first); the active-booking guard in
#: `retire_vehicle` / `bulk_set_status` covers the rest.
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "available": frozenset({"maintenance", "retired"}),
    "reserved": frozenset({"available", "maintenance", "retired"}),
    "rented": frozenset({"available", "maintenance"}),
    "maintenance": frozenset({"available", "rented", "retired"}),
    "retired": frozenset({"available"}),
}

#: Booking states that hold a vehicle. Duplicated here (not imported from
#: booking_service) to avoid a circular import: booking_service imports
#: this module.
_HOLDING_BOOKING_STATUSES = ("pending", "confirmed", "ongoing")

#: Booking states that count as real usage for stats.
RENTED_DAY_STATUSES = ("confirmed", "ongoing", "completed")

#: Maintenance statuses that mean the vehicle is in the shop.
#: Shared with maintenance_service so the two tabs never disagree.
BLOCKING_MAINTENANCE_STATUSES = ("scheduled", "in_progress")


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------


def set_status(session: Session, vehicle: Vehicle, new_status: str) -> Vehicle:
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
    if new_status == "retired":
        vehicle.retired_at = date.today()
    elif current == "retired":
        vehicle.retired_at = None
    return vehicle


def mark_rented(session: Session, vehicle: Vehicle) -> Vehicle:
    vehicle.status = "rented"
    return vehicle


def mark_available(session: Session, vehicle: Vehicle) -> Vehicle:
    if vehicle.status == "retired":
        # A retired car coming back from its last rental stays retired.
        return vehicle
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
    for record in vehicle.maintenance_records:
        if record.status not in BLOCKING_MAINTENANCE_STATUSES:
            continue
        # An unfinished job has no end date yet: it blocks from its start on.
        record_end = record.end_date or date.max
        if start_date < record_end and record.start_date < end_date:
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
    """Compat wrapper: the one lifecycle lives in maintenance_service."""
    from app.services import maintenance_service

    if status == "ongoing":
        status = "in_progress"
    return maintenance_service.start_maintenance(
        session,
        vehicle,
        description=description,
        start_date=start_date,
        end_date=end_date,
        estimate_cost=cost,
        status=status,
    )


def complete_maintenance(session: Session, record: Maintenance_Record) -> Maintenance_Record:
    """Compat wrapper: closes with today's date; final cost stays as quoted
    (use maintenance_service.complete_maintenance to set the final figure)."""
    from app.services import maintenance_service

    return maintenance_service.complete_maintenance(
        session, record, cost=record.cost if record.cost is not None else 0
    )


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
    color: str | None = None,
    registration_expiry=None,
    insurance_expiry=None,
    vehicle_class: str | None = None,
    engine_cc: int | None = None,
) -> Vehicle:
    plate = (plate_number or "").strip().upper()
    if not plate:
        raise ValidationError("Plate number is required.", field="plate_number")
    if len(plate) > 7:
        raise ValidationError(
            "Plate number must be 7 characters or fewer.", field="plate_number"
        )
    if not (make or "").strip() or not (model or "").strip():
        raise ValidationError("Make and model are required.", field="make")
    if year < 1900 or year > date.today().year + 2:
        raise ValidationError(
            f"Year must be between 1900 and {date.today().year + 2}.", field="year"
        )
    rate_value = money(daily_rate, field="daily_rate")
    if rate_value <= 0:
        raise ValidationError("Daily rate must be greater than zero.", field="daily_rate")
    if mileage is not None and mileage < 0:
        raise ValidationError("Mileage cannot be negative.", field="mileage")

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
        color=(color or "").strip() or None,
        registration_expiry=registration_expiry,
        insurance_expiry=insurance_expiry,
        vehicle_class=vehicle_class,
        engine_cc=engine_cc,
        status="available",
        created_at=datetime.now(),
    )
    session.add(vehicle)
    session.flush()
    return vehicle


def update_vehicle(session: Session, vehicle: Vehicle, **changes) -> Vehicle:
    if "plate_number" in changes:
        plate = (changes["plate_number"] or "").strip().upper()
        if not plate:
            raise ValidationError("Plate number is required.", field="plate_number")
        if len(plate) > 7:
            raise ValidationError(
                "Plate number must be 7 characters or fewer.", field="plate_number"
            )
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
    if "make" in changes and changes["make"] is not None:
        if not changes["make"].strip():
            raise ValidationError("Make is required.", field="make")
        changes["make"] = changes["make"].strip()
    if "model" in changes and changes["model"] is not None:
        if not changes["model"].strip():
            raise ValidationError("Model is required.", field="model")
        changes["model"] = changes["model"].strip()
    if "year" in changes and changes["year"] is not None:
        if changes["year"] < 1900 or changes["year"] > date.today().year + 2:
            raise ValidationError(
                f"Year must be between 1900 and {date.today().year + 2}.", field="year"
            )

    if "daily_rate" in changes:
        changes["daily_rate"] = money(changes["daily_rate"], field="daily_rate")
        if changes["daily_rate"] <= 0:
            raise ValidationError(
                "Daily rate must be greater than zero.", field="daily_rate"
            )

    if "mileage" in changes and changes["mileage"] is not None:
        if changes["mileage"] < 0:
            raise ValidationError("Mileage cannot be negative.", field="mileage")
        if changes["mileage"] < (vehicle.mileage or 0):
            raise ValidationError(
                f"Mileage cannot go backwards: it is currently {vehicle.mileage:,} km.",
                field="mileage",
            )

    for field, value in changes.items():
        if not hasattr(vehicle, field):
            raise ValidationError(f"Unknown vehicle field: {field}", field=field)
        setattr(vehicle, field, value)

    session.flush()
    return vehicle


def record_mileage(session: Session, vehicle: Vehicle, mileage: int) -> Vehicle:
    if mileage < vehicle.mileage:
        raise ValidationError(
            f"Mileage cannot go backwards: it is currently "
            f"{vehicle.mileage:,} km.",
            field="mileage",
        )
    vehicle.mileage = mileage
    return vehicle


def _active_booking_count(session: Session, vehicle: Vehicle) -> int:
    from app.models import Booking

    return (
        session.query(Booking)
        .filter(
            Booking.vehicle_id == vehicle.vehicle_id,
            Booking.status.in_(_HOLDING_BOOKING_STATUSES),
        )
        .count()
    )


def retire_vehicle(session: Session, vehicle: Vehicle) -> Vehicle:
    """Deactivate instead of deleting: history keeps its FK forever."""
    if vehicle is None:
        raise NotFoundError("That vehicle no longer exists.")
    if vehicle.status == "retired":
        return vehicle
    if _active_booking_count(session, vehicle):
        raise StateError(
            f"{vehicle.plate_number} still has an active booking; "
            "check it in (or cancel it) before retiring."
        )
    return set_status(session, vehicle, "retired")


def unretire_vehicle(session: Session, vehicle: Vehicle) -> Vehicle:
    if vehicle is None:
        raise NotFoundError("That vehicle no longer exists.")
    if vehicle.status != "retired":
        return vehicle
    return set_status(session, vehicle, "available")


def set_daily_rate(
    session: Session,
    vehicle: Vehicle,
    new_rate,
    *,
    changed_by: int | None = None,
    is_bulk: bool = False,
    note: str | None = None,
) -> Vehicle:
    """Change one rate and log it. Extensions recompute at current rates,
    so the log explains why identical extensions can cost different amounts."""
    from datetime import datetime

    from app.models import Vehicle_Rate_Log

    value = money(new_rate, field="daily_rate")
    if value <= 0:
        raise ValidationError("Daily rate must be greater than zero.", field="daily_rate")
    old = money(vehicle.daily_rate, field="daily_rate")
    if value == old:
        return vehicle
    vehicle.daily_rate = value
    session.add(
        Vehicle_Rate_Log(
            vehicle_id=vehicle.vehicle_id,
            old_rate=old,
            new_rate=value,
            changed_by=changed_by,
            changed_at=datetime.now(),
            is_bulk=is_bulk,
            note=(note or "").strip() or None,
        )
    )
    session.flush()
    return vehicle


def bulk_set_rate(
    session: Session,
    vehicle_ids: list[int],
    new_rate,
    *,
    changed_by: int | None = None,
    only_at_rate=None,
    note: str | None = None,
) -> dict:
    """Write one rate across many vehicles (per-vehicle or per-category scope
    is decided by the caller building `vehicle_ids`). `only_at_rate` protects
    per-vehicle exceptions: rows not currently at that rate are skipped, not
    overwritten. Returns {updated, skipped, errors}."""
    from app.models import Vehicle as _Vehicle

    value = money(new_rate, field="daily_rate")
    if value <= 0:
        raise ValidationError("Daily rate must be greater than zero.", field="daily_rate")
    guard = money(only_at_rate, field="only_at_rate") if only_at_rate is not None else None
    updated, skipped, errors = 0, 0, []
    for vid in dict.fromkeys(vehicle_ids):
        vehicle = session.get(_Vehicle, vid)
        if vehicle is None:
            errors.append((vid, "Vehicle no longer exists."))
            continue
        try:
            if guard is not None and money(vehicle.daily_rate, field="daily_rate") != guard:
                skipped += 1
                continue
            set_daily_rate(
                session, vehicle, value,
                changed_by=changed_by, is_bulk=True, note=note,
            )
            updated += 1
        except Exception as exc:  # noqa: BLE001 - collected per row
            errors.append((vid, str(exc)))
    session.flush()
    return {"updated": updated, "skipped": skipped, "errors": errors}


def bulk_set_status(
    session: Session,
    vehicle_ids: list[int],
    new_status: str,
    *,
    changed_by: int | None = None,
) -> dict:
    """Change status across many vehicles. Retire moves are refused per row
    while that vehicle holds an active booking. Returns {updated, skipped,
    errors}; skipped counts rows already at the target."""
    from app.models import Vehicle as _Vehicle

    if new_status not in VEHICLE_STATUSES:
        raise ValidationError(f"Unknown vehicle status: {new_status}", field="status")
    updated, skipped, errors = 0, 0, []
    for vid in dict.fromkeys(vehicle_ids):
        vehicle = session.get(_Vehicle, vid)
        if vehicle is None:
            errors.append((vid, "Vehicle no longer exists."))
            continue
        if vehicle.status == new_status:
            skipped += 1
            continue
        try:
            if new_status == "retired":
                retire_vehicle(session, vehicle)
            else:
                set_status(session, vehicle, new_status)
            updated += 1
        except Exception as exc:  # noqa: BLE001 - collected per row
            errors.append((vid, str(exc)))
    session.flush()
    return {"updated": updated, "skipped": skipped, "errors": errors}


def preview_bulk_rate(
    session: Session,
    vehicle_ids: list[int],
    *,
    only_at_rate=None,
) -> dict:
    """Count + current-rate spread for the bulk preview line
    ("N vehicles: ₱X → ₱Y")."""
    from app.models import Vehicle as _Vehicle

    guard = money(only_at_rate, field="only_at_rate") if only_at_rate is not None else None
    spread: dict[str, int] = {}
    matched = 0
    for vid in dict.fromkeys(vehicle_ids):
        vehicle = session.get(_Vehicle, vid)
        if vehicle is None:
            continue
        current = money(vehicle.daily_rate, field="daily_rate")
        if guard is not None and current != guard:
            continue
        matched += 1
        spread[f"{current:,.2f}"] = spread.get(f"{current:,.2f}", 0) + 1
    mode = max(spread, key=spread.get) if spread else None
    return {"count": matched, "spread": spread, "mode": mode}


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------


def _date_union_days(ranges: list[tuple[date, date]]) -> int:
    """Union day-count of inclusive [start, end] ranges."""
    points = sorted(ranges)
    total = 0
    cur_start = cur_end = None
    for start, end in points:
        if cur_start is None:
            cur_start, cur_end = start, end
        elif start <= cur_end + timedelta(days=1):
            cur_end = max(cur_end, end)
        else:
            total += (cur_end - cur_start).days + 1
            cur_start, cur_end = start, end
    if cur_start is not None:
        total += (cur_end - cur_start).days + 1
    return total


def _clip(start: date, end: date, win_start: date, win_end: date) -> tuple[date, date] | None:
    clipped = (max(start, win_start), min(end, win_end))
    return clipped if clipped[0] <= clipped[1] else None


def vehicle_stats(
    session: Session,
    vehicle: Vehicle,
    window_days: int = 30,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
) -> dict:
    """Times rented, revenue, and utilization over a trailing window.

    Utilization = rented days ÷ days available, where available excludes
    maintenance and retired days. Ranges clipping the window edge are cut
    at the window start so a booking that began earlier is not over-counted.

    Pass `from_date`/`to_date` for an explicit period (the Reports suite
    does); otherwise the trailing `window_days` ending today is used.
    Open-ended ranges stop at the earlier of today and the window end.
    """
    from app.models import Booking
    from app.services import payment_service

    today = date.today()
    if from_date is not None and to_date is not None:
        if to_date < from_date:
            raise ValidationError("The report cannot end before it starts.")
        win_start, win_end = from_date, to_date
        window_days = (to_date - from_date).days + 1
    else:
        win_start = today - timedelta(days=window_days - 1)
        win_end = today
    anchor = min(today, win_end)

    bookings = (
        session.query(Booking)
        .filter(
            Booking.vehicle_id == vehicle.vehicle_id,
            Booking.status.in_(RENTED_DAY_STATUSES),
        )
        .all()
    )
    rented_ranges = []
    times_rented = 0
    revenue = money(0)
    for booking in bookings:
        if booking.status == "completed":
            times_rented += 1
            revenue += money(booking.total_cost, field="total_cost")
        clipped = _clip(booking.start_date, booking.end_date, win_start, win_end)
        if clipped is not None:
            rented_ranges.append(clipped)
    rented_days = _date_union_days(rented_ranges)

    shop_ranges = []
    for record in vehicle.maintenance_records:
        if record.status not in BLOCKING_MAINTENANCE_STATUSES:
            continue
        # An unfinished job has no end date yet: it blocks from its start on.
        clipped = _clip(record.start_date, record.end_date or anchor, win_start, win_end)
        if clipped is not None:
            shop_ranges.append(clipped)
    maintenance_days = _date_union_days(shop_ranges)

    retired_days = 0
    if vehicle.retired_at is not None:
        clipped = _clip(vehicle.retired_at, anchor, win_start, win_end)
        if clipped is not None:
            retired_days = (clipped[1] - clipped[0]).days + 1
    elif vehicle.status == "retired":
        retired_days = window_days

    available_days = max(0, window_days - maintenance_days - retired_days)
    utilization = (rented_days / available_days) if available_days else None

    maintenance_cost = sum(
        (
            money(r.cost, field="cost")
            for r in vehicle.maintenance_records
            if r.cost is not None
        ),
        money(0),
    )
    return {
        "window_days": window_days,
        "times_rented": times_rented,
        "total_revenue": revenue,
        "rented_days": rented_days,
        "maintenance_days": maintenance_days,
        "retired_days": retired_days,
        "available_days": available_days,
        "utilization": utilization,
        "maintenance_cost": maintenance_cost,
    }


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
