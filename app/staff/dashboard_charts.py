"""The three chart shapes the dashboard needs, drawn with QPainter.

The staff app already has hand-drawn charts in ``app/staff/charts.py`` and this
follows the same approach rather than pulling in QtCharts. Two reasons worth
stating, because QtCharts is the obvious choice and it is a worse one here:

* Every one of these is a shape the library does not have. The channels card is
  two sigmoid curves over a dense tick ruler with a gradient that fades along
  the stroke; the heatmap is a rounded-rect grid on a five-step colour ramp
  whose cell size is derived from whatever width it is given; the sales line is
  a monotone cubic through twelve points with a gradient fill and a floating
  tooltip chip. Each is a few lines of QPainter. Assembling them from QtCharts
  primitives would be longer and would not look right.
* The dashboard redraws every thirty seconds. Chart widgets cache into a scene
  graph; a painted widget with a `1` in its `update()` is a handful of
  `QPainter` calls.

**Every one of these is resizable.** All three recompute their geometry from
``self.width()``/``self.height()`` on every paint and none of them assume a size,
because the shell now opens at the brief's 1440x900 and the window can be
dragged anywhere down to its minimum. Two of the three had to grow an explicit
fit calculation because the brief's own numbers do not fit its own card.

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
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from app.staff.metrics import (
    AXIS_SIZE,
    AXIS_SIZE_LARGE,
    CELL_GAP,
    CELL_MAX,
    CELL_MIN,
    CELL_RADIUS,
    DRAW_MS,
    HEATMAP_BOTTOM,
    HEATMAP_LABEL_GUTTER,
    HEATMAP_TOP,
)
from app.staff.theme import (
    ACCENT,
    BORDER,
    BROWN,
    HEATMAP_LEVELS,
    INK,
    INK_RAISED,
    MUTED,
    PAPER,
    TAN,
)

_EASE = QEasingCurve.Type.InOutCubic

RULER_TICKS = 100
RULER_MAJOR_EVERY = 5
RULER_LABEL_EVERY = 10


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _mix(c1: str, c2: str, t: float) -> QColor:
    """Blend two hex colours."""
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
    an arbitrary number. Rounding to 1/2/2.5/5 x a power of ten is what makes an
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

    def _drawable(self) -> bool:
        """Whether there is enough room to draw anything at all.

        A widget inside a scroll area, or one that has been laid out at 0x0
        before the first real geometry arrives, must not paint: every offset and
        division below assumes a positive extent, and a paint at zero height
        would divide by zero rather than simply draw nothing.
        """
        return self.width() > 8 and self.height() > 8

    def _light(self) -> bool:
        """Whether this chart sits on a light card.

        On the base class rather than per-chart, because all three need it and
        only the heatmap was missing its own copy -- which only showed up as an
        `AttributeError` from inside a `paintEvent`, where Qt swallows the
        traceback's context and the test failure names the chart rather than the
        missing method.
        """
        return _is_light(self)


class ChannelCurves(_AnimatedCanvas):
    """Two S-curves, one per channel, overlaid on a shared count axis.

    The brief asks for two sigmoids that start flat, rise smoothly and plateau
    into a filled end dot, with the value and the caption sitting near that dot
    rather than in a legend. Both curves are drawn on the *same* axes so they
    overlap and can be compared directly; each plateaus at its own total on the
    shared scale, so the taller channel visibly tops the shorter one. They are
    drawn as a small chip *on top of* the chart, not in a column reserved beside
    it: a reserved column shrank both bands, and at the window's minimum height
    the row of figures above the chart was taller than the plot itself. Drawn
    from a logistic rather than sampled from data: the shape is the point (it is
    how a branch reads against its own capacity), and the endpoints are the real
    totals. Interpolating real daily counts would produce a jagged line that
    says something true and useless.

    **The axis is scaled to the data, not to the brief's literal numbers.** The
    brief draws the walk-in curve ending near 4,600 and the online one near 9,400
    on an axis that runs to 20,000, which leaves more than half the plot empty
    and both curves squeezed into the left of it. Those numbers came from a
    reference image with its own arbitrary totals. The span here is derived from
    the taller count and rounded up to something readable, so the curves fill the
    height they are given; the ruler and its labels follow it, so the axis is
    still honest.
    """

    #: (label, count, colour)
    series: list[tuple[str, int, str]] = []

    #: (walk-in, online) end-dot radii
    DOT_RADIUS = (5.0, 7.0)

    def set_series(self, series: Sequence[tuple[str, int, str]]) -> None:
        self.series = list(series)
        self.update()

    # -- geometry -----------------------------------------------------------

    def _ruler_depth(self) -> float:
        """Height of the tick band plus its label row.

        The ruler sits *below* the plot baseline -- the ticks point down and
        the numbers hang under them. Drawn upwards from the baseline, the ticks
        and the numbers shared the band with the curve's own foot, so on a
        short card the two overprinted each other and the chart looked like it
        had come apart.
        """
        return 24.0 + AXIS_SIZE + 4.0

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._drawable() or not self.series:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            label_font = _font(AXIS_SIZE, weight=QFont.Weight.Medium)

            peak = max((n for _, n, _ in self.series), default=0)
            axis_max = _nice_ceiling(peak * 1.1) if peak else 1.0

            bottom = self.height() - self._ruler_depth()
            top = 10.0
            right = self.width() - 8.0
            left = 8.0
            if right <= left or bottom <= top:
                return
            plot = QRectF(left, top, right - left, bottom - top)

            self._ruler(painter, plot, axis_max, label_font)
            painter.setFont(label_font)

            x0 = plot.left() + 2.0
            x1 = plot.right() - 2.0
            if x1 <= x0:
                return
            curves = []
            for label, count, colour in self.series:
                level = min(1.0, count / axis_max) if axis_max else 0.0
                path = self._sigmoid(x0, x0 + (x1 - x0) * self.progress, plot, level)
                curves.append((label, count, colour, path, level))

            if self.progress > 0.02:
                for _label, _count, colour, path, _level in curves:
                    area = QPainterPath(path)
                    area.lineTo(path.currentPosition().x(), plot.bottom())
                    area.lineTo(x0, plot.bottom())
                    area.closeSubpath()
                    fill = QLinearGradient(0, plot.top(), 0, plot.bottom())
                    top_alpha = QColor(colour)
                    top_alpha.setAlpha(0x40)
                    bottom_alpha = QColor(colour)
                    bottom_alpha.setAlpha(0x00)
                    fill.setColorAt(0.0, top_alpha)
                    fill.setColorAt(1.0, bottom_alpha)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QBrush(fill))
                    painter.drawPath(area)

            for _label, _count, colour, path, _level in curves:
                stroke = QLinearGradient(x0, plot.top(), x1, plot.top())
                start = QColor(colour)
                start.setAlpha(0x10)
                end = QColor(colour)
                pen = QPen(QBrush(stroke), 2.5, Qt.PenStyle.SolidLine,
                           Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                stroke.setColorAt(0.0, start)
                stroke.setColorAt(1.0, end)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)

            if self.progress <= 0.9:
                return

            placed: list[tuple[float, float]] = []
            for index, (label, count, colour, _path, level) in enumerate(curves):
                radius = self.DOT_RADIUS[min(index, len(self.DOT_RADIUS) - 1)]
                dot_y = plot.bottom() - plot.height() * level
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(colour))
                painter.drawEllipse(QPointF(x1, dot_y), radius, radius)
                self._overlay(
                    painter, plot, dot_y, radius, label, count, colour, placed
                )
        finally:
            painter.end()

    def _ruler(
        self, painter: QPainter, plot: QRectF, axis_max: float, font: QFont
    ) -> None:
        """The dense tick ruler under the plot.

        ~100 hairlines pointing down from the baseline, every fifth one taller,
        with a number under every tenth. Ticks are ACCENT rather than MUTED
        because at 1px a MUTED line reads as a smudge and the whole row greys
        out. The numbers hang *below* the ticks so neither the ruler nor its
        labels ever share space with the curves.
        """
        minor = QPen(QColor(ACCENT), 1)
        major = QPen(QColor(MUTED), 1)
        for i in range(RULER_TICKS):
            x = plot.left() + plot.width() * i / (RULER_TICKS - 1)
            tall = (i % RULER_MAJOR_EVERY) == 0
            height = 22.0 if tall else 13.0
            painter.setPen(major if tall else minor)
            painter.drawLine(
                QPointF(x, plot.bottom()), QPointF(x, plot.bottom() + height)
            )

        painter.setFont(font)
        painter.setPen(QColor(MUTED))
        label_w = 64.0
        for i in range(0, RULER_TICKS, RULER_LABEL_EVERY):
            x = plot.left() + plot.width() * i / (RULER_TICKS - 1)
            value = axis_max * i / (RULER_TICKS - 1)
            left = min(max(0.0, x - label_w / 2.0), self.width() - label_w)
            painter.drawText(
                QRectF(left, plot.bottom() + 24.0, label_w, AXIS_SIZE + 4),
                Qt.AlignmentFlag.AlignCenter,
                f"{value:,.0f}",
            )

    def _overlay(
        self,
        painter: QPainter,
        plot: QRectF,
        dot_y: float,
        dot_radius: float,
        label: str,
        count: int,
        colour: str,
        placed: list[tuple[float, float]],
    ) -> None:
        """The series' name and total, as a small chip drawn over the chart.

        The chip sits on top of the curve rather than in a column reserved
        beside it: reserving the width shrank both bands, and at the window's
        minimum height a separate row of 28px figures pushed the plot down to a
        handful of pixels. Both channels now share one x-axis, so their end dots
        coincide; `placed` carries the chips already drawn and this one is pushed
        below them when they would collide. Drawn last, so it is in front of the
        graph.
        """
        label_font = _font(AXIS_SIZE)
        value_font = _font(15, tabular=True, weight=QFont.Weight.DemiBold)
        value = f"{count:,}"
        label_w = float(QFontMetrics(label_font).horizontalAdvance(label))
        value_w = float(QFontMetrics(value_font).horizontalAdvance(value))
        pad = 8.0
        chip_w = pad + label_w + 6.0 + value_w + pad
        chip_h = 22.0

        chip_x = plot.right() - chip_w - 2.0
        chip_y = dot_y + dot_radius + 6.0
        for prev_top, prev_bottom in placed:
            if chip_y < prev_bottom and chip_y + chip_h > prev_top:
                chip_y = prev_bottom + 4.0
        chip_y = min(chip_y, plot.bottom() - chip_h - 2.0)
        chip_y = max(chip_y, plot.top() + 2.0)
        placed.append((chip_y, chip_y + chip_h))
        chip = QRectF(chip_x, chip_y, chip_w, chip_h)

        background = QColor(PAPER) if self._light() else QColor(INK_RAISED)
        background.setAlpha(0xEC)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(chip, 8.0, 8.0)

        painter.setFont(label_font)
        painter.setPen(QColor(MUTED))
        painter.drawText(
            QRectF(chip_x + pad, chip_y, label_w, chip_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            label,
        )
        painter.setFont(value_font)
        painter.setPen(QColor(colour))
        painter.drawText(
            QRectF(chip_x + pad + label_w + 6.0, chip_y, value_w, chip_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            value,
        )

    @staticmethod
    def _sigmoid(
        x_start: float, x_end: float, plot: QRectF, level: float
    ) -> QPainterPath:
        """A logistic curve from `x_start` to `x_end`, plateauing at `level`
        of the plot's height."""
        path = QPainterPath(QPointF(x_start, plot.bottom()))
        steps = 64
        width = max(x_end - x_start, 1e-6)
        for i in range(1, steps + 1):
            t = i / steps
            x = x_start + width * t
            y_norm = 1.0 / (1.0 + math.exp(-10.0 * (t - 0.5)))
            y_norm = (y_norm - _sigmoid_at(0.0)) / (1.0 - _sigmoid_at(0.0))
            y_norm = max(0.0, min(1.0, y_norm))
            path.lineTo(x, plot.bottom() - plot.height() * level * y_norm)
        return path


