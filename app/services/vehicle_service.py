from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Vehicle, Vehicle_Category, Vehicle_Media
from app.services import media_service, vehicle_photos


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


def _first_media_value(session: Session, column):
    """The chosen image row's `column`: front34 wins, then lowest media_id.

    Both the url and its `cached_at` are read through this same subquery, so
    the freshness of a url is always judged against its own row.
    """
    return (
        session.query(column)
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


def showcase_vehicle_rows(
    session: Session,
    category: str | None = "Car",
    statuses: tuple[str, ...] | None = ("available",),
    makes: Sequence[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
    filters: "VehicleFilter | None" = None,
) -> list[tuple[Vehicle, str | None]]:
    photo = _first_media_value(session, Vehicle_Media.image_url)
    photo_cached_at = _first_media_value(session, Vehicle_Media.cached_at)

    query = (
        session.query(
            Vehicle,
            photo.label("photo_url"),
            photo_cached_at.label("photo_cached_at"),
        )
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
    now = datetime.now()
    return [
        (
            vehicle,
            url
            if url and media_service.url_is_fresh(url, cached_at, now)
            else None,
        )
        for vehicle, url, cached_at in rows.all()
    ]


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


# =========================================================================
#  SHOWROOM COPY  (customer app: app/ui/vehicle_details.py)
#  -------------------------------------------------------------------------
#  Display-only translations of stored values. The stored value is never
#  changed -- "Petrol" stays "Petrol" in the database and reads "Gas
#  (Unleaded)" on the vehicle page. Everything shown comes from the counter's
#  own columns: the customer app keeps no vehicle copy of its own.
# =========================================================================

#: Fuel wording for the spec grid.
FUEL_LABELS: dict[str, str] = {
    "Petrol": "Gas (Unleaded)",
    "Diesel": "Diesel",
    "Electric": "Electric",
    "Hybrid": "Hybrid",
}

#: What a NULL column reads as in the spec grid.
NOT_GIVEN = "\u2014"

#: The four spec boxes on the vehicle page, in reading order.
SPEC_FIELDS: tuple[str, ...] = ("passengers", "transmission", "fuel", "body")


def fuel_label(fuel_type: str | None) -> str:
    if not fuel_type:
        return NOT_GIVEN
    return FUEL_LABELS.get(fuel_type, fuel_type)


def transmission_label(transmission: str | None) -> str:
    return transmission or NOT_GIVEN


def passengers_label(vehicle: Vehicle) -> str:
    return str(vehicle.seats) if vehicle.seats else NOT_GIVEN


def body_label(vehicle: Vehicle) -> str:
    """'MPV' -- the body style, falling back to the size class."""
    explicit = (vehicle.body_style or "").strip()
    if explicit:
        return explicit
    return (
        VEHICLE_CLASS_LABELS.get((vehicle.vehicle_class or "").strip().lower(), "")
        or NOT_GIVEN
    )


#: Kept as the name the vehicle page reads: what kind of vehicle this is.
category_label = body_label


def display_name(vehicle: Vehicle) -> str:
    """'Toyota Innova' -- make and model, as one phrase."""
    return f"{vehicle.make} {vehicle.model}".strip()


def mileage_label(vehicle: Vehicle) -> str:
    """'84,280 km'. Counter rows always carry a mileage; 0 means unrecorded."""
    if not vehicle.mileage:
        return NOT_GIVEN
    return f"{vehicle.mileage:,} km"


def spec_values(vehicle: Vehicle) -> dict[str, str]:
    """The 2x2 spec grid: caption key -> value, straight from the row."""
    return {
        "passengers": passengers_label(vehicle),
        "transmission": transmission_label(vehicle.transmission),
        "fuel": fuel_label(vehicle.fuel_type),
        "body": body_label(vehicle),
    }


def summary_line(vehicle: Vehicle) -> str:
    """'MPV \u00b7 7 seats \u00b7 Diesel' -- the one-line description."""
    parts = [
        body_label(vehicle),
        f"{vehicle.seats} seats" if vehicle.seats else "",
        vehicle.fuel_type or "",
    ]
    return " \u00b7 ".join(part for part in parts if part and part != NOT_GIVEN)


def showroom_vehicles(
    session: Session, statuses: tuple[str, ...] = ("available",)
) -> list[Vehicle]:
    """Every vehicle the shop is offering, newest first, any category."""
    return showcase_vehicle_records(session, category=None, statuses=statuses)


def showroom_page(
    session: Session,
    *,
    search: str = "",
    limit: int = 24,
    offset: int = 0,
    statuses: tuple[str, ...] = ("available",),
) -> list[tuple[Vehicle, str | None]]:
    """One page of the showroom grid: (vehicle, cached photo url or None).

    The catalog is 1000+ rows on a live database, so the browse page never
    loads it whole: it pages with `limit`/`offset` and searches server-side.
    """
    filters = VehicleFilter(search=(search or "").strip() or None)
    return showcase_vehicle_rows(
        session,
        category=None,
        statuses=statuses,
        limit=limit,
        offset=offset,
        filters=filters,
    )


def showroom_count(
    session: Session, *, search: str = "", statuses: tuple[str, ...] = ("available",)
) -> int:
    """How many vehicles the same search would page through."""
    filters = VehicleFilter(search=(search or "").strip() or None)
    query = session.query(func.count(Vehicle.vehicle_id))
    if statuses:
        query = query.filter(Vehicle.status.in_(statuses))
    if not filters.is_empty():
        query = _apply_filter(query, filters)
    return int(query.scalar() or 0)


# =========================================================================
#  SHOWROOM DASHBOARD  (app/ui/home.py)
#  -------------------------------------------------------------------------
#  The landing page shows the showroom as a hero plus shelves ("Best cars
#  for 4 seaters", "Popular cars for road trips", and the rest below).
#  The available fleet is under a thousand rows, so one pass over it feeds
#  every shelf, the type chips and the search -- the page never runs ten
#  queries to draw ten rows, and paging "See all" touches no database.
# =========================================================================

#: "Best cars for 4 seaters" -- in rental terms a four-seater is the usual
#: four/five-seat car (SEAT_COUNTS' 4 and 5), not the two-seat coupe.
SHOP_FOUR_SEATS: tuple[int, ...] = (4, 5)

#: Where the family shelf starts.
SHOP_FAMILY_SEATS = 7

#: Bodies that make a good road trip.
SHOP_ROADTRIP_BODIES: frozenset[str] = frozenset({"SUV", "MPV", "Crossover"})

#: The working-fleet size classes.
SHOP_WORK_CLASSES: frozenset[str] = frozenset({"van", "pickup", "truck"})

#: How many cards one shelf shows before its "See all".
SHOP_ROW_SIZE = 12


@dataclass(frozen=True)
class ShopVehicle:
    """A showroom row reduced to plain values the customer pages can keep.

    The dashboard is built from one snapshot taken when the window opens;
    nothing here is session-bound, so pages hold it for as long as they live.
    """

    vehicle_id: int
    make: str
    model: str
    year: int
    name: str
    meta: str
    rate: Decimal
    status: str
    body: str
    vehicle_class: str
    seats: int | None
    transmission: str | None
    fuel_type: str | None
    category: str | None
    plate_number: str | None
    photo_ref: vehicle_photos.VehicleRef

    @property
    def eyebrow(self) -> str:
        """'TOYOTA / MPV / 2026' -- the hero's small caption."""
        parts = [self.make]
        if self.body and self.body != NOT_GIVEN:
            parts.append(self.body)
        parts.append(str(self.year))
        return " / ".join(parts).upper()

    @property
    def specs(self) -> str:
        """'7 seats \u00b7 Automatic \u00b7 Diesel' -- the hero's spec line."""
        parts: list[str] = []
        if self.seats:
            parts.append(f"{self.seats} seats")
        if self.transmission:
            parts.append(self.transmission)
        if self.fuel_type:
            parts.append(fuel_label(self.fuel_type))
        return " \u00b7 ".join(parts)


@dataclass(frozen=True)
class ShopSection:
    """One shelf: its name, the cards it shows, how many it holds."""

    key: str
    title: str
    vehicles: tuple[ShopVehicle, ...]
    total: int


@dataclass(frozen=True)
class ShopSectionSpec:
    """A shelf's recipe: how to pick and order its vehicles."""

    key: str
    title: str
    pick: Callable[[Sequence[ShopVehicle]], list[ShopVehicle]]


def _pick_four(items: Sequence[ShopVehicle]) -> list[ShopVehicle]:
    return [item for item in items if (item.seats or 0) in SHOP_FOUR_SEATS]


def _pick_roadtrip(items: Sequence[ShopVehicle]) -> list[ShopVehicle]:
    return [item for item in items if (item.body or "") in SHOP_ROADTRIP_BODIES]


def _pick_family(items: Sequence[ShopVehicle]) -> list[ShopVehicle]:
    return [item for item in items if (item.seats or 0) >= SHOP_FAMILY_SEATS]


def _pick_city(items: Sequence[ShopVehicle]) -> list[ShopVehicle]:
    return [item for item in items if (item.vehicle_class or "").lower() == "small"]


def _pick_automatic(items: Sequence[ShopVehicle]) -> list[ShopVehicle]:
    return [item for item in items if item.transmission == "Automatic"]


def _pick_budget(items: Sequence[ShopVehicle]) -> list[ShopVehicle]:
    # Cars only: the two-wheeler shelf has the cheap scooters already.
    cars = (
        item for item in items if (item.vehicle_class or "").lower() != "motorcycle"
    )
    return sorted(cars, key=lambda item: (item.rate, item.vehicle_id))


def _pick_work(items: Sequence[ShopVehicle]) -> list[ShopVehicle]:
    return [item for item in items if (item.vehicle_class or "").lower() in SHOP_WORK_CLASSES]


def _pick_premium(items: Sequence[ShopVehicle]) -> list[ShopVehicle]:
    # The top of the SUV range, not the work trucks (they cost more per day).
    suvs = (item for item in items if (item.vehicle_class or "").lower() == "suv")
    return sorted(suvs, key=lambda item: (-item.rate, item.vehicle_id))


def _pick_motorcycles(items: Sequence[ShopVehicle]) -> list[ShopVehicle]:
    return [item for item in items if (item.category or "") == "Motorcycle"]


#: The shelves, in the order the page shows them. The snapshot already
#: arrives newest first, so most recipes only filter -- the two that sort
#: (budget, premium) say so on purpose.
SHOP_SECTIONS: tuple[ShopSectionSpec, ...] = (
    ShopSectionSpec("four_seaters", "Best cars for 4 seaters", _pick_four),
    ShopSectionSpec("road_trips", "Popular cars for road trips", _pick_roadtrip),
    ShopSectionSpec("family", "Seven seats and up for the family", _pick_family),
    ShopSectionSpec("city", "Easy in the city", _pick_city),
    ShopSectionSpec("automatic", "Automatic, zero hassle", _pick_automatic),
    ShopSectionSpec("budget", "Ride for less", _pick_budget),
    ShopSectionSpec("work", "Pickups and vans that work", _pick_work),
    ShopSectionSpec("premium", "Premium picks", _pick_premium),
    ShopSectionSpec("two_wheels", "Two wheels, less fuel", _pick_motorcycles),
)


def _to_shop_vehicle(vehicle: Vehicle) -> ShopVehicle:
    return ShopVehicle(
        vehicle_id=vehicle.vehicle_id,
        make=vehicle.make or "",
        model=vehicle.model or "",
        year=int(vehicle.year or 0),
        name=display_name(vehicle),
        meta=summary_line(vehicle),
        rate=Decimal(str(vehicle.daily_rate or 0)),
        status=vehicle.status or "available",
        body=body_label(vehicle),
        vehicle_class=(vehicle.vehicle_class or "").strip(),
        seats=vehicle.seats,
        transmission=vehicle.transmission,
        fuel_type=vehicle.fuel_type,
        category=(
            vehicle.category.category_name if vehicle.category is not None else None
        ),
        plate_number=vehicle.plate_number,
        photo_ref=vehicle_photos.ref_for(vehicle),
    )


def shop_snapshot(
    session: Session, statuses: tuple[str, ...] = ("available",)
) -> list[ShopVehicle]:
    """The whole available showroom, plain values, one query.

    The customer pages keep this snapshot in memory; the live fleet is under
    a thousand available rows, which is one round trip and a few hundred KB.
    """
    return [
        _to_shop_vehicle(vehicle)
        for vehicle in showroom_vehicles(session, statuses=statuses)
    ]


def shop_hero(
    snapshot: Sequence[ShopVehicle], limit: int = 5
) -> list[ShopVehicle]:
    """The hero slides: the newest of each make, motorcycles aside."""
    picks: list[ShopVehicle] = []
    seen: set[str] = set()
    for item in snapshot:
        if (item.category or "") == "Motorcycle" or item.make in seen:
            continue
        seen.add(item.make)
        picks.append(item)
        if len(picks) >= limit:
            break
    return picks


def shop_sections(
    snapshot: Sequence[ShopVehicle], per_row: int = SHOP_ROW_SIZE
) -> list[ShopSection]:
    """Every shelf with something on it, in the order the page shows them."""
    sections: list[ShopSection] = []
    for spec in SHOP_SECTIONS:
        picked = spec.pick(snapshot)
        if picked:
            sections.append(
                ShopSection(
                    key=spec.key,
                    title=spec.title,
                    vehicles=tuple(picked[:per_row]),
                    total=len(picked),
                )
            )
    return sections


def body_section(name: str) -> ShopSectionSpec:
    """A shelf keyed by one body style -- what the type chips open."""
    body = name.strip()
    return ShopSectionSpec(
        key=f"body:{body}",
        title=body,
        pick=lambda items: [item for item in items if item.body == body],
    )


def shop_section_spec(key: str) -> ShopSectionSpec | None:
    """The shelf recipe behind a key ('four_seaters', 'body:SUV', ...)."""
    for spec in SHOP_SECTIONS:
        if spec.key == key:
            return spec
    prefix, _, name = key.partition(":")
    if prefix == "body" and name.strip():
        return body_section(name)
    return None


def shop_body_types(snapshot: Sequence[ShopVehicle]) -> list[tuple[str, int]]:
    """(body style, count) for the type chips, the common ones first."""
    counts = Counter(
        item.body for item in snapshot if item.body and item.body != NOT_GIVEN
    )
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))


def search_snapshot(
    snapshot: Sequence[ShopVehicle], needle: str
) -> list[ShopVehicle]:
    """The top bar's search, over the same snapshot the shelves came from."""
    text = (needle or "").strip().lower()
    if not text:
        return list(snapshot)
    return [
        item
        for item in snapshot
        if text in item.make.lower()
        or text in item.model.lower()
        or text in (item.plate_number or "").lower()
    ]


