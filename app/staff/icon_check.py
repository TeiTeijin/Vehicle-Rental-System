"""Numeric checks on the icon set. Not part of the app.

`python -m app.staff.icon_check` -- reports each glyph's ink coverage and
bounding box, and flags silhouettes that are indistinguishable from one another.

Exists because the icon set has to be checked without being looked at: three
glyphs that render heavier than the rest, or two that come out the same shape,
are invisible to a test that only asserts "renders without raising".
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.staff.icons import PATHS, pixmap
from app.staff.theme import INK

SIZE = 96
#: A pixel counts as ink above this alpha. Below it, antialiasing.
INK_ALPHA = 40


def measure(name: str) -> tuple[int, tuple[int, int, int, int]]:
    """(ink pixel count, bounding box) for one glyph."""
    img = pixmap(name, INK, SIZE).toImage()
    xs: list[int] = []
    ys: list[int] = []
    ink = 0
    for y in range(img.height()):
        for x in range(img.width()):
            if img.pixelColor(x, y).alpha() > INK_ALPHA:
                ink += 1
                xs.append(x)
                ys.append(y)
    if not ink:
        return 0, (-1, -1, -1, -1)
    return ink, (min(xs), min(ys), max(xs), max(ys))


def signature(name: str, cells: int = 8) -> tuple[int, ...]:
    """A coarse filled/not-filled grid, for comparing two glyphs."""
    img = pixmap(name, INK, SIZE).toImage()
    step = SIZE // cells
    return tuple(
        1
        if img.pixelColor((x + 0.5) * step, (y + 0.5) * step).alpha() > INK_ALPHA
        else 0
        for y in range(cells)
        for x in range(cells)
    )


def main() -> int:
    QApplication.instance() or QApplication(sys.argv[:1])

    header = f"{'icon':<12}{'ink%':>7}{'w':>4}{'h':>4}{'top':>5}{'left':>6}{'box':>18}"
    print(header)
    print("-" * len(header))

    signatures: dict[tuple[int, ...], list[str]] = {}
    problems: list[str] = []

    for name in sorted(PATHS):
        ink, (x0, y0, x1, y1) = measure(name)
        if ink == 0:
            problems.append(f"{name}: renders nothing")
            print(f"{name:<12}{'EMPTY':>7}")
            continue

        pct = 100 * ink / (SIZE * SIZE)
        print(
            f"{name:<12}{pct:>6.1f}%{x1 - x0 + 1:>4}{y1 - y0 + 1:>4}"
            f"{y0:>5}{x0:>6}{str((x0, y0, x1, y1)):>18}"
        )
        signatures.setdefault(signature(name), []).append(name)

        # Nothing may touch the outer 2px: a glyph that fills its box has been
        # drawn at the wrong scale and will clip against a card's padding.
        if x0 < 2 or y0 < 2 or x1 > SIZE - 3 or y1 > SIZE - 3:
            problems.append(f"{name}: {(x0, y0, x1, y1)} touches the grid edge")

    print()
    clashes = {tuple(v): k for k, v in signatures.items() if len(v) > 1}
    if clashes:
        for names in clashes:
            problems.append(f"same silhouette: {', '.join(names)}")
            print(f"  ! same silhouette: {', '.join(names)}")
    else:
        print("no duplicate silhouettes")

    print()
    if problems:
        print("PROBLEMS")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"{len(PATHS)} glyphs, all render, all inside the grid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
