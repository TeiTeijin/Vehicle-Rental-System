"""Maintenance action panels: warm sheets inside the frosted overlay.

Same contract as before: each panel collects the kwargs its page action
needs, validation mistakes stay inline, and the page owns the service call
and the toast. The presentation follows the settled warm language from the
counter references: an eyebrow, a heavy ink title, a quiet subtitle, cream
summary blocks, white bordered cards, and one dark footer action.

The sheet is composed locally instead of reaching into the rental wizard:
History and Mark completed are 540px, Log maintenance opens a 900px
two-column sheet with the live summary beside the form.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from app.models import Vehicle
from app.services import maintenance_service
from app.services.maintenance_service import TYPE_LABELS, TYPES, type_label
from app.staff import icons, theme
from app.staff.cards import Card
from app.staff.feedback import describe
from app.staff.tables import TableChip
from app.utils.money import ZERO, money, pesos

# -- palette shared by the three maintenance sheets -----------------------

INK = "#171717"
MUTED = "#6B5E4F"
SUBTLE = "#8B7F70"
BORDER = "#E4D9CA"
CREAM = "#F3EAD9"
AMBER = "#FAE7C8"
NEXT_FIELD = "#F8F1E5"

NARROW_WIDTH = 540
WIDE_WIDTH = 900
SHEET_PADDING = 24


def _label(
    text: str,
    *,
    parent: QWidget | None = None,
    size: int = 13,
    weight: QFont.Weight = QFont.Weight.Normal,
    colour: str = INK,
) -> QLabel:
    label = QLabel(text, parent)
    font = QFont("Inter")
    font.setPixelSize(size)
    font.setWeight(weight)
    label.setFont(font)
    label.setStyleSheet(f"color: {colour}; background: transparent;")
    return label


def _friendly(error: BaseException) -> str:
    message, _field, _kind = describe(error)
    return message


def _vehicle_options(context) -> list[tuple[int, str]]:
    """(vehicle_id, label) for every vehicle still in service."""
    with context.reading() as session:
        cars = (
            session.query(Vehicle)
            .filter(Vehicle.status != "retired")
            .order_by(Vehicle.plate_number)
            .all()
        )
        return [
            (car.vehicle_id, f"{car.plate_number} {car.make} {car.model}")
            for car in cars
        ]


class _WarmCheck(QCheckBox):
    """A square ink checkbox that matches the warm references.

    Qt's native indicator is a tiny platform tile and the QSS replacement
    cannot draw a check mark without an image asset. Painting it here keeps
    the control keyboard- and click-accessible while matching the 20px
    rounded square in the design.
    """

    BOX = 20
    GAP = 9

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("maintenanceCheck")
        font = QFont("Inter")
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.DemiBold)
        self.setFont(font)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(28)
        self.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        metrics = QFontMetrics(self.font())
        return QSize(
            self.BOX + self.GAP + metrics.horizontalAdvance(self.text()) + 2,
            max(28, metrics.height() + 8),
        )

    def hitButton(self, position) -> bool:  # noqa: N802 - Qt naming
        return self.rect().contains(position)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            box = QRectF(
                0.0,
                (self.height() - self.BOX) / 2.0,
                float(self.BOX),
                float(self.BOX),
            )
            if self.isChecked():
                painter.setBrush(QColor(INK))
                painter.setPen(QPen(QColor(INK), 1))
                painter.drawRoundedRect(box, 5.0, 5.0)
                check = icons.pixmap("check", "#FFFFFF", 12)
                painter.drawPixmap(
                    int((self.BOX - check.width()) / 2),
                    int((self.height() - check.height()) / 2),
                    check,
                )
            else:
                painter.setBrush(QColor("#FFFFFF"))
                painter.setPen(QPen(QColor("#B9AC9A"), 1.4))
                painter.drawRoundedRect(
                    box.adjusted(0.5, 0.5, -0.5, -0.5), 5.0, 5.0
                )
            painter.setFont(self.font())
            painter.setPen(
                QColor(
                    "#A79B87"
                    if not self.isEnabled()
                    else INK
                )
            )
            painter.drawText(
                self.BOX + self.GAP,
                0,
                max(0, self.width() - self.BOX - self.GAP),
                self.height(),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self.text(),
            )
        finally:
            painter.end()


class _Sheet(QWidget):
    """Transparent host around one warm white maintenance sheet."""

    def __init__(
        self,
        width: int,
        eyebrow: str,
        title: str,
        subtitle: str = "",
        *,
        eyebrow_pill: bool = True,
        large_title: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.sheet_width = int(width)
        self.setObjectName("maintenanceSheet")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addStretch(1)

        self.card = Card(self, radius=18)
        self.card.setObjectName("maintenanceCard")
        self.card.setFixedWidth(self.sheet_width)
        self.card.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred
        )
        outer.addWidget(self.card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)

        self.header_host = QWidget(self.card)
        self.header_host.setObjectName("maintenanceSheetHeader")
        header = QVBoxLayout(self.header_host)
        header.setContentsMargins(SHEET_PADDING, 22, SHEET_PADDING, 0)
        header.setSpacing(5)

        self.eyebrow_label = QLabel(
            eyebrow.upper() if eyebrow_pill else eyebrow,
            self.header_host,
        )
        self.eyebrow_label.setObjectName(
            "maintenanceEyebrow" if eyebrow_pill else "maintenanceEyebrowPlain"
        )
        self.eyebrow_label.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        if eyebrow_pill:
            self.eyebrow_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(
            self.eyebrow_label, 0, Qt.AlignmentFlag.AlignLeft
        )

        self.title_label = _label(
            title,
            parent=self.header_host,
            size=30 if large_title else 26,
            weight=QFont.Weight.Bold,
        )
        self.title_label.setObjectName("maintenanceTitle")
        self.title_label.setProperty(
            "large", "true" if large_title else "false"
        )
        self.title_label.setWordWrap(True)
        header.addWidget(self.title_label)

        self.subtitle_label = _label(
            subtitle, parent=self.header_host, size=13, colour=MUTED
        )
        self.subtitle_label.setObjectName("maintenanceSubtitle")
        self.subtitle_label.setWordWrap(True)
        header.addWidget(self.subtitle_label)

        header.addSpacing(12)
        divider = QFrame(self.header_host)
        divider.setObjectName("maintenanceDivider")
        divider.setFixedHeight(1)
        header.addWidget(divider)
        self.card.layout().addWidget(self.header_host)

        self.content_host = QWidget(self.card)
        self.content_host.setObjectName("maintenanceSheetBody")
        self.body = QVBoxLayout(self.content_host)
        self.body.setContentsMargins(
            SHEET_PADDING, 18, SHEET_PADDING, 18
        )
        self.body.setSpacing(14)
        self._body = self.body
        self.card.layout().addWidget(self.content_host, 1)

        self.footer = QFrame(self.card)
        self.footer.setObjectName("maintenanceFooter")
        self.footer_layout = QHBoxLayout(self.footer)
        self.footer_layout.setContentsMargins(
            SHEET_PADDING, 14, SHEET_PADDING, 16
        )
        self.footer_layout.setSpacing(10)
        self.footer_layout.addStretch(1)
        self.card.layout().addWidget(self.footer)

        self._error: QLabel | None = None

    # -- shared pieces -------------------------------------------------

    def _error_label(self) -> QLabel:
        label = QLabel("", self.content_host)
        label.setObjectName("maintenanceError")
        label.setWordWrap(True)
        label.setVisible(False)
        self.body.addWidget(label)
        self._error = label
        return label

    def _footer_button(
        self, text: str, *, primary: bool = False
    ) -> QPushButton:
        button = QPushButton(text, self.footer)
        button.setObjectName("modalPrimary" if primary else "modalSecondary")
        button.setMinimumHeight(38)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.footer_layout.addWidget(button)
        return button

    def add_back(self, text: str = "Back") -> QPushButton:
        return self._footer_button(text)

    def add_primary(self, text: str) -> QPushButton:
        return self._footer_button(text, primary=True)


def _section(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text.upper(), parent)
    label.setObjectName("maintenanceSectionLabel")
    font = QFont("Inter")
    font.setPixelSize(11)
    font.setWeight(QFont.Weight.DemiBold)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
    label.setFont(font)
    return label


def _field(
    label: str,
    widget: QWidget,
    *,
    optional: bool = False,
    suffix: str | None = None,
    parent: QWidget | None = None,
) -> QWidget:
    """Label above a control, with the reference's muted suffix slots."""
    host = QWidget(parent if parent is not None else widget.parentWidget())
    host.setObjectName("maintenanceFieldHost")
    column = QVBoxLayout(host)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(6)
    head = QHBoxLayout()
    head.setContentsMargins(0, 0, 0, 0)
    head.setSpacing(5)
    head.addWidget(
        _label(
            label,
            parent=host,
            size=13,
            weight=QFont.Weight.DemiBold,
        )
    )
    if suffix:
        hint = _label(suffix, parent=host, size=13, colour=SUBTLE)
        hint.setObjectName("maintenanceFieldSuffix")
        head.addWidget(hint)
    if optional:
        optional_label = _label(
            "· optional", parent=host, size=13, colour=SUBTLE
        )
        optional_label.setObjectName("maintenanceFieldOptional")
        head.addWidget(optional_label)
    head.addStretch(1)
    column.addLayout(head)
    column.addWidget(widget)
    return host


