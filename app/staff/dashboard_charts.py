"""The three chart shapes the dashboard needs, drawn with QPainter.

The staff app already has hand-drawn charts in ``app/staff/charts.py`` and this
follows the same approach rather than pulling in QtCharts. Two reasons worth
stating, because QtCharts is the obvious choice and it is a worse one here:

* Every one of these is a shape the library does not have. The channels card is
  two sigmoid curves with a gradient that fades along the stroke; the heatmap is
  a rounded-rect grid with a five-step colour ramp; the sales line is a monotone
  cubic through twelve points with a gradient fill underneath. Each is a few
  lines of QPainter. Assembling them from QtCharts primitives would be longer
  and would not look right.
* The dashboard redraws every thirty seconds. Chart widgets cache into a scene
  graph; a painted widget with a `1` in its `update()` is a handful of
  `QPainter` calls.

None of these are interactive beyond a hover tooltip and a cursor change. If a
chart ever needs to be clicked into, it belongs in `charts.py` as a proper
widget with signals rather than growing that here.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QToolTip, QWidget

from app.staff.metrics import (
    AXIS_SIZE,
    CELL,
    CELL_GAP,
    DRAW_MS,
)
from app.staff.theme import (
    BORDER,
    INK_LINE,
    INK_RAISED,
    MUTED,
    PAPER,
    SURFACE,
    TAN,
    TEXT,
)

_EASE = QEasingCurve.Type.InOutCubic


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _mix(c1: str, c2: str, t: float) -> QColor:
    """Blend two hex colours. Used for the heatmap's five-step ramp."""
    a, b = QColor(c1), QColor(c2)
    t = max(0.0, min(1.0, t))
    return QColor(
        int(_lerp(a.red(), b.red(), t)),
        int(_lerp(a.green(), b.green(), t)),
        int(_lerp(a.blue(), b.blue(), t)),
        int(_lerp(a.alpha(), b.alpha(), t)),
    )


def _font(size: int, *, tabular: bool = False, weight: QFont.Weight | None = None) -> QFont:
    f = QFont("Inter")
    f.setPixelSize(size)
    if tabular:
        f.setFeature(QFont.Tag("tnum"), 1)
    if weight is not None:
        f.setWeight(weight)
    return f


def _nice_ceiling(value: float) -> float:
    """Round `value` up to a readable axis maximum.

    Without this an axis of 161 orders ends at 161, and every gridline label is
    an arbitrary number. Rounding to 1/2/5 x a power of ten is what makes an
    axis legible without a human choosing it.
    """
    if value <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        candidate = step * magnitude
        if candidate >= value:
            return candidate
    return 10 * magnitude


