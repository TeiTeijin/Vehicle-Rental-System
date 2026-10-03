"""Hand-drawn charts, with QPainter and no charting dependency.

The brief was a hand-drawn look, which rules out both the stock Qt styles and a
plotting library: neither produces a line that looks drawn. So the charts are
painted directly, and everything about that is deliberate:

  * **Jitter.** A real hand-drawn line does not land on exact pixel values, so
    the points are offset by a few pixels. Without it a chart looks like a
    machine drew it, which defeats the point.
  * **Two passes.** The stroke is drawn once thin and once offset by a pixel or
    two, the way a pen doubles back on itself. This is what reads as
    "hand-drawn" more than the jitter does.
  * **No animation of the shape.** The line is final immediately. What animates
    is the number next to it (see :class:`CountUpLabel`), because a number
    counting up is legible motion and a line drawing itself is not.

`paintEvent` is the only place geometry is decided, so a resize is handled by
the layout and there is no cached size to go stale.
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.staff import theme


def _jitter(seed: str, index: int, spread: float) -> float:
    """A stable small offset in [-spread, spread].

    Deterministic from the label and the index rather than from a global RNG,
    so repainting the same chart does not make the line visibly crawl. That
    would be distracting in a window that refreshes every 30 seconds.
    """
    digest = sum(ord(c) * (i + 7) for i, c in enumerate(seed))
    return (((digest >> (index % 16)) & 0xFF) / 255.0 * 2 - 1) * spread


class HandDrawnChart(QWidget):
    """A line chart with a drawn-on-by-hand feel.

    `values` is a sequence of numbers, oldest first. `labels` optionally names
    a few of the points.
    """

    def __init__(
        self,
        title: str,
        values: list[float],
        *,
        labels: list[str] | None = None,
        colour: str = theme.INK,
        fill: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._values = list(values)
        self._labels = labels or []
        self._colour = colour
        self._fill = fill
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAccessibleName(title)

    def set_values(self, values: list[float], labels: list[str] | None = None) -> None:
        """Update and repaint. Called by the 30-second refresh."""
        self._values = list(values)
        if labels is not None:
            self._labels = labels
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(self.rect()).adjusted(40, 26, -12, -24)
        self._paint_title(painter, rect)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        if not self._values:
            self._paint_placeholder(painter, rect)
            return

        low, high = self._bounds()
        span = (high - low) or 1.0
        points = [
            QPointF(
                rect.left() + (rect.width() * i / max(len(self._values) - 1, 1))
                + _jitter(self._title, i, 1.6),
                rect.bottom()
                - ((value - low) / span) * rect.height()
                + _jitter(self._title, i + 1, 2.4),
            )
            for i, value in enumerate(self._values)
        ]

        if self._fill:
            self._paint_fill(painter, rect, points)

        for offset, width, alpha in ((0.0, 2.0, 255), (1.4, 1.0, 110)):
            pen = QPen(QColor(self._colour))
            pen.setWidthF(width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            colour = QColor(pen.color())
            colour.setAlpha(alpha)
            pen.setColor(colour)
            painter.setPen(pen)
            path = QPainterPath(points[0])
            for point in points[1:]:
                path.lineTo(point + QPointF(offset, offset))
            painter.drawPath(path)

        painter.setPen(QPen(QColor(self._colour), 1.6))
        for point in points:
            painter.drawEllipse(point, 2.6, 2.6)

        self._paint_axis_labels(painter, rect, low, high)

    def _bounds(self) -> tuple[float, float]:
        low, high = min(self._values), max(self._values)
        if low == high:
            # A flat line would divide by zero.
            return low - 1, high + 1
        pad = (high - low) * 0.15
        return low - pad, high + pad

    def _paint_title(self, painter: QPainter, rect: QRectF) -> None:
        font = QFont(theme.FONT)
        font.setPointSizeF(9.0)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(theme.MUTED))
        painter.drawText(
            QRectF(rect.left(), 4, rect.width(), 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._title,
        )

    def _paint_placeholder(self, painter: QPainter, rect: QRectF) -> None:
        font = QFont(theme.FONT)
        font.setPointSizeF(9.0)
        painter.setFont(font)
        painter.setPen(QColor(theme.MUTED))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No data yet")

    def _paint_fill(self, painter: QPainter, rect: QRectF, points: list[QPointF]) -> None:
        """A soft wash under the line.

        Drawn as a vertical gradient rather than a flat tint so a dense chart
        does not turn into a solid block.
        """
        path = QPainterPath(points[0])
        for point in points[1:]:
            path.lineTo(point)
        path.lineTo(points[-1].x(), rect.bottom())
        path.lineTo(points[0].x(), rect.bottom())
        path.closeSubpath()

        colour = QColor(self._colour)
        top = QColor(colour)
        top.setAlpha(46)
        bottom = QColor(colour)
        bottom.setAlpha(0)

        from PySide6.QtGui import QLinearGradient

        gradient = QLinearGradient(QPointF(0, rect.top()), QPointF(0, rect.bottom()))
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        painter.fillPath(path, gradient)

    def _paint_axis_labels(
        self, painter: QPainter, rect: QRectF, low: float, high: float
    ) -> None:
        font = QFont(theme.FONT)
        font.setPointSizeF(7.5)
        painter.setFont(font)
        painter.setPen(QColor(theme.MUTED))

        painter.drawText(
            QRectF(0, rect.top() - 8, 36, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{high:,.0f}",
        )
        painter.drawText(
            QRectF(0, rect.bottom() - 8, 36, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{low:,.0f}",
        )
        for index, label in enumerate(self._labels):
            if index >= len(self._values):
                break
            x = rect.left() + (rect.width() * index / max(len(self._values) - 1, 1))
            painter.drawText(
                QRectF(x - 30, rect.bottom() + 4, 60, 14),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )


class HandDrawnBars(QWidget):
    """Bars for a handful of categories, drawn with the same wobble."""

    def __init__(
        self,
        title: str,
        pairs: list[tuple[str, float]],
        *,
        colour: str = theme.INK,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._pairs = list(pairs)
        self._colour = colour
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAccessibleName(title)

    def set_pairs(self, pairs: list[tuple[str, float]]) -> None:
        self._pairs = list(pairs)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(40, 26, -12, -24)

        font = QFont(theme.FONT)
        font.setPointSizeF(9.0)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(theme.MUTED))
        painter.drawText(
            QRectF(rect.left(), 4, rect.width(), 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._title,
        )

        if not self._pairs:
            plain = QFont(theme.FONT)
            plain.setPointSizeF(9.0)
            painter.setFont(plain)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No data yet")
            return

        high = max(value for _label, value in self._pairs) or 1.0
        slot = rect.width() / len(self._pairs)
        bar_width = min(slot * 0.56, 46.0)

        for index, (label, value) in enumerate(self._pairs):
            centre = rect.left() + slot * (index + 0.5)
            height = (value / high) * rect.height()
            wobble = _jitter(label + self._title, index, 1.8)
            bar = QRectF(
                centre - bar_width / 2,
                rect.bottom() - height,
                bar_width,
                max(height, 1.5),
            )

            pen = QPen(QColor(self._colour))
            pen.setWidthF(1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(QColor(0, 0, 0, 0))
            path = QPainterPath()
            path.moveTo(bar.topLeft() + QPointF(wobble, 0))
            path.lineTo(bar.topRight() + QPointF(wobble, 0))
            path.lineTo(bar.bottomRight())
            path.lineTo(bar.bottomLeft())
            path.closeSubpath()
            painter.drawPath(path)

            small = QFont(theme.FONT)
            small.setPointSizeF(7.0)
            painter.setFont(small)
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(
                QRectF(centre - slot / 2, rect.bottom() + 3, slot, 14),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )


class Sparkline(HandDrawnChart):
    """A 14-day trend with no axis labels, for a tile that is mostly number."""

    def __init__(self, values: list[float], *, colour: str = theme.MUTED, parent=None) -> None:
        super().__init__("", values, labels=[], colour=colour, fill=False, parent=parent)
        self.setFixedHeight(34)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self._values:
            return
        rect = QRectF(self.rect()).adjusted(1, 4, -1, -4)
        low, high = min(self._values), max(self._values)
        span = (high - low) or 1.0
        points = [
            QPointF(
                rect.left() + rect.width() * i / max(len(self._values) - 1, 1),
                rect.bottom() - ((v - low) / span) * rect.height(),
            )
            for i, v in enumerate(self._values)
        ]
        pen = QPen(QColor(self._colour))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        path = QPainterPath(points[0])
        for point in points[1:]:
            path.lineTo(point)
        painter.drawPath(path)


def daily_series(session, days: int, column) -> list[float]:
    """Counts per day for the last `days`, oldest first, gaps filled with 0.

    Filling the gaps is the part that matters: a series that skips a day with
    no bookings would draw that day at the previous day's x position and
    quietly misrepresent the trend.
    """
    today = date.today()
    first = today - timedelta(days=days - 1)
    buckets: dict[date, float] = {first + timedelta(days=i): 0.0 for i in range(days)}

    rows = session.query(column).filter(column >= first).all()
    for (value,) in rows:
        day = value.date() if hasattr(value, "date") else value
        if day in buckets:
            buckets[day] += 1
    return [buckets[first + timedelta(days=i)] for i in range(days)]
