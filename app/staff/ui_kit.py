"""Shared warm UI pieces for the counter's list pages and booking modals.

The counter rental modal established the language: warm paper, white
18px cards, ink primary actions, cream summary panels, and chips instead
of dropdowns. This module packages the reusable parts so Bookings, Fleet,
Customers, and Maintenance stay visually identical without copying
layout code.

Nothing here owns data or business rules; pages keep their own loaders,
filters, and permissions.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.staff.cards import Card

#: Card geometry, sampled from the counter references.
WARM_RADIUS = 18
WARM_MODAL_WIDTH = 540

#: Palette. The page keeps the product cream; cards and controls use the
#: slightly softer reference borders.
WARM_PAGE = "#F1E8D8"
WARM_CARD = "#FFFFFF"
WARM_INK = "#171717"
WARM_MUTED = "#7D7265"
WARM_SUBTITLE = "#8B7F70"
WARM_BORDER = "#DFD4C5"
WARM_CONTROL = "#E0D6C9"
WARM_CREAM = "#EADBBB"
WARM_TILE = "#ECE6DD"
WARM_NOTICE = "#F1E7D6"
WARM_AMBER = "#F7E3C3"
WARM_RED = "#9B2C1F"
WARM_DANGER_FILL = "#A3271D"
WARM_DANGER_OUTLINE = "#C07B72"


def _label(
    text: str,
    *,
    parent: QWidget | None = None,
    size: int = 13,
    weight: QFont.Weight = QFont.Weight.Normal,
    colour: str = WARM_INK,
) -> QLabel:
    label = QLabel(text, parent)
    font = QFont("Inter")
    font.setPixelSize(size)
    font.setWeight(weight)
    label.setFont(font)
    label.setStyleSheet(f"color: {colour}; background: transparent;")
    return label


class PageHeading(QWidget):
    """Page title with an inline count/subtitle, on the warm page."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("warmPageHeading")
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 0, 0, 0)
        row.setSpacing(16)
        self.title_label = _label(
            title, parent=self, size=28, weight=QFont.Weight.Bold
        )
        self.title_label.setObjectName("warmPageTitle")
        self.subtitle_label = _label(
            subtitle, parent=self, size=13, colour=WARM_MUTED
        )
        self.subtitle_label.setObjectName("warmPageSubtitle")
        row.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self.subtitle_label, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)


class WarmPanel(QFrame):
    """White 18px card with the standard 16px inner rhythm."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        padding: int = 16,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pagePanel")
        self.setProperty("variant", "warm")
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(12)


class WarmHost(QFrame):
    """Transparent host that keeps the legacy ``_tools_panel`` contract."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pagePanel")
        self.setProperty("variant", "warmHost")
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(16)


