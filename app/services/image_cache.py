"""On-disk cache of CarImages photo bytes.

The database only stores the *URL* for a vehicle photo, which means the hero
was re-downloading 70-85KB per vehicle on every single launch. This module
keeps the bytes locally so a warm launch needs no HTTP at all.

Why this is keyed on vehicle identity rather than on the URL
----------------------------------------------------------
`media_service.SIGNED_URL_TTL` is one hour: CarImages rotates the `sig=`
parameter on the signed URL roughly every hour. A cache keyed by URL would
therefore miss on almost every launch and buy us nothing.

The *photo* for a given vehicle does not change, only the signature on the
link to it does. So each entry stores the bytes plus the vehicle's
`make`/`model`/`year` identity, and stays valid across any number of URL
rotations. It is invalidated only when that identity changes, or when
`clear()` is called.

Thread safety
-------------
The hero downloads on a 4-thread pool, so every write goes to a temporary
file in the same directory followed by an atomic `os.replace`. Two threads
racing on the same entry therefore never produce a torn file.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "cache" / "media"
_META_SUFFIX = ".json"
_BYTES_SUFFIX = ".img"


@dataclass(frozen=True)
class CachedImage:
    """A photo that was read back from disk, with the URL it came from."""

    data: bytes
    url: str


def _identity(make: str, model: str, year: int) -> str:
    """Stable cache key from the vehicle's identity, not its signed URL."""
    parts = [str(make).strip().lower(), str(model).strip().lower(), str(year).strip()]
    return "-".join(parts).replace(" ", "-")


def _paths(key: str) -> tuple[Path, Path]:
    return CACHE_DIR / f"{key}{_BYTES_SUFFIX}", CACHE_DIR / f"{key}{_META_SUFFIX}"


def load(make: str, model: str, year: int) -> CachedImage | None:
    """Return the cached bytes for this vehicle, or None on any miss.

    A miss is not an error: it just means the caller should download. Any
    corrupt or half-written entry is treated as a miss and removed so the next
    call can repopulate it cleanly.
    """
    bytes_path, meta_path = _paths(_identity(make, model, year))
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        data = bytes_path.read_bytes()
    except (OSError, ValueError):
        # Stale or corrupt sidecar: drop the pair and report a miss.
        _discard(bytes_path, meta_path)
        return None

    if not data:
        _discard(bytes_path, meta_path)
        return None
    return CachedImage(data=data, url=str(meta.get("url", "")))


def store(
    make: str,
    model: str,
    year: int,
    data: bytes,
    url: str,
) -> None:
    """Persist `data` for this vehicle. Never raises: caching is best-effort."""
    if not data:
        return
    key = _identity(make, model, year)
    bytes_path, meta_path = _paths(key)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    try:
        _atomic_write(bytes_path, data)
        _atomic_write(
            meta_path,
            json.dumps(
                {
                    "make": make,
                    "model": model,
                    "year": year,
                    "url": url,
                },
                indent=2,
            ).encode("utf-8"),
        )
    except OSError:
        _discard(bytes_path, meta_path)


def _atomic_write(target: Path, data: bytes) -> None:
    handle, temp_name = tempfile.mkstemp(dir=str(CACHE_DIR), suffix=".part")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temp_name, target)
    except OSError:
        _discard(Path(temp_name))
        raise


def _discard(*paths: Path) -> None:
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass


def clear() -> int:
    """Delete every cached photo. Returns the number of entries removed."""
    if not CACHE_DIR.exists():
        return 0
    removed = sum(1 for _ in CACHE_DIR.glob(f"*{_BYTES_SUFFIX}"))
    shutil.rmtree(CACHE_DIR, ignore_errors=True)
    return removed


def stats() -> tuple[int, int]:
    """(entry count, total bytes on disk)."""
    if not CACHE_DIR.exists():
        return 0, 0
    files = list(CACHE_DIR.glob(f"*{_BYTES_SUFFIX}"))
    return len(files), sum(f.stat().st_size for f in files if f.exists())


__all__ = ["CACHE_DIR", "CachedImage", "clear", "load", "stats", "store"]
