"""The pieces every dashboard card is built from.

`Card`, `IconButton`, `Pill`, `HeroNumber` and `SectionLabel`, in one place,
because the brief's visual language is a *system*: 28px card radius, 12px button
radius, 999px pills, 26px card padding and a 16px gutter. Get those from five
places and the grid quietly stops lining up.

All sizing goes through :mod:`app.staff.metrics` rather than being written into
each widget, because QSS cannot be read back at runtime -- a radius that is 26px
here and 24px in the stylesheet is invisible until two cards sit side by side
and one of them looks wrong.
"""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.staff import icons
from app.staff.metrics import (
    CARD_PADDING,
    CARD_RADIUS,
    HERO_MIN_SIZE,
    HERO_SIZE,
    ICON_BUTTON,
    ICON_BUTTON_RADIUS,
    PILL_HEIGHT,
    PILL_RADIUS,
)
from app.staff.theme import (
    INK_HOVER,
    MUTED,
    PAPER,
    PROP_DARK,
    TEXT,
)


class Card(QFrame):
    """One dashboard panel: rounded, bordered, optionally dark.

    `dark` is a property rather than an object name because it is a variant of
    the same widget. The stylesheet paints from the property, so a dark card is
    still a `Card` and can still be looked up by type.

    Deliberately *not* a `QGroupBox` or a styled `QWidget` with a border: a
    QFrame subclass can own its own paintEvent if the design ever needs a
    gradient or a soft shadow, which it does on the dark cards.
    """

    def __init__(self, parent: QWidget | None = None, *, dark: bool = False) -> None:
        super().__init__(parent)
        self.setProperty(PROP_DARK, dark)
        self.setProperty("class", "card")
        self._dark = dark
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

    def body(self) -> QVBoxLayout:
        """The card's content layout, at the standard padding."""
        out = QVBoxLayout()
        out.setContentsMargins(
            CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING
        )
        out.setSpacing(14)
        return out

    @property
    def dark(self) -> bool:
        return self._dark

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        radius = float(CARD_RADIUS)
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if self._dark:
                shadow = QColor(0x1A, 0x14, 0x0A, 0x1F)
            else:
                shadow = QColor(0x1A, 0x14, 0x0A, 0x14)
            for step, alpha in ((3, 0.45), (2, 0.7), (1, 1.0)):
                pen = QPen(shadow)
                pen.setWidthF(step * 2)
                shadow.setAlpha(int(shadow.alpha() * alpha))
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                inset = step
                painter.drawRoundedRect(
                    self.rect().adjusted(inset, inset, -inset, -inset),
                    radius,
                    radius,
                )
        finally:
            painter.end()
        super().paintEvent(event)


