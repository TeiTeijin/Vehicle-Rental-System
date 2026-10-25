from urllib.parse import urlsplit

import requests

from app.config import CI_API_KEY, CI_API_SECRET

BASE_URL = "https://carimagesapi.com"

TIMEOUT = 30

#: Hosts that receive the API key/secret. Never send credentials to a host
#: merely because a payload or a database row named it.
API_HOSTS = {"carimagesapi.com", "www.carimagesapi.com"}
CDN_HOSTS = {"cdn.carimagesapi.com"}
ALLOWED_DOWNLOAD_HOSTS = API_HOSTS | CDN_HOSTS


def _host_allowed(url: str, hosts: set[str]) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.netloc in hosts


class CarImagesError(Exception):
    pass


class _RESTKeyError(CarImagesError):
    pass


class _RateLimitedError(CarImagesError):
    pass


def _rest_headers() -> dict:
    if not CI_API_KEY:
        raise _RESTKeyError("CI_API_KEY is not set in .env")
    if not CI_API_SECRET:
        raise _RESTKeyError(
            "CI_API_SECRET is not set in .env. "
            "Find it in the CarImages dashboard and add it."
        )
    return {"X-Api-Secret": CI_API_SECRET}


def _handle_error(resp: requests.Response) -> None:
    status = resp.status_code
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    message = f"HTTP {status}"
    if isinstance(payload, dict) and payload.get("error"):
        message = payload["error"]
    if status == 401:
        raise _RESTKeyError(f"{message}")
    if status == 429:
        raise _RateLimitedError(f"{message}")
    if status in (403, 404, 400):
        raise CarImagesError(f"{message}")
    if status >= 400:
        raise requests.RequestException(f"Request failed: {message}")


def _slugify(value: str) -> str:
    return value.strip().lower().replace("_", "-").replace(" ", "-")


def get_signed_image_url(
    make: str,
    model: str | None = None,
    year: int | None = None,
    view: str | None = None,
    width: int | None = None,
    format: str = "webp",
    type: str | None = None,
) -> str:
    if not CI_API_KEY:
        raise _RESTKeyError("CI_API_KEY is not set in .env")

    params = {
        "api_key": CI_API_KEY,
        "make": make,
        "format": format,
    }
    if model:
        params["model"] = model
    if year:
        params["year"] = str(year)
    if view:
        params["view"] = view
    if width:
        params["width"] = str(width)
    if type:
        params["type"] = type

    resp = requests.get(f"{BASE_URL}/api/v1/signed-url", params=params, timeout=TIMEOUT)
    if resp.status_code != 200:
        _handle_error(resp)
    return resp.json()["url"]


def get_signed_image_urls(images: list[dict]) -> list[str]:
    if not CI_API_KEY:
        raise _RESTKeyError("CI_API_KEY is not set in .env")

    headers = {"Content-Type": "application/json"}
    if CI_API_SECRET:
        headers["X-Api-Secret"] = CI_API_SECRET

    url = f"{BASE_URL}/api/v1/signed-urls?api_key={CI_API_KEY}"
    resp = requests.post(url, json={"images": images}, headers=headers, timeout=TIMEOUT)
    if resp.status_code != 200:
        _handle_error(resp)
    return resp.json()["urls"]


