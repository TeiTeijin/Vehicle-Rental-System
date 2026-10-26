"""Shared pieces for the counter rental modal.

The vehicle-details card and the four wizard steps share one visual recipe:
a white 18px card on the frosted overlay, a step rail, a content column with a
warm recap panel beside it, and a footer whose primary action is the only dark
button. Everything here is presentation; the services still own the rules.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.services import image_cache
from app.staff import icons, theme
from app.staff.cards import Caption, Card, MoneyNumber

#: The modal is one size in the reference. 900 leaves the frosted margin on a
#: 1440-wide window and still fits the two-column step body.
MODAL_WIDTH = 900
MODAL_RADIUS = 18
MODAL_PADDING = 24
MODAL_GAP = 20

MODAL_INK = "#171717"
MODAL_MUTED = "#6B5E4F"
MODAL_BORDER = "#D8CCBB"
MODAL_SURFACE = "#EEEAE1"
MODAL_CREAM = "#F1E8D8"
MODAL_DANGER = "#9B2C1F"
MODAL_OK = "#4F7A5B"
MODAL_WARN = "#B07D2B"

STEP_LABELS = ("Vehicle", "Scheduling", "Customer", "Payment")


def _text(
    text: str,
    *,
    parent: QWidget | None = None,
    size: int = 13,
    weight: QFont.Weight = QFont.Weight.Medium,
    colour: str = MODAL_INK,
) -> QLabel:
    label = QLabel(text, parent)
    font = QFont("Inter")
    font.setPixelSize(size)
    font.setWeight(weight)
    label.setFont(font)
    label.setStyleSheet(f"color: {colour}; background: transparent;")
    return label


def vehicle_thumb(
    make: str | None,
    model: str | None,
    year: int | None,
    *,
    width: int = 84,
    height: int = 54,
) -> QPixmap:
    """Cached three-quarter photo cropped to a small tile; no network."""
    payload = None
    try:
        cached = image_cache.load(make or "", model or "", int(year or 0))
        payload = getattr(cached, "data", None)
    except Exception:  # noqa: BLE001 - a thumb never blocks the modal
        payload = None
    if payload:
        image = QImage()
        if image.loadFromData(payload):
            scaled = image.scaled(
                width,
                height,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = max(0, (scaled.width() - width) // 2)
            y = max(0, (scaled.height() - height) // 2)
            return QPixmap.fromImage(scaled.copy(x, y, width, height))
    return icons.pixmap("vehicle", theme.MUTED, 30)


class StatusChip(QLabel):
    """Small rounded status label. Tone drives the QSS colours."""

    def __init__(
        self,
        text: str,
        *,
        tone: str = "neutral",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("statusChip")
        self.setProperty("tone", tone)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)


class StepProgress(QWidget):
    """Vehicle → Scheduling → Customer → Payment, with completed checks."""

    def __init__(self, current: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("stepProgress")
        self.current = current
        self.states = tuple(
            "done"
            if index < current
            else "current"
            if index == current
            else "upcoming"
            for index in range(len(STEP_LABELS))
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        self.chips: list[QFrame] = []
        self.step_labels: list[QLabel] = []
        for index, label in enumerate(STEP_LABELS):
            chip = QFrame(self)
            chip.setObjectName("stepChip")
            chip.setProperty("state", self.states[index])
            chip.setFixedHeight(30)
            row = QHBoxLayout(chip)
            row.setContentsMargins(8, 3, 12, 3)
            row.setSpacing(6)
            dot = QLabel("✓" if self.states[index] == "done" else str(index + 1), chip)
            dot.setObjectName("stepDot")
            dot.setProperty("state", self.states[index])
            dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            dot.setFixedSize(20, 20)
            row.addWidget(dot)
            state = self.states[index]
            name = _text(
                label,
                parent=chip,
                size=12,
                weight=QFont.Weight.DemiBold,
                colour="#FFFFFF"
                if state == "current"
                else "#3F6D4E"
                if state == "done"
                else MODAL_MUTED,
            )
            name.setObjectName("stepName")
            name.setProperty("state", self.states[index])
            row.addWidget(name)
            layout.addWidget(chip)
            self.chips.append(chip)
            self.step_labels.append(name)
        layout.addStretch(1)


class VehicleSummary(QFrame):
    """Small vehicle identity row: thumbnail, name, year/plate, optional link."""

    def __init__(
        self,
        make: str,
        model: str,
        year: int | None,
        plate: str,
        *,
        parent: QWidget | None = None,
        action_text: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("modalVehicleRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        self.thumb = QLabel(self)
        self.thumb.setObjectName("modalVehicleThumb")
        self.thumb.setFixedSize(64, 42)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setPixmap(vehicle_thumb(make, model, year, width=58, height=38))
        layout.addWidget(self.thumb)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        self.name_label = _text(
            f"{make} {model}".strip(),
            parent=self,
            size=13,
            weight=QFont.Weight.DemiBold,
        )
        self.name_label.setObjectName("modalVehicleName")
        self.meta_label = _text(
            f"{year} \u00b7 {plate}" if year else plate,
            parent=self,
            size=11,
            colour=MODAL_MUTED,
        )
        self.meta_label.setObjectName("modalVehicleMeta")
        text.addWidget(self.name_label)
        text.addWidget(self.meta_label)
        layout.addLayout(text, 1)

        self.action: QPushButton | None = None
        if action_text:
            self.action = QPushButton(action_text, self)
            self.action.setObjectName("linkButton")
            self.action.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(self.action, 0, Qt.AlignmentFlag.AlignVCenter)


class NoticeBar(QFrame):
    """Warm inline notice with a leading glyph."""

    def __init__(
        self,
        text: str,
        *,
        tone: str = "warn",
        icon: str = "warning",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("modalNotice")
        self.setProperty("tone", tone)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(8)
        glyph = QLabel(self)
        glyph.setObjectName("modalNoticeIcon")
        colour = MODAL_WARN if tone == "warn" else MODAL_DANGER
        try:
            glyph.setPixmap(icons.pixmap(icon, colour, 16))
        except KeyError:
            glyph.setText("!")
        layout.addWidget(glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        self.label = _text(
            text,
            parent=self,
            size=12,
            weight=QFont.Weight.Medium,
            colour=MODAL_INK,
        )
        self.label.setObjectName("modalNoticeText")
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)


class MetaTile(QFrame):
    """A label/value tile used under the matched customer."""

    def __init__(
        self, label: str, value: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("modalMetaTile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        self.label = _text(label, parent=self, size=10, colour=MODAL_MUTED)
        self.label.setObjectName("metaLabel")
        self.value_label = _text(
            value,
            parent=self,
            size=13,
            weight=QFont.Weight.DemiBold,
        )
        self.value_label.setObjectName("metaValue")
        layout.addWidget(self.label)
        layout.addWidget(self.value_label)


class RecapPanel(QFrame):
    """The warm summary column beside a step's content."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modalRecap")
        self.setFixedWidth(276)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(8)

        self.title = _text(
            title,
            parent=self,
            size=13,
            weight=QFont.Weight.Bold,
            colour=MODAL_INK,
        )
        self.title.setObjectName("recapTitle")
        self._layout.addWidget(self.title)

        self.rows_host = QVBoxLayout()
        self.rows_host.setContentsMargins(0, 0, 0, 0)
        self.rows_host.setSpacing(6)
        self._layout.addLayout(self.rows_host)

        self.rule = QFrame(self)
        self.rule.setObjectName("recapRule")
        self.rule.setFixedHeight(1)
        self._layout.addWidget(self.rule)

        self.key_label = _text(
            "Total",
            parent=self,
            size=12,
            weight=QFont.Weight.DemiBold,
            colour=MODAL_MUTED,
        )
        self._layout.addWidget(self.key_label)
        self.key_amount = MoneyNumber(
            parent=self,
            size=34,
            dim=MODAL_MUTED,
            bright=MODAL_INK,
            weight=QFont.Weight.DemiBold,
        )
        self._layout.addWidget(self.key_amount)

        self.extra_host = QVBoxLayout()
        self.extra_host.setContentsMargins(0, 0, 0, 0)
        self.extra_host.setSpacing(5)
        self._layout.addLayout(self.extra_host)

        self.note_label = Caption("", parent=self, colour=MODAL_MUTED, size=11)
        self.note_label.setWordWrap(True)
        self._layout.addWidget(self.note_label)

        self.rows: list[tuple[QLabel, QWidget]] = []

    def add_row(self, label: str, value: str | QWidget) -> QLabel:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        left = _text(label, parent=self, size=12, colour=MODAL_MUTED)
        left.setWordWrap(True)
        left.setMinimumWidth(0)
        row.addWidget(left, 1)
        if isinstance(value, QWidget):
            right = value
        else:
            right = _text(
                value,
                parent=self,
                size=12,
                weight=QFont.Weight.DemiBold,
                colour=MODAL_INK,
            )
        right.setObjectName("recapValue")
        right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(right)
        self.rows_host.addLayout(row)
        self.rows.append((left, right))
        return left

    def add_extra(self, label: str, value: str | QWidget) -> QLabel:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        left = _text(label, parent=self, size=12, colour=MODAL_MUTED)
        left.setWordWrap(True)
        left.setMinimumWidth(0)
        row.addWidget(left, 1)
        if isinstance(value, QWidget):
            right = value
        else:
            right = _text(
                value,
                parent=self,
                size=12,
                weight=QFont.Weight.DemiBold,
                colour=MODAL_INK,
            )
        right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(right)
        self.extra_host.addLayout(row)
        return left

    def add_widget(self, widget: QWidget) -> None:
        """Put identity content directly under the title (vehicle card, ...)."""
        self._layout.insertWidget(1, widget)

    def set_key(self, label: str, amount) -> None:
        self.key_label.setText(label)
        self.key_amount.set_amount(amount)

    def set_key_visible(self, visible: bool) -> None:
        self.rule.setVisible(visible)
        self.key_label.setVisible(visible)
        self.key_amount.setVisible(visible)

    def set_title(self, text: str) -> None:
        self.title.setText(text)

    def set_note(self, text: str) -> None:
        self.note_label.setText(text)
        self.note_label.setVisible(bool(text))

    def clear_rows(self) -> None:
        while self.rows_host.count():
            item = self.rows_host.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget() is not None:
                        child.widget().deleteLater()
        self.rows.clear()
        while self.extra_host.count():
            item = self.extra_host.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget() is not None:
                        child.widget().deleteLater()