class IconButton(QWidget):
    """A square that holds one icon, 40x40 by default.

    A QWidget rather than a QPushButton so it never draws a text label or a
    default-button ring: at 40px with a 20px glyph, anything Qt adds on its own
    is something the design did not ask for. Clicked signal replaces
    `clicked` so the call sites read the same as the rest of the app.
    """

    clicked = Signal()

    def __init__(
        self,
        name: str,
        *,
        parent: QWidget | None = None,
        colour: str | None = None,
        size: int = ICON_BUTTON,
        icon_size: int = 20,
        tooltip: str = "",
        background: str | None = None,
        outline: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._name = name
        self._colour = colour
        self._icon_size = icon_size
        self._background = background
        self._outline = outline
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if tooltip:
            self.setToolTip(tooltip)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            hovered = self.underMouse() or self.hasFocus()
            bg = self._background
            if bg is None:
                bg = INK_HOVER if hovered else "transparent"
            if self._outline is not None:
                painter.setBrush(
                    QColor(INK_HOVER if hovered else "#00000000")
                )
                painter.setPen(QPen(QColor(self._outline), 1))
                radius = min(self.width(), self.height()) / 2
                painter.drawRoundedRect(
                    self.rect().adjusted(0, 0, -1, -1), radius, radius
                )
            elif bg != "transparent":
                painter.setBrush(QColor(bg))
                painter.setPen(Qt.PenStyle.NoPen)
                radius = (
                    float(self.width()) / 2
                    if self.width() > 32
                    else float(ICON_BUTTON_RADIUS)
                )
                painter.drawRoundedRect(self.rect(), radius, radius)
            colour = self._colour or (TEXT if not self._dark_parent() else PAPER)
            glyph = icons.pixmap(self._name, colour, self._icon_size)
            painter.drawPixmap(
                (self.width() - glyph.width()) // 2,
                (self.height() - glyph.height()) // 2,
                glyph,
            )
        finally:
            painter.end()

    def _dark_parent(self) -> bool:
        node = self.parentWidget()
        while node is not None:
            if isinstance(node, Card):
                return node.dark
            node = node.parentWidget()
        return False

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit()
        else:
            super().keyPressEvent(event)


class Pill(QWidget):
    """A rounded status chip: a small dot and a short label.

    Replaces the QSS `QFrame#statusPill` used by the table screens. Same idea,
    drawn here instead, because the dashboard needs three sizes and a dot whose
    colour can differ from the label's -- neither is expressible in the stylesheet
    without one selector per combination.
    """

    def __init__(
        self,
        text: str,
        *,
        parent: QWidget | None = None,
        colour: str = MUTED,
        background: str | None = None,
        dot: bool = True,
        dot_colour: str | None = None,
        border_colour: str | None = None,
        height: int = PILL_HEIGHT,
        radius: int = PILL_RADIUS,
        font_size: int = 12,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self._colour = colour
        self._background = background
        self._border_colour = border_colour
        self._dot = dot
        self._dot_colour = dot_colour or colour
        self._height = height
        self._radius = radius
        self._font_size = font_size
        self._label_font = QFont("Inter")
        self._label_font.setPixelSize(font_size)
        self._label_font.setWeight(QFont.Weight.DemiBold)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self.updateGeometry()
        self.update()

    def set_text_colour(self, colour: str) -> None:
        self._colour = colour
        self.update()

    def set_dot_colour(self, colour: str) -> None:
        self._dot_colour = colour
        self.update()

    def sizeHint(self):  # noqa: N802
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QFontMetrics

        width = QFontMetrics(self._label_font).horizontalAdvance(self._text)
        dot = 8 if self._dot else 0
        pad = 11
        return QSize(width + dot + pad * 2 + (4 if self._dot else 0), self._height)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if self._background:
                painter.setBrush(QColor(self._background))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(
                    self.rect(), self._radius, self._radius
                )
            if self._border_colour:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(self._border_colour), 1))
                painter.drawRoundedRect(
                    QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                    float(self._radius),
                    float(self._radius),
                )
            left = 11
            if self._dot:
                painter.setBrush(QColor(self._dot_colour))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(left, self.height() // 2 - 4, 8, 8)
                left += 12
            painter.setFont(self._label_font)
            painter.setPen(QColor(self._colour))
            from PySide6.QtGui import QFontMetrics

            baseline = (self.height() + QFontMetrics(self._label_font).height()) // 2 - 2
            painter.drawText(left, baseline, self._text)
        finally:
            painter.end()


class HeroNumber(QLabel):
    """The big figure on a card: 64px, medium weight, tabular.

    Tabular figures are not a nicety here. The dashboard refreshes every thirty
    seconds, and with proportional digits the peso value changes width as the
    thousands digit ticks over, so the number visibly jitters left and right
    every refresh for no reason. `tnum` pins every digit to the same advance.
    """

    def __init__(
        self,
        text: str = "0",
        *,
        parent: QWidget | None = None,
        size: int = HERO_SIZE,
        colour: str = TEXT,
        weight: QFont.Weight = QFont.Weight.Medium,
        suffix: str = "",
    ) -> None:
        super().__init__(text, parent)
        #: Largest size the figure may use.
        self._size = size
        self._min_size = HERO_MIN_SIZE
        #: Full, un-elided text; `QLabel.text()` may hold an elided form.
        self._full = text
        self._suffix = suffix
        self._weight = weight
        self._font_size = -1
        self._set_font(size)
        self.setStyleSheet(f"color: {colour}; background: transparent;")

    def set_colour(self, colour: str) -> None:
        self.setStyleSheet(f"color: {colour}; background: transparent;")

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        self._full = text
        self._apply_fit()

    def set_value(self, value: Decimal | float | int) -> None:
        self.setText(f"{value}{self._suffix}")

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._apply_fit()

    def _set_font(self, size: int) -> None:
        if size == self._font_size and self.font().pixelSize() == size:
            return
        self._font_size = size
        font = QFont("Inter")
        font.setPixelSize(size)
        font.setWeight(self._weight)
        font.setFeature(QFont.Tag("tnum"), 1)
        self.setFont(font)

    def _fits(self, text: str, size: int, width: int) -> bool:
        font = QFont("Inter")
        font.setPixelSize(size)
        font.setWeight(self._weight)
        font.setFeature(QFont.Tag("tnum"), 1)
        return QFontMetrics(font).horizontalAdvance(text) <= width - 2

    def _apply_fit(self) -> None:
        width = self.width()
        if width <= 0:
            self._set_font(self._size)
            super().setText(self._full)
            self.setToolTip("")
            return

        size = self._size
        while size > self._min_size and not self._fits(self._full, size, width):
            size -= 1
        self._set_font(size)

        if self._fits(self._full, size, width):
            super().setText(self._full)
            self.setToolTip("")
        else:
            metrics = QFontMetrics(self.font())
            super().setText(
                metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, width)
            )
            self.setToolTip(self._full)

    @staticmethod
    def tabular(font: QFont) -> QFont:
        out = QFont(font)
        out.setFeature(QFont.Tag("tnum"), 1)
        return out


