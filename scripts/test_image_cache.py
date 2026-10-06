from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from app.services import image_cache

failures: list[str] = []
_original_dir = image_cache.CACHE_DIR


def check(label: str, got, want) -> None:
    ok = got == want
    if not ok:
        failures.append(f"{label}: got {got!r}, want {want!r}")
    print(f"  [{'ok' if ok else 'FAIL'}] {label:<52} {got!r}")


def note(msg: str) -> None:
    print(f"  ...  {msg}")


def main() -> int:
    # Redirect the cache at a scratch directory so this never disturbs the
    # photos the real app is using.
    scratch = Path(time.strftime("cache_test_%H%M%S"))
    image_cache.CACHE_DIR = scratch

    try:
        return run(scratch)
    finally:
        image_cache.CACHE_DIR = _original_dir
        import shutil

        shutil.rmtree(scratch, ignore_errors=True)


def run(scratch: Path) -> int:
    payload = b"\x89PNG\r\n\x1a\n" + b"fake-photo-bytes" * 40
    url_a = "https://carimagesapi.com/image?make=Toyota&sig=AAA"
    url_b = "https://carimagesapi.com/image?make=Toyota&sig=BBB"

    print("\n=== cold miss ===")
    check("miss on first call", image_cache.load("Toyota", "Fortuner", 2023), None)
    check("cache dir not created by a miss", scratch.exists(), False)

    print("\n=== store then load ===")
    image_cache.store("Toyota", "Fortuner", 2023, payload, url_a)
    hit = image_cache.load("Toyota", "Fortuner", 2023)
    check("hit after store", hit is not None, True)
    check("bytes round-trip intact", hit.data if hit else None, payload)
    check("url recorded", hit.url if hit else None, url_a)

    print("\n=== keying is on identity, not the signed url ===")
    # CarImages rotates `sig=` hourly. A different signature for the same
    # vehicle must still hit, otherwise the cache would be useless.
    image_cache.store("Toyota", "Fortuner", 2023, payload, url_b)
    rotated = image_cache.load("Toyota", "Fortuner", 2023)
    check("still hits after url re-signing", rotated is not None, True)
    check("no duplicate entry written", image_cache.stats()[0], 1)

    print("\n=== a different vehicle is a different key ===")
    check("Vios does not hit Fortuner", image_cache.load("Toyota", "Vios", 2022), None)
    check("different year is a different key",
          image_cache.load("Toyota", "Fortuner", 2020), None)
    check("case and spacing normalised",
          image_cache.load("toyota", "  fortuner ", 2023) is not None, True)
    check("no extra files from misses", image_cache.stats()[0], 1)

    print("\n=== identity change invalidates ===")
    image_cache.store("Toyota", "Fortuner", 2024, b"new-year-bytes", url_a)
    check("2023 still cached", image_cache.load("Toyota", "Fortuner", 2023) is not None, True)
    check("2024 separately cached",
          image_cache.load("Toyota", "Fortuner", 2024).data, b"new-year-bytes")

    print("\n=== corruption is treated as a miss, not a crash ===")
    # Two entries exist so far: Fortuner 2023 and Fortuner 2024.
    entries, size = image_cache.stats()
    check("two entries on disk", entries, 2)
    note(f"{size} bytes total")

    # Empty only the 2023 payload; 2024 must be unaffected.
    (scratch / "toyota-fortuner-2023.img").write_bytes(b"")
    check("empty image file reports miss", image_cache.load("Toyota", "Fortuner", 2023), None)
    check("surviving entry untouched", image_cache.load("Toyota", "Fortuner", 2024) is not None, True)
    check("empty file removed from disk", image_cache.stats()[0], 1)

    meta = scratch / "toyota-fortuner-2024.json"
    check("sidecar exists where expected", meta.exists(), True)
    meta.write_text("{ not valid json")
    check("corrupt metadata reports miss", image_cache.load("Toyota", "Fortuner", 2024), None)
    check("corrupt pair removed", image_cache.stats(), (0, 0))

    print("\n=== concurrent writers never leave a torn file ===")
    import threading

    big = b"x" * 400_000
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            for _ in range(25):
                image_cache.store("Honda", "Civic", 2021, big, url_a)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("no writer raised", errors, [])
    stored = image_cache.load("Honda", "Civic", 2021)
    check("file is whole, not interleaved", len(stored.data) if stored else None, len(big))
    check("no leftover .part files", list(scratch.glob("*.part")), [])

    print("\n=== empty payloads are rejected ===")
    image_cache.store("Toyota", "Vios", 2022, b"", url_a)
    check("empty payload not stored", image_cache.load("Toyota", "Vios", 2022), None)

    print("\n=== sidecar records the identity ===")
    sidecar = json.loads((scratch / "honda-civic-2021.json").read_text(encoding="utf-8"))
    check("sidecar make", sidecar["make"], "Honda")
    check("sidecar model", sidecar["model"], "Civic")
    check("sidecar year", sidecar["year"], 2021)
    check("sidecar url", sidecar["url"], url_a)

    print("\n=== clear ===")
    before = image_cache.stats()[0]
    check("had entries before clear", before > 0, True)
    removed = image_cache.clear()
    check("clear reported removals", removed, before)
    check("cache empty after clear", image_cache.stats(), (0, 0))
    check("clear on empty cache is a no-op", image_cache.clear(), 0)

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL IMAGE CACHE TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
