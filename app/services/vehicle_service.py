from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Vehicle, Vehicle_Category, Vehicle_Media


CC_BUCKETS: tuple[tuple[str, int | None, int | None], ...] = (
    ("Up to 125cc", None, 125),
    ("126 - 155cc", 126, 155),
    ("156 - 400cc", 156, 400),
    ("Over 400cc", 401, None),
)

#: Every size class the filter popover offers, in the order it is shown.
VEHICLE_CLASSES: tuple[str, ...] = (
    "small",
    "medium",
    "suv",
    "van",
    "pickup",
    "truck",
    "motorcycle",
)

#: Human labels for the size classes.
VEHICLE_CLASS_LABELS: dict[str, str] = {
    "small": "Small",
    "medium": "Medium",
    "suv": "SUV",
    "van": "Van",
    "pickup": "Pickup",
    "truck": "Truck",
    "motorcycle": "Motorcycle",
}

#: Every transmission the column's enum accepts.
TRANSMISSIONS: tuple[str, ...] = ("Automatic", "Manual")

#: Every fuel type the column's enum accepts. Electric has no vehicles in the
#: demo fleet but is a legal value, so it is offered.
FUEL_TYPES: tuple[str, ...] = ("Petrol", "Diesel", "Electric", "Hybrid")

#: Passenger counts, as offered in the popover. A count of 0 or 1 is not a thing
#: a rental fleet has, so the list is the real spread rather than 1..N.
SEAT_COUNTS: tuple[int, ...] = (2, 4, 5, 6, 7)

FILTER_AXES: tuple[str, ...] = (
    "makes",
    "vehicle_classes",
    "cc_buckets",
    "transmissions",
    "fuel_types",
    "seats",
)

#: What "this axis is not filtered on" looks like for `dataclasses.replace`, so
#: `axis_availability` can drop one axis without special-casing its type.
_EMPTY_AXIS: dict[str, object] = {
    "makes": frozenset(),
    "vehicle_classes": frozenset(),
    "cc_buckets": frozenset(),
    "transmissions": frozenset(),
    "fuel_types": frozenset(),
    "seats": frozenset(),
    "min_rate": None,
    "max_rate": None,
}

#: Column each discrete axis groups by. Engine CC is the exception -- it is a
#: number that has to be bucketed in Python, because the bucket is a label and
#: not something SQL can `GROUP BY` back into a range.
_AXIS_COLUMN: dict[str, object] = {
    "makes": Vehicle.make,
    "vehicle_classes": Vehicle.vehicle_class,
    "transmissions": Vehicle.transmission,
    "fuel_types": Vehicle.fuel_type,
    "seats": Vehicle.seats,
}


def cc_bucket_label(cc: int | None) -> str | None:
    if cc is None:
        return None
    for label, low, high in CC_BUCKETS:
        if (low is None or cc >= low) and (high is None or cc <= high):
            return label
    return None


@dataclass(frozen=True)
class VehicleFilter:
    makes: frozenset[str] = frozenset()
    vehicle_classes: frozenset[str] = frozenset()
    cc_buckets: frozenset[str] = frozenset()
    min_rate: int | None = None
    max_rate: int | None = None
    seats: frozenset[int] = frozenset()
    transmissions: frozenset[str] = frozenset()
    fuel_types: frozenset[str] = frozenset()
    search: str | None = None

    def is_empty(self) -> bool:
        return not any(
            (
                self.makes,
                self.vehicle_classes,
                self.cc_buckets,
                self.seats,
                self.transmissions,
                self.fuel_types,
                self.min_rate is not None or self.max_rate is not None,
                (self.search or "").strip(),
            )
        )

    def describe(self) -> str:
        parts = []
        if self.makes:
            parts.append(len(self.makes))
        if self.vehicle_classes:
            parts.append(len(self.vehicle_classes))
        if self.cc_buckets:
            parts.append(len(self.cc_buckets))
        if self.min_rate is not None or self.max_rate is not None:
            parts.append(1)
        if self.seats:
            parts.append(len(self.seats))
        if self.transmissions:
            parts.append(len(self.transmissions))
        if self.fuel_types:
            parts.append(len(self.fuel_types))
        if (self.search or "").strip():
            parts.append(1)
        if not parts:
            return "No filters"
        return f"{sum(parts)} filter{'s' if sum(parts) != 1 else ''}"


