from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
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
) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    radius = float(SIDEBAR_RADIUS) * HOVER_RADIUS_FACTOR
    height = rect.height()

    if hovered:
        painter.setBrush(QColor(theme.SURFACE))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, radius, radius)

    if active:
        painter.setBrush(QColor(theme.INK))
        painter.setPen(Qt.PenStyle.NoPen)
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
        text_left = label_x + glyph.width() + 12

    font = QFont("Inter")
    font.setPixelSize(LABEL_PX)
    font.setWeight(QFont.Weight.DemiBold if active else QFont.Weight.Medium)
    painter.setFont(font)
    painter.setPen(QColor(theme.INK))
    painter.drawText(
        QRectF(text_left, 0, rect.width() - text_left - 24, height),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        label,
    )


class ActiveRow(QAbstractButton):
    def __init__(
        self,
        label: str,
        *,
        parent: QWidget | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(parent)
        self._label_text = label
        self._hovered = False

        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

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
            paint_active_row(
                painter,
                self.rect(),
                active=self.isChecked(),
                hovered=self._hovered or self.hasFocus(),
                label=self._label_text,
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