class _ExclusiveChips(QWidget):
    """Shared gate for the two chip-row widgets."""

    changed = Signal(str)

    def __init__(
        self,
        options: Sequence[tuple[str, str]],
        *,
        object_name: str,
        height: int = 30,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._options = tuple(options)
        self.buttons: dict[str, QPushButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._selected = self._options[0][0] if self._options else ""
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(12)
        for key, text in self._options:
            button = QPushButton(text, self)
            button.setObjectName(object_name)
            button.setCheckable(True)
            button.setFixedHeight(height)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setSizePolicy(
                QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
            )
            button.clicked.connect(
                lambda _checked=False, chosen=key: self._on_click(chosen)
            )
            self._group.addButton(button)
            self.buttons[key] = button
            self._layout.addWidget(button)
        self._layout.addStretch(1)
        self.select(self._selected)

    @property
    def selected(self) -> str:
        return self._selected

    def select(self, key: str, *, emit: bool = False) -> None:
        button = self.buttons.get(key)
        if button is None:
            return
        self._selected = key
        was_blocked = button.blockSignals(True)
        try:
            button.setChecked(True)
        finally:
            button.blockSignals(was_blocked)
        if emit:
            self.changed.emit(key)

    def _on_click(self, key: str) -> None:
        self._selected = key
        self.changed.emit(key)


class StatusChipRow(_ExclusiveChips):
    """Filter chips under a tools card."""

    def __init__(
        self,
        options: Sequence[tuple[str, str]],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            options, object_name="statusFilterChip", parent=parent
        )


class ChipGroup(_ExclusiveChips):
    """Modal choices: payment kind, method, quick dates, fuel, reasons."""

    def __init__(
        self,
        options: Sequence[tuple[str, str]],
        *,
        variant: str = "choice",
        expanding: bool = False,
        height: int | None = None,
        spacing: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        self._variant = variant
        self._expanding = expanding
        super().__init__(
            options,
            object_name="choiceChip",
            height=height or 32,
            parent=parent,
        )
        self._layout.setSpacing(
            spacing if spacing is not None else (8 if variant == "choice" else 10)
        )
        if expanding:
            # The shared row ends with a stretch so chips hug their labels.
            # A segmented control (fuel) needs the stretch gone so the five
            # chips divide the full 492px body evenly.
            last = self._layout.takeAt(self._layout.count() - 1)
            if last is not None and last.spacerItem() is None:
                self._layout.addItem(last)
        for button in self.buttons.values():
            button.setProperty("variant", variant)
            if expanding:
                button.setSizePolicy(
                    QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
                )
        self.style().unpolish(self)
        self.style().polish(self)


class SelectionBar(QFrame):
    """Cream bar naming the selected row and holding its actions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("selectionBar")
        self.setMinimumHeight(60)
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 12, 12)
        row.setSpacing(12)
        self.summary_label = QLabel("Select a row to act on it.", self)
        self.summary_label.setObjectName("selectionSummary")
        self.summary_label.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(self.summary_label, 1)
        self.actions = QHBoxLayout()
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.actions.setSpacing(12)
        row.addLayout(self.actions)

    def set_summary(self, text: str) -> None:
        self.summary_label.setText(text)

    def set_parts(self, prefix: str, name: str, detail: str = "") -> None:
        parts = f"{escape(prefix)}<b>{escape(name)}</b>"
        if detail:
            parts += f" · {escape(detail)}"
        self.summary_label.setText(parts)

    def set_active(self, active: bool) -> None:
        """Arm the bar. Inactive arms nothing; active leaves per-row rules
        to the page, which runs immediately after this call."""
        self.setProperty("active", active)
        if not active:
            self.summary_label.setText("Select a row to act on it.")
            self.set_actions_enabled(False)
        self.style().unpolish(self)
        self.style().polish(self)

    def add_action(self, button: QPushButton, *, kind: str = "action") -> None:
        object_name = {
            "primary": "barPrimary",
            "danger": "barDanger",
            "action": "barAction",
        }.get(kind, "barAction")
        button.setObjectName(object_name)
        button.setMinimumHeight(36)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.actions.addWidget(button)

    def set_actions_enabled(self, enabled: bool) -> None:
        for index in range(self.actions.count()):
            item = self.actions.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setEnabled(enabled)


class MetricTile(QFrame):
    """Small cream label/value tile used in the booking modals."""

    def __init__(
        self,
        label: str,
        value: str,
        *,
        tone: str = "neutral",
        large: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("warmMetricTile")
        self.setProperty("tone", tone)
        self.setProperty("large", "true" if large else "false")
        if large:
            self.setMinimumHeight(68)
        else:
            self.setMinimumHeight(52)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(1)
        self.label = _label(label, parent=self, size=11, colour=WARM_MUTED)
        self.label.setObjectName("metricLabel")
        self.value_label = _label(
            value,
            parent=self,
            size=20 if large else 18,
            weight=QFont.Weight.Bold,
        )
        self.value_label.setObjectName("metricValue")
        layout.addWidget(self.label)
        layout.addWidget(self.value_label)

    def set_value(self, text: str) -> None:
        self.value_label.setText(text)


class SectionTitle(QLabel):
    """Uppercase 10px section label inside a modal or card."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text.upper(), parent)
        self.setObjectName("warmSectionTitle")
        font = QFont("Inter")
        font.setPixelSize(10)
        font.setWeight(QFont.Weight.DemiBold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.7)
        self.setFont(font)
        self.setStyleSheet(
            f"color: {WARM_MUTED}; background: transparent;"
        )


class FormField(QWidget):
    """Label above a control, with the reference's optional suffix."""

    def __init__(
        self,
        label: str,
        widget: QWidget,
        *,
        optional: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("warmFormField")
        self.widget = widget
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(5)
        self.label = _label(
            label,
            parent=self,
            size=12,
            weight=QFont.Weight.DemiBold,
        )
        self.label.setObjectName("warmFieldLabel")
        head.addWidget(self.label)
        if optional:
            suffix = _label(
                "· optional", parent=self, size=12, colour=WARM_MUTED
            )
            suffix.setObjectName("warmFieldOptional")
            head.addWidget(suffix)
        head.addStretch(1)
        layout.addLayout(head)
        layout.addWidget(widget)


class SummaryBox(QFrame):
    """Cream two/three-row money summary with inset dividers."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modalSummary")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 4, 0, 4)
        self._layout.setSpacing(0)
        self.rows: list[tuple[QLabel, QLabel]] = []

    def add_row(self, label: str, value: str) -> QLabel:
        if self.rows:
            host = QWidget(self)
            host.setObjectName("summaryDividerHost")
            line = QHBoxLayout(host)
            line.setContentsMargins(20, 0, 20, 0)
            rule = QFrame(host)
            rule.setObjectName("summaryDivider")
            rule.setFixedHeight(1)
            line.addWidget(rule)
            self._layout.addWidget(host)

        row = QWidget(self)
        row.setObjectName("summaryRow")
        line = QHBoxLayout(row)
        line.setContentsMargins(20, 10, 20, 10)
        line.setSpacing(10)
        left = _label(label, parent=row, size=13, colour=WARM_MUTED)
        left.setObjectName("summaryLabel")
        right = _label(
            value,
            parent=row,
            size=13,
            weight=QFont.Weight.Bold,
        )
        right.setObjectName("summaryValue")
        right.setAlignment(Qt.AlignmentFlag.AlignRight)
        line.addWidget(left, 1)
        line.addWidget(right)
        self._layout.addWidget(row)
        self.rows.append((left, right))
        return left


class NoticeBox(QFrame):
    """Tone-coloured inline notice used by the booking modals."""

    def __init__(
        self,
        text: str,
        *,
        tone: str = "cream",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("warmNotice")
        self.setProperty("tone", tone)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(8)
        self.label = _label(
            text, parent=self, size=12, colour=WARM_INK
        )
        self.label.setObjectName("warmNoticeText")
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)

    def set_text(self, text: str) -> None:
        self.label.setText(text)


class BookingModal(QWidget):
    """Transparent host around a 540px white booking sheet.

    Header (eyebrow/title/subtitle), scroll-free body, and a sticky
    footer with a top divider. Hosts keep the outer frosted overlay
    responsible for overflow.
    """

    def __init__(
        self,
        eyebrow: str,
        title: str,
        subtitle: str = "",
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("bookingModal")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addStretch(1)
        self.card = Card(self, radius=20)
        self.card.setObjectName("bookingModalCard")
        self.card.setFixedWidth(WARM_MODAL_WIDTH)
        self.card.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred
        )
        outer.addWidget(self.card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)

        self.header_host = QWidget(self.card)
        self.header_host.setObjectName("bookingModalHeader")
        header = QVBoxLayout(self.header_host)
        header.setContentsMargins(24, 21, 24, 0)
        header.setSpacing(5)

        self.eyebrow_label = QLabel(eyebrow.upper(), self.header_host)
        self.eyebrow_label.setObjectName("bookingEyebrow")
        self.eyebrow_label.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self.eyebrow_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.eyebrow_label, 0, Qt.AlignmentFlag.AlignLeft)

        self.title_label = _label(
            title,
            parent=self.header_host,
            size=24,
            weight=QFont.Weight.Bold,
        )
        self.title_label.setObjectName("bookingModalTitle")
        self.title_label.setWordWrap(True)
        header.addWidget(self.title_label)

        self.subtitle_label = _label(
            subtitle,
            parent=self.header_host,
            size=13,
            colour=WARM_SUBTITLE,
        )
        self.subtitle_label.setObjectName("bookingModalSubtitle")
        self.subtitle_label.setWordWrap(True)
        header.addWidget(self.subtitle_label)

        header.addSpacing(12)
        divider = QFrame(self.header_host)
        divider.setObjectName("bookingModalDivider")
        divider.setFixedHeight(1)
        header.addWidget(divider)
        self.card.layout().addWidget(self.header_host)

        self.content_host = QWidget(self.card)
        self.content_host.setObjectName("bookingModalBody")
        self.body = QVBoxLayout(self.content_host)
        self.body.setContentsMargins(24, 16, 24, 18)
        self.body.setSpacing(12)
        self._body = self.body
        self.card.layout().addWidget(self.content_host, 1)

        self.footer = QFrame(self.card)
        self.footer.setObjectName("bookingModalFooter")
        self.footer_layout = QHBoxLayout(self.footer)
        self.footer_layout.setContentsMargins(24, 15, 24, 16)
        self.footer_layout.setSpacing(10)
        self.footer_layout.addStretch(1)
        self.card.layout().addWidget(self.footer)

        self.error_label = QLabel("", self.content_host)
        self.error_label.setObjectName("modalError")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        self._error = self.error_label

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)

    def set_error(self, text: str) -> None:
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text))

    def add_back(self, text: str = "Back") -> QPushButton:
        button = QPushButton(text, self.footer)
        button.setObjectName("modalSecondary")
        button.setMinimumHeight(36)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.footer_layout.addWidget(button)
        return button

    def add_primary(self, text: str, *, danger: bool = False) -> QPushButton:
        button = QPushButton(text, self.footer)
        button.setObjectName("modalDanger" if danger else "modalPrimary")
        button.setMinimumHeight(36)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.footer_layout.addWidget(button)
        return button

    def add_footer_button(
        self, text: str, *, object_name: str = "modalSecondary"
    ) -> QPushButton:
        button = QPushButton(text, self.footer)
        button.setObjectName(object_name)
        button.setMinimumHeight(36)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.footer_layout.addWidget(button)
        return button
