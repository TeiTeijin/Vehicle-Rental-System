from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import requests
from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    Property,
    QPropertyAnimation,
    QRectF,
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
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from app.models import Vehicle
from app.services import image_cache, media_service
from app.staff import icons, theme
from app.staff.cards import ElidedLabel
from app.staff.metrics import (
    HOVER_LIFT,
    HOVER_MS,
    HOVER_REVEAL_MS,
    HOVER_ZOOM,
    HOVER_ZOOM_MS,
    VEHICLE_BTN_H,
    VEHICLE_CARD_H,
    VEHICLE_CARD_MIN_W,
    VEHICLE_CARD_PAD,
    VEHICLE_CARD_RADIUS,
    VEHICLE_IMAGE_H,
    VEHICLE_IMAGE_RADIUS,
    VEHICLE_LINE_H,
    VEHICLE_META_H,
    VEHICLE_NAME_SIZE,
    VEHICLE_OVERLAY_H,
    VEHICLE_OVERLAY_OFFSET,
)
from app.widgets.skeleton import SKELETON_FADE_MS, Skeleton

IMAGE_TIMEOUT = 30

#: A hair lighter than the page sheet's border, so the cards read as raised.
CARD_BORDER = "#E6DECD"


@dataclass(frozen=True)
class RentalVehicle:
    vehicle_id: int
    make: str
    model: str
    year: int
    daily_rate: Decimal
    seats: int | None
    photo_url: str | None = None
    #: Size class and displacement, so the card can show what the rail filtered
    #: on. Both optional: a pre-migration row has neither.
    vehicle_class: str | None = None
    engine_cc: int | None = None

    @property
    def name(self) -> str:
        return f"{self.make} {self.model}".strip()

    @property
    def price(self) -> str:
        return f"\u20b1{self.daily_rate:,.0f} / day"

    @property
    def capacity(self) -> str:
        return f"{self.seats} seats" if self.seats is not None else "Seats unknown"


def to_rental_vehicle(vehicle, photo_url: str | None = None) -> RentalVehicle:
    rate = vehicle.daily_rate
    return RentalVehicle(
        vehicle_id=vehicle.vehicle_id,
        make=vehicle.make or "",
        model=vehicle.model or "",
        year=int(vehicle.year or 0),
        daily_rate=Decimal(str(rate)) if rate is not None else Decimal("0"),
        seats=vehicle.seats,
        photo_url=photo_url,
        vehicle_class=vehicle.vehicle_class,
        engine_cc=vehicle.engine_cc,
    )