def _clock_input(edit: QDateEdit) -> QDateEdit:
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("dd MMM yyyy")
    edit.setObjectName("warmInput")
    edit.setMinimumHeight(40)
    return edit


def _metric_tile(label: str, value: str, parent: QWidget) -> QFrame:
    tile = QFrame(parent)
    tile.setObjectName("maintenanceMetricTile")
    tile.setMinimumHeight(76)
    tile.setSizePolicy(
        QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
    )
    column = QVBoxLayout(tile)
    column.setContentsMargins(14, 11, 14, 11)
    column.setSpacing(1)
    caption = _label(label, parent=tile, size=13, colour=MUTED)
    caption.setObjectName("maintenanceMetricLabel")
    figure = _label(
        value,
        parent=tile,
        size=26,
        weight=QFont.Weight.Bold,
    )
    figure.setObjectName("maintenanceMetricValue")
    column.addWidget(caption)
    column.addWidget(figure)
    return tile


def _status_text(status: str | None) -> str:
    """Reference sentence case: "In progress", "Completed"."""
    if (status or "").strip().lower() == "in_progress":
        return "In progress"
    return theme.status_label(status)


def _status_tone(status: str | None) -> str:
    key = (status or "").strip().lower()
    if key == "completed":
        return "ok"
    if key == "in_progress":
        return "warn"
    if key == "cancelled":
        return "danger"
    return "neutral"