def _apply_filter(stmt, filters: "VehicleFilter"):
    if filters.makes:
        stmt = stmt.where(Vehicle.make.in_(list(filters.makes)))

    if filters.vehicle_classes:
        stmt = stmt.where(Vehicle.vehicle_class.in_(list(filters.vehicle_classes)))

    if filters.cc_buckets:
        # An OR of ANDs: (>=126 AND <=155) OR (>=401), composed by ORing the
        # per-bucket conditions rather than intersecting them, which would be
        # unsatisfiable for any two buckets.
        from sqlalchemy import and_, or_

        parts = []
        for label, low, high in CC_BUCKETS:
            if label not in filters.cc_buckets:
                continue
            clause = []
            if low is not None:
                clause.append(Vehicle.engine_cc >= low)
            if high is not None:
                clause.append(Vehicle.engine_cc <= high)
            if clause:
                parts.append(and_(*clause))
        if parts:
            stmt = stmt.where(or_(*parts))

    if filters.min_rate is not None:
        stmt = stmt.where(Vehicle.daily_rate >= filters.min_rate)
    if filters.max_rate is not None:
        stmt = stmt.where(Vehicle.daily_rate <= filters.max_rate)

    if filters.seats:
        stmt = stmt.where(Vehicle.seats.in_(list(filters.seats)))

    if filters.transmissions:
        stmt = stmt.where(Vehicle.transmission.in_(list(filters.transmissions)))

    if filters.fuel_types:
        stmt = stmt.where(Vehicle.fuel_type.in_(list(filters.fuel_types)))

    needle = (filters.search or "").strip().lower()
    if needle:
        like = f"%{needle}%"
        stmt = stmt.where(
            Vehicle.make.ilike(like)
            | Vehicle.model.ilike(like)
            | Vehicle.plate_number.ilike(like)
        )

    return stmt


@dataclass(frozen=True)
class ShowcaseVehicle:
    vehicle_id: int
    make: str
    model: str
    year: int
    daily_rate: Decimal
    seats: int | None
    transmission: str | None
    fuel_type: str | None
    body_style: str | None

    @property
    def eyebrow(self) -> str:
        return f"{self.make} / {self.body_style or 'VEHICLE'} / {self.year}".upper()

    @property
    def name(self) -> str:
        return self.model.upper()

    @property
    def price(self) -> str:
        return f"\u20b1{self.daily_rate:,.0f} / day"

    @property
    def specs(self) -> str:
        parts: list[str] = []
        if self.seats is not None:
            parts.append(f"{self.seats} seats")
        if self.transmission:
            parts.append(self.transmission)
        if self.fuel_type:
            parts.append(self.fuel_type)
        return " \u00b7 ".join(parts)


def showcase_vehicle_records(
    session: Session,
    category: str | None = "Car",
    statuses: tuple[str, ...] | None = ("available",),
) -> list[Vehicle]:
    query = session.query(Vehicle).options(joinedload(Vehicle.category))
    if category:
        query = query.join(
            Vehicle_Category, Vehicle.category_id == Vehicle_Category.category_id
        ).filter(Vehicle_Category.category_name == category)
    if statuses:
        query = query.filter(Vehicle.status.in_(statuses))

    return query.order_by(Vehicle.year.desc(), Vehicle.vehicle_id.asc()).all()


