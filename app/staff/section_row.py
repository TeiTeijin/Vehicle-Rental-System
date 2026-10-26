from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from app.staff import theme
from app.staff.metrics import NAV_ACTIVE_BAR, SIDEBAR_RADIUS

#: Corner radius of the hover wash, as a fraction of the panel radius. Matches
#: the sidebar: a hover wash that does not share the panel's curve reads as a
#: different control sitting on top of it.
HOVER_RADIUS_FACTOR = 0.42

#: Inset of the active bar from the row's top and bottom edges.
ACTIVE_BAR_INSET = 4.0

#: Inset of the active bar from the row's leading edge.
ACTIVE_BAR_X = 4.0

#: Corner radius of the active bar.
ACTIVE_BAR_RADIUS = 1.5

#: Label font size, in logical pixels.
LABEL_PX = 13

#: Left edge of the label text, as an offset from the row's leading edge.
LABEL_X = 16


def paint_active_row(
    painter: QPainter,
    rect,
    *,
    active: bool,
    hovered: bool,
    label: str,
    label_x: float = LABEL_X,
    glyph=None,
    glyph_gap: float = 12,
    reserve: float = 24,
    variant: str = "",
) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    radius = float(SIDEBAR_RADIUS) * HOVER_RADIUS_FACTOR
    height = rect.height()
    pill = variant == "reportNav"

    if hovered and not (pill and active):
        painter.setBrush(QColor(theme.SURFACE))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, radius, radius)

    if active:
        painter.setBrush(QColor(theme.INK))
        painter.setPen(Qt.PenStyle.NoPen)
        if pill:
            painter.drawRoundedRect(rect, 10, 10)
        else:
            painter.drawRoundedRect(
                QRectF(
                    ACTIVE_BAR_X,
                    ACTIVE_BAR_INSET,
                    float(NAV_ACTIVE_BAR),
                    height - ACTIVE_BAR_INSET * 2,
                ),
                ACTIVE_BAR_RADIUS,
                ACTIVE_BAR_RADIUS,
            )

    text_left = label_x
    if glyph is not None:
        painter.drawPixmap(int(label_x), (height - glyph.height()) // 2, glyph)
        text_left = label_x + glyph.width() + glyph_gap

    # From the painter (the widget font, i.e. the bundled Inter), not a
    # hardcoded family: QFont("Inter") misses the bundled "Inter 18pt" family
    # and falls back to a wider system font, which is what clipped the longer
    # nav labels inside the narrow filter column.
    font = QFont(painter.font())
    font.setPixelSize(LABEL_PX)
    font.setWeight(QFont.Weight.DemiBold if active else QFont.Weight.Medium)
    painter.setFont(font)
    painter.setPen(QColor(theme.PAPER if pill and active else theme.INK))
    # Elide instead of clip: a label wider than its row (fallback fonts,
    # text scaling) fades with "..." rather than losing letters mid-glyph.
    available = max(0.0, float(rect.width()) - text_left - reserve)
    shown = QFontMetrics(font).elidedText(
        label, Qt.TextElideMode.ElideRight, max(0, int(available))
    )
    painter.drawText(
        QRectF(text_left, 0, available, height),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        shown,
    )


class ActiveRow(QAbstractButton):
    def __init__(
        self,
        label: str,
        *,
        parent: QWidget | None = None,
        enabled: bool = True,
        variant: str = "",
    ) -> None:
        super().__init__(parent)
        self._label_text = label
        self._hovered = False
        self._glyph = None
        self._glyph_active = None
        self._glyph_gap = 12.0
        self._reserve = 24.0
        self._variant = variant
        if variant:
            self.setProperty("variant", variant)

        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_glyph(self, pixmap=None, active_pixmap=None, gap: float = 12.0) -> None:
        """Leading icon. Falls back to `pixmap` when no active variant given."""
        self._glyph = pixmap
        self._glyph_active = active_pixmap
        self._glyph_gap = gap
        self.update()

    def set_reserve(self, reserve: float) -> None:
        """Trailing room kept clear after the label text."""
        self._reserve = reserve
        self.update()

    @property
    def label(self) -> str:
        return self._label_text

    def set_label(self, label: str) -> None:
        if label != self._label_text:
            self._label_text = label
            self.update()

    def set_active(self, active: bool) -> None:
        if self.isChecked() != active:
            self.setChecked(active)
            self.update()

    def is_active(self) -> bool:
        return self.isChecked()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            checked = self.isChecked()
            glyph = self._glyph
            if checked and self._glyph_active is not None:
                glyph = self._glyph_active
            paint_active_row(
                painter,
                self.rect(),
                active=checked,
                hovered=self._hovered or self.hasFocus(),
                label=self._label_text,
                glyph=glyph,
                glyph_gap=self._glyph_gap,
                reserve=self._reserve,
                variant=self._variant,
            )
        finally:
            painter.end()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)
