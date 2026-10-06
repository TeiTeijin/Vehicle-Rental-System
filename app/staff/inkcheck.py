from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from PIL import Image

#: name -> (colour, tolerance for "close enough to be this colour")
WATCH = {
    "tan": ((184, 166, 138), 28),
    "brown": ((138, 111, 78), 28),
    "ink": ((23, 23, 23), 10),
    "ink_raised": ((35, 34, 32), 8),
    "cream": ((241, 232, 216), 6),
    "paper": ((255, 255, 255), 3),
}

NEAR_BLACK = 40


def close(a: tuple[int, int, int], b: tuple[int, int, int], tol: int) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def report(path: Path) -> int:
    image = Image.open(path).convert("RGB")
    counts: Counter[str] = Counter()
    dark = 0
    for pixel in image.convert("RGB").get_flattened_data():
        for name, (target, tol) in WATCH.items():
            if close(pixel, target, tol):
                counts[name] += 1
                break
        if max(pixel) < NEAR_BLACK:
            dark += 1

    total = image.width * image.height
    print(f"{path.name}  {image.width}x{image.height}  {total:,} px")
    for name in WATCH:
        n = counts[name]
        print(f"  {name:<11}{n:>9,}  {100 * n / total:>6.2f}%")
    print(f"  {'near-black':<11}{dark:>9,}  {100 * dark / total:>6.2f}%")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    for target in argv[1:]:
        report(Path(target))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