def _resolve_model_slug(make: str, model: str, namespace: str = "makes") -> str | None:
    headers = _rest_headers()
    resp = requests.get(
        f"{BASE_URL}/api/v1/{namespace}/{_slugify(make)}/models",
        params={"api_key": CI_API_KEY},
        headers=headers,
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        return None

    candidates = resp.json().get("data") or []
    needle = model.strip().lower()

    direct = next(
        (m for m in candidates if m.get("slug") == _slugify(needle) or m.get("name", "").lower() == needle),
        None,
    )
    if direct:
        return direct["slug"]

    def norm(value: str) -> str:
        return "".join(ch for ch in value.lower() if ch.isalnum())

    return next(
        (m["slug"] for m in candidates if norm(m.get("name", "")) == norm(needle)),
        None,
    )


def _resolve_generation_slug(make: str, model: str, year: int, namespace: str = "makes") -> str | None:
    headers = _rest_headers()

    model_slug = _slugify(model)
    resp = requests.get(
        f"{BASE_URL}/api/v1/{namespace}/{_slugify(make)}/models/{model_slug}",
        params={"api_key": CI_API_KEY},
        headers=headers,
        timeout=TIMEOUT,
    )
    if resp.status_code == 404:
        model_slug = _resolve_model_slug(make, model, namespace)
        if not model_slug:
            return None
        resp = requests.get(
            f"{BASE_URL}/api/v1/{namespace}/{_slugify(make)}/models/{model_slug}",
            params={"api_key": CI_API_KEY},
            headers=headers,
            timeout=TIMEOUT,
        )
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        _handle_error(resp)

    generations = resp.json().get("generations") or []
    if not generations:
        return None

    best = None
    for gen in generations:
        y_start = gen.get("year_start")
        y_end = gen.get("year_end")
        if y_start is not None and y_end is not None and y_start <= year <= y_end:
            return gen["slug"]
        if best is None:
            best = gen
    return best["slug"] if best else None


def resolve_generation_slug(make: str, model: str, year: int) -> str | None:
    return _resolve_generation_slug(make, model, year, namespace="makes")


def resolve_moto_generation_slug(make: str, model: str, year: int) -> str | None:
    return _resolve_generation_slug(make, model, year, namespace="motos")


def get_vehicle_image_payload(make: str, model: str, gen: str) -> dict:
    headers = _rest_headers()
    resp = requests.get(
        f"{BASE_URL}/api/v1/vehicles/{_slugify(make)}/{_slugify(model)}/{gen}/image",
        params={"api_key": CI_API_KEY},
        headers=headers,
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        _handle_error(resp)
    return resp.json()


def get_vehicle_model_3d_payload(make: str, model: str, gen: str) -> dict:
    headers = _rest_headers()
    resp = requests.get(
        f"{BASE_URL}/api/v1/vehicles/{_slugify(make)}/{_slugify(model)}/{gen}/model",
        params={"api_key": CI_API_KEY, "redirect": 0},
        headers=headers,
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        _handle_error(resp)
    return resp.json()


def get_moto_image_payload(make: str, model: str, gen: str) -> dict:
    headers = _rest_headers()
    resp = requests.get(
        f"{BASE_URL}/api/v1/motos/{_slugify(make)}/{_slugify(model)}/{gen}/image",
        params={"api_key": CI_API_KEY},
        headers=headers,
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        _handle_error(resp)
    return resp.json()


def follow_model_redirect(endpoint_url: str) -> str | None:
    """Resolve a `.../model?redirect=1` endpoint URL to the playable CDN URL.

    The endpoint needs key + secret, which a browser must never see, so this
    runs server-side: first hop carries auth and is never followed blindly
    (requests would re-send the secret to the CDN), the CDN hop goes out
    clean. Returns None when anything in the chain fails.
    """
    if not endpoint_url or not _host_allowed(endpoint_url, API_HOSTS):
        return None
    try:
        first = requests.get(
            endpoint_url,
            params={"api_key": CI_API_KEY},
            headers=_rest_headers(),
            timeout=TIMEOUT,
            allow_redirects=False,
        )
    except Exception:
        return None
    if first.status_code not in (301, 302, 303, 307, 308):
        return None
    location = first.headers.get("location") or ""
    if not location or not _host_allowed(location, ALLOWED_DOWNLOAD_HOSTS):
        return None
    return location


def download_model_glb(glb_url: str) -> bytes:
    parsed = urlsplit(glb_url)
    if parsed.scheme != "https" or parsed.netloc not in ALLOWED_DOWNLOAD_HOSTS:
        raise CarImagesError(
            f"Refusing to download GLB from untrusted host: {parsed.netloc}"
        )

    # Only the API host gets the secret. A signed CDN URL is fetched clean:
    # following a redirect automatically would let requests forward custom
    # headers across hosts, so the credentialed hop is done by hand.
    credentialed = parsed.netloc in API_HOSTS
    headers = _rest_headers() if credentialed else {}
    target = glb_url
    if credentialed:
        separator = "&" if "?" in target else "?"
        target = f"{target}{separator}api_key={CI_API_KEY}"

    resp = requests.get(
        target,
        headers=headers,
        timeout=60,
        allow_redirects=not credentialed,
    )
    if credentialed and resp.status_code in (301, 302, 303, 307, 308):
        location = resp.headers.get("location") or ""
        if not _host_allowed(location, ALLOWED_DOWNLOAD_HOSTS):
            raise CarImagesError("Model redirect points at an untrusted host")
        resp = requests.get(location, timeout=60)
    if resp.status_code != 200:
        _handle_error(resp)
    return resp.content


def ping() -> bool:
    """Cheap live reachability/credential check for the Settings button.

    Resolving a common model needs both the API key and the secret. A missing
    key, bad credentials, a timeout, or a malformed payload all mean "no".
    """
    try:
        return resolve_generation_slug("Toyota", "Vios", 2022) is not None
    except Exception:  # noqa: BLE001 - a status probe must never raise
        return False