def _record_snapshot(record) -> dict:
    """Plain values for one history row; the reading session expires ORM rows."""
    return {
        "maintenance_type": record.maintenance_type,
        "status": record.status,
        "description": record.description,
        "start_date": record.start_date,
        "end_date": record.end_date,
        "cost": record.cost,
        "estimate_cost": record.estimate_cost,
        "shop": record.shop,
        "next_due_date": record.next_due_date,
        "next_due_odometer": record.next_due_odometer,
    }


def _record_card(
    record: dict,
    *,
    parent: QWidget,
) -> QFrame:
    """One white history card; keeps every row the old caption list showed."""
    card = QFrame(parent)
    card.setObjectName("maintenanceRecordCard")
    column = QVBoxLayout(card)
    column.setContentsMargins(16, 14, 16, 14)
    column.setSpacing(4)

    head = QHBoxLayout()
    head.setContentsMargins(0, 0, 0, 0)
    head.setSpacing(10)
    kind = _label(
        type_label(record["maintenance_type"]),
        parent=card,
        size=15,
        weight=QFont.Weight.Bold,
    )
    kind.setObjectName("maintenanceRecordType")
    head.addWidget(kind, 1)
    chip = TableChip(
        _status_text(record["status"]),
        tone=_status_tone(record["status"]),
        parent=card,
    )
    head.addWidget(chip, 0, Qt.AlignmentFlag.AlignTop)
    column.addLayout(head)

    when = f"{record['start_date']:%d %b %Y}"
    if record["end_date"]:
        when += f" → {record['end_date']:%d %b %Y}"
    amount = (
        pesos(record["cost"])
        if record["cost"] is not None
        else (
            f"est. {pesos(record['estimate_cost'])}"
            if record["estimate_cost"] is not None
            else "no cost yet"
        )
    )
    meta = _label(f"{when} · {amount}", parent=card, size=13, colour=MUTED)
    meta.setObjectName("maintenanceRecordMeta")
    meta.setWordWrap(True)
    column.addWidget(meta)

    description = _label(
        record["description"], parent=card, size=14, colour=INK
    )
    description.setObjectName("maintenanceRecordBody")
    description.setWordWrap(True)
    column.addWidget(description)

    if record["shop"]:
        shop = _label(
            f"Shop: {record['shop']}", parent=card, size=13, colour=MUTED
        )
        shop.setObjectName("maintenanceRecordDue")
        column.addWidget(shop)
    if record["next_due_date"] or record["next_due_odometer"]:
        bits = []
        if record["next_due_date"]:
            bits.append(f"{record['next_due_date']:%d %b %Y}")
        if record["next_due_odometer"]:
            bits.append(f"{record['next_due_odometer']:,} km")
        due = _label(
            "Next due: " + " / ".join(bits),
            parent=card,
            size=13,
            colour=MUTED,
        )
        due.setObjectName("maintenanceRecordDue")
        due.setWordWrap(True)
        column.addWidget(due)
    return card


