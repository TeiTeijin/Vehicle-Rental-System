"""Vehicle photos for the customer screens, from the CarImages API.

The counter app caches one three-quarter shot per vehicle in VEHICLE_MEDIA.
The vehicle page needs more than that (front, both sides and the 3D model), so
this module talks to the API directly and keeps the bytes in the shared media
cache (app/services/image_cache.py), keyed per view. Nothing here writes to the
database: the live catalog is read-only for the customer app.

Every function that can reach the network BLOCKS for up to `TIMEOUT` seconds,
so the UI calls them from a worker thread (see app/ui/vehicle_photo.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import requests

from app.config import CI_API_KEY, CI_API_SECRET
from app.services import api_cache
from app.services import carimages_client as ci
from app.services import image_cache

TIMEOUT = 30


@dataclass(frozen=True)
class VehicleRef:
    """What a photo needs from a vehicle: nothing session-bound.

    The widgets load photos on worker threads, and a SQLAlchemy row must not
    cross that line -- it belongs to its session. Taking a snapshot up front
    also means a closed session can never leave a tile half-drawn.
    """

    make: str
    model: str
    year: int
    body_style: str | None = None
    motorcycle: bool = False


def ref_for(vehicle) -> VehicleRef:
    """Snapshot a Vehicle row for the photo workers."""
    motorcycle = (vehicle.body_style or "") in ("Underbone", "Scooter")
    category = getattr(vehicle, "category", None)
    if category is not None and getattr(category, "category_name", None) == "Motorcycle":
        motorcycle = True
    return VehicleRef(
        make=vehicle.make,
        model=vehicle.model,
        year=vehicle.year,
        body_style=vehicle.body_style,
        motorcycle=motorcycle,
    )

#: The image views the vehicle page offers, in reading order. "3d" is the
#: model-viewer tab and is served by `model_glb_path`, not `photo_bytes`.
IMAGE_VIEWS: tuple[str, ...] = ("front", "left", "right")

#: Every tab on the vehicle page.
VIEWS: tuple[str, ...] = IMAGE_VIEWS + ("3d",)

#: Button captions.
VIEW_LABELS: dict[str, str] = {
    "front": "Front",
    "left": "Left Side",
    "right": "Right Side",
    "3d": "3D Model",
}

#: The payload's own name for each view. The API spells them differently and
#: only honors `view` on the authenticated /api/v1 endpoints, so the name
#: matters: `?view=side` on the legacy endpoint silently returns the default
#: three-quarter shot.
API_VIEWS: dict[str, str] = {
    "front": "front",
    "left": "side",
    "right": "side-r",
}

#: The three-quarter shot, used for list thumbnails.
DEFAULT_VIEW = "front34"

#: Only these hosts receive credentials; the CDN gets a clean request.
_API_HOSTS = {"carimagesapi.com", "www.carimagesapi.com"}
_DOWNLOAD_HOSTS = _API_HOSTS | {"cdn.carimagesapi.com"}

#: Payload per (kind, make, model, year): three views would otherwise mean
#: three identical lookups. The kind keeps a motorcycle and a car that happen
#: to share a name apart. Failures are not kept, so a blip self-heals.
_payloads: dict[tuple[str, str, str, int], dict] = {}


def _vehicle_key(vehicle) -> tuple[str, str, int]:
    return (
        str(vehicle.make).strip().lower(),
        str(vehicle.model).strip().lower(),
        int(vehicle.year),
    )


def _glb_path(vehicle) -> Path:
    """A collision-resistant local path for one vehicle's 3D model.

    Reuses the image cache's digest identity; a readable slug alone would let
    make "A-B" + model "C" overwrite make "A" + model "B-C".
    """
    key = image_cache._identity(
        vehicle.make, vehicle.model, vehicle.year, "3d"
    )
    return image_cache.CACHE_DIR.resolve() / f"{key}.glb"


def is_motorcycle(vehicle: VehicleRef) -> bool:
    """Motorcycles have their own payload: one photo, no 3D model."""
    return bool(getattr(vehicle, "motorcycle", False))


def views_for(vehicle) -> tuple[str, ...]:
    return ("front",) if is_motorcycle(vehicle) else VIEWS


def _payload(vehicle) -> dict | None:
    """The API's image payload for this vehicle, or None when it has none.

    Lookups are kept twice: in memory for this page, and on disk for the next
    launch. A relaunch with cached photos therefore opens without asking the
    API to resolve the same generations again.
    """
    key = _vehicle_key(vehicle)
    kind = "moto-image" if is_motorcycle(vehicle) else "car-image"
    memory_key = (kind, *key)
    if memory_key in _payloads:
        return _payloads[memory_key]

    cached = api_cache.load(*key, kind=kind)
    if cached is not None:
        _payloads[memory_key] = cached
        return cached

    payload: dict | None = None
    try:
        if is_motorcycle(vehicle):
            generation = ci.resolve_moto_generation_slug(*key)
            if generation:
                payload = ci.get_moto_image_payload(vehicle.make, vehicle.model, generation)
        else:
            generation = ci.resolve_generation_slug(*key)
            if generation:
                payload = ci.get_vehicle_image_payload(vehicle.make, vehicle.model, generation)
    except (ci.CarImagesError, requests.RequestException):
        payload = None

    if payload:
        _payloads[memory_key] = payload
        api_cache.store(*key, payload, kind=kind)
    return payload


def _payload_url(vehicle, view: str) -> str | None:
    payload = _payload(vehicle)
    if not payload:
        return None
    if is_motorcycle(vehicle):
        images = payload.get("images") or {}
        return images.get("webp") or images.get("jpg") or images.get("png")
    api_name = API_VIEWS.get(view, view)
    entry = (payload.get("views") or {}).get(api_name) or {}
    return entry.get("webp") or entry.get("jpg") or entry.get("png")


def _legacy_url(vehicle, view: str) -> str | None:
    """The keyed /image endpoint, which always answers with the front34 shot.

    Good enough for "front" and for thumbnails; it cannot produce a side view.
    """
    if view not in ("front", DEFAULT_VIEW):
        return None
    try:
        return ci.get_signed_image_url(
            vehicle.make,
            vehicle.model,
            vehicle.year,
            type="moto" if is_motorcycle(vehicle) else None,
        )
    except (ci.CarImagesError, requests.RequestException):
        return None


def _download(url: str, *, with_key: bool) -> bytes | None:
    """Fetch one image without ever sending the secret to the CDN.

    A keyed URL on the API host gets the key and secret. A URL on the CDN is
    fetched clean, even when the caller says it came from a keyed payload.
    Anything outside the known hosts is refused.
    """
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc not in _DOWNLOAD_HOSTS:
        return None
    authenticated = with_key and parsed.netloc in _API_HOSTS
    params = {"api_key": CI_API_KEY} if authenticated else None
    headers = {"X-Api-Secret": CI_API_SECRET} if authenticated else None
    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=TIMEOUT,
            allow_redirects=not authenticated,
        )
    except requests.RequestException:
        return None

    if response.status_code == 200 and response.content:
        return response.content
    if response.status_code not in (301, 302, 303, 307, 308):
        return None

    location = response.headers.get("location") or ""
    parsed_location = urlsplit(location)
    if (
        parsed_location.scheme != "https"
        or parsed_location.netloc not in _DOWNLOAD_HOSTS
    ):
        return None
    try:
        clean = requests.get(location, timeout=TIMEOUT)
    except requests.RequestException:
        return None
    return clean.content if clean.status_code == 200 and clean.content else None


def fetch_photo(vehicle, view: str) -> bytes | None:
    """Download one view (blocking). Prefers the API's per-view images and
    falls back to the keyed endpoint where that is honest."""
    url = _payload_url(vehicle, view)
    data = _download(url, with_key=True) if url else None
    if data:
        return data
    fallback = _legacy_url(vehicle, view)
    if not fallback:
        return None
    return _download(fallback, with_key=False)


def photo_bytes(vehicle, view: str) -> bytes | None:
    """One view's image, cached on disk (blocking: call it off the UI thread)."""
    if view == "3d":
        return None
    cached = image_cache.load(vehicle.make, vehicle.model, vehicle.year, view)
    if cached is not None:
        return cached.data
    data = fetch_photo(vehicle, view)
    if data:
        image_cache.store(
            vehicle.make, vehicle.model, vehicle.year, data, "", variant=view
        )
    return data


