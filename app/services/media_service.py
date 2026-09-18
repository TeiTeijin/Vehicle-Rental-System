from datetime import datetime, timedelta

from app.models import Vehicle_Media
from app.services import carimages_client as ci

SIGNED_URL_TTL = timedelta(hours=1)
CATALOG_URL_TTL = timedelta(days=1)


def is_motorcycle(vehicle) -> bool:
    category = getattr(vehicle, "category", None)
    return bool(category and category.category_name == "Motorcycle")


def _row_is_fresh(row: Vehicle_Media, now: datetime) -> bool:
    if not row.cached_at:
        return False
    ttl = SIGNED_URL_TTL if "sig=" in (row.image_url or "") else CATALOG_URL_TTL
    return now - row.cached_at < ttl


def _fresh_rows(session, vehicle) -> list[Vehicle_Media]:
    now = datetime.now()
    rows = (
        session.query(Vehicle_Media)
        .filter_by(vehicle_id=vehicle.vehicle_id)
        .all()
    )
    fresh = [row for row in rows if _row_is_fresh(row, now)]
    return fresh if len(fresh) == len(rows) and rows else []


def _cache_for_car(session, vehicle) -> None:
    image_url = ci.get_signed_image_url(vehicle.make, vehicle.model, vehicle.year)
    model_url = None
    watermarked = True

    try:
        gen = ci.resolve_generation_slug(vehicle.make, vehicle.model, vehicle.year)
        if gen:
            model = ci.get_vehicle_model_3d_payload(vehicle.make, vehicle.model, gen)
            glb = (model.get("model") or {}).get("glb")
            if glb:
                model_url = glb
                watermarked = bool((model.get("model") or {}).get("watermarked"))
    except ci.CarImagesError:
        pass

    session.add(
        Vehicle_Media(
            vehicle_id=vehicle.vehicle_id,
            source="carimagesapi",
            view_angle="front34",
            image_url=image_url,
            model_3d_url=model_url or "",
            is_watermarked=watermarked,
            cached_at=datetime.now(),
        )
    )
    if model_url:
        session.add(
            Vehicle_Media(
                vehicle_id=vehicle.vehicle_id,
                source="carimagesapi",
                view_angle="model_3d",
                image_url=image_url,
                model_3d_url=model_url,
                is_watermarked=watermarked,
                cached_at=datetime.now(),
            )
        )


def _cache_for_motorcycle(session, vehicle) -> None:
    image_url = None
    try:
        gen = ci.resolve_moto_generation_slug(vehicle.make, vehicle.model, vehicle.year)
        if gen:
            image = ci.get_moto_image_payload(vehicle.make, vehicle.model, gen)
            webp = (image.get("images") or {}).get("webp")
            if webp:
                image_url = webp
    except ci.CarImagesError:
        pass

    if not image_url:
        image_url = ci.get_signed_image_url(vehicle.make, vehicle.model, vehicle.year, type="moto")

    session.add(
        Vehicle_Media(
            vehicle_id=vehicle.vehicle_id,
            source="carimagesapi",
            view_angle="front34",
            image_url=image_url,
            model_3d_url="",
            is_watermarked=True,
            cached_at=datetime.now(),
        )
    )


def get_or_fetch_media(session, vehicle) -> list[Vehicle_Media]:
    cached = _fresh_rows(session, vehicle)
    if cached:
        return cached

    session.query(Vehicle_Media).filter_by(vehicle_id=vehicle.vehicle_id).delete()

    if is_motorcycle(vehicle):
        _cache_for_motorcycle(session, vehicle)
    else:
        _cache_for_car(session, vehicle)

    session.flush()
    return list(
        session.query(Vehicle_Media)
        .filter_by(vehicle_id=vehicle.vehicle_id)
        .order_by(Vehicle_Media.media_id)
        .all()
    )