# -- Log maintenance ------------------------------------------------------


class RecordPanel(_Sheet):
    """Log a shop stay. Overlapping bookings warn, never block."""

    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(self, context, parent=None) -> None:
        super().__init__(
            WIDE_WIDTH,
            "Log maintenance",
            "Put a vehicle in the shop.",
            "Block the dates so no new booking can land on this vehicle.",
            eyebrow_pill=False,
            large_title=True,
            parent=parent,
        )
        self._context = context
        self._error = self._error_label()

        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(24)
        left = QWidget(self.content_host)
        left.setObjectName("maintenanceColumn")
        form = QVBoxLayout(left)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(16)
        right = QWidget(self.content_host)
        right.setObjectName("maintenanceRightColumn")
        right.setFixedWidth(280)
        side = QVBoxLayout(right)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(16)

        form.addWidget(_section("Vehicle", left))
        self.vehicle_combo = QComboBox(left)
        self.vehicle_combo.setObjectName("warmInput")
        self.vehicle_combo.setMinimumHeight(40)
        self._vehicles = _vehicle_options(context)
        for _vid, label in self._vehicles:
            self.vehicle_combo.addItem(label)
        form.addWidget(
            _field("Vehicle", self.vehicle_combo, parent=left)
        )

        type_status = QHBoxLayout()
        type_status.setContentsMargins(0, 0, 0, 0)
        type_status.setSpacing(14)
        self.type_combo = QComboBox(left)
        self.type_combo.setObjectName("warmInput")
        self.type_combo.setMinimumHeight(40)
        self.type_combo.addItems([TYPE_LABELS[t] for t in TYPES])
        type_status.addWidget(
            _field("Type", self.type_combo, parent=left), 1
        )
        self.status_combo = QComboBox(left)
        self.status_combo.setObjectName("warmInput")
        self.status_combo.setMinimumHeight(40)
        self.status_combo.addItems(["Scheduled", "In Progress"])
        type_status.addWidget(
            _field("Status", self.status_combo, parent=left), 1
        )
        form.addLayout(type_status)

        form.addWidget(_section("Schedule", left))
        schedule = QHBoxLayout()
        schedule.setContentsMargins(0, 0, 0, 0)
        schedule.setSpacing(14)
        self.start_edit = _clock_input(QDateEdit(left))
        self.start_edit.setDate(QDate.currentDate())
        schedule.addWidget(
            _field("Start", self.start_edit, parent=left), 1
        )

        end_host = QWidget(left)
        end_host.setObjectName("maintenanceFieldHost")
        end_column = QVBoxLayout(end_host)
        end_column.setContentsMargins(0, 0, 0, 0)
        end_column.setSpacing(6)
        end_head = QHBoxLayout()
        end_head.setContentsMargins(0, 0, 0, 0)
        end_head.setSpacing(5)
        end_head.addWidget(
            _label(
                "End",
                parent=end_host,
                size=13,
                weight=QFont.Weight.DemiBold,
            )
        )
        end_head.addStretch(1)
        end_column.addLayout(end_head)

        self._end_stack_host = QWidget(end_host)
        self._end_stack_host.setObjectName("maintenanceFieldHost")
        self._end_stack = QStackedLayout(self._end_stack_host)
        self._end_stack.setContentsMargins(0, 0, 0, 0)
        self._end_stack.setStackingMode(
            QStackedLayout.StackingMode.StackOne
        )
        self.end_edit = _clock_input(QDateEdit(self._end_stack_host))
        self.end_edit.setDate(QDate.currentDate().addDays(3))
        self.end_edit.setEnabled(False)
        self._end_stack.addWidget(self.end_edit)

        self.end_placeholder = QLabel("No end date yet", self._end_stack_host)
        self.end_placeholder.setObjectName("maintenanceEndPlaceholder")
        self.end_placeholder.setFixedHeight(40)
        self.end_placeholder.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._end_stack.addWidget(self.end_placeholder)
        self._end_stack.setCurrentWidget(self.end_placeholder)
        end_column.addWidget(self._end_stack_host)
        schedule.addWidget(end_host, 1)
        form.addLayout(schedule)

        self.open_check = _WarmCheck("Open-ended", left)
        self.open_check.setChecked(True)
        open_row = QHBoxLayout()
        open_row.setContentsMargins(0, 0, 0, 0)
        open_row.setSpacing(8)
        open_row.addWidget(
            self.open_check, 0, Qt.AlignmentFlag.AlignVCenter
        )
        open_row.addWidget(
            _label(
                "· set the end date once the shop confirms",
                parent=left,
                size=12,
                colour=MUTED,
            ),
            1,
        )
        form.addLayout(open_row)

        form.addWidget(_section("Cost and odometer", left))
        costs = QHBoxLayout()
        costs.setContentsMargins(0, 0, 0, 0)
        costs.setSpacing(14)
        self.estimate_edit = QLineEdit(left)
        self.estimate_edit.setObjectName("warmInput")
        self.estimate_edit.setMinimumHeight(40)
        self.estimate_edit.setPlaceholderText("0.00")
        costs.addWidget(
            _field(
                "Estimate (PHP)",
                self.estimate_edit,
                optional=True,
                parent=left,
            ),
            1,
        )
        self.odometer_edit = QSpinBox(left)
        self.odometer_edit.setObjectName("warmInput")
        self.odometer_edit.setMinimumHeight(40)
        self.odometer_edit.setRange(0, 9_999_999)
        self.odometer_edit.setSpecialValueText("e.g. 48,200")
        costs.addWidget(
            _field(
                "Odometer (km)",
                self.odometer_edit,
                optional=True,
                parent=left,
            ),
            1,
        )
        form.addLayout(costs)

        form.addWidget(_section("Details", left))
        details = QHBoxLayout()
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(14)
        self.shop_edit = QLineEdit(left)
        self.shop_edit.setObjectName("warmInput")
        self.shop_edit.setMinimumHeight(40)
        self.shop_edit.setPlaceholderText("Who is doing the work?")
        details.addWidget(
            _field(
                "Shop or mechanic",
                self.shop_edit,
                optional=True,
                parent=left,
            ),
            1,
        )
        self.booking_edit = QLineEdit(left)
        self.booking_edit.setObjectName("warmInput")
        self.booking_edit.setMinimumHeight(40)
        self.booking_edit.setPlaceholderText("e.g. 343")
        details.addWidget(
            _field(
                "Booking #",
                self.booking_edit,
                suffix="· damage repairs",
                parent=left,
            ),
            1,
        )
        form.addLayout(details)

        self.description_edit = QLineEdit(left)
        self.description_edit.setObjectName("warmInput")
        self.description_edit.setMinimumHeight(40)
        self.description_edit.setPlaceholderText("What is being done?")
        form.addWidget(
            _field("Work to be done", self.description_edit, parent=left)
        )
        form.addStretch(1)

        # -- right column: live summary + amber overlap notice ----------
        summary = QFrame(right)
        summary.setObjectName("maintenanceSummary")
        self.summary_panel = summary
        summary_column = QVBoxLayout(summary)
        summary_column.setContentsMargins(16, 16, 16, 14)
        summary_column.setSpacing(6)
        summary_column.addWidget(
            _section("Summary", summary)
        )
        self.summary_vehicle = _label(
            self.vehicle_combo.currentText() or "No vehicle available",
            parent=summary,
            size=17,
            weight=QFont.Weight.Bold,
        )
        self.summary_vehicle.setObjectName("maintenanceSummaryTitle")
        self.summary_vehicle.setWordWrap(True)
        summary_column.addWidget(self.summary_vehicle)
        self.summary_values: dict[str, QLabel] = {}
        summary_keys = ("Type", "Status", "Starts", "Ends", "Estimate")
        for index, key in enumerate(summary_keys):
            if index:
                rule = QFrame(summary)
                rule.setObjectName("maintenanceSummaryRule")
                rule.setFixedHeight(1)
                summary_column.addWidget(rule)
            row = QWidget(summary)
            row.setObjectName("maintenanceSummaryRow")
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 7, 0, 7)
            line.setSpacing(10)
            line.addWidget(
                _label(key, parent=row, size=13, colour=SUBTLE), 1
            )
            value = _label(
                "—",
                parent=row,
                size=13,
                weight=QFont.Weight.DemiBold,
            )
            value.setObjectName("maintenanceSummaryValue")
            value.setAlignment(
                Qt.AlignmentFlag.AlignRight
                | Qt.AlignmentFlag.AlignVCenter
            )
            line.addWidget(value)
            summary_column.addWidget(row)
            self.summary_values[key.lower()] = value
        side.addWidget(summary)

        self.clash_panel = QFrame(right)
        self.clash_panel.setObjectName("maintenanceNotice")
        notice = QVBoxLayout(self.clash_panel)
        notice.setContentsMargins(16, 14, 16, 14)
        notice.setSpacing(6)
        self.clash_title = _label(
            "Booking overlap",
            parent=self.clash_panel,
            size=14,
            weight=QFont.Weight.Bold,
            colour="#5F4413",
        )
        self.clash_title.setObjectName("maintenanceNoticeTitle")
        notice.addWidget(self.clash_title)
        self.clash_note = _label(
            "",
            parent=self.clash_panel,
            size=13,
            colour="#5F4413",
        )
        self.clash_note.setObjectName("maintenanceNoticeText")
        self.clash_note.setWordWrap(True)
        notice.addWidget(self.clash_note)
        self.clash_panel.setVisible(False)
        side.addWidget(self.clash_panel)
        side.addStretch(1)

        columns.addWidget(left, 1)
        columns.addWidget(right, 0, Qt.AlignmentFlag.AlignTop)
        self.body.addLayout(columns)

        back, go = self.add_back(), self.add_primary("Log it")
        back.clicked.connect(self.cancelled.emit)
        go.clicked.connect(self._on_go)

        # Keep the old advisory wiring, then refresh the live summary.
        for widget in (
            self.vehicle_combo,
            self.type_combo,
            self.status_combo,
        ):
            widget.currentIndexChanged.connect(self._refresh_clashes)
            widget.currentIndexChanged.connect(self._refresh_summary)
        for widget in (self.start_edit, self.end_edit):
            widget.dateChanged.connect(self._refresh_clashes)
            widget.dateChanged.connect(self._refresh_summary)
        self.open_check.toggled.connect(self._on_open_toggled)
        self.estimate_edit.textChanged.connect(self._refresh_summary)
        self._refresh_summary()
        self._refresh_clashes()

    # -- selection / validation ---------------------------------------

    def _selection(self) -> tuple[int | None, date, date | None]:
        vid = None
        if self._vehicles:
            vid = self._vehicles[self.vehicle_combo.currentIndex()][0]
        start = self.start_edit.date().toPython()
        end = (
            None
            if self.open_check.isChecked()
            else self.end_edit.date().toPython()
        )
        return vid, start, end

    def _on_open_toggled(self, on: bool) -> None:
        self.end_edit.setEnabled(not on)
        self._end_stack.setCurrentWidget(
            self.end_placeholder if on else self.end_edit
        )
        self._refresh_clashes()
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        self.summary_vehicle.setText(
            self.vehicle_combo.currentText()
            if self._vehicles
            else "No vehicle available"
        )
        self.summary_values["type"].setText(
            self.type_combo.currentText() or "—"
        )
        self.summary_values["status"].setText(
            self.status_combo.currentText() or "—"
        )
        self.summary_values["starts"].setText(
            self.start_edit.date().toString("dd MMM yyyy")
        )
        self.summary_values["ends"].setText(
            "Open-ended"
            if self.open_check.isChecked()
            else self.end_edit.date().toString("dd MMM yyyy")
        )
        raw = self.estimate_edit.text().strip()
        if not raw:
            estimate_text = "Not set"
        else:
            try:
                estimate_text = pesos(money(raw, field="estimate_cost"))
            except Exception:  # noqa: BLE001 - summary mirrors the raw text
                estimate_text = raw
        self.summary_values["estimate"].setText(estimate_text)

    def _refresh_clashes(self) -> None:
        vid, start, end = self._selection()
        if vid is None:
            self.clash_note.setText("")
            self.clash_panel.setVisible(False)
            return
        try:
            with self._context.reading() as session:
                vehicle = session.get(Vehicle, vid)
                clashes = maintenance_service.booking_conflicts(
                    session, vehicle, start, end
                )
                # Read the ids here: the session closes with the block and
                # the rows detach, so nothing ORM may be touched below.
                refs = ", ".join(f"#{b.booking_id}" for b in clashes)
        except Exception:  # noqa: BLE001 - advisory preview only
            self.clash_note.setText("")
            self.clash_panel.setVisible(False)
            return
        if refs:
            plural = "booking" if len(clashes) == 1 else "bookings"
            self.clash_title.setText(f"Overlaps {plural}")
            self.clash_note.setText(
                f"{refs}: reassign it first, or log anyway. The handover "
                "will refuse until it is moved."
            )
            self.clash_panel.setVisible(True)
        else:
            self.clash_title.setText("Booking overlap")
            self.clash_note.setText("No booking clash in this window.")
            self.clash_panel.setVisible(False)

    def _on_go(self) -> None:
        vid, start, end = self._selection()
        if vid is None:
            self._error.setText("Pick a vehicle first.")
            self._error.setVisible(True)
            return
        if not self.description_edit.text().strip():
            self._error.setText("Describe the work first.")
            self._error.setVisible(True)
            return
        estimate = None
        raw_estimate = self.estimate_edit.text().strip()
        if raw_estimate:
            try:
                estimate = money(raw_estimate, field="estimate_cost")
            except Exception:  # noqa: BLE001 - inline below
                self._error.setText("Estimate must be a number.")
                self._error.setVisible(True)
                return
            if estimate < ZERO:
                self._error.setText("Estimate cannot be negative.")
                self._error.setVisible(True)
                return
        booking_id = None
        raw_booking = self.booking_edit.text().strip().lstrip("#")
        if raw_booking:
            if not raw_booking.isdigit():
                self._error.setText("Booking must be a number, like #12.")
                self._error.setVisible(True)
                return
            booking_id = int(raw_booking)
        self._error.setVisible(False)
        self.submitted.emit(
            {
                "vehicle_id": vid,
                "maintenance_type": TYPES[self.type_combo.currentIndex()],
                "status": (
                    "in_progress"
                    if self.status_combo.currentIndex() == 1
                    else "scheduled"
                ),
                "start_date": start,
                "end_date": end,
                "estimate_cost": estimate,
                "odometer": self.odometer_edit.value() or None,
                "shop": self.shop_edit.text().strip(),
                "booking_id": booking_id,
                "description": self.description_edit.text().strip(),
            }
        )