def _sigmoid_at(t: float) -> float:
    return 1.0 / (1.0 + math.exp(-10.0 * (t - 0.5)))


class RevenueSparkline(_AnimatedCanvas):
    """The last seven days of takings as a thin line with a soft fill.

    Drawn under the revenue card's day picker, with no axes and no labels: it is
    not read for its numbers, it is read for its *shape*, which is the context
    the percent change above it is missing. A lone "↓ 92.08%" looks like a
    collapse; over seven days it is visibly one quiet day after a busy one, or a
    real slide, and staff can tell which at a glance.

    The scale runs from zero to the week's peak, not from its own minimum. That
    is the point: a day that is 8% of the best day drops almost to the floor and
    matches the alarming percent, while an ordinary wobble around the peak stays
    flat and reads as noise. Scaling to the data's own range would make every
    small variation look like a cliff.

    The line is BROWN and the fill a TAN gradient that fades to nothing, so it
    sits on the light card without competing with the figure above it. The last
    point -- today -- carries a dot.
    """

    #: Takings per day, oldest first.
    points: list[float] = []

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.setMinimumHeight(40)

    def set_series(self, points: Sequence[float]) -> None:
        self.points = [float(p) for p in points]
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._drawable() or len(self.points) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            top = 8.0
            bottom = self.height() - 8.0
            left = 2.0
            right = self.width() - 2.0
            if right <= left or bottom <= top:
                return

            peak = max(self.points)
            span = peak if peak > 0 else 1.0
            count = len(self.points)
            step = (right - left) / (count - 1)
            coords = [
                QPointF(
                    left + step * i,
                    bottom - (bottom - top) * (value / span),
                )
                for i, value in enumerate(self.points)
            ]
            curve = _monotone_path(coords)

            if self.progress > 0.02:
                partial = _partial_path(curve, self.progress)
                area = QPainterPath(partial)
                end = partial.currentPosition()
                area.lineTo(end.x(), bottom)
                area.lineTo(coords[0].x(), bottom)
                area.closeSubpath()
                fill = QLinearGradient(0, top, 0, bottom)
                warm = QColor(TAN)
                warm.setAlpha(0x66)
                clear = QColor(TAN)
                clear.setAlpha(0x00)
                fill.setColorAt(0.0, warm)
                fill.setColorAt(1.0, clear)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(fill))
                painter.drawPath(area)

            pen = QPen(
                QColor(BROWN),
                1.6,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(_partial_path(curve, self.progress))

            if self.progress <= 0.9:
                return

            painter.setPen(QPen(QColor(PAPER), 2.0))
            painter.setBrush(QColor(BROWN))
            painter.drawEllipse(coords[-1], 3.5, 3.5)
        finally:
            painter.end()


def _legend_positions(
    width: float,
    less_w: float,
    more_w: float,
    *,
    box: float = 14.0,
    gap: float = 4.0,
    label_gap: float = 8.0,
) -> tuple[float, list[float], float]:
    """`(less_x, swatch_xs, more_x)` for a right-anchored `Less ■■■■■ More`.

    The legend is laid out as one right-anchored run -- label, five swatches,
    label -- so neither word can land on top of the colour squares. The old code
    right-anchored the *swatches* to the widget edge and then placed "More" at a
    hardcoded offset well inside that span, which painted the word over the top
    shades; the caller only ever saw "Less" and "More" colliding with the ramp.

    Kept as a pure function so the geometry is testable without a painter.
    """
    strip = 5 * box + 4 * gap
    total = less_w + label_gap + strip + label_gap + more_w
    x = max(0.0, width - total)
    less_x = x
    x += less_w + label_gap
    swatch_xs: list[float] = []
    for _ in range(5):
        swatch_xs.append(x)
        x += box + gap
    # The loop leaves one trailing gap on `x`; swap it for the label gap.
    more_x = x - gap + label_gap
    return less_x, swatch_xs, more_x


class ActivityHeatmap(_AnimatedCanvas):
    """A GitHub-style contribution grid: weeks across, weekdays down.

    Rows are Monday..Sunday and columns are weeks, oldest at the left. Sixteen
    weeks of `date` -> count.

    **The cell size is derived, and that is the whole difficulty.** The brief
    wants ~28px cells with a 6px gap across 12-16 weeks, inside a card that is
    30% of a 1146px grid. That is 334px of card, 252px of drawable width once
    the padding and the weekday labels are out, and sixteen 28px cells with
    fifteen 6px gaps need 538px of it. So the cell is whatever width leaves the
    requested number of weeks on screen -- about 11px at the reference -- and
    only if even :data:`CELL_MIN` will not fit are the oldest weeks dropped.

    A fixed cell size is the alternative and it is worse in both directions: at
    13px the busiest week in the brief's own 30%-wide card runs 36px off the
    right edge, and at 28px a card narrower than 538px silently clips weeks with
    no indication that anything is missing.

    The cells are staggered in over ~360ms. On a live figure that is the
    difference between "this chart is loading" and "nothing is happening".
    """

    #: date -> count. The window the caller wants shown.
    counts: dict = {}
    first_day: date | None = None
    #: (column, row) under the cursor, or None. The hovered cell lifts.
    _hover: tuple[int, int] | None = None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(
            HEATMAP_TOP
            + 7 * CELL_MIN
            + 6 * CELL_GAP
            + HEATMAP_BOTTOM
        )

    def set_counts(self, counts: dict, first_day: date) -> None:
        self.counts = counts
        self.first_day = first_day
        self._hover = None
        self.update()

    # -- data shape ---------------------------------------------------------

    def _weeks(self) -> list[list[tuple[date | None, int]]]:
        """The grid as columns of seven, oldest at the left.

        Columns may be shorter than seven at either end: the window starts on a
        Monday but stops on whatever day today is, and a cell for a day that has
        not happened is a hole rather than a zero -- padding the current week to
        seven would draw six empty squares suggesting the week is dead.
        """
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

    def _fit(self, columns: int) -> tuple[float, int]:
        """`(cell size, how many weeks fit)` for the current width.

        Keeps the requested weeks first and shrinks the cell to do it; drops the
        oldest weeks only once the cell would go under `CELL_MIN`, which is the
        point at which the five shades stop being tellable apart. The ceiling is
        `CELL_MAX` so a very wide card gets big friendly squares rather than
        60px slabs, which would stop reading as a contribution grid.
        """
        usable = self.width() - HEATMAP_LABEL_GUTTER
        if usable <= 0 or columns <= 0:
            return float(CELL_MIN), 1

        def cell_for(count: int) -> float:
            return (usable - (count - 1) * CELL_GAP) / count

        if cell_for(columns) >= CELL_MIN:
            return min(cell_for(columns), CELL_MAX), columns
        for count in range(columns - 1, 0, -1):
            if cell_for(count) >= CELL_MIN:
                return cell_for(count), count
        return float(CELL_MIN), 1

    def _colour_for(self, count: int, peak: int) -> QColor:
        """One of the five named shades, or the empty-cell colour at zero.

        The ramp is the theme's explicit list rather than a blend, so the top
        two steps are a chosen contrast instead of whatever falls out of
        interpolating between two ends. On a light card the list is inverted --
        L4 becomes the cell and L0 the empty slot -- so "more orders" always
        means "more contrast against the card" whichever way round it is.
        """
        levels = list(HEATMAP_LEVELS)
        if self._light():
            levels.reverse()
        if count <= 0:
            return QColor(levels[0])
        step = (count - 1) / max(peak - 1, 1) if peak > 1 else 1.0
        level = max(0, min(4, int(round(step * 4))))
        return QColor(levels[level])

    # -- painting -----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._drawable():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            weeks = self._weeks()
            if not weeks:
                return

            cell, shown = self._fit(len(weeks))
            weeks = weeks[len(weeks) - shown :]
            peak = max(self.counts.values()) if self.counts else 1
            light = self._light()

            label_font = _font(AXIS_SIZE_LARGE)
            painter.setFont(label_font)

            grid_left = HEATMAP_LABEL_GUTTER
            grid_top = HEATMAP_TOP

            for row in (0, 2, 4):
                names = ("Mon", "Wed", "Fri")
                painter.setPen(QColor(MUTED))
                painter.drawText(
                    QRectF(0, grid_top + row * (cell + CELL_GAP),
                           HEATMAP_LABEL_GUTTER - 8, cell),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    names[row // 2],
                )

            for col, week in enumerate(weeks):
                col_progress = min(1.0, max(0.0, self.progress * 1.6 - col * 0.012))
                if col_progress <= 0:
                    continue
                for row, (day, count) in enumerate(week):
                    if day is None:
                        continue
                    x = grid_left + col * (cell + CELL_GAP)
                    y = grid_top + row * (cell + CELL_GAP)
                    colour = self._colour_for(count, peak)
                    hovered = self._hover == (col, row)
                    if hovered:
                        colour = colour.lighter(115)
                        y -= 1
                    colour.setAlpha(int(255 * min(1.0, col_progress)))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(colour)
                    radius = min(CELL_RADIUS, cell / 2.0)
                    painter.drawRoundedRect(
                        QRectF(x, y, cell, cell), radius, radius
                    )

            self._month_labels(painter, weeks, grid_left, light)
            self._legend(painter, light)
        finally:
            painter.end()

    def _month_labels(
        self, painter: QPainter, weeks: list, grid_left: float, light: bool
    ) -> None:
        """Month names above the columns that begin one."""
        painter.setFont(_font(AXIS_SIZE_LARGE))
        painter.setPen(QColor(MUTED))
        seen: set[str] = set()
        for col, week in enumerate(weeks):
            day = week[0][0]
            if day is None or day.day > 7:
                continue
            label = day.strftime("%b")
            if label in seen:
                continue
            seen.add(label)
            painter.drawText(
                QRectF(grid_left + col * (CELL_GAP + self._cell_now(weeks)), 0,
                       self._cell_now(weeks) * 3, HEATMAP_TOP - 2),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )

    def _cell_now(self, weeks: list) -> float:
        cell, _ = self._fit(len(weeks))
        return cell

    def _legend(self, painter: QPainter, light: bool) -> None:
        """`Less ■■■■■ More` in the trailing corner, as the brief asks.

        The whole run is right-anchored as one unit (see `_legend_positions`),
        rather than anchoring the swatches and dropping the words at hardcoded
        offsets -- which is what let "More" land on top of the top shades.
        """
        levels = list(HEATMAP_LEVELS)
        if light:
            levels.reverse()
        box = 14.0
        y = self.height() - box - 2

        painter.setFont(_font(AXIS_SIZE))
        metrics = painter.fontMetrics()
        less_w = float(metrics.horizontalAdvance("Less"))
        more_w = float(metrics.horizontalAdvance("More"))
        less_x, swatch_xs, more_x = _legend_positions(
            self.width(), less_w, more_w
        )

        painter.setPen(QColor(MUTED))
        painter.drawText(
            QRectF(less_x, y, less_w, box),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Less",
        )
        for x, colour in zip(swatch_xs, levels):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colour))
            painter.drawRoundedRect(QRectF(x, y, box, box), 4.0, 4.0)
        painter.setPen(QColor(MUTED))
        painter.drawText(
            QRectF(more_x, y, more_w, box),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "More",
        )

    # -- hover --------------------------------------------------------------

    def _cell_at(self, pos) -> tuple[int, int] | None:
        """The (column, row) under a cursor position, or None."""
        weeks = self._weeks()
        if not weeks:
            return None
        cell, shown = self._fit(len(weeks))
        col = int((pos.x() - HEATMAP_LABEL_GUTTER) // (cell + CELL_GAP))
        row = int((pos.y() - HEATMAP_TOP) // (cell + CELL_GAP))
        weeks = weeks[len(weeks) - shown :]
        if 0 <= col < len(weeks) and 0 <= row < len(weeks[col]):
            return col, row
        return None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        hit = self._cell_at(event.position())
        if hit != self._hover:
            self._hover = hit
            self.update()
        if hit is None:
            QToolTip.hideText()
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        col, row = hit
        weeks = self._weeks()
        _, shown = self._fit(len(weeks))
        day, count = weeks[len(weeks) - shown :][col][row]
        if day is None:
            QToolTip.hideText()
            return
        word = "order" if count == 1 else "orders"
        QToolTip.showText(
            event.globalPosition().toPoint(),
            f"{count} {word} · {day:%b %-d}",
            self,
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def leaveEvent(self, event) -> None:  # noqa: N802
        QToolTip.hideText()
        if self._hover is not None:
            self._hover = None
            self.update()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)


class SalesTargetChart(_AnimatedCanvas):
    """Monthly takings as a smooth line, with the year's target drawn on top.

    A monotone cubic, not a natural spline: a natural spline overshoots between
    points, and an overshoot on a revenue chart invents a month of takings that
    did not happen. Monotone means the curve never rises where the data falls.

    **All twelve months, not the brief's six.** The brief says "over Jan to Jun"
    on a chart captioned "This Year" against an annual target, which are two
    different claims. In October a Jan-Jun line is a half-year total presented as
    a year's progress, and the pace line drawn across it would compare six
    months of takings against a twelfth of the target -- so the card would read
    "comfortably behind" in every month after June. Twelve months is what the
    service returns and what the caption claims.
    """

    months: list = []  # list[MonthSales]
    target_total: Decimal = Decimal("0")
    achieved_total: Decimal = Decimal("0")
    #: Month the floating chip marks.
    _hover_month: int | None = None

    def set_months(
        self, months: list, target_total: Decimal, achieved_total: Decimal
    ) -> None:
        self.months = list(months)
        while self.months and self.months[-1].total <= 0:
            self.months = self.months[:-1]
        self.target_total = target_total
        self.achieved_total = achieved_total
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._drawable() or not self.months:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            label_font = _font(AXIS_SIZE)
            axis_font = _font(AXIS_SIZE, tabular=True)
            painter.setFont(label_font)

            peak = max(float(m.total) for m in self.months)
            peak = max(peak, float(self.target_total) / 12.0)
            axis_max = _nice_ceiling(peak * 1.25) if peak else 1.0

            plot = QRectF(46, 14, self.width() - 56, self.height() - 42)
            if plot.width() <= 0 or plot.height() <= 0:
                return

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

            curve = _monotone_path(points)

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

            pace = float(self.target_total) / 12.0
            if pace > 0 and pace < axis_max:
                pace_y = plot.bottom() - plot.height() * (pace / axis_max)
                target_pen = QPen(QColor(MUTED), 1.2, Qt.PenStyle.DashLine)
                target_pen.setDashPattern([4, 4])
                painter.setPen(target_pen)
                painter.drawLine(
                    QPointF(plot.left(), pace_y), QPointF(plot.right(), pace_y)
                )
                painter.setFont(_font(AXIS_SIZE))
                painter.setPen(QColor(MUTED))
                painter.drawText(
                    QRectF(plot.right() - 150, pace_y - 17, 150, 15),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    f"monthly pace {_compact(pace)}",
                )

            if self.progress <= 0.9:
                return

            paper = PAPER if self._light() else INK_RAISED
            for index, point in enumerate(points):
                radius = 5.0 if index == self._hover_month else 3.5
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(paper))
                painter.drawEllipse(point, radius, radius)
                painter.setPen(QPen(QColor(TAN), 1.6))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(point, radius, radius)

            if self._hover_month is not None and 0 <= self._hover_month < len(points):
                self._chip(
                    painter,
                    plot,
                    points[self._hover_month],
                    float(self.months[self._hover_month].total),
                )

        finally:
            painter.end()

    def _chip(
        self, painter: QPainter, plot: QRectF, point: QPointF, value: float
    ) -> None:
        """The floating tooltip: a dotted guide to the axis and an INK chip.

        INK on a light card is the one inverted surface on the dashboard, which is
        what makes it read as sitting *above* the chart rather than being drawn
        on it. The pointer is a small triangle on the chip's underside, so the
        chip is attached to the point instead of floating near it.
        """
        pen = QPen(QColor(MUTED), 1.0, Qt.PenStyle.DotLine)
        pen.setDashPattern([1, 3])
        painter.setPen(pen)
        painter.drawLine(
            QPointF(point.x(), point.y()), QPointF(point.x(), plot.bottom())
        )

        text = _compact(value)
        font = _font(12, tabular=True, weight=QFont.Weight.DemiBold)
        painter.setFont(font)
        width = QFontMetrics(font).horizontalAdvance(text) + 20
        height = 26.0
        top = point.y() - height - 14
        if top < 2:
            top = point.y() + 14
        left = min(
            max(2.0, point.x() - width / 2),
            self.width() - width - 2,
        )
        rect = QRectF(left, top, width, height)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(INK))
        painter.drawRoundedRect(rect, 12.0, 12.0)
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(point.x() - 5, top + height - 1),
                    QPointF(point.x() + 5, top + height - 1),
                    QPointF(point.x(), top + height + 6),
                ]
            )
        )
        painter.setFont(font)
        painter.setPen(QColor(PAPER))
        painter.drawText(
            rect, Qt.AlignmentFlag.AlignCenter, text
        )

    def _point_index_at(self, x: float, plot: QRectF) -> int | None:
        if not self.months or plot.width() <= 0:
            return None
        step = plot.width() / max(len(self.months) - 1, 1)
        if step <= 0:
            return None
        index = int(round((x - plot.left()) / step))
        if 0 <= index < len(self.months):
            return index
        return None

    def _plot_rect(self) -> QRectF:
        return QRectF(46, 14, self.width() - 56, self.height() - 42)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = self._point_index_at(event.position().x(), self._plot_rect())
        if index != self._hover_month:
            self._hover_month = index
            self.update()
        if index is None:
            QToolTip.hideText()
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        month = self.months[index]
        QToolTip.showText(
            event.globalPosition().toPoint(),
            f"{month.label} {_compact(float(month.total))}",
            self,
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def leaveEvent(self, event) -> None:  # noqa: N802
        QToolTip.hideText()
        if self._hover_month is not None:
            self._hover_month = None
            self.update()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)


