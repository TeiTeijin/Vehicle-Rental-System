from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "cache" / "media"
_META_SUFFIX = ".json"
_BYTES_SUFFIX = ".img"
#: Anything that is not a lowercase ASCII letter or digit. Make/model are
#: staff-typed, so every path separator and `..` has to disappear here.
_UNSAFE = re.compile(r"[^a-z0-9]+")
_SLUG_LIMIT = 64
_DIGEST_LENGTH = 12


@dataclass(frozen=True)
class CachedImage:
    data: bytes
    url: str


def _identity(make: str, model: str, year: int, variant: str = "") -> str:
    """A filesystem-safe key for one vehicle photo.

    The readable slug comes from the lowercased identity; the SHA-256 digest
    keeps two vehicles whose names slugify to the same text apart. The raw
    values are hashed, so `../../evil` cannot contribute a path component.
    The optional variant keeps multiple photos of one vehicle apart.
    """
    make_text = str(make).strip()
    model_text = str(model).strip()
    year_text = str(year).strip()
    raw_parts = [make_text.lower(), model_text.lower(), year_text]
    slug_parts = [make_text, model_text, year_text]
    if variant:
        variant_text = str(variant).strip()
        raw_parts.append(variant_text.lower())
        slug_parts.append(variant_text)
    raw = "\0".join(raw_parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]
    slug = _UNSAFE.sub("-", "-".join(slug_parts).lower()).strip("-")
    slug = slug[:_SLUG_LIMIT].strip("-") or "vehicle"
    return f"{slug}-{digest}"


def _paths(key: str) -> tuple[Path, Path]:
    """Byte and metadata paths for a key, refusing to leave `CACHE_DIR`.

    `_identity` already strips separators. This is the second line of
    defence, in case a future caller passes a hand-built key.
    """
    root = CACHE_DIR.resolve()
    bytes_path = (CACHE_DIR / f"{key}{_BYTES_SUFFIX}").resolve()
    meta_path = (CACHE_DIR / f"{key}{_META_SUFFIX}").resolve()
    for target in (bytes_path, meta_path):
        if not target.is_relative_to(root):
            raise ValueError(f"cache key escapes the cache directory: {key!r}")
    return bytes_path, meta_path


def load(
    make: str, model: str, year: int, variant: str = ""
) -> CachedImage | None:
    try:
        bytes_path, meta_path = _paths(_identity(make, model, year, variant))
    except (OSError, ValueError):
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        data = bytes_path.read_bytes()
    except (FileNotFoundError, PermissionError):
        # A concurrent writer may be replacing one of the two files. Treat
        # this as a miss but never delete the other writer's entry.
        return None
    except OSError:
        return None
    except (ValueError, TypeError):
        _discard(bytes_path, meta_path)
        return None

    if not isinstance(meta, dict):
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
    variant: str = "",
) -> None:
    if not data:
        return
    try:
        bytes_path, meta_path = _paths(_identity(make, model, year, variant))
    except (OSError, ValueError):
        return
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    metadata = json.dumps(
        {
            "make": make,
            "model": model,
            "year": year,
            "variant": variant,
            "url": url,
        },
        indent=2,
    ).encode("utf-8")
    try:
        # Metadata first: if the bytes write then fails, the previous image
        # is still readable against the new (purely descriptive) sidecar. A
        # failed write must never delete a valid entry another thread wrote.
        _atomic_write(meta_path, metadata)
        _atomic_write(bytes_path, data)
    except OSError:
        pass


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
    if not CACHE_DIR.exists():
        return 0
    removed = sum(1 for _ in CACHE_DIR.glob(f"*{_BYTES_SUFFIX}"))
    shutil.rmtree(CACHE_DIR, ignore_errors=True)
    return removed


def stats() -> tuple[int, int]:
    if not CACHE_DIR.exists():
        return 0, 0
    files = list(CACHE_DIR.glob(f"*{_BYTES_SUFFIX}"))
    total = 0
    for path in files:
        try:
            total += path.stat().st_size
        except OSError:
            # Another thread cleared/replaced it between glob and stat.
            continue
    return len(files), total


__all__ = ["CACHE_DIR", "CachedImage", "clear", "load", "stats", "store"]