# -- Mark completed -------------------------------------------------------


class CompletePanel(_Sheet):
    """The one button staff mustn't forget: close the job, release the car."""

    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(self, description: str, estimate, parent=None) -> None:
        subtitle = description
        if estimate is not None and estimate > ZERO:
            subtitle = f"{description} · Quoted {pesos(estimate)}"
        super().__init__(
            NARROW_WIDTH,
            "Mark completed",
            "Close out the job",
            subtitle,
            parent=parent,
        )
        self._error = self._error_label()

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(14)
        self.end_edit = _clock_input(QDateEdit(self.content_host))
        self.end_edit.setDate(QDate.currentDate())
        top.addWidget(
            _field("Finished", self.end_edit, parent=self.content_host), 1
        )
        self.cost_edit = QLineEdit(self.content_host)
        self.cost_edit.setObjectName("warmInput")
        self.cost_edit.setMinimumHeight(40)
        if estimate is not None:
            self.cost_edit.setText(f"{estimate:,.2f}")
        self.cost_edit.setPlaceholderText("0.00")
        top.addWidget(
            _field(
                "Final cost (PHP)",
                self.cost_edit,
                parent=self.content_host,
            ),
            1,
        )
        self.body.addLayout(top)

        self.odometer_edit = QSpinBox(self.content_host)
        self.odometer_edit.setObjectName("warmInput")
        self.odometer_edit.setMinimumHeight(40)
        self.odometer_edit.setRange(0, 9_999_999)
        self.odometer_edit.setSpecialValueText("e.g. 48,200")
        self.body.addWidget(
            _field(
                "Odometer (km)",
                self.odometer_edit,
                optional=True,
                parent=self.content_host,
            )
        )

        self.next_panel = QFrame(self.content_host)
        self.next_panel.setObjectName("maintenanceNextPanel")
        next_column = QVBoxLayout(self.next_panel)
        next_column.setContentsMargins(16, 15, 16, 16)
        next_column.setSpacing(12)
        next_column.addWidget(_section("Next service · optional", self.next_panel))

        checks = QHBoxLayout()
        checks.setContentsMargins(0, 0, 0, 0)
        checks.setSpacing(24)
        self.due_date_check = _WarmCheck("Next due date", self.next_panel)
        self.due_km_check = _WarmCheck(
            "Next due odometer", self.next_panel
        )
        checks.addWidget(
            self.due_date_check, 1, Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
        )
        checks.addWidget(
            self.due_km_check, 1, Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
        )
        next_column.addLayout(checks)

        inputs = QHBoxLayout()
        inputs.setContentsMargins(0, 0, 0, 0)
        inputs.setSpacing(14)
        self.due_date_edit = QDateEdit(self.next_panel)
        self.due_date_edit.setCalendarPopup(True)
        self.due_date_edit.setDisplayFormat("dd MMM yyyy")
        self.due_date_edit.setDate(QDate.currentDate().addDays(90))
        self.due_date_edit.setEnabled(False)
        self.due_date_edit.setObjectName("nextServiceInput")
        self.due_date_edit.setMinimumHeight(40)
        inputs.addWidget(self.due_date_edit, 1)
        self.due_km_edit = QSpinBox(self.next_panel)
        self.due_km_edit.setObjectName("nextServiceInput")
        self.due_km_edit.setMinimumHeight(40)
        self.due_km_edit.setRange(0, 9_999_999)
        self.due_km_edit.setSuffix(" km")
        self.due_km_edit.setEnabled(False)
        inputs.addWidget(self.due_km_edit, 1)
        next_column.addLayout(inputs)
        self.body.addWidget(self.next_panel)

        self.due_date_check.toggled.connect(self.due_date_edit.setEnabled)
        self.due_km_check.toggled.connect(self.due_km_edit.setEnabled)

        back, go = self.add_back(), self.add_primary("Mark completed")
        back.clicked.connect(self.cancelled.emit)
        go.clicked.connect(self._on_go)

    def _on_go(self) -> None:
        try:
            cost = money(self.cost_edit.text(), field="cost")
        except Exception as exc:  # noqa: BLE001
            self._error.setText(f"Final cost must be a number: {exc}")
            self._error.setVisible(True)
            return
        if cost < ZERO:
            self._error.setText("Final cost cannot be negative.")
            self._error.setVisible(True)
            return
        self._error.setVisible(False)
        self.submitted.emit(
            {
                "end_date": self.end_edit.date().toPython(),
                "cost": cost,
                "odometer": self.odometer_edit.value() or None,
                "next_due_date": (
                    self.due_date_edit.date().toPython()
                    if self.due_date_check.isChecked()
                    else None
                ),
                "next_due_odometer": (
                    self.due_km_edit.value()
                    if self.due_km_check.isChecked()
                    else None
                ),
            }
        )


