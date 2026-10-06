from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import requests
from PySide6.QtCore import (
    QObject,
    QPropertyAnimation,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QPainter,
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
from app.widgets.skeleton import SKELETON_FADE_MS, LabelSkeleton, Skeleton

ICONS_DIR = Path(__file__).with_name("icons")

HERO_BG = "#EEEAE1"
IMAGE_SIZE = QSize(1040, 693)
ARROW_SIZE = 48
ARROW_GAP = 16
SECTION_GAP = 20
PAGE_MARGIN = 24

NAME_PX = 132
NAME_TARGET_WIDTH = 1460
NAME_MAX_PX = 900

HERO_TOP_OFFSET = 100

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

# Four slides means "all of them": one thread per slide.
PREFETCH_THREADS = 4


@dataclass(frozen=True)
class HeroSlide:
    vehicle: ShowcaseVehicle
    image_url: str | None = None


def fit_cover(image: QImage, size: QSize) -> QImage:
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
    ready = Signal(list)
    image = Signal(int, QImage)
    failed = Signal(str)


class HeroLoader(QRunnable):
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
        try:
            rows = media_service.get_or_fetch_media(
                session, vehicle, include_3d=self._include_3d
            )
        except Exception:
            return None
        return media_service.pick_photo_url(rows)


class ImageLoader(QRunnable):
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


class ElidedLabel(QLabel):
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
        self.name = QLabel()
        self.name.setObjectName("heroName")
        self.name.setAlignment(Qt.AlignmentFlag.AlignCenter)
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

        self.eyebrow_skeleton = LabelSkeleton(self.eyebrow, SKELETON_WIDTHS["eyebrow"])
        self.eyebrow_skeleton.setObjectName("heroEyebrowSkeleton")
        self.price_skeleton = LabelSkeleton(self.price, SKELETON_WIDTHS["price"])
        self.price_skeleton.setObjectName("heroPriceSkeleton")
        self.specs_skeleton = LabelSkeleton(self.specs, SKELETON_WIDTHS["specs"])
        self.specs_skeleton.setObjectName("heroSpecsSkeleton")

        # Empty price/specs labels are too narrow for the block, so set a floor.
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

        self.name.setParent(self)
        # Positioned before the first paint, or the block shows at the default rect.
        self._position_name()
        self.set_skeletons_busy(True)
        self.reload()

    # ---------- construction helpers ----------

    def _media_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addStretch(1)
        row.addLayout(self.media_stack)
        row.addStretch(1)
        return row

    # ---------- hand-placed children ----------

    def _name_font(self, text: str) -> QFont:
        font = QFont(self.name.font())
        font.setPixelSize(NAME_PX)
        natural = QFontMetrics(font).horizontalAdvance(text)
        if natural <= 0:
            return font
        size = round(NAME_PX * NAME_TARGET_WIDTH / natural)
        # Runaway guard only; never clamps a real vehicle name.
        font.setPixelSize(max(1, min(size, NAME_MAX_PX)))
        return font

    def _photo_top(self) -> int:
        return self.image.y() or (self.eyebrow.height() + SECTION_GAP)

    def _name_box(self, text: str) -> tuple[int, int, int]:
        metrics = QFontMetrics(self._name_font(text))
        # Ink-only bounds; boundingRect() returns the full line box and zeroes it.
        ink_offset = metrics.ascent() + metrics.tightBoundingRect(text).top()
        return metrics.horizontalAdvance(text) + 2, metrics.height(), ink_offset

    def _set_name(self, text: str) -> None:
        self.name.setFont(self._name_font(text))
        self.name.setText(text)
        # Two spare px: a narrower label would start eliding.
        width, height, ink_offset = self._name_box(text)
        self._name_size = (width, height)
        self._name_ink_offset = ink_offset
        self.name.setFixedSize(width, height)
        self._position_name()

    def _name_top(self) -> int:
        if self._name_ink_offset is None:
            return self._photo_top()
        return self._photo_top() - self._name_ink_offset

    def _position_name(self) -> None:
        centre_x = self.image.x() + self.image.width() // 2
        if self._name_size is None:
            self.name.hide()
        else:
            width, height = self._name_size
            self.name.setGeometry(
                centre_x - width // 2, self._name_top(), width, height
            )
            # Hidden above; setText() does not clear the flag.
            self.name.show()
        # raise_() moves a widget up, so the deepest layer goes first.
        self.name.raise_()
        self.image_skeleton.raise_()
        self.image.raise_()

    def _position_arrows(self) -> None:
        centre_y = self.image.y() + self.image.height() // 2
        top = centre_y - ARROW_SIZE // 2
        self.arrow_prev.move(PAGE_MARGIN, top)
        right = self.width() - PAGE_MARGIN - ARROW_SIZE
        self.arrow_next.move(max(PAGE_MARGIN, right), top)

    def _position_floaters(self) -> None:
        self._position_name()
        self._position_arrows()

    def _reserve_name_height(self) -> None:
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
        # Kept so the shimmer can blank the caption and put it back.
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

        # Measured up front so the photo row is sized for the tallest name.
        self._max_name_height = max(
            (self._name_box(s.vehicle.name)[1] for s in self._slides),
            default=0,
        )
        self._reserve_name_height()

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
        return (
            self.eyebrow_skeleton,
            self.price_skeleton,
            self.specs_skeleton,
            self.image_skeleton,
            self.view_details_skeleton,
            self.book_vehicle_skeleton,
        )

    def set_skeletons_busy(self, busy: bool) -> None:
        for skeleton in self._all_skeletons():
            skeleton.set_busy(busy)
        # The block would cover the caption, so the caption steps aside.
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
        self.set_skeletons_busy(False)
        self.media_stack.setCurrentWidget(self.image)

    def _prefetch(self, index: int) -> None:
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
        return self._max_name_height

    def media_row_extra(self) -> int:
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

        self._position_floaters()

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