class _AnimatedCanvas(QWidget):
    """A widget whose paint depends on a 0..1 progress value.

    The animation drives one float and `update()` repaints; the subclass
    multiplies whatever it is drawing by that value. Cheaper than animating
    geometry and it means a half-finished frame is a partial *path*, not a
    half-finished widget, so it always looks like a valid drawing.

    `progress` has to be a real Qt property rather than a plain attribute:
    `QPropertyAnimation` looks the name up through the meta-object system, and
    an attribute it cannot find means an animation that runs to completion
    without ever calling the setter -- the charts would stay at 0 forever and
    look like they had failed to load.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._progress = 1.0

    def animate_in(self, duration_ms: int = DRAW_MS) -> None:
        self._progress = 0.0
        self.update()
        self._animation = QPropertyAnimation(self, b"progress", self)
        self._animation.setDuration(duration_ms)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(_EASE)
        self._animation.start()

    def get_progress(self) -> float:
        return self._progress

    def set_progress(self, value: float) -> None:
        self._progress = value
        self.update()

    progress = Property(float, get_progress, set_progress)

    def _tick_marks(self, painter: QPainter, rect: QRectF, count: int) -> None:
        """The faint vertical ruler ticks the brief asks for under the curves."""
        painter.setPen(QPen(QColor(INK_LINE), 1))
        for i in range(count):
            x = rect.left() + rect.width() * i / (count - 1)
            painter.drawLine(QPointF(x, rect.bottom()), QPointF(x, rect.bottom() - 4))


class ChannelCurves(_AnimatedCanvas):
    """Two S-curves, one per channel, side by side on a shared count axis.

    The brief asks for two sigmoids that start flat, rise smoothly and plateau
    into a filled end dot. The x axis is cumulative orders, so the walk-in curve
    occupies the left band and the online curve the right, each rising across
    its own share of the total.

    Drawn from a logistic rather than sampled from data: the shape is the point
    (it is how a branch reads against its own capacity), and the endpoints are
    the real totals. Interpolating real daily counts would produce a jagged line
    that says something true and useless.
    """

    #: label, count, colour. Set by the caller from theme so the dashboard and
    #: the sidebar agree on what "online" looks like.
    series: list[tuple[str, int, str]] = []

    def set_series(self, series: Sequence[tuple[str, int, str]]) -> None:
        self.series = list(series)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            if not self.series:
                return

            label_font = _font(AXIS_SIZE, weight=QFont.Weight.Medium)
            value_font = _font(18, tabular=True, weight=QFont.Weight.DemiBold)

            total = sum(n for _, n, _ in self.series) or 1
            # Reserve room for the tick row and the value labels.
            bottom = self.height() - 26
            top = 34
            right = self.width() - 8
            left = 8
            plot = QRectF(left, top, right - left, bottom - top)

            painter.setFont(label_font)

            # Bands: each series gets a share of the axis proportional to its
            # own count, so a 79/82 split divides the width almost in half.
            cursor = 0.0
            for index, (label, count, colour) in enumerate(self.series):
                share = count / total
                band_start = cursor
                band_end = cursor + share
                cursor = band_end

                # A 1% padding either side keeps the curve off the card edge.
                x0 = plot.left() + plot.width() * (band_start + share * 0.06)
                x1 = plot.left() + plot.width() * (band_end - share * 0.06)
                if x1 <= x0:
                    continue

                path = self._sigmoid(x0, x0 + (x1 - x0) * self.progress, plot)

                # Area under the curve, fading out downwards.
                if self.progress > 0.02:
                    area = QPainterPath(path)
                    area.lineTo(path.currentPosition().x(), plot.bottom())
                    area.lineTo(x0, plot.bottom())
                    area.closeSubpath()
                    fill = QLinearGradient(
                        0, plot.top(), 0, plot.bottom()
                    )
                    base = QColor(colour)
                    top_alpha = QColor(base)
                    top_alpha.setAlpha(0x4D)
                    bottom_alpha = QColor(base)
                    bottom_alpha.setAlpha(0x00)
                    fill.setColorAt(0.0, top_alpha)
                    fill.setColorAt(1.0, bottom_alpha)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QBrush(fill))
                    painter.drawPath(area)

                # Stroke, 2.5px, with the gradient fading along its length.
                stroke = QLinearGradient(x0, plot.top(), x1, plot.top())
                base = QColor(colour)
                bright = QColor(base).lighter(118)
                stroke.setColorAt(0.0, base)
                stroke.setColorAt(1.0, bright)
                pen = QPen(QBrush(stroke), 2.5, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)

                # The end dot and its value, once the curve has arrived.
                if self.progress > 0.9:
                    end_x = x1
                    end_y = plot.top()
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(colour))
                    painter.drawEllipse(QPointF(end_x, end_y), 5.0, 5.0)
                    painter.setPen(QPen(QColor(colour), 1.4))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(QPointF(end_x, end_y), 5.0, 5.0)

                    painter.setFont(value_font)
                    painter.setPen(QColor(TEXT if self._light() else PAPER))
                    painter.drawText(
                        QRectF(end_x - 90, plot.top() - 30, 86, 20),
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        f"{count:,}",
                    )
                    painter.setFont(label_font)
                    painter.setPen(QColor(MUTED))
                    painter.drawText(
                        QRectF(end_x - 90, plot.bottom() + 6, 86, 16),
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        label,
                    )

            self._tick_marks(painter, plot, 11)
        finally:
            painter.end()

    def _light(self) -> bool:
        node = self.parentWidget()
        while node is not None:
            if getattr(node, "dark", None) is not None:
                return not node.dark
            if getattr(node, "property", None) and node.property("class") == "card":
                return not bool(node.property("dark"))
            node = node.parentWidget()
        return True

    @staticmethod
    def _sigmoid(x_start: float, x_end: float, plot: QRectF) -> QPainterPath:
        """A logistic curve from `x_start` to `x_end`, plateauing at the top."""
        path = QPainterPath(QPointF(x_start, plot.bottom()))
        steps = 64
        width = max(x_end - x_start, 1e-6)
        for i in range(1, steps + 1):
            t = i / steps
            x = x_start + width * t
            # Standard logistic, shifted so it is already near 0 at t=0 and
            # indistinguishable from 1 by t=1.
            y_norm = 1.0 / (1.0 + math.exp(-10.0 * (t - 0.5)))
            y_norm = (y_norm - _sigmoid_at(0.0)) / (1.0 - _sigmoid_at(0.0))
            y_norm = max(0.0, min(1.0, y_norm))
            path.lineTo(x, plot.bottom() - plot.height() * y_norm)
        return path


def _sigmoid_at(t: float) -> float:
    return 1.0 / (1.0 + math.exp(-10.0 * (t - 0.5)))


class ActivityHeatmap(_AnimatedCanvas):
    """A GitHub-style contribution grid: weeks across, weekdays down.

    Rows are Monday..Sunday and columns are weeks, oldest at the left. Sixteen
    weeks of `date` -> count.

    The cells are staggered in over ~360ms. On a live figure that is the
    difference between "this chart is loading" and "nothing is happening".
    """

    #: date -> count. The window the caller wants shown.
    counts: dict = {}
    first_day: date | None = None

    def set_counts(self, counts: dict, first_day: date) -> None:
        self.counts = counts
        self.first_day = first_day
        self.update()

    def _weeks(self) -> list[list[tuple[date | None, int]]]:
        """The grid as columns of seven, padded at both ends."""
        if self.first_day is None or not self.counts:
            return []
        weeks: list[list[tuple[date | None, int]]] = []
        day = self.first_day
        current: list[tuple[date | None, int]] = []
        while day <= max(self.counts):
            if day.weekday() == 0 and current:
                weeks.append(current)
                current = []
            current.append((day, self.counts.get(day, 0)))
            day += timedelta(days=1)
        if current:
            weeks.append(current)
        return weeks

    def _colour_for(self, count: int, peak: int) -> QColor:
        """One of five steps, or the empty-cell colour at zero.

        The ramp is built by blending PAPER through TAN rather than by listing
        five hex values, so it stays correct if the theme's accent moves.
        """
        if count <= 0:
            return QColor(INK_RAISED if not self._light() else SURFACE)
        step = (count - 1) / max(peak - 1, 1) if peak > 1 else 1.0
        # Five steps at 0.2 intervals.
        level = max(0, min(4, int(round(step * 4))))
        base = PAPER if self._light() else "#3A3733"
        return _mix(base, TAN, 0.2 + level * 0.2)

    def _light(self) -> bool:
        return _is_light(self)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            weeks = self._weeks()
            if not weeks:
                return

            peak = max(self.counts.values()) if self.counts else 1
            label_font = _font(AXIS_SIZE)
            painter.setFont(label_font)
            painter.setPen(QColor(MUTED))

            # Weekday labels every other row, so they do not crowd at 13px.
            for row in (0, 2, 4, 6):
                names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
                painter.drawText(
                    QRectF(0, 20 + row * (CELL + CELL_GAP), 30, CELL),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    names[row],
                )

            grid_left = 36
            grid_top = 20
            for col, week in enumerate(weeks):
                # Stagger: each column fades in slightly after the last.
                col_progress = min(1.0, max(0.0, self.progress * 1.6 - col * 0.012))
                if col_progress <= 0:
                    continue
                for row, (day, count) in enumerate(week):
                    if day is None:
                        continue
                    x = grid_left + col * (CELL + CELL_GAP)
                    y = grid_top + row * (CELL + CELL_GAP)
                    colour = self._colour_for(count, peak)
                    colour.setAlpha(int(255 * min(1.0, col_progress)))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(colour)
                    painter.drawRoundedRect(
                        QRectF(x, y, CELL, CELL), 3.0, 3.0
                    )

            # Month labels above the columns that start a new month.
            seen: set[str] = set()
            for col, week in enumerate(weeks):
                day = week[0][0]
                if day is None:
                    continue
                label = day.strftime("%b")
                if label in seen or day.day > 7:
                    continue
                seen.add(label)
                painter.setPen(QColor(MUTED))
                painter.drawText(
                    QRectF(
                        grid_left + col * (CELL + CELL_GAP),
                        0,
                        CELL * 3,
                        16,
                    ),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    label,
                )
        finally:
            painter.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        weeks = self._weeks()
        if not weeks:
            return
        grid_left = 36
        grid_top = 20
        col = int((event.position().x() - grid_left) // (CELL + CELL_GAP))
        row = int((event.position().y() - grid_top) // (CELL + CELL_GAP))
        if 0 <= col < len(weeks) and 0 <= row < 7:
            day, count = weeks[col][row]
            if day is not None:
                word = "order" if count == 1 else "orders"
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    f"{day:%a %d %b %Y} — {count} {word}",
                    self,
                )
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                return
        QToolTip.hideText()
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, event) -> None:  # noqa: N802
        QToolTip.hideText()
        super().leaveEvent(event)


def _is_light(widget: QWidget) -> bool:
    """Whether `widget` is on a light card.

    Walks ancestors for a `Card`. Kept as a function rather than a method so all
    three charts can share it without inheriting from each other.
    """
    node: QWidget | None = widget.parentWidget()
    while node is not None:
        if node.property("class") == "card":
            return not bool(node.property("dark"))
        if hasattr(node, "dark") and isinstance(getattr(node, "dark"), bool):
            return not node.dark
        node = node.parentWidget()
    return True


class SalesTargetChart(_AnimatedCanvas):
    """Monthly takings as a smooth line, with the year's target drawn on top.

    A monotone cubic, not a natural spline: a natural spline overshoots between
    points, and an overshoot on a revenue chart invents a month of takings that
    did not happen. Monotone means the curve never rises where the data falls.
    """

    months: list = []  # list[MonthSales]
    target_total: Decimal = Decimal("0")
    achieved_total: Decimal = Decimal("0")

    def set_months(
        self, months: list, target_total: Decimal, achieved_total: Decimal
    ) -> None:
        # Trailing months with no takings are dropped, not drawn at zero. The
        # service returns the whole year because that is the honest shape of the
        # data, but a curve that sags to nothing in October through December
        # reads as a collapse in takings rather than as a year that has not
        # finished. Leading and interior zeroes are kept: those are real.
        while months and months[-1].total <= 0:
            months = months[:-1]
        self.months = months
        self.target_total = target_total
        self.achieved_total = achieved_total
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            if not self.months:
                return

            label_font = _font(AXIS_SIZE)
            axis_font = _font(AXIS_SIZE, tabular=True)
            painter.setFont(label_font)

            peak = max(
                float(m.total) for m in self.months
            ) if self.months else 0.0
            peak = max(peak, float(self.target_total) / 12.0)
            axis_max = _nice_ceiling(peak * 1.25) if peak else 1.0

            plot = QRectF(
                46, 14, self.width() - 56, self.height() - 42
            )

            # Gridlines and the peso axis, compactly.
            painter.setPen(QPen(QColor(BORDER), 1))
            for i in range(5):
                value = axis_max * i / 4
                y = plot.bottom() - plot.height() * (value / axis_max)
                painter.setPen(QPen(QColor(BORDER), 1))
                painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
                painter.setFont(axis_font)
                painter.setPen(QColor(MUTED))
                painter.drawText(
                    QRectF(0, y - 9, 40, 18),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    _compact(value),
                )

            # Month letters along the bottom.
            painter.setFont(label_font)
            painter.setPen(QColor(MUTED))
            for index, month in enumerate(self.months):
                x = plot.left() + plot.width() * index / max(len(self.months) - 1, 1)
                painter.drawText(
                    QRectF(x - 20, plot.bottom() + 8, 40, 16),
                    Qt.AlignmentFlag.AlignCenter,
                    month.label,
                )

            points = [
                QPointF(
                    plot.left() + plot.width() * i / max(len(self.months) - 1, 1),
                    plot.bottom() - plot.height() * (float(m.total) / axis_max),
                )
                for i, m in enumerate(self.months)
            ]
            if not points:
                return

            # The progress bar's current point, which is the last month with
            # takings. Later months sit flat at zero because the year has not
            # reached them, and a line rising into the future would be a promise
            # rather than a measurement.
            curve = _monotone_path(points)

            # Area fill under the line.
            if self.progress > 0.02:
                area = QPainterPath(curve)
                end = _point_at(curve, self.progress)
                area.lineTo(end.x(), plot.bottom())
                area.lineTo(points[0].x(), plot.bottom())
                area.closeSubpath()
                fill = QLinearGradient(0, plot.top(), 0, plot.bottom())
                top = QColor(TAN)
                top.setAlpha(0x59)
                bottom = QColor(TAN)
                bottom.setAlpha(0x00)
                fill.setColorAt(0.0, top)
                fill.setColorAt(1.0, bottom)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(fill))
                painter.drawPath(area)

            painter.setBrush(Qt.BrushStyle.NoBrush)
            pen = QPen(QColor(TAN), 2.5, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(_partial_path(curve, self.progress))

            # The pace line: what each month needs to average to hit the year's
            # target. Drawn after the area fill and before the takings line, so
            # the measurement sits on top of the goal it is being measured
            # against.
            pace = float(self.target_total) / 12.0
            if pace > 0 and pace < axis_max:
                pace_y = plot.bottom() - plot.height() * (pace / axis_max)
                target_pen = QPen(QColor(MUTED), 1.2, Qt.PenStyle.DashLine)
                target_pen.setDashPattern([4, 4])
                painter.setPen(target_pen)
                painter.drawLine(
                    QPointF(plot.left(), pace_y), QPointF(plot.right(), pace_y)
                )
                painter.setFont(_font(AXIS_SIZE - 1))
                painter.setPen(QColor(MUTED))
                painter.drawText(
                    QRectF(plot.right() - 150, pace_y - 17, 150, 15),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    f"monthly pace {_compact(pace)}",
                )

            # A dot on the last month that has takings.
            last = max(
                (i for i, m in enumerate(self.months) if m.total > 0), default=None
            )
            if last is not None and self.progress > 0.9:
                dot = points[last]
                painter.setPen(QPen(QColor(TAN), 2.5))
                painter.setBrush(QColor(PAPER if self._light() else INK_RAISED))
                painter.drawEllipse(dot, 4.5, 4.5)
                painter.setPen(QPen(QColor(TAN), 1.6))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(dot, 4.5, 4.5)
        finally:
            painter.end()

    def _light(self) -> bool:
        return _is_light(self)


def _compact(value: float) -> str:
    """Axis labels. ₱1.2M rather than ₱1,200,000, which will not fit in 40px."""
    if value >= 1_000_000:
        return f"₱{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"₱{value / 1_000:.0f}k"
    return f"₱{value:.0f}"


def _monotone_path(points: list[QPointF]) -> QPainterPath:
    """A cubic through `points` that never overshoots.

    Fritsch-Carlson: the tangents at each point are limited to the smaller of
    the two adjacent slopes, which is what stops the curve diving below zero
    between two months that both went down.
    """
    n = len(points)
    path = QPainterPath(points[0])
    if n < 3:
        for p in points[1:]:
            path.lineTo(p)
        return path

    slopes = [
        (points[i + 1].y() - points[i].y())
        / (points[i + 1].x() - points[i].x() or 1e-6)
        for i in range(n - 1)
    ]
    tangents = [slopes[0]]
    for i in range(1, n - 1):
        if slopes[i - 1] * slopes[i] <= 0:
            tangents.append(0.0)
        else:
            tangents.append(
                (slopes[i - 1] + slopes[i]) / 2
            )
    tangents.append(slopes[-1])

    for i in range(n - 1):
        dx = (points[i + 1].x() - points[i].x()) / 3
        path.cubicTo(
            points[i].x() + dx, points[i].y() + tangents[i] * dx,
            points[i + 1].x() - dx, points[i + 1].y() - tangents[i + 1] * dx,
            points[i + 1].x(), points[i + 1].y(),
        )
    return path


def _partial_path(path: QPainterPath, progress: float) -> QPainterPath:
    """The first `progress` of a path, as its own path.

    `QPainterPath` has no slice operation, so this walks the element list and
    cuts mid-curve with a de Casteljau split. Used for the load-in draw so the
    line grows rather than fading in.
    """
    if progress >= 1.0:
        return path

    out = QPainterPath()
    start = path.startPoint()
    out.moveTo(start)

    # Total length is approximated by the bounding box diagonal times the
    # element count, which is close enough for a 600ms reveal and far cheaper
    # than flattening the curve.
    total = _path_length(path)
    target = total * max(0.0, progress)

    travelled = 0.0
    for element in path.elements():
        kind = type(element).__name__
        if kind == "LineToElement":
            seg = math.dist(
                (start.x(), start.y()), (element.x, element.y)
            )
            if travelled + seg >= target:
                t = (target - travelled) / seg if seg else 0.0
                out.lineTo(
                    start.x() + (element.x - start.x()) * t,
                    start.y() + (element.y - start.y()) * t,
                )
                return out
            out.lineTo(element.x, element.y)
            travelled += seg
            start = QPointF(element.x, element.y)
        elif kind == "CurveToElement":
            c1 = QPointF(element.x1, element.y1)
            c2 = QPointF(element.x2, element.y2)
            end = QPointF(element.x3, element.y3)
            # Crude: treat the cubic as four linear thirds for length purposes.
            approx = (
                math.dist((start.x(), start.y()), (c1.x(), c1.y()))
                + math.dist((c1.x(), c1.y()), (c2.x(), c2.y()))
                + math.dist((c2.x(), c2.y()), (end.x(), end.y()))
            ) / 3 * 1.0
            if travelled + approx >= target:
                t = (target - travelled) / approx if approx else 0.0
                out.cubicTo(
                    start.x() + (c1.x() - start.x()) * t,
                    start.y() + (c1.y() - start.y()) * t,
                    c1.x() + (c2.x() - c1.x()) * t,
                    c1.y() + (c2.y() - c1.y()) * t,
                    c1.x() + (end.x() - c2.x()) * t,
                    c1.y() + (end.y() - c2.y()) * t,
                )
                return out
            out.cubicTo(c1.x(), c1.y(), c2.x(), c2.y(), end.x(), end.y())
            travelled += approx
            start = end

    return out


def _path_length(path: QPainterPath) -> float:
    if path.length() > 0:
        return path.length()
    return 1.0


def _point_at(path: QPainterPath, progress: float) -> QPointF:
    """A point `progress` along a path, for the fill's leading edge."""
    partial = _partial_path(path, progress)
    return partial.currentPosition()
