"""A small, real spinner for inline loading states.

The staff pages used to signal work with a static "Loading..." line, which
looks identical whether the app is fetching or stuck. This paints a rotating
arc instead: start() while a job is in flight, stop() when it lands or fails.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

#: 60fps is smooth and cheap at this size; the timer only runs while visible.
TICK_MS = 16

#: Degrees advanced per tick. One turn is ~0.7s.
STEP_DEGREES = 14


class Spinner(QWidget):
    """A rotating arc. Hidden until `start()` is called."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        size: int = 20,
        colour: str = "#171717",
        track_colour: str = "#E6DECD",
    ) -> None:
        super().__init__(parent)
        self._angle = 0
        self._running = False
        self._colour = QColor(colour)
        self._track_colour = QColor(track_colour)
        self.setFixedSize(QSize(size, size))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._advance)
        self.hide()

    # -- state ---------------------------------------------------------

    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True
        self.show()
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()
        self.hide()

    def set_running(self, running: bool) -> None:
        if running:
            self.start()
        else:
            self.stop()

    # -- painting ------------------------------------------------------

    def _advance(self) -> None:
        self._angle = (self._angle + STEP_DEGREES) % 360
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            side = min(self.width(), self.height())
            pen_width = max(2.0, side * 0.14)
            rect = QRectF(
                pen_width / 2 + 1,
                pen_width / 2 + 1,
                side - pen_width - 2,
                side - pen_width - 2,
            )
            track = QPen(self._track_colour)
            track.setWidthF(pen_width)
            track.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(track)
            painter.drawEllipse(rect)

            arc = QPen(self._colour)
            arc.setWidthF(pen_width)
            arc.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(arc)
            # 100 degrees of arc, starting at the top and rotating clockwise.
            start = int((90 - self._angle) * 16)
            painter.drawArc(rect, start, -100 * 16)
        finally:
            painter.end()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(20, 20)
