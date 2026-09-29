"""Read-side queries for the dashboard showcase carousel."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session, joinedload

from app.models import Vehicle, Vehicle_Category, Vehicle_Media


@dataclass(frozen=True)
class ShowcaseVehicle:
    """Everything the hero needs for one carousel slide."""

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
        """e.g. 'TOYOTA / SUV / 2023'"""
        return f"{self.make} / {self.body_style or 'VEHICLE'} / {self.year}".upper()

    @property
    def name(self) -> str:
        """e.g. 'FORTUNER'"""
        return self.model.upper()

    @property
    def price(self) -> str:
        """e.g. '\u20b14,500 / day'"""
        return f"\u20b1{self.daily_rate:,.0f} / day"

    @property
    def specs(self) -> str:
        """e.g. '7 seats \u00b7 Automatic \u00b7 Diesel', skipping any unknown parts."""
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
    category: str = "Car",
    statuses: tuple[str, ...] = ("available",),
) -> list[Vehicle]:
    """The Vehicle rows behind the hero carousel, newest year first.

    Deliberately excludes motorcycles: the hero is designed around a car
    photo and the two categories need different image lookups.
    """
    query = (
        session.query(Vehicle)
        .options(joinedload(Vehicle.category))
        .join(Vehicle_Category, Vehicle.category_id == Vehicle_Category.category_id)
        .filter(Vehicle_Category.category_name == category)
    )
    if statuses:
        query = query.filter(Vehicle.status.in_(statuses))

    return query.order_by(Vehicle.year.desc(), Vehicle.vehicle_id.asc()).all()


def showcase_vehicle_rows(
    session: Session,
    category: str = "Car",
    statuses: tuple[str, ...] = ("available",),
) -> list[tuple[Vehicle, str | None]]:
    """Vehicles paired with their `front34` photo URL, in one round trip.

    The hero previously did N+1: one query for the vehicles, then a separate
    query per vehicle inside `media_service.get_or_fetch_media`. Against the
    remote Aiven instance that was 9 round trips at ~250ms each.

    This LEFT OUTER JOINs `Vehicle_Media` so the vehicle list and the photo
    URLs arrive together. `front34` is preferred, but any cached row with a
    usable URL is accepted as a fallback, so a vehicle that only has a
    `model_3d` row still gets a photo.

    The URL is returned raw and unvalidated: freshness is `media_service`'s
    concern, and a stale-but-present URL is still better than nothing here
    because `image_cache` will usually satisfy the request without HTTP.
    """
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
        .join(Vehicle_Category, Vehicle.category_id == Vehicle_Category.category_id)
        .filter(Vehicle_Category.category_name == category)
    )
    if statuses:
        query = query.filter(Vehicle.status.in_(statuses))

    rows = query.order_by(Vehicle.year.desc(), Vehicle.vehicle_id.asc()).all()
    return [(vehicle, url or None) for vehicle, url in rows]


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
    """Display-ready DTOs for the hero carousel."""
    return [to_showcase(v) for v in showcase_vehicle_records(session, category, statuses)]