def showcase_vehicle_rows(
    session: Session,
    category: str | None = "Car",
    statuses: tuple[str, ...] | None = ("available",),
    makes: Sequence[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
    filters: "VehicleFilter | None" = None,
) -> list[tuple[Vehicle, str | None]]:
    photo = (
        session.query(Vehicle_Media.image_url)
        .filter(
            Vehicle_Media.vehicle_id == Vehicle.vehicle_id,
            Vehicle_Media.image_url.isnot(None),
            Vehicle_Media.image_url != "",
        )
        .order_by(
            (Vehicle_Media.view_angle == "front34").desc(),
            Vehicle_Media.media_id.asc(),
        )
        .limit(1)
        .correlate(Vehicle)
        .scalar_subquery()
    )

    query = (
        session.query(Vehicle, photo.label("photo_url"))
        .options(joinedload(Vehicle.category))
    )
    if category:
        query = query.join(
            Vehicle_Category, Vehicle.category_id == Vehicle_Category.category_id
        ).filter(Vehicle_Category.category_name == category)
    if statuses:
        query = query.filter(Vehicle.status.in_(statuses))
    if makes:
        query = query.filter(Vehicle.make.in_(list(makes)))
    if filters is not None and not filters.is_empty():
        query = _apply_filter(query, filters)

    rows = query.order_by(Vehicle.year.desc(), Vehicle.vehicle_id.asc())
    if limit is not None:
        rows = rows.offset(offset).limit(limit)
    return [(vehicle, url or None) for vehicle, url in rows.all()]


def vehicle_brands(session: Session) -> list[str]:
    rows = (
        session.query(Vehicle.make)
        .filter(Vehicle.make.isnot(None), Vehicle.make != "")
        .distinct()
        .order_by(Vehicle.make.asc())
        .all()
    )
    return [make for (make,) in rows]


def _reachability_query(session: Session, filters: "VehicleFilter"):
    query = session.query(Vehicle)
    if not filters.is_empty():
        query = _apply_filter(query, filters)
    return query


def axis_availability(
    session: Session,
    filters: "VehicleFilter",
    *,
    exclude: str | None = None,
) -> dict[str, set]:
    result: dict[str, set] = {}

    for axis in FILTER_AXES:
        partial = replace(filters, **{axis: _EMPTY_AXIS[axis]})
        query = _reachability_query(session, partial)

        if axis == "cc_buckets":
            ccs = query.with_entities(Vehicle.engine_cc).distinct().all()
            result[axis] = {
                label for (cc,) in ccs if (label := cc_bucket_label(cc)) is not None
            }
            continue

        column = _AXIS_COLUMN[axis]
        values = query.with_entities(column).distinct().all()
        result[axis] = {value for (value,) in values if value is not None and value != ""}

    return result


def rate_bounds(
    session: Session,
    filters: "VehicleFilter",
    *,
    exclude: bool = False,
) -> tuple[int | None, int | None]:
    partial = replace(filters, min_rate=None, max_rate=None) if exclude else filters
    low, high = (
        _reachability_query(session, partial)
        .with_entities(func.min(Vehicle.daily_rate), func.max(Vehicle.daily_rate))
        .one()
    )
    return (
        int(low) if low is not None else None,
        int(high) if high is not None else None,
    )


def count_filtered_vehicles(
    session: Session,
    filters: "VehicleFilter",
    *,
    available_only: bool = False,
) -> int:
    query = session.query(func.count(Vehicle.vehicle_id))
    if available_only:
        query = query.filter(Vehicle.status == "available")
    if not filters.is_empty():
        query = _apply_filter(query, filters)
    return int(query.scalar() or 0)


def to_showcase(vehicle: Vehicle) -> ShowcaseVehicle:
    return ShowcaseVehicle(
        vehicle_id=vehicle.vehicle_id,
        make=vehicle.make,
        model=vehicle.model,
        year=vehicle.year,
        daily_rate=Decimal(str(vehicle.daily_rate)),
        seats=vehicle.seats,
        transmission=vehicle.transmission,
        fuel_type=vehicle.fuel_type,
        body_style=vehicle.body_style,
    )


def list_showcase_vehicles(
    session: Session,
    category: str = "Car",
    statuses: tuple[str, ...] = ("available",),
) -> list[ShowcaseVehicle]:
    return [to_showcase(v) for v in showcase_vehicle_records(session, category, statuses)]