class MoneyNumber(QLabel):
    """A peso amount whose sign and cents are dimmed and whose digits are not.

    The brief splits the hero three ways: `₱` and `.00` in MUTED, the digits in
    PAPER, all at 64px. That reads as one number rather than three because the
    dimming is subtle, and the eye picks up the magnitude first and the exact
    cents second -- which is the right priority when the question is "how much
    came in today", not "what is the exact figure".

    Rich text rather than three adjacent labels: three labels put the `₱` and
    the `.00` on their own baselines and the alignment goes wrong the moment the
    integer part changes width, and this is a number that changes every thirty
    seconds.
    """

    def __init__(
        self,
        *,
        parent: QWidget | None = None,
        size: int = HERO_SIZE,
        dim: str = MUTED,
        bright: str = PAPER,
        weight: QFont.Weight = QFont.Weight.Medium,
    ) -> None:
        super().__init__(parent)
        self._dim = dim
        self._bright = bright
        font = QFont("Inter")
        font.setPixelSize(size)
        font.setWeight(weight)
        font.setFeature(QFont.Tag("tnum"), 1)
        self.setFont(font)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setText(self._markup(0, 2))

    def _markup(self, amount: Decimal | float, decimals: int) -> str:
        value = abs(Decimal(str(amount)))
        whole = f"{value:,.0f}"
        body = f"{value:,.{decimals}f}"
        sign = "−" if amount < 0 else ""
        return (
            f'<span style="color:{self._dim}">{sign}₱</span>'
            f'<span style="color:{self._bright}">{whole}</span>'
            f'<span style="color:{self._dim}">.{body.rsplit(".", 1)[1]}</span>'
        )

    def set_amount(self, amount: Decimal | float, *, decimals: int = 2) -> None:
        self.setText(self._markup(amount, decimals))

    def set_colours(self, *, dim: str, bright: str) -> None:
        self._dim = dim
        self._bright = bright
        current = self.text()
        self.setText(current.replace(self._dim, "\0").replace(self._bright, "\1")
                     .replace("\0", dim).replace("\1", bright))

    @property
    def amount_text(self) -> str:
        import re

        return re.sub(r"<[^>]+>", "", self.text())


class Caption(QLabel):
    """A small uppercase-ish label above a figure or a group of rows."""

    def __init__(
        self,
        text: str,
        *,
        parent: QWidget | None = None,
        colour: str = MUTED,
        size: int = 13,
        weight: QFont.Weight = QFont.Weight.Medium,
    ) -> None:
        super().__init__(text, parent)
        font = QFont("Inter")
        font.setPixelSize(size)
        font.setWeight(weight)
        self.setFont(font)
        self.setStyleSheet(f"color: {colour}; background: transparent;")


