"""Hero showcase carousel for the dashboard.

Loading strategy
----------------
The hero is scrollable, but the user only ever looks at *one* slide at a time,
so eagerly downloading all four photos before showing anything wasted most of
the wait. Instead:

1. One JOIN query returns every slide's text fields. The hero paints names,
   prices, specs and buttons from this alone, typically in well under a second.
2. Only the visible slide's photo is fetched first.
3. The neighbours are prefetched quietly in the background on a small thread
   pool, so arrowing to an adjacent car is already warm.
4. Photos are cached on disk by `image_cache`, keyed on the vehicle's
   identity, which survives CarImages' hourly URL re-signing. A warm launch
   therefore performs no image HTTP at all.

Network and decode work stays off the GUI thread; only finished `QImage`
objects cross back, and they become `QPixmap` on the GUI thread as Qt requires.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import requests
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QVariantAnimation,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.database import SessionLocal
from app.services import image_cache, media_service
from app.services.vehicle_service import (
    ShowcaseVehicle,
    showcase_vehicle_rows,
    to_showcase,
)

ICONS_DIR = Path(__file__).with_name("icons")

HERO_BG = "#EEEAE1"
IMAGE_SIZE = QSize(1040, 693)
ARROW_SIZE = 48
ARROW_GAP = 16
SECTION_GAP = 20
PAGE_MARGIN = 24

# The big name sits *behind* the photo and bleeds past both its edges, so it has
# to be wider than the photo. Names differ hugely in width (FORTUNER is 714px at
# 132px, VIOS only 320px), so a single pixel size cannot bleed them all: each
# name is scaled to this target width instead. NAME_PX stays as the reference
# size the ratio is measured from.
NAME_PX = 132
NAME_TARGET_WIDTH = 1460
# Runaway guard only; no real vehicle name needs more than ~600px. See
# `_name_font`.
NAME_MAX_PX = 900

# Distance from the navbar's bottom edge to the top of the eyebrow line. This is
# the hero's own top margin, not an addition to the page margin, so "100px below
# the navbar" means 100px and no arithmetic.
HERO_TOP_OFFSET = 100

# The gap between the eyebrow and the name is not a constant of its own. The name
# is pinned by its ink to the photo's top edge, and the photo sits `SECTION_GAP`
# below the eyebrow, so the visible gap is `SECTION_GAP` minus whatever descender
# slack the eyebrow's 12px line box has. A constant here was the original bug: it
# was measured from the name's line box, which is 60-140px taller than its own
# glyphs, so the real gap came out at 85px for one vehicle and 161px for another.
# See `_name_box` and `_name_top`.

# Fixed skeleton widths, in px, sized to the real text that replaces them. There
# is deliberately no "name" entry: the name has no loading block.
SKELETON_WIDTHS = {
    "eyebrow": 220,
    "price": 150,
    "specs": 300,
}

EYEBROW_PX = 12
PRICE_PX = 24
SPECS_PX = 12

VIEW_DETAILS_SIZE = QSize(145, 44)
BOOK_SIZE = QSize(200, 44)
BUTTON_GAP = 16

IMAGE_TIMEOUT = 30

# How many photos to fetch at once. Four slides means "all of them", which is
# the same as asking for one thread per slide, but this stays correct if the
# carousel is ever filtered down to fewer vehicles.
PREFETCH_THREADS = 4

# Skeleton greys, in the same warm family as the #EEEAE1 hero background. These
# are cosmetic only: they make the wait legible, they do not shorten it.
SKELETON_BASE = "#DDD8CD"
SKELETON_SHIMMER = "#F4F1E8"
SKELETON_RADIUS = 6
SKELETON_SHIMMER_MS = 1100
# How much of the block's width the highlight band covers, and how opaque it
# gets at its brightest. Both are what make the sweep readable rather than a
# barely-there flicker.
SKELETON_BAND = 0.55
SKELETON_SHIMMER_ALPHA = 120
SKELETON_FADE_MS = 150


@dataclass(frozen=True)
class HeroSlide:
    vehicle: ShowcaseVehicle
    image_url: str | None = None


def fit_cover(image: QImage, size: QSize) -> QImage:
    """Scale to fill `size` then centre-crop, so the result is exactly `size`."""
    scaled = image.scaled(
        size, Qt.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
    )
    width = min(size.width(), scaled.width())
    height = min(size.height(), scaled.height())
    x = max(0, (scaled.width() - width) // 2)
    y = max(0, (scaled.height() - height) // 2)
    return scaled.copy(x, y, width, height)


def _placeholder() -> QImage:
    image = QImage(IMAGE_SIZE, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.transparent)
    return image


def _decode(data: bytes) -> QImage | None:
    image = QImage()
    return image if image.loadFromData(data) else None


class _LoaderSignals(QObject):
    """`ready` carries the text-only slides; `image` adds photos as they land."""

    ready = Signal(list)
    image = Signal(int, QImage)
    failed = Signal(str)


class HeroLoader(QRunnable):
    """Text fields for every slide, then the visible photo first, then neighbours.

    Each photo is fetched by its own `ImageLoader` so the pool can work on
    several at once. The metadata step keeps a single short-lived session; the
    image tasks deliberately do not touch the database.
    """

    def __init__(self, first_index: int = 0, include_3d: bool = False) -> None:
        super().__init__()
        self.signals = _LoaderSignals()
        self._first_index = first_index
        self._include_3d = include_3d

    @Slot()
    def run(self) -> None:
        session = SessionLocal()
        try:
            rows = showcase_vehicle_rows(session)

            # Cold path only: a vehicle with no usable URL at all needs one
            # fetched. A vehicle that merely has a stale URL keeps it, because
            # `image_cache` will usually serve the photo without HTTP anyway.
            resolved: list[str | None] = []
            for vehicle, url in rows:
                resolved.append(url or self._fetch_url(session, vehicle))
            session.commit()

            slides = [
                HeroSlide(vehicle=to_showcase(vehicle), image_url=url)
                for (vehicle, _), url in zip(rows, resolved)
            ]
        except Exception as exc:
            session.rollback()
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        finally:
            session.close()

        self.signals.ready.emit(slides)

    def _fetch_url(self, session, vehicle) -> str | None:
        """Populate the media rows for a vehicle that has no photo URL yet."""
        try:
            rows = media_service.get_or_fetch_media(
                session, vehicle, include_3d=self._include_3d
            )
        except Exception:
            return None
        return next(
            (r.image_url for r in rows if r.view_angle == "front34" and r.image_url),
            None,
        ) or next((r.image_url for r in rows if r.image_url), None)


class ImageLoader(QRunnable):
    """One photo: disk cache first, then HTTP, then disk again.

    The `image` signal is emitted on success only. A miss falls back to a
    transparent placeholder so a broken photo never blanks the whole slide.
    """

    def __init__(self, index: int, vehicle: ShowcaseVehicle, url: str | None) -> None:
        super().__init__()
        self.index = index
        self.vehicle = vehicle
        self.url = url
        self.signals = _LoaderSignals()

    @Slot()
    def run(self) -> None:
        payload: bytes | None = None

        cached = image_cache.load(
            self.vehicle.make, self.vehicle.model, self.vehicle.year
        )
        if cached is not None:
            payload = cached.data
        elif self.url:
            try:
                response = requests.get(self.url, timeout=IMAGE_TIMEOUT)
                response.raise_for_status()
                payload = response.content
            except requests.RequestException:
                payload = None

        if not payload:
            self.signals.image.emit(self.index, _placeholder())
            return

        image = _decode(payload)
        if image is None:
            self.signals.image.emit(self.index, _placeholder())
            return

        if cached is None:
            image_cache.store(
                self.vehicle.make,
                self.vehicle.model,
                self.vehicle.year,
                payload,
                self.url or "",
            )

        self.signals.image.emit(self.index, fit_cover(image, IMAGE_SIZE))


class Skeleton(QWidget):
    """A rounded grey block with a highlight sweeping across it while loading.

    Cosmetic only. It makes a wait legible instead of leaving a black gap. The
    sweep is driven by the animation's own value rather than a timer, so a
    hidden block costs nothing and stops entirely.
    """

    def __init__(self, parent: QWidget | None = None, radius: int = SKELETON_RADIUS) -> None:
        super().__init__(parent)
        self._radius = radius
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._animation = QVariantAnimation(self)
        self._animation.setDuration(SKELETON_SHIMMER_MS)
        self._animation.setLoopCount(-1)
        # Linear, because an eased sweep visibly stalls at each end and reads as
        # a stutter rather than as travel.
        self._animation.setEasingCurve(QEasingCurve.Type.Linear)
        # These two are not optional. Left unset, a QVariantAnimation advances
        # its clock but never interpolates: currentValue() stays None and
        # valueChanged never fires, so the band would sit off the block forever
        # and nothing would even ask for a repaint.
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.valueChanged.connect(self._on_value)

    def set_busy(self, busy: bool) -> None:
        """Show this block and start the sweep, or hide it and stand down."""
        self.setVisible(busy)
        if busy:
            if not self._animation.state() == self._animation.State.Running:
                self._animation.start()
        else:
            self._animation.stop()

    def _on_value(self, value) -> None:
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(SKELETON_BASE))
        painter.drawRoundedRect(self.rect(), self._radius, self._radius)

        if self.width() <= 0 or self.height() <= 0:
            return

        # The band travels from fully off the left edge to fully off the right,
        # so the loop is seamless: at t=0 and t=1 it is off the block entirely.
        band = max(1.0, self.width() * SKELETON_BAND)
        # `currentValue()` is None until the animation has run at least once, and
        # returning None from paintEvent takes the process down with it.
        value = self._animation.currentValue()
        t = 0.0 if value is None else float(value)
        start = -band + (self.width() + 2 * band) * t

        highlight = QColor(SKELETON_SHIMMER)
        highlight.setAlpha(SKELETON_SHIMMER_ALPHA)
        gradient = QLinearGradient(start, 0.0, start + band, 0.0)
        gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
        gradient.setColorAt(0.5, highlight)
        gradient.setColorAt(1.0, QColor(0, 0, 0, 0))

        # Clipped to the same rounded path, or the band would square off the
        # corners that the fill above just rounded.
        clip = QPainterPath()
        clip.addRoundedRect(self.rect(), self._radius, self._radius)
        painter.save()
        painter.setClipPath(clip)
        painter.fillRect(self.rect(), gradient)
        painter.restore()

    def hideEvent(self, event) -> None:
        # No point animating a block nobody can see.
        self._animation.stop()
        super().hideEvent(event)


class LabelSkeleton(Skeleton):
    """A `Skeleton` that keeps itself sized to the label it stands in for.

    The label is the parent, so the block is always clipped to its own row and
    cannot drift away from it. Height follows the label's font line height; width
    is the smaller of a hand-set estimate and the label's own width, which makes
    it self-correcting: a label that hugs its text (price, specs) gets a bar
    exactly as wide as the text, while one that stretches (eyebrow) uses the
    estimate.
    """

    def __init__(self, label: QLabel, width: int, radius: int = SKELETON_RADIUS) -> None:
        super().__init__(label, radius)
        self._width = width
        label.installEventFilter(self)
        self._sync()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.parentWidget() and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.FontChange,
            QEvent.Type.LayoutRequest,
        ):
            self._sync()
        return super().eventFilter(watched, event)

    def _sync(self) -> None:
        host = self.parentWidget()
        if host is None:
            return
        box = host.contentsRect()
        line = max(1, host.fontMetrics().height())
        height = min(line, max(1, box.height()))
        width = min(self._width, max(1, box.width()))
        # Follow the label's own alignment. The eyebrow centres its text across
        # the full page width, so a block pinned to x=0 sits ~540px to the left
        # of the text it is standing in for; this is what puts the two together.
        centred = bool(host.alignment() & Qt.AlignmentFlag.AlignHCenter)
        x = (box.width() - width) // 2 if centred else 0
        y = (box.height() - height) // 2
        self.setGeometry(x, y, width, height)
        self.raise_()


class ElidedLabel(QLabel):
    """Centred label that shortens its text instead of clipping it."""

    def __init__(self) -> None:
        super().__init__()
        self._full = ""
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def setFullText(self, text: str) -> None:
        self._full = text
        self._refresh()

    def _refresh(self) -> None:
        available = self.width() - 8
        if available <= 24 or not self._full:
            super().setText(self._full)
            return
        metrics = QFontMetrics(self.font())
        super().setText(metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, available))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()


class HeroCarousel(QWidget):
    """One vehicle at a time: metadata, photo, price, actions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("hero")
        self.setAutoFillBackground(True)

        self._slides: list[HeroSlide] = []
        self._index = 0
        self._pixmaps: list[QPixmap | None] = []
        self._loaded: set[int] = set()
        self._in_flight: set[int] = set()
        self._fade: QPropertyAnimation | None = None
        self._name_size: tuple[int, int] | None = None
        self._name_ink_offset: int | None = None
        self._max_name_height = 0

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(PREFETCH_THREADS)

        self.eyebrow = ElidedLabel()
        self.eyebrow.setObjectName("heroEyebrow")
        # The name is not an `ElidedLabel`: it is positioned to the pixel and
        # sized to its own measured text, so there is nothing left to elide and
        # a centred label would only fight the manual geometry.
        self.name = QLabel()
        self.name.setObjectName("heroName")
        self.name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # A stacked layout lets the photo and its skeleton share one slot
        # exactly, so the swap cannot move anything.
        self.media_stack = QStackedLayout()
        self.media_stack.setObjectName("heroMediaStack")
        self.media_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)

        self.image = QLabel()
        self.image.setObjectName("heroImage")
        self.image.setFixedSize(IMAGE_SIZE)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.image_skeleton = Skeleton()
        self.image_skeleton.setObjectName("heroImageSkeleton")
        self.image_skeleton.setFixedSize(IMAGE_SIZE)

        self.media_stack.addWidget(self.image)
        self.media_stack.addWidget(self.image_skeleton)
        self.media_stack.setCurrentWidget(self.image)

        self.price = QLabel()
        self.price.setObjectName("heroPrice")

        self.specs = QLabel()
        self.specs.setObjectName("heroSpecs")

        # The name deliberately has no loading block. It is the one field whose
        # box cannot be known in advance: the display size is scaled from the
        # name itself, so a placeholder would have to guess at a height between
        # 327px and 728px and then resize the moment the text landed. An empty
        # gap behind the photo reads better than a grey slab that jumps, and the
        # photo's own block already covers the middle of that area.
        self.eyebrow_skeleton = LabelSkeleton(self.eyebrow, SKELETON_WIDTHS["eyebrow"])
        self.eyebrow_skeleton.setObjectName("heroEyebrowSkeleton")
        self.price_skeleton = LabelSkeleton(self.price, SKELETON_WIDTHS["price"])
        self.price_skeleton.setObjectName("heroPriceSkeleton")
        self.specs_skeleton = LabelSkeleton(self.specs, SKELETON_WIDTHS["specs"])
        self.specs_skeleton.setObjectName("heroSpecsSkeleton")

        # A `LabelSkeleton` is a child, so it is clipped to its label, and it
        # sizes itself to the label's width. An empty price label is 16px wide
        # and an empty specs label is 8px, so during the query both blocks were
        # being crushed to nothing and the two fields simply showed no loading
        # state at all. A floor on the label is what gives the block room.
        self.price.setMinimumWidth(SKELETON_WIDTHS["price"])
        self.specs.setMinimumWidth(SKELETON_WIDTHS["specs"])

        self.arrow_prev = self._arrow("arrow_left.png", "Previous vehicle")
        self.arrow_next = self._arrow("arrow_right.png", "Next vehicle")
        self.arrow_prev.clicked.connect(self.previous_slide)
        self.arrow_next.clicked.connect(self.next_slide)

        self.view_details = self._button("VIEW DETAILS", "heroViewDetails", VIEW_DETAILS_SIZE)
        self.book_vehicle = self._button("BOOK THIS VEHICLE", "heroBook", BOOK_SIZE)
        self.view_details.clicked.connect(self._on_view_details)
        self.book_vehicle.clicked.connect(self._on_book)

        # The buttons sit outside the text fields but load at the same time, so
        # they shimmer too. Each block is a child of its own button, so it needs
        # no positioning logic beyond mirroring the button's rect.
        self.view_details_skeleton = Skeleton(self.view_details)
        self.view_details_skeleton.setObjectName("heroViewDetailsSkeleton")
        self.book_vehicle_skeleton = Skeleton(self.book_vehicle)
        self.book_vehicle_skeleton.setObjectName("heroBookSkeleton")

        self.status = QLabel()
        self.status.setObjectName("heroStatus")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.hide()

        root = QVBoxLayout(self)
        root.setContentsMargins(
            PAGE_MARGIN, HERO_TOP_OFFSET, PAGE_MARGIN, PAGE_MARGIN
        )
        root.setSpacing(SECTION_GAP)
        root.addWidget(self.eyebrow)
        self._media_row_layout = self._media_row()
        root.addLayout(self._media_row_layout)
        root.addLayout(self._bottom_row())
        root.addWidget(self.status)

        # Free children have to be added after the layout exists, and they are
        # positioned by hand in `_position_floaters`.
        self.name.setParent(self)
        # Placed before the first paint rather than waiting for the name to
        # arrive, so the block is never seen at the default (0, 0, 100, 30).
        self._position_name()
        self.set_skeletons_busy(True)
        self.reload()

    # ---------- construction helpers ----------

    def _media_row(self) -> QHBoxLayout:
        """Holds only the photo.

        The arrows are deliberately *not* in here: they are pinned to the hero's
        outer edges by hand, so they must not contribute to its minimum width.
        """
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addStretch(1)
        row.addLayout(self.media_stack)
        row.addStretch(1)
        return row

    # ---------- hand-placed children ----------

    def _name_font(self, text: str) -> QFont:
        """Scale the name so it comes out `NAME_TARGET_WIDTH` px wide.

        Always measured at `NAME_PX`, never at the label's current size: this
        label's font is the thing being replaced, so measuring it would feed the
        previous slide's size into the next slide's ratio and run away.
        """
        font = QFont(self.name.font())
        font.setPixelSize(NAME_PX)
        natural = QFontMetrics(font).horizontalAdvance(text)
        if natural <= 0:
            return font
        size = round(NAME_PX * NAME_TARGET_WIDTH / natural)
        # A guard, not a design choice: a bad measurement should not be able to
        # produce a 6000px name and a 7000px hero. The longest real name needs
        # ~280px and the shortest ~600px, so this never clamps a real vehicle.
        font.setPixelSize(max(1, min(size, NAME_MAX_PX)))
        return font

    def _photo_top(self) -> int:
        """Top of the photo, with a layout fallback before the first layout pass."""
        return self.image.y() or (self.eyebrow.height() + SECTION_GAP)

    def _name_box(self, text: str) -> tuple[int, int, int]:
        """Width, height and ink offset for `text` at its display size.

        The ink offset is how far the top of the line box sits above the first
        pixel of the name's glyphs. A 270px font has a 327px line box, so ~62px
        of that box is empty air above the cap height, and a 600px font has ~140px
        of it. Measuring the box instead of the letters is what made the gap to
        the eyebrow read as 85px for one vehicle and 161px for another, when both
        were specified as 20px.
        """
        metrics = QFontMetrics(self._name_font(text))
        # tightBoundingRect is the ink-only bounds of the string, measured up from
        # the baseline, so `ascent + top` is where the first pixel of the name
        # sits inside its line box. boundingRect() looks like the right call here
        # and is not: it returns the full line box, which puts the ink offset at
        # exactly 0 and silently restores the original bug. Using the string
        # rather than capHeight also keeps this correct for lowercase ascenders.
        ink_offset = metrics.ascent() + metrics.tightBoundingRect(text).top()
        return metrics.horizontalAdvance(text) + 2, metrics.height(), ink_offset

    def _set_name(self, text: str) -> None:
        """Write the name and remember the box it needs."""
        self.name.setFont(self._name_font(text))
        self.name.setText(text)
        # Two spare pixels in the width: a label narrower than its own text would
        # start eliding, and this label has no elide path any more.
        width, height, ink_offset = self._name_box(text)
        self._name_size = (width, height)
        self._name_ink_offset = ink_offset
        self.name.setFixedSize(width, height)
        self._position_name()

    def _name_top(self) -> int:
        """Top of the name's line box, positioned so the glyphs tuck under the eyebrow.

        The name is pinned by its ink, not by its box: the ink is placed at the
        photo's top edge, which is `SECTION_GAP` below the eyebrow and the closest
        the name can sit to it while still being fully hidden behind the photo.
        Anything higher and a sliver of letter tops would show above the photo,
        reading as the name floating in front of it instead of behind it.
        """
        if self._name_ink_offset is None:
            return self._photo_top()
        return self._photo_top() - self._name_ink_offset

    def _position_name(self) -> None:
        """Centre the name block horizontally on the photo, hung off the eyebrow.

        `media_stack` is a layout and has no geometry, but the stacked widgets are
        reparented onto the hero, so the image's rect is already in hero
        coordinates.
        """
        centre_x = self.image.x() + self.image.width() // 2
        if self._name_size is None:
            # Still loading, and the name has no placeholder. It is a free child
            # of the hero with nothing behind it, so it must be hidden outright
            # rather than left sitting on an empty page.
            self.name.hide()
        else:
            width, height = self._name_size
            self.name.setGeometry(
                centre_x - width // 2, self._name_top(), width, height
            )
            # Shown explicitly, because hiding it above set a flag that
            # setText() does not clear.
            self.name.show()
        # The photo, and the block standing in for it, both win the z-order
        # fight, so the name reads as being behind the picture rather than on it.
        # raise_() moves a widget *up*, so the deepest layer goes first.
        self.name.raise_()
        self.image_skeleton.raise_()
        self.image.raise_()

    def _position_arrows(self) -> None:
        """Pin the arrows to the hero's own left and right edges.

        They follow the window because the hero does, and because they are not
        in a layout they cannot drag the hero's minimum width up with them.
        """
        centre_y = self.image.y() + self.image.height() // 2
        top = centre_y - ARROW_SIZE // 2
        self.arrow_prev.move(PAGE_MARGIN, top)
        right = self.width() - PAGE_MARGIN - ARROW_SIZE
        self.arrow_next.move(max(PAGE_MARGIN, right), top)

    def _position_floaters(self) -> None:
        self._position_name()
        self._position_arrows()

    def _reserve_name_height(self) -> None:
        """Pad below the photo so a tall name is never clipped.

        The name is pinned by its ink, so each one starts at the same line but its
        line box starts a different amount higher (the ink offset scales with the
        font). The bottom is therefore per-slide and has to be measured per-slide
        rather than as one shared top plus one shared height. Reserving the
        largest shortfall keeps the hero's height identical on every slide instead
        of jumping as you arrow.
        """
        if not self._slides:
            return
        photo_top = self._photo_top()
        name_bottom = max(
            photo_top - ink_offset + height
            for slide in self._slides
            for (_, height, ink_offset) in (self._name_box(slide.vehicle.name),)
        )
        photo_bottom = photo_top + self.image.height()
        self._media_row_layout.setContentsMargins(
            0, 0, 0, max(0, name_bottom - photo_bottom)
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_floaters()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._position_floaters()

    def _bottom_row(self) -> QHBoxLayout:
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(4)
        left.addWidget(self.price, 0, Qt.AlignmentFlag.AlignLeft)
        left.addWidget(self.specs, 0, Qt.AlignmentFlag.AlignLeft)

        right = QHBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(BUTTON_GAP)
        right.addWidget(self.view_details, 0, Qt.AlignmentFlag.AlignVCenter)
        right.addWidget(self.book_vehicle, 0, Qt.AlignmentFlag.AlignVCenter)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addLayout(left)
        row.addStretch(1)
        row.addLayout(right)
        return row

    def _arrow(self, filename: str, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("heroArrow")
        button.setToolTip(tooltip)
        button.setFixedSize(ARROW_SIZE, ARROW_SIZE)
        button.setIconSize(QSize(ARROW_SIZE, ARROW_SIZE))
        button.setIcon(QIcon(str(ICONS_DIR / filename)))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    @staticmethod
    def _button(label: str, name: str, size: QSize) -> QPushButton:
        button = QPushButton(label)
        button.setObjectName(name)
        button.setFixedSize(size)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        # Kept so `set_skeletons_busy` can blank the caption for the shimmer and
        # put the same caption back, rather than hardcoding it twice.
        button.setProperty("idle_text", label)
        return button

    # ---------- data ----------

    def reload(self) -> None:
        self.status.setText("Loading vehicles...")
        self.status.show()
        self.arrow_prev.setEnabled(False)
        self.arrow_next.setEnabled(False)
        self.set_skeletons_busy(True)

        self._slides = []
        self._pixmaps = []
        self._loaded.clear()
        self._in_flight.clear()

        loader = HeroLoader()
        loader.signals.ready.connect(self._on_loaded)
        loader.signals.failed.connect(self._on_failed)
        self._pool.start(loader)

    @Slot(list)
    def _on_loaded(self, slides: list) -> None:
        self._slides = list(slides)
        self._pixmaps = [None] * len(self._slides)
        self.status.hide()

        if not self._slides:
            self.status.setText("No vehicles available")
            self.status.show()
            self.set_skeletons_busy(False)
            self.media_stack.setCurrentWidget(self.image)
            return

        self._index = 0
        has_many = len(self._slides) > 1
        self.arrow_prev.setVisible(has_many)
        self.arrow_next.setVisible(has_many)
        self.arrow_prev.setEnabled(True)
        self.arrow_next.setEnabled(True)

        # Every name is measured up front so the photo row can be sized for the
        # tallest one. Otherwise the hero would change height every time you
        # arrow to a vehicle whose name renders taller.
        self._max_name_height = max(
            (self._name_box(s.vehicle.name)[1] for s in self._slides),
            default=0,
        )
        self._reserve_name_height()

        # Paint the real text first, then start fetching. The text fields are
        # the only writers of eyebrow/name/price/specs, so this must run before
        # the photo fetch or the hero stays blank.
        self.set_skeletons_busy(False)
        self._apply()

    @Slot(int, QImage)
    def _on_image(self, index: int, image: QImage) -> None:
        if not 0 <= index < len(self._pixmaps):
            return
        self._pixmaps[index] = QPixmap.fromImage(image)
        self._loaded.add(index)
        self._in_flight.discard(index)
        if index == self._index:
            self._show_photo()

    def _all_skeletons(self) -> tuple:
        """Every block that participates in the loading state, in paint order."""
        return (
            self.eyebrow_skeleton,
            self.price_skeleton,
            self.specs_skeleton,
            self.image_skeleton,
            self.view_details_skeleton,
            self.book_vehicle_skeleton,
        )

    def set_skeletons_busy(self, busy: bool) -> None:
        """Show or hide the grey blocks, text ones and the photo one together."""
        for skeleton in self._all_skeletons():
            skeleton.set_busy(busy)
        # A block painted over a labelled button would just hide the label behind
        # a grey slab, so the caption steps aside while the shimmer runs and
        # comes back when the real state is in.
        for button, skeleton in (
            (self.view_details, self.view_details_skeleton),
            (self.book_vehicle, self.book_vehicle_skeleton),
        ):
            skeleton.setGeometry(button.rect())
            button.setText("" if busy else str(button.property("idle_text")))

    @property
    def skeletons_visible(self) -> bool:
        return any(skeleton.isVisible() for skeleton in self._all_skeletons())

    def _show_photo(self) -> None:
        """Swap in the real photo, with a short fade so the change is soft."""
        pixmap = self._pixmaps[self._index] if self._index < len(self._pixmaps) else None
        if pixmap is None:
            self.media_stack.setCurrentWidget(self.image_skeleton)
            self.image_skeleton.set_busy(True)
            return

        self.image.setPixmap(pixmap)
        self.media_stack.setCurrentWidget(self.image)
        self.image_skeleton.set_busy(False)

        effect = QGraphicsOpacityEffect(self.image)
        self.image.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(SKELETON_FADE_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.finished.connect(lambda: self.image.setGraphicsEffect(None))
        self._fade = fade
        fade.start()

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self.status.setText(f"Could not load vehicles\n{message}")
        self.status.show()
        # Nothing is coming, so stop pretending and clear the placeholders.
        self.set_skeletons_busy(False)
        self.media_stack.setCurrentWidget(self.image)

    def _prefetch(self, index: int) -> None:
        """Load `index`, then its neighbours, skipping anything in flight.

        The visible slide is fetched on its own first. CarImages throttles
        concurrent large downloads hard: measured side by side, three parallel
        fetches finished in ~1.5s of each other but took 35s in total, versus
        ~2s for a single fetch. So neighbours only start once the photo the
        user is actually looking at has landed.
        """
        total = len(self._slides)
        if not total:
            return

        if index not in self._loaded and index not in self._in_flight:
            self._in_flight.add(index)
            self._start_image(index)
            return

        if index not in self._loaded:
            # Visible slide still downloading: hold the neighbours back.
            return

        for position in ((index + 1) % total, (index - 1) % total):
            if position in self._loaded or position in self._in_flight:
                continue
            self._in_flight.add(position)
            self._start_image(position)

    def _start_image(self, position: int) -> None:
        slide = self._slides[position]
        loader = ImageLoader(position, slide.vehicle, slide.image_url)
        loader.signals.image.connect(self._on_image)
        self._pool.start(loader)

    # ---------- navigation ----------

    @property
    def slides(self) -> list[HeroSlide]:
        return self._slides

    @property
    def max_name_height(self) -> int:
        """Tallest name across every slide, in px."""
        return self._max_name_height

    def media_row_extra(self) -> int:
        """Space reserved below the photo for a tall name, in px."""
        return self._media_row_layout.contentsMargins().bottom()

    @property
    def current_index(self) -> int:
        return self._index

    @property
    def loaded_indices(self) -> set[int]:
        return set(self._loaded)

    def next_slide(self) -> None:
        self._move(1)

    def previous_slide(self) -> None:
        self._move(-1)

    def _move(self, delta: int) -> None:
        if len(self._slides) > 1:
            self._index = (self._index + delta) % len(self._slides)
            self._apply()

    def _apply(self) -> None:
        if not self._slides:
            return
        slide = self._slides[self._index]
        vehicle = slide.vehicle
        self.eyebrow.setFullText(vehicle.eyebrow)
        self._set_name(vehicle.name)
        self.price.setText(vehicle.price)
        self.specs.setText(vehicle.specs)
        self.setAccessibleName(f"{vehicle.eyebrow} - {vehicle.name}")

        if self._index in self._loaded:
            self._show_photo()
        else:
            # Photo is on its way: grey block stands in, no black gap.
            self.media_stack.setCurrentWidget(self.image_skeleton)
            self.image_skeleton.set_busy(True)

        # The media row may have moved when its margins were reserved, so the
        # hand-placed children have to be told.
        self._position_floaters()

        # A slide changed, so its neighbours are worth warming again.
        self._prefetch(self._index)

    # ---------- placeholder ----------

    def _on_view_details(self) -> None:
        if self._slides:
            print(f"hero -> view details: {self._slides[self._index].vehicle.name}")

    def _on_book(self) -> None:
        if self._slides:
            print(f"hero -> book: {self._slides[self._index].vehicle.name}")

    def closeEvent(self, event) -> None:
        self._pool.clear()
        self._pool.waitForDone()
        super().closeEvent(event)


__all__ = [
    "ARROW_SIZE",
    "BOOK_SIZE",
    "EYEBROW_PX",
    "HERO_TOP_OFFSET",
    "HeroCarousel",
    "HeroSlide",
    "IMAGE_SIZE",
    "NAME_PX",
    "NAME_TARGET_WIDTH",
    "PREFETCH_THREADS",
    "PRICE_PX",
    "SPECS_PX",
    "VIEW_DETAILS_SIZE",
    "fit_cover",
]
