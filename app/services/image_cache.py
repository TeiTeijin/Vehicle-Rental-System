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
    data: bytes
    url: str


def _identity(make: str, model: str, year: int) -> str:
    parts = [str(make).strip().lower(), str(model).strip().lower(), str(year).strip()]
    return "-".join(parts).replace(" ", "-")


def _paths(key: str) -> tuple[Path, Path]:
    return CACHE_DIR / f"{key}{_BYTES_SUFFIX}", CACHE_DIR / f"{key}{_META_SUFFIX}"


def load(make: str, model: str, year: int) -> CachedImage | None:
    bytes_path, meta_path = _paths(_identity(make, model, year))
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        data = bytes_path.read_bytes()
    except (OSError, ValueError):
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
    if not CACHE_DIR.exists():
        return 0
    removed = sum(1 for _ in CACHE_DIR.glob(f"*{_BYTES_SUFFIX}"))
    shutil.rmtree(CACHE_DIR, ignore_errors=True)
    return removed


def stats() -> tuple[int, int]:
    if not CACHE_DIR.exists():
        return 0, 0
    files = list(CACHE_DIR.glob(f"*{_BYTES_SUFFIX}"))
    return len(files), sum(f.stat().st_size for f in files if f.exists())


__all__ = ["CACHE_DIR", "CachedImage", "clear", "load", "stats", "store"]