class SpecTile(QFrame):
    def __init__(self, label: str, value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modalSpecTile")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        self.label = _text(label, parent=self, size=10, colour=MODAL_MUTED)
        self.label.setObjectName("specLabel")
        self.value_label = _text(
            value,
            parent=self,
            size=13,
            weight=QFont.Weight.DemiBold,
            colour=MODAL_INK,
        )
        self.value_label.setObjectName("specValue")
        layout.addWidget(self.label)
        layout.addWidget(self.value_label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class DayTile(QFrame):
    def __init__(self, day: date, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dayTile")
        self.day = day
        self.setFixedSize(46, 52)
        self.state = "free"
        self.setProperty("state", self.state)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)
        self.weekday_label = _text(
            day.strftime("%a")[:2],
            parent=self,
            size=9,
            colour=MODAL_MUTED,
        )
        self.weekday_label.setObjectName("dayWeekday")
        self.weekday_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.number_label = _text(
            str(day.day),
            parent=self,
            size=13,
            weight=QFont.Weight.DemiBold,
            colour=MODAL_INK,
        )
        self.number_label.setObjectName("dayNumber")
        self.number_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.weekday_label)
        layout.addWidget(self.number_label)

    def set_state(self, state: str) -> None:
        self.state = state
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)
        selected = state == "selected"
        weekday = "#E3D8C8" if selected else MODAL_MUTED
        number = "#FFFFFF" if selected else MODAL_INK
        self.weekday_label.setStyleSheet(
            f"color: {weekday}; background: transparent;"
        )
        self.number_label.setStyleSheet(
            f"color: {number}; background: transparent;"
        )