class ElidedLabel(QLabel):
    """A left-aligned label that shortens its text instead of clipping it.

    The recent-transactions list is four columns sharing the card's width on a
    flexible name column, so a long name has to give way to the method, channel
    and amount beside it. Clipping mid-letter reads as a rendering fault; an
    ellipsis reads as a truncation. `Ignored` width lets the grid squeeze it
    below its own size hint, which is what makes room for the ellipsis to appear.
    """

    def __init__(
        self,
        text: str = "",
        *,
        parent: QWidget | None = None,
        colour: str = TEXT,
        size: int = 14,
        weight: QFont.Weight = QFont.Weight.Medium,
    ) -> None:
        super().__init__(parent)
        self._full = text
        font = QFont("Inter")
        font.setPixelSize(size)
        font.setWeight(weight)
        self.setFont(font)
        self.setStyleSheet(f"color: {colour}; background: transparent;")
        self.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self._refresh()

    def full_text(self) -> str:
        return self._full

    def _refresh(self) -> None:
        available = self.width()
        if available <= 0 or not self._full:
            super().setText(self._full)
            return
        metrics = QFontMetrics(self.font())
        super().setText(
            metrics.elidedText(
                self._full, Qt.TextElideMode.ElideRight, available
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh()


class TrendLabel(QLabel):
    """A percentage with its arrow, green when up and red when down.

    The arrow points in the direction of travel, and for revenue a fall is red.
    Colour alone is not the signal -- the arrow and the sign carry it too, for
    anyone who cannot separate the two hues.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        font = QFont("Inter")
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        font.setFeature(QFont.Tag("tnum"), 1)
        self.setFont(font)
        self.setStyleSheet("background: transparent;")

    def set_change(self, pct: float | None, *, up_is_good: bool = True) -> None:
        if not pct:
            self.setText("0.00%")
            self.setStyleSheet(f"color: {MUTED}; background: transparent;")
            return
        rising = pct > 0
        good = rising == up_is_good
        arrow = "↑" if rising else "↓"
        self.setText(f"{arrow} {abs(pct):.2f}%")
        self.setStyleSheet(
            f"color: {'#4F7A5B' if good else '#A8452F'}; background: transparent;"
        )


class SectionLabel(QLabel):
    """A card's title. One weight up from `Caption`, never a heading."""

    def __init__(
        self,
        text: str,
        *,
        parent: QWidget | None = None,
        colour: str = TEXT,
        size: int = 16,
    ) -> None:
        super().__init__(text, parent)
        font = QFont("Inter")
        font.setPixelSize(size)
        font.setWeight(QFont.Weight.DemiBold)
        self.setFont(font)
        self.setStyleSheet(f"color: {colour}; background: transparent;")


class FadeIn(QWidget):
    """Wraps a widget and fades it in on first show.

    Cards are built before the window is shown, so there is nothing to fade
    *from* -- the widget goes from opacity 0 to 1 over 400ms when the page first
    appears. A `QGraphicsOpacityEffect` is the cheapest thing that does this
    without touching layout; the alternative (hiding and re-showing) would make
    the cards pop in one at a time and fight the grid.
    """

    def __init__(self, child: QWidget, *, delay_ms: int = 0, parent=None) -> None:
        super().__init__(parent)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(child)
        self._child = child
        self._delay = delay_ms
        self._animation: QPropertyAnimation | None = None

    def play(self) -> None:
        self._effect.setOpacity(0.0)
        self._child.setGraphicsEffect(self._effect)
        self._animation = QPropertyAnimation(self._effect, b"opacity", self)
        self._animation.setDuration(400)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def start() -> None:
            assert self._animation is not None
            self._animation.start()

        if self._delay:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(self._delay, start)
        else:
            start()


def row_layout(spacing: int = 12) -> QHBoxLayout:
    """A horizontal layout with no margins, which is what a card row wants."""
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    return layout


def padded_column(spacing: int = 18) -> QVBoxLayout:
    """A vertical layout at the standard card padding."""
    layout = QVBoxLayout()
    layout.setContentsMargins(CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING)
    layout.setSpacing(spacing)
    return layout
