"""Delete the locally cached CarImages photos.

The hero caches photo bytes under `cache/media/`, keyed on each vehicle's
make/model/year. That cache survives CarImages re-signing its URLs, so it will
not notice if a photo is replaced upstream. Run this when you want to force a
fresh download.

    python -m scripts.clear_media_cache
"""

from __future__ import annotations

import sys

from app.services import image_cache


def main() -> int:
    entries, size = image_cache.stats()
    print(f"cache directory : {image_cache.CACHE_DIR}")
    print(f"before          : {entries} photo(s), {size / 1024:.1f} KB")

    if not entries:
        print("nothing to clear")
        return 0

    removed = image_cache.clear()
    print(f"removed         : {removed} photo(s)")

    entries, size = image_cache.stats()
    print(f"after           : {entries} photo(s), {size / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