class DayStrip(QWidget):
    """Continuous day-by-day availability preview, grouped by month."""

    #: 11 tiles fit the 552px content column (11 * 46 + 10 * 4 = 546).
    COLUMNS = 11

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dayStrip")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self.tiles: dict[date, DayTile] = {}
        self.states: dict[date, str] = {}
        self.month_labels: list[str] = []

    def set_range(
        self,
        first: date,
        last: date,
        states: dict[date, str],
        selected: tuple[date, date] | None = None,
    ) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget() is not None:
                        child.widget().deleteLater()
        self.tiles.clear()
        self.month_labels.clear()
        self.states = dict(states)
        if last < first:
            return

        month = date(first.year, first.month, 1)
        end_month = date(last.year, last.month, 1)
        while month <= end_month:
            heading = _text(
                month.strftime("%B"),
                parent=self,
                size=11,
                weight=QFont.Weight.DemiBold,
                colour=MODAL_MUTED,
            )
            heading.setObjectName("dayStripMonth")
            self._layout.addWidget(heading)
            self.month_labels.append(heading.text())

            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(4)
            grid.setVerticalSpacing(4)
            grid.setAlignment(Qt.AlignmentFlag.AlignLeft)
            _, days_in_month = calendar.monthrange(month.year, month.month)
            month_end = date(month.year, month.month, days_in_month)
            segment_first = max(first, month)
            segment_last = min(last, month_end)
            for offset in range((segment_last - segment_first).days + 1):
                day = segment_first + timedelta(days=offset)
                tile = DayTile(day, self)
                state = states.get(day, "free")
                if selected is not None and selected[0] <= day <= selected[1]:
                    state = "selected"
                tile.set_state(state)
                grid.addWidget(tile, offset // self.COLUMNS, offset % self.COLUMNS)
                self.tiles[day] = tile
            self._layout.addLayout(grid)
            if month.month == 12:
                month = date(month.year + 1, 1, 1)
            else:
                month = date(month.year, month.month + 1, 1)


class ModalStep(QWidget):
    """A wizard step: progress rail, title, two columns, sticky footer."""

    cancel_requested = Signal()

    def __init__(self, context, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addStretch(1)

        self.card = Card(self, radius=MODAL_RADIUS)
        self.card.setObjectName("vehicleDetailsCard")
        self.card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.card.setMaximumWidth(MODAL_WIDTH)
        outer.addWidget(self.card)
        outer.addStretch(1)

        card_layout = self.card.layout()
        self.header_host = QWidget(self.card)
        self.header_host.setObjectName("wizardStep")
        self.header_layout = QVBoxLayout(self.header_host)
        self.header_layout.setContentsMargins(
            MODAL_PADDING, 22, MODAL_PADDING, 0
        )
        self.header_layout.setSpacing(10)
        card_layout.addWidget(self.header_host)

        self.content_host = QWidget(self.card)
        self.content_host.setObjectName("wizardStep")
        self._body = QVBoxLayout(self.content_host)
        self._body.setContentsMargins(
            MODAL_PADDING, 24, MODAL_PADDING, 16
        )
        self._body.setSpacing(12)
        card_layout.addWidget(self.content_host, 1)

        self.footer = QFrame(self.card)
        self.footer.setObjectName("modalFooter")
        self.footer_layout = QHBoxLayout(self.footer)
        self.footer_layout.setContentsMargins(
            MODAL_PADDING, 12, MODAL_PADDING, 14
        )
        self.footer_layout.setSpacing(8)
        self.footer.setVisible(False)
        card_layout.addWidget(self.footer)

        #: The error label is created by each step and lives under the content.
        self._error: QLabel | None = None

    def _head(
        self,
        current: int,
        title: str,
        subtitle: str | None = None,
        *,
        status: tuple[str, str] | None = None,
        leading: QWidget | None = None,
    ) -> None:
        self.progress = StepProgress(current, self.header_host)
        self.header_layout.addWidget(self.progress)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        if leading is not None:
            row.addWidget(leading, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title_label = _text(
            title,
            parent=self.header_host,
            size=26,
            weight=QFont.Weight.Bold,
            colour=MODAL_INK,
        )
        self.title_label.setObjectName("modalTitle")
        row.addWidget(self.title_label, 1)
        self.status_chip: StatusChip | None = None
        if status is not None:
            self.status_chip = StatusChip(status[0], tone=status[1], parent=self.header_host)
            row.addWidget(self.status_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        self.header_layout.addLayout(row)

        self.subtitle_label = None
        if subtitle:
            self.subtitle_label = _text(
                subtitle,
                parent=self.header_host,
                size=12,
                colour=MODAL_MUTED,
            )
            self.subtitle_label.setObjectName("modalSubtitle")
            self.header_layout.addWidget(self.subtitle_label)

        rule = QFrame(self.header_host)
        rule.setObjectName("modalRule")
        rule.setFixedHeight(1)
        self.header_layout.addWidget(rule)

    def _two_columns(self, recap_title: str) -> tuple[QVBoxLayout, RecapPanel]:
        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(24)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(14)
        self.recap = RecapPanel(recap_title, self.content_host)
        columns.addLayout(left, 1)
        columns.addWidget(
            self.recap, 0, Qt.AlignmentFlag.AlignTop
        )
        self._body.addLayout(columns)
        return left, self.recap

    def _error_label(self) -> QLabel:
        label = QLabel("", self.content_host)
        label.setObjectName("fieldError")
        label.setWordWrap(True)
        label.setVisible(False)
        self._body.addWidget(label)
        self._error = label
        return label

    def _buttons(
        self,
        primary_text: str,
        *,
        cancel: bool = True,
        hint: str | None = None,
    ) -> tuple[QPushButton, QPushButton]:
        while self.footer_layout.count():
            item = self.footer_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if cancel:
            leave = QPushButton("Cancel rental", self.footer)
            leave.setObjectName("modalCancel")
            leave.setCursor(Qt.CursorShape.PointingHandCursor)
            leave.clicked.connect(self.cancel_requested.emit)
            self.footer_layout.addWidget(leave)
        self.footer_layout.addStretch(1)
        self.hint_label = None
        if hint:
            self.hint_label = _text(
                hint,
                parent=self.footer,
                size=12,
                colour=MODAL_MUTED,
            )
            self.hint_label.setObjectName("modalHint")
            self.footer_layout.addWidget(self.hint_label)
        back = QPushButton("Back", self.footer)
        back.setObjectName("secondaryButton")
        back.setMinimumHeight(38)
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        primary = QPushButton(primary_text, self.footer)
        primary.setObjectName("primaryButton")
        primary.setMinimumHeight(38)
        primary.setCursor(Qt.CursorShape.PointingHandCursor)
        self.footer_layout.addWidget(back)
        self.footer_layout.addWidget(primary)
        self.footer.setVisible(True)
        return back, primary

    def _amount(self, value, *, size: int = 15) -> MoneyNumber:
        label = MoneyNumber(
            parent=self.content_host,
            size=size,
            dim=MODAL_MUTED,
            bright=MODAL_INK,
            weight=QFont.Weight.DemiBold,
        )
        label.set_amount(value)
        return label

    def _rule(self) -> QFrame:
        rule = QFrame(self.content_host)
        rule.setObjectName("modalRule")
        rule.setFixedHeight(1)
        return rule