# -- Cancel record --------------------------------------------------------


class CancelPanel(_Sheet):
    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(
            NARROW_WIDTH,
            "Cancel record",
            "Cancel record",
            "The row stays on the log.",
            parent=parent,
        )
        self._error = self._error_label()
        self.reason_edit = QLineEdit(self.content_host)
        self.reason_edit.setObjectName("warmInput")
        self.reason_edit.setMinimumHeight(40)
        self.reason_edit.setPlaceholderText("Why is this being cancelled?")
        self.body.addWidget(
            _field(
                "Reason",
                self.reason_edit,
                parent=self.content_host,
            )
        )
        self.body.addStretch(1)
        back, go = self.add_back(), self.add_primary("Continue")
        back.clicked.connect(self.cancelled.emit)
        go.clicked.connect(self._on_go)

    def _on_go(self) -> None:
        if not self.reason_edit.text().strip():
            self._error.setText("A reason is required to cancel.")
            self._error.setVisible(True)
            return
        self._error.setVisible(False)
        self.submitted.emit({"reason": self.reason_edit.text().strip()})


# -- History --------------------------------------------------------------


class HistoryPanel(_Sheet):
    closed = Signal()

    def __init__(self, context, vehicle_id: int, parent=None) -> None:
        from app.services.maintenance_service import history_for

        self._context = context
        with context.reading() as session:
            vehicle = session.get(Vehicle, vehicle_id)
            if vehicle is None:
                super().__init__(
                    NARROW_WIDTH,
                    "History",
                    "Vehicle not found",
                    "That vehicle no longer exists.",
                    parent=parent,
                )
                self.records: list = []
                self.spent = ZERO
            else:
                super().__init__(
                    NARROW_WIDTH,
                    "History",
                    f"{vehicle.plate_number} {vehicle.make} {vehicle.model}",
                    "Every maintenance record for this vehicle",
                    parent=parent,
                )
                records, spent = history_for(session, vehicle)
                self.records = [_record_snapshot(r) for r in records]
                self.spent = spent

        if vehicle is None:
            self.close_button = self.add_primary("Close")
            self.close_button.clicked.connect(self.closed.emit)
            return

        tiles = QHBoxLayout()
        tiles.setContentsMargins(0, 0, 0, 0)
        tiles.setSpacing(12)
        self.records_tile = _metric_tile(
            "Records", str(len(self.records)), self.content_host
        )
        self.spent_tile = _metric_tile(
            "Spent", pesos(self.spent), self.content_host
        )
        tiles.addWidget(self.records_tile, 1)
        tiles.addWidget(self.spent_tile, 1)
        self.body.addLayout(tiles)

        self.cards: list[QFrame] = []
        for record in self.records:
            card = _record_card(record, parent=self.content_host)
            self.body.addWidget(card)
            self.cards.append(card)

        self.close_button = self.add_primary("Close")
        self.close_button.clicked.connect(self.closed.emit)