def _compact(value: float) -> str:
    """Axis labels. ₱1.2M rather than ₱1,200,000, which will not fit in 40px.

    The trailing ``.0`` is dropped: on an axis, `₱1.0M` and `₱1M` mean the same
    number and the extra character is width the labels do not have.
    """
    if value >= 1_000_000:
        text = f"{value / 1_000_000:.1f}M"
        return f"₱{text[:-2] if text.endswith('.0M') else text}"
    if value >= 1_000:
        return f"₱{value / 1_000:,.0f}k"
    return f"₱{value:,.0f}"


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
    count = path.elementCount()
    if count == 0:
        return out
    first = path.elementAt(0)
    start = QPointF(first.x, first.y)
    out.moveTo(start)

    total = _path_length(path)
    target = total * max(0.0, progress)

    kinds = QPainterPath.ElementType
    travelled = 0.0
    index = 1
    while index < count:
        element = path.elementAt(index)
        kind = element.type
        if kind == kinds.LineToElement:
            end = QPointF(element.x, element.y)
            seg = math.dist((start.x(), start.y()), (end.x(), end.y()))
            if travelled + seg >= target:
                t = (target - travelled) / seg if seg else 0.0
                out.lineTo(
                    start.x() + (end.x() - start.x()) * t,
                    start.y() + (end.y() - start.y()) * t,
                )
                return out
            out.lineTo(end)
            travelled += seg
            start = end
            index += 1
        elif kind == kinds.CurveToElement:
            # A cubic spans three consecutive elements; `Element` carries only x/y.
            if index + 2 >= count:
                break
            c1 = QPointF(element.x, element.y)
            data1 = path.elementAt(index + 1)
            data2 = path.elementAt(index + 2)
            c2 = QPointF(data1.x, data1.y)
            end = QPointF(data2.x, data2.y)
            approx = (
                math.dist((start.x(), start.y()), (c1.x(), c1.y()))
                + math.dist((c1.x(), c1.y()), (c2.x(), c2.y()))
                + math.dist((c2.x(), c2.y()), (end.x(), end.y()))
            ) / 3
            if travelled + approx >= target:
                t = (target - travelled) / approx if approx else 0.0
                p01 = _lerp(start, c1, t)
                p12 = _lerp(c1, c2, t)
                p23 = _lerp(c2, end, t)
                p012 = _lerp(p01, p12, t)
                p123 = _lerp(p12, p23, t)
                out.cubicTo(p01, p012, _lerp(p012, p123, t))
                return out
            out.cubicTo(c1, c2, end)
            travelled += approx
            start = end
            index += 3
        else:
            index += 1

    return out


def _lerp(a: QPointF, b: QPointF, t: float) -> QPointF:
    return QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t)


def _path_length(path: QPainterPath) -> float:
    if path.length() > 0:
        return path.length()
    return 1.0


def _point_at(path: QPainterPath, progress: float) -> QPointF:
    """A point `progress` along a path, for the fill's leading edge."""
    partial = _partial_path(path, progress)
    return partial.currentPosition()
