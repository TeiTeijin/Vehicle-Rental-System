"""Pixel- and geometry-level checks for the hero showcase.

    python -m scripts.test_hero
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests
from PySide6.QtCore import QAbstractAnimation, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFontMetrics, QImage

os.environ.setdefault("HERO_TEST_PLATFORM", "offscreen")
os.environ["QT_QPA_PLATFORM"] = os.environ["HERO_TEST_PLATFORM"]

IMAGE_WARM_BUDGET_MS = 1500

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from app.ui.dashboard import DashboardWindow, load_fonts  # noqa: E402
from app.ui.hero import (  # noqa: E402
    ARROW_SIZE,
    BOOK_SIZE,
    EYEBROW_PX,
    HERO_TOP_OFFSET,
    HeroCarousel,
    IMAGE_SIZE,
    NAME_MAX_PX,
    NAME_PX,
    NAME_TARGET_WIDTH,
    PAGE_MARGIN,
    PRICE_PX,
    SKELETON_WIDTHS,
    SPECS_PX,
    VIEW_DETAILS_SIZE,
)

HERO_BG = (0xEE, 0xEA, 0xE1)
NAME_RGB = (0x9B, 0x97, 0x8F)
INK = (0x1A, 0x1A, 0x1A)

EXPECTED_SLIDES = 4
EXPECTED_ORDER = ["FORTUNER", "VIOS", "CIVIC", "CAMRY"]
LOAD_TIMEOUT = 60.0

failures: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    if not ok:
        failures.append(f"{label}: got {got!r}, want {want!r}")
    print(f"  [{'ok' if ok else 'FAIL'}] {label:<46} {got!r}")


def blend(fg, alpha: float, bg=HERO_BG):
    return tuple(round(alpha * fg[i] + (1 - alpha) * bg[i]) for i in range(3))


def hexes(rgb) -> str:
    return "#%02X%02X%02X" % rgb


def rect_in_window(window, widget) -> QRect:
    """widget.rect() is widget-local; the grab is in window coordinates."""
    return QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())


def closest_pixel(image, rect: QRect, target):
    """Antialiased text never hits the pure colour, so take the nearest pixel."""
    rect = rect.intersected(image.rect())
    best, best_delta = None, None
    for y in range(rect.top(), rect.bottom() + 1):
        for x in range(rect.left(), rect.right() + 1):
            c = QColor(image.pixel(x, y))
            delta = sum((c.red() - target[i]) ** 2 for i in range(3))
            if best_delta is None or delta < best_delta:
                best_delta, best = delta, (c.red(), c.green(), c.blue())
    return best, None if best_delta is None else round(best_delta ** 0.5)


def _png_bytes(width: int, height: int) -> bytes:
    """A real, decodable PNG so decode paths are genuinely exercised."""
    from PySide6.QtCore import QBuffer
    from PySide6.QtGui import QImage

    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#3366AA"))
    for x in range(0, width, 7):
        for y in range(0, height, 7):
            image.setPixelColor(x, y, QColor("#CC8844"))
    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def _run_loader(vehicle, url: str):
    """Run one ImageLoader's real body and capture the image it emits.

    `ImageLoader.run()` is invoked directly rather than via a pool thread so
    the result is available synchronously and a failure shows up as a traceback
    instead of a silent timeout. The code under test is unchanged; only the
    scheduling differs.
    """
    from app.ui.hero import ImageLoader

    got: dict = {}
    loader = ImageLoader(0, vehicle, url)
    loader.signals.image.connect(
        lambda index, image: got.__setitem__("image", image),
        Qt.ConnectionType.DirectConnection,
    )
    loader.run()
    return got.get("image")


def main() -> int:
    app = QApplication([])
    load_fonts()

    window = DashboardWindow()
    window.show()

    hero = window.hero

    # Text must be usable well before any photo arrives, and the visible photo
    # must land quickly. Both are the whole point of the lazy loader, so they
    # are asserted rather than assumed.
    started = time.time()
    deadline = started + LOAD_TIMEOUT
    while time.time() < deadline and not hero.slides:
        app.processEvents()
        time.sleep(0.01)
    text_ms = (time.time() - started) * 1000
    app.processEvents()
    print(f"  info: text fields usable after {text_ms:.0f} ms")
    # Bounded by one round trip to the remote Aiven instance, which varies with
    # network conditions (observed 1.8-2.7s). The budget is loose on purpose:
    # it only has to be far below the ~10s a regression to all-or-nothing
    # loading would cost. HTTP avoidance is asserted exactly, further down.
    check("text visible well before the old 10s", text_ms < 6000, True)
    check("text present while photo may still load", bool(hero.name.text()), True)

    if not hero.slides:
        print("no slides loaded - aborting")
        return 1

    photo_start = time.time()
    while time.time() < photo_start + LOAD_TIMEOUT and 0 not in hero.loaded_indices:
        app.processEvents()
        time.sleep(0.01)
    photo_ms = (time.time() - photo_start) * 1000
    app.processEvents()
    print(f"  info: first photo visible {photo_ms:.0f} ms after the text")
    # Disk-cached photos should arrive essentially instantly. A cold first
    # run downloads instead, so allow headroom rather than assert a tight
    # number that only holds once `image_cache` is populated.
    check("first photo under 4s", photo_ms < 4000, True)

    print("\n=== data ===")
    check("slides loaded", len(hero.slides), EXPECTED_SLIDES)
    check("eyebrow text", hero.eyebrow.text(), "TOYOTA / SUV / 2023")
    # Regression guard: the text fields are only ever written by _apply(), so
    # dropping that call leaves the hero blank while the photo still appears.
    check("name text present", hero.name.text(), "FORTUNER")
    check("price text present", hero.price.text(), "\u20b14,500 / day")
    check("specs text present", bool(hero.specs.text()), True)
    check("price text", hero.price.text(), "\u20b14,500 / day")
    check("spec text", hero.specs.text(), "7 seats \u00b7 Automatic \u00b7 Diesel")
    check("motos excluded",
          all("NMAX" not in s.vehicle.name and "Click" not in s.vehicle.name
              for s in hero.slides), True)

    print("\n=== skeletons ===")
    # Cosmetic only, but they must be correct when shown and gone when not.
    check("skeletons hidden once loaded", hero.skeletons_visible, False)
    check("photo slot showing the photo",
          hero.media_stack.currentWidget() is hero.image, True)
    check("image skeleton sized to the photo slot",
          (hero.image_skeleton.width(), hero.image_skeleton.height()),
          (IMAGE_SIZE.width(), IMAGE_SIZE.height()))
    # Each text block is a child of the label it stands in for, which is what
    # keeps it in its row. As layout siblings (the previous version) they were
    # measured at QRect(640, 276, 640, 480) - floating over the photo, invisible
    # in practice - so this asserts the real thing: parented, and sized.
    for field, skeleton, label in (
        ("eyebrow", hero.eyebrow_skeleton, hero.eyebrow),
        ("price", hero.price_skeleton, hero.price),
        ("specs", hero.specs_skeleton, hero.specs),
    ):
        check(f"{field} skeleton is a child of its label",
              skeleton.parent() is label, True)
        check(f"{field} skeleton has real width", skeleton.width() > 0, True)
        check(f"{field} skeleton fits inside its label",
              skeleton.width() <= label.width()
              and skeleton.height() <= label.height(), True)
        # Alignment is inherited from the label, not assumed to be the left edge.
        # The eyebrow centres its text across the whole 1218px page, so a block
        # pinned at x=0 sat 542px away from the text it stands in for. Comparing
        # centres rather than left edges is what lets a wider-than-text block
        # still count as aligned.
        text_width = QFontMetrics(label.font()).horizontalAdvance(label.text())
        if label.alignment() & Qt.AlignHCenter:
            delta = abs(
                (skeleton.x() + skeleton.width() / 2)
                - (label.width() - text_width) / 2 - text_width / 2
            )
        else:
            delta = abs(skeleton.x() - label.contentsRect().left())
        check(f"{field} skeleton lines up with its text", delta <= 1, True)

    # A fresh carousel must show them while the query is in flight.
    from app.ui.hero import Skeleton

    probe = Skeleton()
    probe.set_busy(True)
    check("skeleton becomes visible when busy", probe.isVisible(), True)
    running = QAbstractAnimation.State.Running
    stopped = QAbstractAnimation.State.Stopped
    check("shimmer runs while busy", probe._animation.state(), running)
    probe.set_busy(False)
    check("skeleton hides when done", probe.isVisible(), False)
    check("shimmer stops when hidden", probe._animation.state(), stopped)

    # Drive the real animation and watch the bright spot move. Sampling a
    # single frame could only ever assert where the band happened to be; over a
    # full sweep it has to have travelled.
    paint_probe = Skeleton()
    paint_probe.resize(120, 24)
    paint_probe.set_busy(True)

    def scan(widget):
        frame = QImage(widget.size(), QImage.Format.Format_ARGB32)
        frame.fill(QColor("#000000"))
        widget.render(frame)
        row = frame.height() // 2
        best_value, best_x, brightest_c = -1, -1, None
        darkest = 255
        for x in range(frame.width()):
            c = QColor(frame.pixel(x, row))
            value = c.red() + c.green() + c.blue()
            darkest = min(darkest, c.red())
            if value > best_value:
                best_value, best_x, brightest_c = value, x, c
        return best_x, brightest_c, darkest

    positions, peak, floor_seen = set(), 0, 255
    deadline = time.time() + 0.9
    while time.time() < deadline:
        x, c, darkest = scan(paint_probe)
        positions.add(x)
        peak = max(peak, c.red())
        floor_seen = min(floor_seen, darkest)
        app.processEvents()
        time.sleep(0.02)
    check("shimmer band moves across the block", len(positions) > 1, True)
    check("shimmer sweeps most of the width", len(positions) > 3, True)
    # The band is brighter than the #DDD8CD base, and never covers the whole
    # block, so the base itself is still visible at some point in the sweep.
    check("band brightens the block", peak > 0xDD, True)
    check("base grey still shows between passes", floor_seen, 0xDD)
    _, brightest_c, _ = scan(paint_probe)
    check("skeleton grey is opaque", brightest_c.alpha(), 255)
    # Rounded corner: pixel (0,0) falls outside the rounded path, so it cannot
    # carry the fill. Compared against the centre rather than a literal, because
    # render() paints the widget background there first.
    corner = QColor(paint_probe.grab().toImage().pixel(0, 0))
    centre = QColor(paint_probe.grab().toImage().pixel(60, 12))
    check("skeleton corner is rounded, not square",
          (corner.red(), corner.green(), corner.blue())
          != (centre.red(), centre.green(), centre.blue()), True)

    print("\n=== the loading state itself is correct ===")
    # Every bug in this block was invisible after load, because a loaded hero has
    # real text in every field. A second, freshly constructed carousel reproduces
    # the window between construction and the query returning, which is the only
    # time these states are ever on screen.
    fresh = HeroCarousel()
    fresh.show()
    app.processEvents()
    try:
        check("every skeleton is on before the data lands",
              fresh.skeletons_visible, True)
        check("a skeleton exists for each of the six loading fields",
              sum(1 for s in fresh._all_skeletons() if s.isVisible()), 6)

        # The name has no loading block by design, so there must be nothing at
        # all in its place. It is a free child of the hero with nothing behind it,
        # so an empty label left visible paints an empty box over the page.
        check("the name has no loading block",
              hasattr(fresh, "name_skeleton"), False)
        check("no block is defined for the name width",
              "name" in SKELETON_WIDTHS, False)
        check("the name is hidden while loading", fresh.name.isVisible(), False)
        check("the name is empty while loading", fresh.name.text(), "")
        # A stray block at the default (0, 0, 100, 30) is what put a grey lump in
        # the dead top-left corner for the whole query, so assert nothing at all
        # is left sitting there.
        stray = [
            s for s in fresh.children()
            if s.isWidgetType() and s.isVisible() and s.geometry() == QRect(0, 0, 100, 30)
        ]
        check("nothing is stranded at the default top-left geometry",
              stray, [])

        # A LabelSkeleton is a child, so it is clipped to its label and sizes
        # itself to the label's width. Empty, the price label is 16px and specs
        # is 8px, so both blocks were crushed to a sliver and those two fields
        # showed no loading state at all.
        for field, skeleton, label, want in (
            ("price", fresh.price_skeleton, fresh.price, SKELETON_WIDTHS["price"]),
            ("specs", fresh.specs_skeleton, fresh.specs, SKELETON_WIDTHS["specs"]),
        ):
            check(f"{field} block is not crushed by an empty label",
                  skeleton.width(), want)
            check(f"{field} label is wide enough to hold its block",
                  label.width() >= want, True)
            check(f"{field} block fits inside its label",
                  skeleton.width() <= label.width(), True)

        # The buttons are in the bottom right and had no loading state at all.
        for field, skeleton, button, want in (
            ("view details", fresh.view_details_skeleton, fresh.view_details,
             VIEW_DETAILS_SIZE),
            ("book", fresh.book_vehicle_skeleton, fresh.book_vehicle, BOOK_SIZE),
        ):
            check(f"{field} button has a skeleton", skeleton.isVisible(), True)
            check(f"{field} block covers the button",
                  (skeleton.width(), skeleton.height()),
                  (want.width(), want.height()))
            check(f"{field} block is a child of the button",
                  skeleton.parent() is button, True)
            # A block painted over a labelled button would just be a grey slab
            # hiding a caption, so the caption steps aside for the shimmer.
            check(f"{field} caption is blanked while loading",
                  button.text(), "")
    finally:
        fresh.deleteLater()
        app.processEvents()

    print("\n=== geometry ===")

    check("image widget size", (hero.image.width(), hero.image.height()),
          (IMAGE_SIZE.width(), IMAGE_SIZE.height()))
    first = hero._pixmaps[0]
    check("first slide image decoded to display size",
          (first.width(), first.height()) if first else None,
          (IMAGE_SIZE.width(), IMAGE_SIZE.height()))
    check("arrow size", (hero.arrow_prev.width(), hero.arrow_prev.height()),
          (ARROW_SIZE, ARROW_SIZE))
    check("view details size",
          (hero.view_details.width(), hero.view_details.height()),
          (VIEW_DETAILS_SIZE.width(), VIEW_DETAILS_SIZE.height()))
    check("book size", (hero.book_vehicle.width(), hero.book_vehicle.height()),
          (BOOK_SIZE.width(), BOOK_SIZE.height()))

    img_left = hero.image.mapTo(window, hero.image.rect().topLeft()).x()
    img_right = hero.image.mapTo(window, hero.image.rect().topRight()).x()
    check("prev arrow left of image",
          hero.arrow_prev.mapTo(window, hero.arrow_prev.rect().topRight()).x() < img_left, True)
    check("next arrow right of image",
          hero.arrow_next.mapTo(window, hero.arrow_next.rect().topLeft()).x() > img_right, True)

    img_mid = hero.image.mapTo(window, hero.image.rect().center()).y()
    for name, arrow in (("prev", hero.arrow_prev), ("next", hero.arrow_next)):
        dy = arrow.mapTo(window, arrow.rect().center()).y() - img_mid
        check(f"{name} arrow centred on image (dy)", abs(dy) <= 1, True)

    print("\n=== arrows pinned to the window edges ===")
    # The arrows used to sit just outside the photo, so they moved with the
    # photo's centring rather than the window's edges. They are hand-placed now,
    # so their x must be the hero's edge at any width.
    edge_rows = []
    for width in (1280, 1600, 1100):
        window.resize(width, 800)
        app.processEvents()
        time.sleep(0.05)
        app.processEvents()
        edge_rows.append((hero.arrow_prev.x(), hero.arrow_next.x(), hero.width()))
    for prev_x, next_x, hero_w in edge_rows:
        print(f"  info: hero {hero_w}px -> prev {prev_x}, next {next_x}")
        check("prev arrow at the left edge", prev_x, PAGE_MARGIN)
        check("next arrow at the right edge", next_x, hero_w - PAGE_MARGIN - ARROW_SIZE)
    check("arrows track the window, not the photo",
          len({row[1] for row in edge_rows}) > 1, True)
    # The navbar checks further down assume the window's original width.
    window.resize(1280, 800)
    app.processEvents()
    time.sleep(0.05)
    app.processEvents()

    print("\n=== the big name bleeds past the photo ===")
    # Scaled per slide so every name reaches the same width, then centred on the
    # photo: it is *behind* the image, and only the overhang either side is
    # visible. A name narrower than the photo would be completely hidden.
    hero._index = 0
    hero._apply()
    app.processEvents()
    for index in range(len(hero.slides)):
        hero._index = index
        hero._apply()
        app.processEvents()
        name = hero.name
        slide = hero.slides[index].vehicle.name
        metrics = QFontMetrics(name.font())
        measured = metrics.horizontalAdvance(slide)
        left = hero.image.x() - name.x()
        right = (name.x() + name.width()) - (hero.image.x() + hero.image.width())
        print(f"  info: {slide:<10} {name.font().pixelSize():>4}px  "
              f"width {measured}  overhang L{left} R{right}")
        check(f"{slide} is wider than the photo", name.width() > hero.image.width(), True)
        check(f"{slide} lands on the target width",
              abs(measured - NAME_TARGET_WIDTH) <= NAME_TARGET_WIDTH * 0.02, True)
        check(f"{slide} font is under the runaway guard",
              name.font().pixelSize() <= NAME_MAX_PX, True)
        check(f"{slide} bleeds past the left edge", left > 0, True)
        check(f"{slide} bleeds past the right edge", right > 0, True)
        check(f"{slide} bleeds symmetrically", abs(left - right) <= 2, True)
        check(f"{slide} is centred on the photo",
              name.x() + name.width() // 2,
              hero.image.x() + hero.image.width() // 2)
        # Pinned by its ink to the top of the photo, not by its line box. The box
        # is 60-140px taller than the glyphs, so a box-based check would pass
        # while the name sat visibly too far below the eyebrow.
        check(f"{slide} glyphs start at the top of the photo",
              name.y() + hero._name_box(slide)[2], hero.image.y())
        check(f"{slide} glyphs clear the eyebrow",
              name.y() + hero._name_box(slide)[2]
              > hero.eyebrow.y() + hero.eyebrow.height(), True)
        check(f"{slide} overlaps the photo vertically",
              name.y() < hero.image.y() + hero.image.height()
              and (name.y() + name.height()) > hero.image.y(), True)
        # QWidget.children() is paint order, last is on top, so the photo has to
        # come after the name for the type to read as being behind it.
        order = [id(c) for c in hero.children()]
        check(f"{slide} is painted behind the photo",
              order.index(id(hero.image)) > order.index(id(name)), True)
    hero._index = 0
    hero._apply()
    app.processEvents()

    print("\n=== hero height is stable across slides ===")
    # The name is pinned by its ink to the top of the photo, so even the tallest
    # one (VIOS, a 728px line box against a 693px photo) now ends inside the
    # photo rather than running past it. That is why no space has to be reserved
    # below the shot, and it is the reason the height no longer varies.
    heights = []
    for index in range(len(hero.slides)):
        hero._index = index
        hero._apply()
        app.processEvents()
        heights.append(hero.sizeHint().height())
    print(f"  info: hero heights {heights}")
    check("hero height identical on every slide", len(set(heights)), 1)
    bottoms = []
    for index in range(len(hero.slides)):
        hero._index = index
        hero._apply()
        app.processEvents()
        _, height, ink = hero._name_box(hero.slides[index].vehicle.name)
        bottoms.append(hero._photo_top() - ink + height)
    print(f"  info: name box bottoms {bottoms}, photo bottom "
          f"{hero.image.y() + hero.image.height()}, "
          f"reserved {hero.media_row_extra()}")
    # The reserve is computed from the same per-slide measurement, so this is the
    # real invariant: nothing spills past the bottom of the photo, whether or not
    # that leaves the reserve at zero.
    check("no name spills past the bottom of the photo",
          max(bottoms) <= hero.image.y() + hero.image.height(), True)
    check("tallest name stops above the price row",
          max(bottoms) <= hero.price.y(), True)
    check("the reserve matches the tallest overhang",
          hero.media_row_extra(),
          max(0, max(bottoms) - (hero.image.y() + hero.image.height())))
    hero._index = 0
    hero._apply()
    app.processEvents()

    print("\n=== text sits 100px below the navbar, name 20px below that ===")
    eyebrow_top = hero.eyebrow.mapTo(window, hero.eyebrow.rect().topLeft()).y()
    check("navbar height", window.navbar.height(), 72)
    print(f"  info: eyebrow top {eyebrow_top}px, navbar bottom 72px, "
          f"offset {HERO_TOP_OFFSET}px")
    # HERO_TOP_OFFSET is the hero's own top margin, not an addition to the page
    # margin, so this is a plain sum. Adding PAGE_MARGIN here is what put the
    # eyebrow 24px lower than "100px below the navbar" actually asks for.
    check("eyebrow sits exactly HERO_TOP_OFFSET below the navbar", eyebrow_top,
          72 + HERO_TOP_OFFSET)
    check("the offset is the requested 100px", HERO_TOP_OFFSET, 100)
    check("photo sits below the eyebrow",
          hero.image.mapTo(window, hero.image.rect().topLeft()).y() > eyebrow_top, True)

    print("\n=== scrolling ===")
    bar = window.content.verticalScrollBar()
    need = hero.sizeHint().height() - window.content.viewport().height()
    print(f"  info: hero {hero.sizeHint().height()}px vs viewport "
          f"{window.content.viewport().height()}px -> scrolls {need}px")
    check("hero overflows (scroll needed)", need > 0, True)
    check("vertical scrollbar visible", bar.isVisible(), True)
    check("horizontal scrollbar hidden",
          window.content.horizontalScrollBar().isVisible(), False)
    check("hero bottom reachable", bar.maximum() >= need, True)

    print("\n=== fonts ===")
    for name, label, size, weight in (
        ("eyebrow", hero.eyebrow, EYEBROW_PX, 700),
        ("price", hero.price, PRICE_PX, 700),
        ("specs", hero.specs, SPECS_PX, 400),
    ):
        font = label.font()
        check(f"{name} px/weight", (font.pixelSize(), int(font.weight())), (size, weight))
        check(f"{name} family", font.family(), "Inter 18pt")

    # The name cannot be pinned to NAME_PX any more: it is scaled per slide so
    # it reaches NAME_TARGET_WIDTH. What has to hold is the family, the weight,
    # and that the size is neither the reference size nor the runaway guard.
    name_font = hero.name.font()
    check("name weight", int(name_font.weight()), 700)
    check("name family", name_font.family(), "Inter 18pt")
    check("name is scaled off the reference size",
          name_font.pixelSize() != NAME_PX, True)
    check("name under the runaway guard", name_font.pixelSize() <= NAME_MAX_PX, True)
    print(f"  info: FORTUNER renders at {name_font.pixelSize()}px, "
          f"line box {QFontMetrics(name_font).height()}px")

    print("\n=== rendered colours (top of hero) ===")
    shot = window.grab().toImage()

    def probe(label, widget, target, tol=16):
        probe_rect(label, rect_in_window(window, widget), target, tol)

    def probe_rect(label, rect, target, tol=16):
        got, delta = closest_pixel(shot, rect, target)
        ok = delta is not None and delta <= tol
        if not ok:
            failures.append(
                f"{label}: closest {hexes(got) if got else None} delta={delta} "
                f"want {hexes(target)}")
        print(f"  [{'ok' if ok else 'FAIL'}] {label:<46} "
              f"{hexes(got) if got else '?'} (want {hexes(target)}, delta {delta})")

    check("hero background", QColor(shot.pixel(4, 76)).getRgb()[:3], HERO_BG)
    probe("eyebrow rgba(26,26,26,0.90)", hero.eyebrow, blend(INK, 0.90))

    # The name is *behind* the photo, so its own rect samples the picture, not
    # the type. The only pixels of it a user ever sees are the two overhangs
    # either side of the image, so that is what gets probed. The right-hand
    # strip is used because the left one runs off the window at this width.
    name_rect = rect_in_window(window, hero.name)
    photo_left = hero.image.mapTo(window, hero.image.rect().topLeft()).x()
    photo_right = hero.image.mapTo(window, hero.image.rect().topRight()).x()
    bleed = QRect(photo_right + 4, name_rect.top() + 8, 60, 120)
    bleed = bleed.intersected(shot.rect())
    check("name overhang strip is on screen", bleed.isValid() and not bleed.isEmpty(), True)
    print(f"  info: name rect {name_rect}, photo [{photo_left}..{photo_right}], "
          f"probing overhang at {bleed}")
    probe_rect("car name rgba(155,151,143,0.95) past the photo",
               bleed, blend(NAME_RGB, 0.95))

    print("\n=== rendered colours (bottom of hero, after scrolling) ===")
    bar.setValue(bar.maximum())
    app.processEvents()
    shot = window.grab().toImage()
    price_rect = rect_in_window(window, hero.price)
    specs_rect = rect_in_window(window, hero.specs)
    check("price row is now on screen", price_rect.intersected(shot.rect()).isValid(), True)
    probe("price #1A1A1A", hero.price, INK)
    probe("specs rgba(26,26,26,0.80)", hero.specs, blend(INK, 0.80))
    bar.setValue(0)
    app.processEvents()

    print("\n=== carousel ===")
    seen = []
    for _ in range(len(hero.slides) + 1):
        seen.append(hero.slides[hero.current_index].vehicle.name)
        hero.next_slide()
    check("next cycles and wraps", seen, EXPECTED_ORDER + [EXPECTED_ORDER[0]])

    while hero.current_index != 0:
        hero.previous_slide()
    check("previous reaches first", hero.slides[hero.current_index].vehicle.name,
          EXPECTED_ORDER[0])
    hero.previous_slide()
    check("previous wraps to last", hero.slides[hero.current_index].vehicle.name,
          EXPECTED_ORDER[-1])
    hero.next_slide()
    check("text follows index", hero.name.text(), hero.slides[hero.current_index].vehicle.name)
    check("eyebrow follows index", hero.eyebrow.text(), hero.slides[hero.current_index].vehicle.eyebrow)

    print("\n=== no skeleton flicker on a warm arrow click ===")
    # A cached slide must swap instantly. Showing a skeleton here would be a
    # flash on every click, which is the exact thing skeletons must not do.
    deadline = time.time() + LOAD_TIMEOUT
    while time.time() < deadline and 0 not in hero.loaded_indices:
        app.processEvents()
        time.sleep(0.01)

    while hero.current_index != 0 or 0 not in hero.loaded_indices:
        hero.previous_slide()
        app.processEvents()
    hero.next_slide()
    app.processEvents()
    check("arrived on slide 1", hero.current_index, 1)

    deadline = time.time() + LOAD_TIMEOUT
    while time.time() < deadline and 1 not in hero.loaded_indices:
        app.processEvents()
        time.sleep(0.01)
    check("slide 1 photo is available", 1 in hero.loaded_indices, True)

    # Now that it is definitely cached, re-apply and require an instant swap.
    hero._index = 0
    hero._apply()
    app.processEvents()
    hero._index = 1
    hero._apply()
    app.processEvents()
    check("warm slide shows the photo, not a skeleton",
          hero.media_stack.currentWidget() is hero.image, True)
    check("no skeleton on a warm swap", hero.skeletons_visible, False)

    print("\n=== navbar not regressed ===")
    check("navbar height", window.navbar.height(), 72)

    # Derived, not hardcoded: text advance differs per platform plugin
    # (102px on windows, 107px offscreen), so 32px QSS padding + 2px border.
    button = window.navbar.auth_button
    metrics = QFontMetrics(button.font())
    expected_width = metrics.horizontalAdvance(button.text()) + 32 + 2
    check("auth button width fits its label",
          button.width(), expected_width)
    check("auth button height", button.height(), 40)
    check("auth button text not squeezed", button.width() >= expected_width, True)

    tabs = [b for b in window.navbar.findChildren(QPushButton)
            if b.objectName() == "navItem"]
    check("nav tabs", [b.text() for b in tabs],
          ["Vehicles", "Bookings", "Payments", "Notification"])
    left = min(b.mapTo(window, b.rect().topLeft()).x() for b in tabs)
    right = max(b.mapTo(window, b.rect().topRight()).x() for b in tabs)
    # Sub-pixel tolerance: Qt rounds each widget edge to a device pixel, so the
    # group can land on a half-pixel centre on the real platform (639.5) while
    # being exactly 640.0 offscreen. Half a pixel of asymmetry is not visible.
    check("nav group centred on 640 (within 1px)",
          abs((left + right) / 2 - 640.0) <= 1.0, True)
    check("nav group symmetric (gap left == gap right, within 1px)",
          abs((window.width() - right) - left) <= 1, True)
    check("all checkable", all(b.isCheckable() for b in tabs), True)

    print("\n=== ImageLoader: cold downloads, warm does not ===")
    # Keyed on vehicle identity, so a re-signed URL still hits the cache. This
    # is asserted directly rather than through a window, because a window run
    # would populate the cache first and the test could not tell the two paths
    # apart. (It did, before: an earlier version of this check passed even with
    # the cache deleted, which made it meaningless.)
    import app.ui.hero as hero_module
    from app.services import image_cache as cache_module
    from app.services.vehicle_service import ShowcaseVehicle
    from decimal import Decimal

    real_get = hero_module.requests.get
    real_dir = cache_module.CACHE_DIR
    scratch = Path("cache_hero_test")
    import shutil

    shutil.rmtree(scratch, ignore_errors=True)
    cache_module.CACHE_DIR = scratch

    vehicle = ShowcaseVehicle(
        vehicle_id=9999, make="Toyota", model="Cacheprobe", year=2024,
        daily_rate=Decimal("1000"), seats=5, transmission="Automatic",
        fuel_type="Petrol", body_style="Sedan",
    )
    fake_photo = _png_bytes(400, 300)
    calls: list[str] = []

    def fake_get(url, *args, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 200
        response._content = fake_photo
        return response

    try:
        hero_module.requests.get = fake_get

        # Cold: no cached bytes, so exactly one download, and it gets stored.
        calls.clear()
        cold = _run_loader(vehicle, "https://carimagesapi.com/image?sig=ONE")
        check("cold load downloaded once", len(calls), 1)
        check("cold load produced a photo", cold is not None and not cold.isNull(), True)
        check("cold photo stored on disk", (scratch / "toyota-cacheprobe-2024.img").exists(), True)

        # Warm, same vehicle, *re-signed* URL: must not touch the network.
        calls.clear()
        warm = _run_loader(vehicle, "https://carimagesapi.com/image?sig=TWO")
        check("warm load made zero requests", len(calls), 0)
        check("warm load produced a photo", warm is not None and not warm.isNull(), True)

        # Same vehicle, re-signed URL, endpoint now unreachable: the cached
        # bytes must win, so a dead network cannot blank an existing slide.
        calls.clear()

        def dead_get(url, *args, **kwargs):
            calls.append(url)
            raise requests.ConnectionError("simulated outage")

        hero_module.requests.get = dead_get
        broken = _run_loader(vehicle, "https://carimagesapi.com/image?sig=THREE")
        check("warm load ignores a dead URL", len(calls), 0)
        check("warm load still shows a photo", broken is not None and not broken.isNull(), True)

        # With no cache and a dead endpoint, the slide must degrade to a
        # transparent placeholder rather than raising.
        calls.clear()
        cold_broken = _run_loader(
            ShowcaseVehicle(
                vehicle_id=8888, make="Toyota", model="Nevercached", year=2021,
                daily_rate=Decimal("900"), seats=5, transmission="Manual",
                fuel_type="Petrol", body_style="Sedan",
            ),
            "https://carimagesapi.com/image?sig=FOUR",
        )
        check("cold load did try the network", len(calls), 1)
        check("dead endpoint yields a placeholder, not a crash",
              cold_broken is not None and not cold_broken.isNull(), True)
        check("placeholder is exactly the image size",
              (cold_broken.width(), cold_broken.height())
              if cold_broken is not None else None,
              (IMAGE_SIZE.width(), IMAGE_SIZE.height()))
    finally:
        hero_module.requests.get = real_get
        cache_module.CACHE_DIR = real_dir
        shutil.rmtree(scratch, ignore_errors=True)

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL HERO TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