def thumbnail_bytes(vehicle) -> bytes | None:
    cached = image_cache.load(
        vehicle.make, vehicle.model, vehicle.year, DEFAULT_VIEW
    )
    if cached is not None:
        return cached.data
    data = fetch_photo(vehicle, DEFAULT_VIEW)
    if data:
        image_cache.store(
            vehicle.make, vehicle.model, vehicle.year, data, "", variant=DEFAULT_VIEW
        )
    return data


def _model_payload(vehicle) -> dict | None:
    """The API's 3D payload, read from disk before asking the network."""
    key = _vehicle_key(vehicle)
    cached = api_cache.load(*key, kind="car-model")
    if cached is not None:
        return cached

    try:
        generation = ci.resolve_generation_slug(*key)
        if not generation:
            return None
        payload = ci.get_vehicle_model_3d_payload(
            vehicle.make, vehicle.model, generation
        )
    except (ci.CarImagesError, requests.RequestException):
        return None
    if payload:
        api_cache.store(*key, payload, kind="car-model")
    return payload


def model_glb_path(vehicle) -> Path | None:
    """Local .glb for the 3D tab, downloading it once (blocking).

    Returns None when the vehicle has no model, so the tab can say so instead
    of showing an empty viewer.
    """
    if is_motorcycle(vehicle):
        return None
    path = _glb_path(vehicle)
    if path.exists() and path.stat().st_size:
        return path

    model = _model_payload(vehicle)
    if not model:
        return None
    glb = (model.get("model") or {}).get("glb")
    if not glb:
        return None
    try:
        # The payload hands back an auth-gated endpoint on carimagesapi.com;
        # download_model_glb carries the key and follows it to the CDN.
        data = ci.download_model_glb(glb)
    except (ci.CarImagesError, requests.RequestException):
        return None
    if not data:
        return None

    try:
        image_cache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError:
        return None
    return path


__all__ = [
    "API_VIEWS",
    "DEFAULT_VIEW",
    "IMAGE_VIEWS",
    "TIMEOUT",
    "VIEW_LABELS",
    "VIEWS",
    "VehicleRef",
    "fetch_photo",
    "is_motorcycle",
    "model_glb_path",
    "photo_bytes",
    "ref_for",
    "thumbnail_bytes",
    "views_for",
]
