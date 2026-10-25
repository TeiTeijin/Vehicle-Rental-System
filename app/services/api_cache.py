"""On-disk JSON cache for CarImages API lookups.

The image bytes already live in :mod:`app.services.image_cache`. This keeps
the *lookup* payload as well: resolving a model's generation and asking for
its image URLs currently costs two API round trips per vehicle, every time the
process starts. The payload is catalog data with stable URLs, so it is safe to
reuse across launches; the downloaded bytes still go through their own cache.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "cache" / "api"
_SUFFIX = ".json"
_VERSION = 1

#: Anything that is not a letter or a digit becomes one dash. Keeps model
#: names such as "CX-5" or "Land Cruiser" from escaping the cache folder.
_UNSAFE = re.compile(r"[^a-z0-9]+")


def _key(make: str, model: str, year: int, kind: str) -> str:
    raw = f"{kind}-{make}-{model}-{year}".lower()
    return _UNSAFE.sub("-", raw).strip("-")


def _path(make: str, model: str, year: int, kind: str) -> Path:
    return CACHE_DIR / f"{_key(make, model, year, kind)}{_SUFFIX}"


def load(make: str, model: str, year: int, kind: str = "image") -> dict | None:
    """The cached payload for this vehicle, or None when there is no usable one."""
    path = _path(make, model, year, kind)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _discard(path)
        return None

    if not isinstance(record, dict) or record.get("version") != _VERSION:
        _discard(path)
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict) or not payload:
        _discard(path)
        return None
    return payload


def store(
    make: str,
    model: str,
    year: int,
    payload: dict,
    kind: str = "image",
) -> None:
    """Write one payload atomically; a cache failure never breaks the page."""
    if not isinstance(payload, dict) or not payload:
        return
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    record = {
        "version": _VERSION,
        "make": make,
        "model": model,
        "year": year,
        "kind": kind,
        "payload": payload,
    }
    target = _path(make, model, year, kind)
    try:
        _atomic_write(target, json.dumps(record, ensure_ascii=False).encode("utf-8"))
    except OSError:
        _discard(target)


def _atomic_write(target: Path, data: bytes) -> None:
    handle, temp_name = tempfile.mkstemp(dir=str(CACHE_DIR), suffix=".part")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temp_name, target)
    except OSError:
        _discard(Path(temp_name))
        raise


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def clear() -> int:
    """Remove every cached payload and return how many records were removed."""
    if not CACHE_DIR.exists():
        return 0
    records = list(CACHE_DIR.glob(f"*{_SUFFIX}"))
    shutil.rmtree(CACHE_DIR, ignore_errors=True)
    return len(records)


def stats() -> tuple[int, int]:
    """(record count, total bytes) for the settings screen."""
    if not CACHE_DIR.exists():
        return 0, 0
    records = list(CACHE_DIR.glob(f"*{_SUFFIX}"))
    return len(records), sum(path.stat().st_size for path in records if path.exists())


__all__ = ["CACHE_DIR", "clear", "load", "stats", "store"]
