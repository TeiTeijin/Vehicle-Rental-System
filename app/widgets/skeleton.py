from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QVariantAnimation, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QLabel, QWidget

SKELETON_BASE = "#DDD8CD"
SKELETON_SHIMMER = "#F4F1E8"
SKELETON_RADIUS = 6
SKELETON_SHIMMER_MS = 1100
SKELETON_BAND = 0.55
SKELETON_SHIMMER_ALPHA = 120
SKELETON_FADE_MS = 150


class Skeleton(QWidget):
    def __init__(self, parent: QWidget | None = None, radius: int = SKELETON_RADIUS) -> None:
        super().__init__(parent)
        self._radius = radius
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._animation = QVariantAnimation(self)
        self._animation.setDuration(SKELETON_SHIMMER_MS)
        self._animation.setLoopCount(-1)
        # Linear: an eased sweep stalls at each end and reads as a stutter.
        self._animation.setEasingCurve(QEasingCurve.Type.Linear)
        # Required: without start/end values the animation never interpolates.
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.valueChanged.connect(self._on_value)

    def set_busy(self, busy: bool) -> None:
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

        band = max(1.0, self.width() * SKELETON_BAND)
        # currentValue() is None until the first tick; float(None) would raise.
        value = self._animation.currentValue()
        t = 0.0 if value is None else float(value)
        start = -band + (self.width() + 2 * band) * t

        highlight = QColor(SKELETON_SHIMMER)
        highlight.setAlpha(SKELETON_SHIMMER_ALPHA)
        gradient = QLinearGradient(start, 0.0, start + band, 0.0)
        gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
        gradient.setColorAt(0.5, highlight)
        gradient.setColorAt(1.0, QColor(0, 0, 0, 0))

        clip = QPainterPath()
        clip.addRoundedRect(self.rect(), self._radius, self._radius)
        painter.save()
        painter.setClipPath(clip)
        painter.fillRect(self.rect(), gradient)
        painter.restore()

    def hideEvent(self, event) -> None:
        self._animation.stop()
        super().hideEvent(event)


class LabelSkeleton(Skeleton):
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
        # The eyebrow centres its text across the page, so the block must centre too.
        centred = bool(host.alignment() & Qt.AlignmentFlag.AlignHCenter)
        x = (box.width() - width) // 2 if centred else 0
        y = (box.height() - height) // 2
        self.setGeometry(x, y, width, height)
        self.raise_()
