from datetime import datetime, timedelta

from app.models import Vehicle_Media
from app.services import carimages_client as ci

SIGNED_URL_TTL = timedelta(hours=1)
CATALOG_URL_TTL = timedelta(days=1)


def is_motorcycle(vehicle) -> bool:
    category = getattr(vehicle, "category", None)
    return bool(category and category.category_name == "Motorcycle")


def url_is_fresh(
    url: str | None,
    cached_at: datetime | None,
    now: datetime | None = None,
) -> bool:
    """Signed URLs expire after an hour, catalog URLs after a day.

    Callers that hand a stored url to an image widget (which downloads it
    asynchronously and swallows failures) must treat a stale url as absent,
    or the widget silently paints nothing.
    """
    if not cached_at:
        return False
    ttl = SIGNED_URL_TTL if "sig=" in (url or "") else CATALOG_URL_TTL
    return (now or datetime.now()) - cached_at < ttl


def _row_is_fresh(row: Vehicle_Media, now: datetime) -> bool:
    return url_is_fresh(row.image_url, row.cached_at, now)


def _fresh_rows(session, vehicle) -> list[Vehicle_Media]:
    now = datetime.now()
    rows = (
        session.query(Vehicle_Media)
        .filter_by(vehicle_id=vehicle.vehicle_id)
        .all()
    )
    fresh = [row for row in rows if _row_is_fresh(row, now)]
    return fresh if len(fresh) == len(rows) and rows else []


def _cache_for_car(session, vehicle, include_3d: bool = False) -> None:
    image_url = ci.get_signed_image_url(vehicle.make, vehicle.model, vehicle.year)
    model_url = None
    watermarked = True

    if include_3d:
        try:
            gen = ci.resolve_generation_slug(vehicle.make, vehicle.model, vehicle.year)
            if gen:
                model = ci.get_vehicle_model_3d_payload(vehicle.make, vehicle.model, gen)
                glb = (model.get("model") or {}).get("glb")
                if glb:
                    # The payload hands back the auth-gated endpoint, not a
                    # playable file: resolve it server-side once and store
                    # the signed CDN URL the viewer can load directly.
                    model_url = ci.follow_model_redirect(glb) or None
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


def pick_photo_url(rows: list[Vehicle_Media]) -> str | None:
    return next(
        (r.image_url for r in rows if r.view_angle == "front34" and r.image_url),
        None,
    ) or next((r.image_url for r in rows if r.image_url), None)


def playable_model_url(session, row: Vehicle_Media) -> str:
    """URL the embedded viewer can load for one media row.

    Old rows store the auth-gated `.../model?redirect=1` endpoint (a browser
    gets 401 on it): resolve once server-side and persist the signed CDN URL
    so later views cost nothing. Already-playable URLs pass through.
    """
    url = row.model_3d_url or ""
    if "/api/v1/" in url and "/model" in url:
        resolved = ci.follow_model_redirect(url)
        if resolved and resolved != url:
            row.model_3d_url = resolved
            session.flush()
            return resolved
        return ""
    return url


def get_or_fetch_media(session, vehicle, include_3d: bool = False) -> list[Vehicle_Media]:
    cached = _fresh_rows(session, vehicle)
    if cached:
        return cached

    session.query(Vehicle_Media).filter_by(vehicle_id=vehicle.vehicle_id).delete()

    if is_motorcycle(vehicle):
        _cache_for_motorcycle(session, vehicle)
    else:
        _cache_for_car(session, vehicle, include_3d=include_3d)

    session.flush()
    return list(
        session.query(Vehicle_Media)
        .filter_by(vehicle_id=vehicle.vehicle_id)
        .order_by(Vehicle_Media.media_id)
        .all()
    )


def _is_not_found(error: BaseException) -> bool:
    """True only for the API's unknown-make/model answer.

    `CarImagesError` itself is the 400/403/404 bucket; its subclasses
    (`_RESTKeyError`, `_RateLimitedError`) and every `requests` failure
    mean the check itself could not run.
    """
    return type(error) is ci.CarImagesError


def probe_media(make: str, model: str, year: int | None, category_name: str | None) -> str:
    """Would this make/model get photos? Read-only, never writes rows.

    Returns `"ok"`, `"not_found"` (check the spelling), or `"unreachable"`
    (the API or keys could not be reached -- never block a save on this).
    Mirrors the photo half of `get_or_fetch_media`.
    """
    if not (make or "").strip() or not (model or "").strip():
        return "not_found"
    try:
        if (category_name or "").strip() == "Motorcycle":
            try:
                gen = ci.resolve_moto_generation_slug(make, model, year)
            except ci.CarImagesError as exc:
                return "not_found" if _is_not_found(exc) else "unreachable"
            if gen:
                try:
                    payload = ci.get_moto_image_payload(make, model, gen)
                except ci.CarImagesError as exc:
                    return "not_found" if _is_not_found(exc) else "unreachable"
                if (payload.get("images") or {}).get("webp"):
                    return "ok"
            try:
                ci.get_signed_image_url(make, model, year, type="moto")
            except ci.CarImagesError as exc:
                return "not_found" if _is_not_found(exc) else "unreachable"
            return "ok"
        try:
            ci.get_signed_image_url(make, model, year)
        except ci.CarImagesError as exc:
            return "not_found" if _is_not_found(exc) else "unreachable"
        return "ok"
    except Exception:  # noqa: BLE001 - a probe never raises; doubt means retry later
        return "unreachable"