def fit_cover(image: QImage, size: QSize) -> QImage:
    scaled = image.scaled(
        size, Qt.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
    )
    width = min(size.width(), scaled.width())
    height = min(size.height(), scaled.height())
    x = max(0, (scaled.width() - width) // 2)
    y = max(0, (scaled.height() - height) // 2)
    return scaled.copy(x, y, width, height)


def _decode(data: bytes) -> QImage | None:
    image = QImage()
    return image if image.loadFromData(data) else None


class _PhotoSignals(QObject):
    image = Signal(QImage)


class PhotoLoader(QRunnable):
    def __init__(self, vehicle: RentalVehicle, url: str | None) -> None:
        super().__init__()
        self.vehicle = vehicle
        self.url = url
        self.signals = _PhotoSignals()

    @Slot()
    def run(self) -> None:
        cached = None
        payload: bytes | None = None

        try:
            cached = image_cache.load(
                self.vehicle.make, self.vehicle.model, self.vehicle.year
            )
        except Exception:
            cached = None

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
            self.signals.image.emit(QImage())
            return

        image = _decode(payload)
        if image is None:
            self.signals.image.emit(QImage())
            return

        if cached is None:
            try:
                image_cache.store(
                    self.vehicle.make,
                    self.vehicle.model,
                    self.vehicle.year,
                    payload,
                    self.url or "",
                )
            except Exception:
                pass

        self.signals.image.emit(image)


class VehiclePhoto(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._scaled: QPixmap | None = None
        self._zoom = 1.0
        self._reveal = 0.0
        self.setMinimumHeight(VEHICLE_IMAGE_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.details = QPushButton("Check details", self)
        self.details.setObjectName("vehicleDetailsButton")
        self.details.setCursor(Qt.CursorShape.PointingHandCursor)
        self.details.setFixedHeight(VEHICLE_BTN_H)
        self._effect = QGraphicsOpacityEffect(self.details)
        self.details.setGraphicsEffect(self._effect)
        self.set_reveal(0.0)

    def get_zoom(self) -> float:
        return self._zoom

    def set_zoom(self, value: float) -> None:
        self._zoom = float(value)
        self.update()

    zoom = Property(float, get_zoom, set_zoom)

    def get_reveal(self) -> float:
        return self._reveal

    def set_reveal(self, value: float) -> None:
        self._reveal = max(0.0, min(1.0, float(value)))
        self._effect.setOpacity(self._reveal)
        # Keep the button clickable programmatically; only stop it catching the
        # mouse while it is still hidden behind the photo.
        self.details.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, self._reveal <= 0.05
        )
        self._place_details()
        self.update()

    reveal = Property(float, get_reveal, set_reveal)

    def set_image(self, image: QImage | None) -> None:
        self._image = None if image is None or image.isNull() else image
        self._rescale()
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._rescale()
        self._place_details()

    def _rescale(self) -> None:
        if self._image is None or self.width() <= 0 or self.height() <= 0:
            self._scaled = None
            return
        self._scaled = QPixmap.fromImage(
            fit_cover(self._image, QSize(self.width(), self.height()))
        )

    def _place_details(self) -> None:
        width = min(140, max(0, self.width() - VEHICLE_CARD_PAD * 2))
        self.details.setFixedWidth(width or 140)
        offset = int((1.0 - self._reveal) * VEHICLE_OVERLAY_OFFSET)
        y = self.height() - VEHICLE_BTN_H - 12 + offset
        x = (self.width() - self.details.width()) // 2
        self.details.move(x, y)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, VEHICLE_IMAGE_RADIUS, VEHICLE_IMAGE_RADIUS)
        painter.setClipPath(path)
        if self._scaled is not None:
            width = self.width() * self._zoom
            height = self.height() * self._zoom
            x = (self.width() - width) / 2
            y = (self.height() - height) / 2
            painter.drawPixmap(
                QRectF(x, y, width, height),
                self._scaled,
                QRectF(self._scaled.rect()),
            )
        else:
            painter.fillRect(self.rect(), QColor(theme.SURFACE))
            glyph = icons.pixmap("vehicle", theme.TAN, 44)
            painter.drawPixmap(
                (self.width() - glyph.width()) // 2,
                (self.height() - glyph.height()) // 2,
                glyph,
            )
        if self._reveal > 0.0:
            gradient = QLinearGradient(
                0, self.height() - VEHICLE_OVERLAY_H, 0, self.height()
            )
            gradient.setColorAt(0.0, QColor(0x1A, 0x14, 0x0A, 0))
            gradient.setColorAt(1.0, QColor(0x1A, 0x14, 0x0A, int(150 * self._reveal)))
            painter.fillRect(self.rect(), gradient)
        painter.end()


class VehicleCardSkeleton(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("vehicleCardSkeleton")
        self.setFixedHeight(VEHICLE_CARD_H)

        body = QVBoxLayout(self)
        body.setContentsMargins(
            VEHICLE_CARD_PAD,
            VEHICLE_CARD_PAD + HOVER_LIFT,
            VEHICLE_CARD_PAD,
            VEHICLE_CARD_PAD,
        )
        body.setSpacing(8)

        self.image = Skeleton(radius=VEHICLE_IMAGE_RADIUS)
        self.image.setFixedHeight(VEHICLE_IMAGE_H)
        self.image.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        body.addWidget(self.image)

        self.title = Skeleton()
        self.title.setFixedSize(160, VEHICLE_LINE_H - 6)
        body.addWidget(self.title)

        self.meta = Skeleton()
        self.meta.setFixedSize(96, VEHICLE_META_H - 6)
        body.addWidget(self.meta)

        self.price = Skeleton()
        self.price.setFixedSize(96, VEHICLE_LINE_H - 6)
        body.addWidget(self.price)

        self._bars = (self.image, self.title, self.meta, self.price)

    def set_busy(self, busy: bool) -> None:
        for bar in self._bars:
            bar.set_busy(busy)


class VehicleCard(QFrame):
    image_ready = Signal()
    details_requested = Signal(int)

    def __init__(self, vehicle: RentalVehicle, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.vehicle = vehicle
        self._hovered = False

        self.setObjectName("vehicleCard")
        self.setFixedHeight(VEHICLE_CARD_H)
        self.setMinimumWidth(VEHICLE_CARD_MIN_W)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        self._surface = QWidget(self)
        self._surface.setObjectName("vehicleCardSurface")

        body = QVBoxLayout(self._surface)
        body.setContentsMargins(
            VEHICLE_CARD_PAD, VEHICLE_CARD_PAD, VEHICLE_CARD_PAD, VEHICLE_CARD_PAD
        )
        body.setSpacing(8)

        self.photo = VehiclePhoto()
        self.photo.setFixedHeight(VEHICLE_IMAGE_H)
        body.addWidget(self.photo)

        self.name = ElidedLabel(
            self.vehicle.name,
            colour=theme.TEXT,
            size=VEHICLE_NAME_SIZE,
            weight=QFont.Weight.Bold,
        )
        self.name.setFixedHeight(VEHICLE_LINE_H)
        self.name.setToolTip(self.vehicle.name)
        body.addWidget(self.name)

        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(6)
        seat_icon = QLabel()
        seat_icon.setPixmap(icons.pixmap("customers", theme.MUTED, 16))
        seat_icon.setFixedSize(16, 16)
        seat_icon.setStyleSheet("background: transparent;")
        meta.addWidget(seat_icon)

        self.meta = QLabel(self.vehicle.capacity)
        meta_font = QFont("Inter")
        meta_font.setPixelSize(13)
        meta_font.setWeight(QFont.Weight.Medium)
        self.meta.setFont(meta_font)
        self.meta.setStyleSheet(f"color: {theme.MUTED}; background: transparent;")
        meta.addWidget(self.meta)
        meta.addStretch(1)
        body.addLayout(meta)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(8)
        self.price = QLabel(self.vehicle.price)
        price_font = QFont("Inter")
        price_font.setPixelSize(15)
        price_font.setWeight(QFont.Weight.DemiBold)
        self.price.setFont(price_font)
        self.price.setStyleSheet(f"color: {theme.TEXT}; background: transparent;")
        bottom.addWidget(self.price)
        bottom.addStretch(1)
        body.addLayout(bottom)

        # The button lives on the photo's hover overlay, so it moves and fades
        # with the reveal rather than the body.
        self.details = self.photo.details
        self.details.clicked.connect(self._emit_details)

        self._surface.setGeometry(0, HOVER_LIFT, self.width(), self.height() - HOVER_LIFT)

        # Persistent animations, reused on every hover: creating one per event
        # leaks them, and a `DeleteWhenStopped` policy hands `_animate_*` a
        # dangling pointer the next time it fires.
        self._lift = QPropertyAnimation(self._surface, b"pos", self)
        self._lift.setDuration(HOVER_MS)
        self._lift.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._lift.valueChanged.connect(self._on_lift)

        self._hover_anim = QParallelAnimationGroup(self)
        self._zoom = QPropertyAnimation(self.photo, b"zoom", self)
        self._zoom.setDuration(HOVER_ZOOM_MS)
        self._zoom.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._reveal = QPropertyAnimation(self.photo, b"reveal", self)
        self._reveal.setDuration(HOVER_REVEAL_MS)
        self._reveal.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.addAnimation(self._zoom)
        self._hover_anim.addAnimation(self._reveal)

    def load_photo(self, pool: QThreadPool, url: str | None = None) -> None:
        self.photo.set_image(None)
        loader = PhotoLoader(self.vehicle, url)
        loader.signals.image.connect(self._on_image)
        self._loader = loader
        pool.start(loader)

    def _on_image(self, image: QImage) -> None:
        self.photo.set_image(image)
        self.image_ready.emit()

    def _emit_details(self) -> None:
        self.details_requested.emit(self.vehicle.vehicle_id)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        top = 0 if self._hovered else HOVER_LIFT
        self._surface.setGeometry(0, top, self.width(), self.height() - HOVER_LIFT)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self._animate_lift(0)
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._animate_lift(HOVER_LIFT)
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802
        self._animate_hover(1.0)
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self._animate_hover(0.0)
        super().focusOutEvent(event)

    def _animate_hover(self, target: float) -> None:
        self._hover_anim.stop()
        self._zoom.setStartValue(self.photo.zoom)
        self._zoom.setEndValue(HOVER_ZOOM if target else 1.0)
        self._reveal.setStartValue(self.photo.reveal)
        self._reveal.setEndValue(1.0 if target else 0.0)
        self._hover_anim.start()

    def _animate_lift(self, target_y: int) -> None:
        self._lift.stop()
        self._lift.setStartValue(self._surface.pos())
        self._lift.setEndValue(QPoint(0, int(target_y)))
        self._lift.start()

    def _on_lift(self, value) -> None:
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        radius = float(VEHICLE_CARD_RADIUS)
        rect = QRectF(self._surface.geometry()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        shadow = QColor(0x1A, 0x14, 0x0A, 0x1F if self._hovered else 0x14)
        for step, factor in ((3, 0.45), (2, 0.7), (1, 1.0)):
            pen = QPen(shadow)
            pen.setWidthF(step * 2)
            shadow.setAlpha(int(shadow.alpha() * factor))
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                rect.adjusted(step, step, -step, -step), radius, radius
            )

        painter.setPen(QPen(QColor(CARD_BORDER), 1))
        painter.setBrush(QColor(theme.PAPER))
        painter.drawRoundedRect(rect, radius, radius)
        painter.end()


class VehicleSlot(QWidget):
    def __init__(self, vehicle: RentalVehicle, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("vehicleSlot")
        self.vehicle = vehicle
        self.built = False
        self.card: VehicleCard | None = None
        self._fade: QPropertyAnimation | None = None
        self._photo_url = vehicle.photo_url
        self._pool: QThreadPool | None = None

        self.setFixedHeight(VEHICLE_CARD_H)
        self.setMinimumWidth(VEHICLE_CARD_MIN_W)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.setSpacing(0)
        self.skeleton = VehicleCardSkeleton()
        self._stack.addWidget(self.skeleton)
        self._stack.setCurrentWidget(self.skeleton)
        self.skeleton.set_busy(True)

    def build(self, pool: QThreadPool) -> None:
        if self.built:
            return
        self.built = True
        self._pool = pool
        self.card = VehicleCard(self.vehicle)
        self.card.image_ready.connect(self._reveal)
        self._stack.addWidget(self.card)
        self.card.load_photo(pool, self._photo_url)

    def set_photo_url(self, url: str | None) -> None:
        if not url or url == self._photo_url:
            return
        self._photo_url = url
        if self.built and self.card is not None and self._pool is not None:
            self.card.load_photo(self._pool, url)

    def _reveal(self) -> None:
        if self.card is None:
            return
        self._stack.setCurrentWidget(self.card)
        if self._fade is not None:
            self._fade.stop()
        effect = QGraphicsOpacityEffect(self.card)
        effect.setOpacity(0.0)
        self.card.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(SKELETON_FADE_MS)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.finished.connect(lambda: self.card.setGraphicsEffect(None))
        self._fade = animation
        animation.start()


class _ResolverSignals(QObject):
    # `object`, not `dict`: Qt maps `dict` to QVariantMap, which only accepts
    # string keys, and ours are vehicle ids.
    resolved = Signal(object)


class MediaResolver(QRunnable):
    def __init__(self, context, vehicle_ids: list[int] | None = None) -> None:
        super().__init__()
        self.context = context
        self.vehicle_ids = None if vehicle_ids is None else set(vehicle_ids)
        self.signals = _ResolverSignals()

    @Slot()
    def run(self) -> None:
        resolved: dict[int, str] = {}
        try:
            with self.context.session() as session:
                query = session.query(Vehicle)
                if self.vehicle_ids is not None:
                    query = query.filter(
                        Vehicle.vehicle_id.in_(list(self.vehicle_ids))
                    )
                for vehicle in query.all():
                    try:
                        rows = media_service.get_or_fetch_media(session, vehicle)
                    except Exception:
                        continue
                    url = media_service.pick_photo_url(rows)
                    if url:
                        resolved[vehicle.vehicle_id] = url
        except Exception:
            pass
        self.signals.resolved.emit(resolved)
