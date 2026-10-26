"""Bookings action panels: warm 540px sheets inside the frosted overlay.

Every panel keeps the field/signal contract the page and the tests use, but
the presentation now matches the counter's settled language: eyebrow, title,
booking subtitle, metric tiles, chip selectors, cream notices, and a sticky
footer with one primary action. Services still own every rule.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from PySide6.QtCore import QDate, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.models import Booking
from app.services import booking_service, payment_service, settings_service
from app.services.booking_service import FUEL_LEVELS
from app.staff import theme
from app.staff.cards import Caption
from app.staff.feedback import describe
from app.staff.tables import TableChip
from app.staff.ui_kit import (
    WARM_INK,
    WARM_MUTED,
    WARM_SUBTITLE,
    BookingModal,
    ChipGroup,
    FormField,
    MetricTile,
    NoticeBox,
    SectionTitle,
    SummaryBox,
)
from app.staff.widgets import toast
from app.utils.money import ZERO, money, pesos


# -- small local presentation helpers --------------------------------------


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


def _divider(parent: QWidget) -> QFrame:
    rule = QFrame(parent)
    rule.setFixedHeight(1)
    rule.setStyleSheet("background: #EDE6DA; border: none;")
    return rule


def _status_tone(status: str | None) -> str:
    key = (status or "").strip().lower()
    if key in ("confirmed", "ongoing", "paid"):
        return "ok"
    if key == "pending":
        return "warn"
    if key == "cancelled":
        return "danger"
    return "neutral"


def _modal_subtitle(
    booking_id: int | None, customer: str = "", vehicle: str = ""
) -> str:
    parts: list[str] = []
    if booking_id is not None:
        parts.append(f"#{booking_id}")
    if customer:
        parts.append(customer)
    if vehicle:
        parts.append(vehicle)
    return " · ".join(parts)


def _friendly(error: BaseException) -> str:
    message, _field, _kind = describe(error)
    return message


def _labelled_field(
    label: str,
    widget: QWidget,
    *,
    suffix: str | None = None,
    optional: bool = False,
    parent: QWidget | None = None,
) -> QWidget:
    """A form label above a control, optionally with a muted suffix."""
    if suffix is None:
        return FormField(label, widget, optional=optional, parent=parent)
    host = QWidget(parent)
    host.setObjectName("warmFieldHost")
    column = QVBoxLayout(host)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(6)
    head = QHBoxLayout()
    head.setContentsMargins(0, 0, 0, 0)
    head.setSpacing(5)
    name = _label(
        label,
        parent=host,
        size=12,
        weight=QFont.Weight.DemiBold,
        colour=WARM_INK,
    )
    head.addWidget(name)
    hint = _label(
        suffix, parent=host, size=12, colour=WARM_SUBTITLE
    )
    hint.setObjectName("warmSuffix")
    head.addWidget(hint)
    if optional:
        head.addWidget(
            _label("· optional", parent=host, size=12, colour=WARM_SUBTITLE)
        )
    head.addStretch(1)
    column.addLayout(head)
    column.addWidget(widget)
    host.hint_label = hint
    return host


def _fact_row(
    layout: QVBoxLayout,
    parent: QWidget,
    label: str,
    value: str,
) -> QWidget:
    host = QWidget(parent)
    host.setObjectName("warmFactRow")
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 5, 0, 5)
    row.setSpacing(10)
    left = _label(label, parent=host, size=12, colour=WARM_MUTED)
    right = _label(
        value,
        parent=host,
        size=12,
        weight=QFont.Weight.DemiBold,
        colour=WARM_INK,
    )
    right.setWordWrap(True)
    right.setAlignment(
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    )
    row.addWidget(left, 0)
    row.addWidget(right, 1)
    layout.addWidget(host)
    layout.addWidget(_divider(host))
    return host


class _ToggleChipRow(QWidget):
    """Multi-select chips (damage presets) built from the shared chip style."""

    def __init__(
        self,
        labels: tuple[str, ...],
        *,
        height: int = 32,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.buttons: dict[str, QPushButton] = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for label in labels:
            button = QPushButton(label, self)
            button.setObjectName("choiceChip")
            button.setProperty("variant", "choice")
            button.setCheckable(True)
            button.setFixedHeight(height)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(button)
            self.buttons[label] = button
        row.addStretch(1)


def _fuel_chips(
    parent: QWidget, *, initial: str = "full"
) -> tuple[QComboBox, ChipGroup]:
    options = (
        ("empty", "Empty"),
        ("quarter", "¼"),
        ("half", "½"),
        ("three_quarter", "¾"),
        ("full", "Full"),
    )
    combo = QComboBox(parent)
    combo.setObjectName("warmInput")
    combo.addItems(list(FUEL_LEVELS))
    combo.setCurrentText(initial)
    combo.setVisible(False)
    chips = ChipGroup(
        options,
        variant="fuel",
        expanding=True,
        height=34,
        spacing=4,
        parent=parent,
    )
    chips.select(initial)
    chips.changed.connect(combo.setCurrentText)
    combo.currentTextChanged.connect(chips.select)
    return combo, chips


def _method_chips(parent: QWidget) -> tuple[QComboBox, ChipGroup]:
    options = (("cash", "Cash"), ("gcash", "GCash"), ("card", "Card"))
    combo = QComboBox(parent)
    combo.setObjectName("warmInput")
    combo.addItems(list(payment_service.METHODS))
    combo.setVisible(False)
    chips = ChipGroup(
        options, variant="choice", height=32, spacing=8, parent=parent
    )
    chips.select(combo.currentText())
    chips.changed.connect(combo.setCurrentText)
    return combo, chips


# -- booking details -------------------------------------------------------


class BookingDetailsPanel(BookingModal):
    closed = Signal()

    def __init__(self, context, booking_id: int, parent=None) -> None:
        super().__init__(
            "BOOKING",
            "Booking details",
            f"Booking #{booking_id}",
            parent=parent,
        )
        self._context = context
        self._booking_id = booking_id
        self._error = self.error_label
        self.body.addWidget(self.error_label)
        self._sections: list[str] = []
        self._rows: list[QLabel] = []
        self.status_chip: TableChip | None = None
        self.total_tile: MetricTile | None = None
        self.paid_tile: MetricTile | None = None
        self.owed_tile: MetricTile | None = None
        self.meta_label: QLabel | None = None

        self.print_button = self.add_footer_button("Print receipt")
        self.close_button = self.add_primary("Close")
        self.print_button.clicked.connect(self._on_print)
        self.close_button.clicked.connect(self.closed.emit)
        self._load()

    # -- building ---------------------------------------------------------

    def _section(self, title: str, lines: list[str]) -> None:
        self._sections.append(title)
        self.body.addWidget(SectionTitle(title, parent=self.content_host))
        for line in lines:
            label = _label(line, parent=self.content_host, size=13)
            label.setWordWrap(True)
            self.body.addWidget(label)
            self._rows.append(label)

    def _load(self) -> None:
        with self._context.reading() as session:
            booking = session.get(Booking, self._booking_id)
            if booking is None:
                self.set_error("That booking no longer exists.")
                self.print_button.setEnabled(False)
                return

            user = booking.user
            vehicle = booking.vehicle
            balance = payment_service.booking_balance(session, booking)

            status = booking.status
            channel = {"walk_in": "Walk-in", "online": "Online"}.get(
                booking.channel or "", "—"
            )
            days = max(1, (booking.end_date - booking.start_date).days)
            reference = booking.reference_code or ""
            pickup = booking.pickup_dt
            give_back = booking.return_dt
            pickup_branch = booking.pickup_branch
            return_branch = booking.return_branch
            payments = [
                f"{pesos(p.amount)} via {p.method} ({p.status})"
                for p in booking.payments
            ] or ["No payments recorded."]
            inspections = [
                f"{r.inspection_type} · {r.mileage_reading:,} km · "
                f"fuel {r.fuel_level} · {r.damage_notes or 'no damage'}"
                for r in booking.inspections
            ] or ["No inspections yet."]
            penalties = [
                f"{p.penalty_type}: {pesos(p.amount)} · {p.description}"
                for p in booking.penalties
            ] or ["No penalties."]

            self.set_subtitle(f"#{booking.booking_id} · {user.full_name}")
            self.meta_label = _label(
                f"{booking.start_date:%d %b %Y} → "
                f"{booking.end_date:%d %b %Y} · {days} days · {channel}",
                parent=self.content_host,
                size=13,
                colour=WARM_MUTED,
            )
            self.meta_label.setWordWrap(True)
            self.body.addWidget(self.meta_label)

            self.status_chip = TableChip(
                theme.status_label(status),
                tone=_status_tone(status),
                parent=self.content_host,
            )
            self.body.addWidget(
                self.status_chip, 0, Qt.AlignmentFlag.AlignLeft
            )

            tiles = QHBoxLayout()
            tiles.setContentsMargins(0, 0, 0, 0)
            tiles.setSpacing(9)
            self.total_tile = MetricTile(
                "Total due",
                pesos(balance.total_due),
                parent=self.content_host,
            )
            self.paid_tile = MetricTile(
                "Paid",
                pesos(balance.amount_paid),
                parent=self.content_host,
            )
            self.owed_tile = MetricTile(
                "Owed",
                pesos(balance.balance),
                tone="owed" if balance.balance > ZERO else "neutral",
                parent=self.content_host,
            )
            tiles.addWidget(self.total_tile)
            tiles.addWidget(self.paid_tile)
            tiles.addWidget(self.owed_tile)
            self.body.addLayout(tiles)

            columns = QHBoxLayout()
            columns.setContentsMargins(0, 0, 0, 0)
            columns.setSpacing(18)
            customer_column = QWidget(self.content_host)
            customer_column.setObjectName("warmDetailColumn")
            customer_layout = QVBoxLayout(customer_column)
            customer_layout.setContentsMargins(0, 0, 0, 0)
            customer_layout.setSpacing(0)
            customer_layout.addWidget(
                SectionTitle("Customer", parent=customer_column)
            )
            _fact_row(
                customer_layout, customer_column, "Name", user.full_name
            )
            _fact_row(
                customer_layout, customer_column, "Email", user.email or "—"
            )
            _fact_row(
                customer_layout, customer_column, "Phone", user.phone or "—"
            )
            _fact_row(
                customer_layout,
                customer_column,
                "Licence",
                user.license_number or "—",
            )

            vehicle_column = QWidget(self.content_host)
            vehicle_column.setObjectName("warmDetailColumn")
            vehicle_layout = QVBoxLayout(vehicle_column)
            vehicle_layout.setContentsMargins(0, 0, 0, 0)
            vehicle_layout.setSpacing(0)
            vehicle_layout.addWidget(
                SectionTitle("Vehicle", parent=vehicle_column)
            )
            _fact_row(
                vehicle_layout,
                vehicle_column,
                "Vehicle",
                f"{vehicle.make} {vehicle.model} ({vehicle.year})",
            )
            _fact_row(
                vehicle_layout,
                vehicle_column,
                "Plate",
                vehicle.plate_number or "—",
            )
            _fact_row(
                vehicle_layout,
                vehicle_column,
                "State",
                theme.status_label(vehicle.status),
            )
            columns.addWidget(customer_column, 1)
            columns.addWidget(vehicle_column, 1)
            self.body.addLayout(columns)

            self.deposit_notice = NoticeBox(
                f"Deposit held {pesos(balance.deposit_held)} · refundable "
                "when the vehicle comes back in the same condition.",
                tone="cream",
                parent=self.content_host,
            )
            self.body.addWidget(self.deposit_notice)

            booking_lines: list[str] = []
            if reference:
                booking_lines.append(f"Reference {reference}")
            if pickup is not None and give_back is not None:
                booking_lines.append(
                    f"Pickup {pickup:%d %b %Y %H:%M}"
                )
                booking_lines.append(
                    f"Return {give_back:%d %b %Y %H:%M}"
                )
            else:
                booking_lines.append(
                    f"{booking.start_date:%d %b %Y} → "
                    f"{booking.end_date:%d %b %Y}"
                )
            if pickup_branch or return_branch:
                booking_lines.append(
                    f"Pickup {pickup_branch.name if pickup_branch else '-'} "
                    f"· Return "
                    f"{return_branch.name if return_branch else '-'}"
                )
            self._section("Booking", booking_lines)

            self._section(
                "Money",
                [
                    f"Total {pesos(balance.total_due)} · "
                    f"paid {pesos(balance.amount_paid)} · "
                    f"owed {pesos(balance.balance)}",
                    f"Deposit held {pesos(balance.deposit_held)}",
                    *payments,
                ],
            )
            self._section("Inspections", inspections)
            self._section("Penalties", penalties)

    # -- receipt ----------------------------------------------------------

    def _on_print(self) -> None:
        from app.services.receipt_service import generate_receipt_pdf

        try:
            with self._context.session() as session:
                booking = session.get(Booking, self._booking_id)
                path = generate_receipt_pdf(session, booking)
                auto_open = settings_service.get_bool(
                    session, "receipt_auto_open"
                )
        except Exception as exc:  # noqa: BLE001
            self.set_error(_friendly(exc))
            return
        if auto_open:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            toast(self, f"Receipt saved: {path}")


# -- cancel ----------------------------------------------------------------


class ReasonPanel(BookingModal):
    submitted = Signal(dict)
    cancelled = Signal()

    _OPTIONS = (
        ("customer_request", "Customer\nrequest"),
        ("no_show", "No-show"),
        ("vehicle_unavailable", "Vehicle\nunavailable"),
        ("payment_issue", "Payment\nissue"),
        ("other", "Other"),
    )

    def __init__(
        self,
        parent=None,
        *,
        booking_id: int | None = None,
        customer: str = "",
        vehicle: str = "",
        paid_so_far: Decimal = ZERO,
        refund_due: Decimal = ZERO,
    ) -> None:
        subtitle = _modal_subtitle(booking_id, customer, vehicle)
        super().__init__(
            "CANCEL BOOKING",
            "Cancel booking",
            subtitle or "Why is this being cancelled?",
            parent=parent,
        )
        self._error = self.error_label
        self.body.addWidget(self.error_label)
        self.paid_so_far = money(paid_so_far, field="paid_so_far")
        self.refund_due = money(refund_due, field="refund_due")

        self.reason_chips = ChipGroup(
            self._OPTIONS,
            variant="reason",
            height=48,
            spacing=8,
            parent=self.content_host,
        )
        # A reason chip is a deliberate choice; no chip is preselected so the
        # old free-text-only contract remains available.
        group = self.reason_chips._group
        group.setExclusive(False)
        for button in self.reason_chips.buttons.values():
            button.setChecked(False)
        group.setExclusive(True)
        self.reason_chips._selected = ""
        self.body.addWidget(self.reason_chips)

        self.reason_edit = QLineEdit(self.content_host)
        self.reason_edit.setObjectName("warmInput")
        self.reason_edit.setPlaceholderText(
            "Add a short note (required for Other)"
        )
        self.body.addWidget(
            FormField(
                "Details",
                self.reason_edit,
                optional=True,
                parent=self.content_host,
            )
        )

        self.summary = SummaryBox(self.content_host)
        self.summary.add_row("Paid so far", pesos(self.paid_so_far))
        self.summary.add_row("Refund due", pesos(self.refund_due))
        self.paid_so_far_label = self.summary.rows[0][1]
        self.refund_due_label = self.summary.rows[1][1]
        self.body.addWidget(self.summary)

        self.notice = NoticeBox(
            "The security deposit is returned in full. Rental payments "
            "already taken are not refunded.",
            tone="red",
            parent=self.content_host,
        )
        self.body.addWidget(self.notice)

        self.back_button = self.add_back("Keep booking")
        self.back_button.clicked.connect(self.cancelled.emit)
        self.go_button = self.add_primary("Cancel booking", danger=True)
        self.go_button.clicked.connect(self._on_go)

    def _on_go(self) -> None:
        key = self.reason_chips.selected
        detail = self.reason_edit.text().strip()
        labels = dict(self._OPTIONS)
        if key == "other" and not detail:
            self.set_error("Describe the reason when choosing Other.")
            return
        if key:
            label = labels[key].replace("\n", " ")
            reason = f"{label} — {detail}" if detail else label
        elif detail:
            # Compatibility with the old free-text-only reason field.
            reason = detail
        else:
            self.set_error("Choose a reason or describe why.")
            return
        self.set_error("")
        self.submitted.emit({"reason": reason})


# -- check in ---------------------------------------------------------------


class CheckInPanel(BookingModal):
    submitted = Signal(dict)
    cancelled = Signal()
    collect_requested = Signal()

    _DAMAGE_OPTIONS = ("No damage", "Scratch", "Dent", "Cracked glass")
    _DAMAGE_WORDS = ("Scratch", "Dent", "Cracked glass")

    def __init__(
        self,
        parent=None,
        *,
        current_mileage: int = 0,
        outstanding: Decimal = ZERO,
        booking_id: int | None = None,
        customer: str = "",
        vehicle: str = "",
    ) -> None:
        subtitle = _modal_subtitle(booking_id, customer, vehicle)
        super().__init__(
            "CHECK IN",
            "Release the vehicle",
            subtitle or "Record the odometer, fuel, and damage before handover.",
            parent=parent,
        )
        self._error = self.error_label
        self.body.addWidget(self.error_label)
        self._floor = int(current_mileage or 0)
        self._outstanding = money(outstanding, field="outstanding")

        if self._outstanding > ZERO:
            self.outstanding_notice = NoticeBox(
                f"Outstanding {pesos(self._outstanding)}: record payment "
                "before releasing the vehicle.",
                tone="red",
                parent=self.content_host,
            )
            self.body.addWidget(self.outstanding_notice)

        self.mileage_edit = QSpinBox(self.content_host)
        self.mileage_edit.setObjectName("warmInput")
        self.mileage_edit.setRange(0, 9_999_999)
        self.mileage_edit.setValue(self._floor)
        self.body.addWidget(
            FormField(
                "Odometer (km)", self.mileage_edit, parent=self.content_host
            )
        )

        self.fuel_combo, self.fuel_chips = _fuel_chips(
            self.content_host, initial="full"
        )
        self.body.addWidget(self.fuel_combo)
        self.body.addWidget(
            FormField(
                "Fuel level", self.fuel_chips, parent=self.content_host
            )
        )

        self.damage_chips = _ToggleChipRow(
            self._DAMAGE_OPTIONS, parent=self.content_host
        )
        self.damage_chip_buttons = self.damage_chips.buttons
        for label, button in self.damage_chips.buttons.items():
            button.clicked.connect(
                lambda _checked=False, name=label: self._on_damage_chip(name)
            )
        self.body.addWidget(
            FormField("Damage", self.damage_chips, parent=self.content_host)
        )

        self.damage_edit = QLineEdit(self.content_host)
        self.damage_edit.setObjectName("warmInput")
        self.damage_edit.setPlaceholderText("Scratches, dents… (optional)")
        self.notes_edit = self.damage_edit
        self.body.addWidget(
            FormField(
                "Notes",
                self.damage_edit,
                optional=True,
                parent=self.content_host,
            )
        )

        self.collect_button = QPushButton(
            "Record payment first", self.content_host
        )
        self.collect_button.setObjectName("modalSecondary")
        self.collect_button.setMinimumHeight(36)
        self.collect_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collect_button.setVisible(self._outstanding > ZERO)
        self.collect_button.clicked.connect(self.collect_requested.emit)
        collect_row = QHBoxLayout()
        collect_row.setContentsMargins(0, 0, 0, 0)
        collect_row.addWidget(self.collect_button)
        collect_row.addStretch(1)
        self.body.addLayout(collect_row)

        back = self.add_back()
        back.clicked.connect(self.cancelled.emit)
        self.go_button = self.add_primary("Check in")
        self.go_button.clicked.connect(self._on_go)
        if self._outstanding > ZERO:
            self.go_button.setEnabled(False)

    def _current_damage_tokens(self) -> list[str]:
        return [
            token.strip()
            for token in self.damage_edit.text().split(",")
            if token.strip()
        ]

    def _on_damage_chip(self, label: str) -> None:
        button = self.damage_chips.buttons[label]
        if label == "No damage":
            for name, other in self.damage_chips.buttons.items():
                if name != "No damage":
                    other.setChecked(False)
            if button.isChecked():
                self.damage_edit.clear()
            return
        self.damage_chips.buttons["No damage"].setChecked(False)
        tokens = self._current_damage_tokens()
        words = [t for t in tokens if t in self._DAMAGE_WORDS]
        free = [t for t in tokens if t not in self._DAMAGE_WORDS]
        if button.isChecked():
            if label not in words:
                words.append(label)
        elif label in words:
            words.remove(label)
        ordered = [word for word in self._DAMAGE_WORDS if word in words]
        self.damage_edit.setText(", ".join(ordered + free))

    def _on_go(self) -> None:
        if self._outstanding > ZERO:
            self.set_error(
                "Collect the outstanding balance before releasing the vehicle."
            )
            return
        mileage = int(self.mileage_edit.value())
        if mileage < 0:
            self.set_error("Mileage cannot be negative.")
            return
        if mileage < self._floor:
            self.set_error(
                f"Odometer reading of {mileage:,} km is below the "
                f"{self._floor:,} km on record."
            )
            return
        if self.fuel_combo.currentText() not in FUEL_LEVELS:
            self.set_error("Choose a fuel level.")
            return
        self.set_error("")
        self.submitted.emit(
            {
                "mileage": mileage,
                "fuel_level": self.fuel_combo.currentText(),
                "damage_notes": self.damage_edit.text().strip(),
            }
        )


# -- check out --------------------------------------------------------------


class CheckOutPanel(BookingModal):
    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(
        self,
        outstanding: Decimal,
        parent=None,
        *,
        pickup_mileage: int = 0,
        booking_id: int | None = None,
        customer: str = "",
        vehicle: str = "",
    ) -> None:
        subtitle = _modal_subtitle(booking_id, customer, vehicle)
        super().__init__(
            "CHECK OUT",
            "Take the vehicle back",
            subtitle or "Record the return and settle any balance.",
            parent=parent,
        )
        self._error = self.error_label
        self.body.addWidget(self.error_label)
        self._floor = int(pickup_mileage or 0)
        self._outstanding = money(outstanding, field="outstanding")

        if self._outstanding > ZERO:
            self.outstanding_notice = NoticeBox(
                f"Outstanding {pesos(self._outstanding)}: closing needs "
                "settlement first.",
                tone="amber",
                parent=self.content_host,
            )
            self.body.addWidget(self.outstanding_notice)

        tiles = QHBoxLayout()
        tiles.setContentsMargins(0, 0, 0, 0)
        tiles.setSpacing(9)
        self.pickup_tile = MetricTile(
            "Pickup odometer",
            f"{self._floor:,} km",
            large=True,
            parent=self.content_host,
        )
        self.distance_tile = MetricTile(
            "Distance", "0 km", large=True, parent=self.content_host
        )
        tiles.addWidget(self.pickup_tile)
        tiles.addWidget(self.distance_tile)
        self.body.addLayout(tiles)

        self.mileage_edit = QSpinBox(self.content_host)
        self.mileage_edit.setObjectName("warmInput")
        self.mileage_edit.setRange(0, 9_999_999)
        self.mileage_edit.setValue(self._floor)
        mileage_field = _labelled_field(
            "Return odometer (km)",
            self.mileage_edit,
            suffix=f"· not below {self._floor:,} km",
            parent=self.content_host,
        )
        self.floor_label = mileage_field.hint_label
        self.body.addWidget(mileage_field)
        self.mileage_edit.valueChanged.connect(self._update_distance)

        self.fuel_combo, self.fuel_chips = _fuel_chips(
            self.content_host, initial="full"
        )
        self.body.addWidget(self.fuel_combo)
        self.body.addWidget(
            FormField(
                "Fuel level", self.fuel_chips, parent=self.content_host
            )
        )

        self.damage_edit = QLineEdit(self.content_host)
        self.damage_edit.setObjectName("warmInput")
        self.damage_edit.setPlaceholderText("Scratches, dents… (optional)")
        self.charge_edit = QLineEdit(self.content_host)
        self.charge_edit.setObjectName("warmInput")
        self.charge_edit.setPlaceholderText("0.00 (optional)")
        fields = QHBoxLayout()
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(12)
        fields.addWidget(
            FormField(
                "Damage notes",
                self.damage_edit,
                optional=True,
                parent=self.content_host,
            ),
            1,
        )
        fields.addWidget(
            FormField(
                "Damage charge (PHP)",
                self.charge_edit,
                optional=True,
                parent=self.content_host,
            ),
            1,
        )
        self.body.addLayout(fields)

        self.notice = NoticeBox(
            "The refundable deposit is returned after any damage or "
            "late-return charge has been applied.",
            tone="green",
            parent=self.content_host,
        )
        self.body.addWidget(self.notice)

        back = self.add_back()
        back.clicked.connect(self.cancelled.emit)
        self.go_button = self.add_primary("Check out")
        self.go_button.clicked.connect(self._on_go)

    def _update_distance(self) -> None:
        distance = max(0, int(self.mileage_edit.value()) - self._floor)
        self.distance_tile.set_value(f"{distance:,} km")

    def _on_go(self) -> None:
        mileage = int(self.mileage_edit.value())
        if mileage < self._floor:
            self.set_error(
                f"Return odometer reading of {mileage:,} km is below the "
                f"{self._floor:,} km recorded at pick-up."
            )
            return
        if self.fuel_combo.currentText() not in FUEL_LEVELS:
            self.set_error("Choose a fuel level.")
            return
        raw = self.charge_edit.text().strip()
        charge = None
        if raw:
            try:
                charge = money(raw, field="damage_charge")
            except Exception:  # noqa: BLE001 - inline below
                self.set_error("Damage charge must be a number.")
                return
            if charge <= ZERO:
                charge = None
        self.set_error("")
        self.submitted.emit(
            {
                "mileage": self.mileage_edit.value(),
                "fuel_level": self.fuel_combo.currentText(),
                "damage_notes": self.damage_edit.text().strip(),
                "damage_charge": charge,
            }
        )


# -- extend ----------------------------------------------------------------


class ExtendPanel(BookingModal):
    submitted = Signal(dict)
    cancelled = Signal()

    _QUICK = (
        ("1", "1 day"),
        ("3", "3 days"),
        ("7", "1 week"),
        ("14", "2 weeks"),
    )

    def __init__(
        self, context, booking_id: int, current_end: date, parent=None
    ) -> None:
        super().__init__(
            "EXTEND BOOKING",
            "Extend booking",
            "Push the return date forward.",
            parent=parent,
        )
        self._context = context
        self._booking_id = booking_id
        self._current_end = current_end
        self._error = self.error_label
        self.body.addWidget(self.error_label)
        self._first = QDate(
            current_end.year, current_end.month, current_end.day
        ).addDays(1)

        dates = QHBoxLayout()
        dates.setContentsMargins(0, 0, 0, 0)
        dates.setSpacing(12)
        self.current_edit = QDateEdit(self.content_host)
        self.current_edit.setObjectName("warmInput")
        self.current_edit.setCalendarPopup(False)
        self.current_edit.setDisplayFormat("dd MMM yyyy")
        self.current_edit.setDate(
            QDate(current_end.year, current_end.month, current_end.day)
        )
        self.current_edit.setReadOnly(True)
        self.current_edit.setButtonSymbols(
            QDateEdit.ButtonSymbols.NoButtons
        )
        dates.addWidget(
            FormField(
                "Current return",
                self.current_edit,
                parent=self.content_host,
            ),
            1,
        )

        self.end_edit = QDateEdit(self.content_host)
        self.end_edit.setObjectName("warmInput")
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDisplayFormat("dd MMM yyyy")
        self.end_edit.setDate(self._first)
        self.end_edit.setMinimumDate(self._first)
        self.end_edit.dateChanged.connect(self._refresh_preview)
        dates.addWidget(
            FormField(
                "New return date",
                self.end_edit,
                parent=self.content_host,
            ),
            1,
        )
        self.body.addLayout(dates)

        self.quick_chips = ChipGroup(
            self._QUICK,
            variant="choice",
            height=32,
            spacing=8,
            parent=self.content_host,
        )
        self.quick_chips.select("1")
        self.quick_chips.changed.connect(self._on_quick_choice)
        self.body.addWidget(self.quick_chips)

        self.notice = NoticeBox(
            "The vehicle must stay free for the extra days; extending "
            "re-prices the whole booking.",
            tone="amber",
            parent=self.content_host,
        )
        self.body.addWidget(self.notice)

        self.preview = Caption("", parent=self.content_host)
        self.preview.setWordWrap(True)
        self.body.addWidget(self.preview)

        self.summary = SummaryBox(self.content_host)
        self.summary.add_row("Was", "—")
        self.summary.add_row("Now", "—")
        self.summary.add_row("Extra to collect", "—")
        self.was_value = self.summary.rows[0][1]
        self.now_value = self.summary.rows[1][1]
        self.extra_value = self.summary.rows[2][1]
        self.body.addWidget(self.summary)

        back = self.add_back()
        back.clicked.connect(self.cancelled.emit)
        self.go_button = self.add_primary("Extend")
        self.go_button.clicked.connect(
            lambda: self.submitted.emit(
                {"new_end_date": self.end_edit.date().toPython()}
            )
        )
        self._refresh_preview()

    def _on_quick_choice(self, key: str) -> None:
        try:
            delta = int(key)
        except (TypeError, ValueError):
            return
        target = QDate(
            self._current_end.year,
            self._current_end.month,
            self._current_end.day,
        ).addDays(delta)
        self.end_edit.setDate(target)

    def _sync_quick_selection(self) -> None:
        target = self.end_edit.date()
        for key, _label_text in self._QUICK:
            candidate = QDate(
                self._current_end.year,
                self._current_end.month,
                self._current_end.day,
            ).addDays(int(key))
            if candidate == target:
                self.quick_chips.select(key)
                return
        # A typed custom date matches no preset: clear the stale highlight.
        group = self.quick_chips._group
        group.setExclusive(False)
        for button in self.quick_chips.buttons.values():
            button.setChecked(False)
        group.setExclusive(True)
        self.quick_chips._selected = ""

    def _refresh_preview(self) -> None:
        self.go_button.setText(
            f"Extend to {self.end_edit.date().toString('dd MMM yyyy')}"
        )
        self._sync_quick_selection()
        try:
            with self._context.reading() as session:
                booking = session.get(Booking, self._booking_id)
                old, new = booking_service.extension_quote(
                    session, booking, self.end_edit.date().toPython()
                )
        except Exception as exc:  # noqa: BLE001 - advisory preview only
            self.preview.setText(_friendly(exc))
            self.was_value.setText("—")
            self.now_value.setText("—")
            self.extra_value.setText("—")
            return
        self.preview.setText(
            f"Was {pesos(old)} → now {pesos(new)} (+{pesos(new - old)} due)."
        )
        self.was_value.setText(pesos(old))
        self.now_value.setText(pesos(new))
        self.extra_value.setText(pesos(new - old))


# -- record payment ---------------------------------------------------------


class PaymentPanel(BookingModal):
    submitted = Signal(dict)
    cancelled = Signal()

    _KINDS = (("rental", "Rental"), ("deposit", "Deposit"))
    _QUICK_AMOUNTS = (
        ("full", "Full balance"),
        ("half", "Half"),
        ("custom", "Custom"),
    )

    def __init__(
        self,
        outstanding: Decimal,
        parent=None,
        *,
        heading: str = "Record payment",
        total: Decimal | None = None,
        paid: Decimal | None = None,
        show_kind: bool = False,
        booking_id: int | None = None,
        customer: str = "",
        vehicle: str = "",
    ) -> None:
        self.heading = heading
        record = heading == "Record payment"
        eyebrow = "RECORD PAYMENT" if record else "CHECK OUT"
        title = "Take money for this booking" if record else heading
        subtitle = _modal_subtitle(booking_id, customer, vehicle)
        super().__init__(
            eyebrow,
            title,
            subtitle or "Cash, GCash, or card — the balance is rechecked on save.",
            parent=parent,
        )
        self._error = self.error_label
        self.body.addWidget(self.error_label)
        self.show_kind = bool(show_kind)
        self._outstanding = max(
            money(outstanding, field="outstanding"), ZERO
        )
        self._paid = (
            money(paid, field="paid") if paid is not None else ZERO
        )
        self._total = (
            money(total, field="total")
            if total is not None
            else money(self._outstanding + self._paid, field="total")
        )

        tiles = QHBoxLayout()
        tiles.setContentsMargins(0, 0, 0, 0)
        tiles.setSpacing(9)
        self.total_tile = MetricTile(
            "Total due", pesos(self._total), parent=self.content_host
        )
        self.paid_tile = MetricTile(
            "Paid", pesos(self._paid), parent=self.content_host
        )
        self.owed_tile = MetricTile(
            "Owed",
            pesos(self._outstanding),
            tone="owed" if self._outstanding > ZERO else "neutral",
            parent=self.content_host,
        )
        tiles.addWidget(self.total_tile)
        tiles.addWidget(self.paid_tile)
        tiles.addWidget(self.owed_tile)
        self.body.addLayout(tiles)

        self.kind_combo = QComboBox(self.content_host)
        self.kind_combo.setVisible(False)
        self.kind_combo.addItem("Rental payment", "rental")
        self.kind_combo.addItem("Security deposit", "deposit")
        self.body.addWidget(self.kind_combo)
        self.kind_chips: ChipGroup | None = None
        if self.show_kind:
            self.kind_chips = ChipGroup(
                self._KINDS,
                variant="choice",
                height=32,
                spacing=8,
                parent=self.content_host,
            )
            self.kind_chips.select("rental")
            self.kind_chips.changed.connect(self._on_kind_choice)
            self.kind_combo.currentIndexChanged.connect(
                self._on_kind_combo_changed
            )
            self.body.addWidget(
                FormField(
                    "Payment for",
                    self.kind_chips,
                    parent=self.content_host,
                )
            )

        amount_host = QFrame(self.content_host)
        amount_host.setObjectName("warmAmountBox")
        amount_host.setFixedHeight(50)
        amount_row = QHBoxLayout(amount_host)
        amount_row.setContentsMargins(16, 0, 16, 0)
        amount_row.setSpacing(0)
        prefix = _label(
            "₱",
            parent=amount_host,
            size=26,
            weight=QFont.Weight.Bold,
            colour=WARM_INK,
        )
        prefix.setObjectName("warmAmountPrefix")
        amount_row.addWidget(prefix)
        self.amount_edit = QLineEdit(amount_host)
        self.amount_edit.setObjectName("warmAmount")
        self.amount_edit.setText(f"{self._outstanding:,.2f}")
        amount_row.addWidget(self.amount_edit, 1)
        self.body.addWidget(
            FormField("Amount", amount_host, parent=self.content_host)
        )

        self.quick_amount_chips = ChipGroup(
            self._QUICK_AMOUNTS,
            variant="choice",
            height=32,
            spacing=8,
            parent=self.content_host,
        )
        self.quick_amount_chips.changed.connect(self._on_quick_amount)
        self.body.addWidget(self.quick_amount_chips)

        self.method_combo, self.method_chips = _method_chips(
            self.content_host
        )
        self.body.addWidget(self.method_combo)
        self.body.addWidget(
            FormField(
                "Method", self.method_chips, parent=self.content_host
            )
        )

        self.ref_edit = QLineEdit(self.content_host)
        self.ref_edit.setObjectName("warmInput")
        self.ref_edit.setPlaceholderText("GCash ref (required for GCash)")
        self.note_edit = QLineEdit(self.content_host)
        self.note_edit.setObjectName("warmInput")
        fields = QHBoxLayout()
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(12)
        fields.addWidget(
            FormField(
                "Reference no.",
                self.ref_edit,
                optional=True,
                parent=self.content_host,
            ),
            1,
        )
        fields.addWidget(
            FormField(
                "Note",
                self.note_edit,
                optional=True,
                parent=self.content_host,
            ),
            1,
        )
        self.body.addLayout(fields)

        self.method_combo.currentTextChanged.connect(
            self._on_method_changed
        )
        self.method_chips.changed.connect(self.method_combo.setCurrentText)
        self._sync_ref(self.method_combo.currentText())

        self.amount_edit.textChanged.connect(self._refresh_total_button)

        back = self.add_back()
        back.clicked.connect(self.cancelled.emit)
        self.go_button = self.add_primary("Record payment")
        self.go_button.clicked.connect(self._on_go)
        self._refresh_total_button()

    def _on_kind_choice(self, key: str) -> None:
        index = self.kind_combo.findData(key)
        if index >= 0:
            self.kind_combo.setCurrentIndex(index)

    def _on_kind_combo_changed(self, index: int) -> None:
        if self.kind_chips is None:
            return
        key = self.kind_combo.itemData(index)
        if key:
            self.kind_chips.select(key)

    def _on_quick_amount(self, key: str) -> None:
        if key == "custom":
            self.amount_edit.setFocus()
            self.amount_edit.selectAll()
            return
        value = self._outstanding
        if key == "half":
            value = money(self._outstanding / Decimal("2"), field="amount")
        self.amount_edit.setText(f"{value:,.2f}")

    def _on_method_changed(self, method: str) -> None:
        self.method_chips.select(method)
        self._sync_ref(method)

    def _sync_ref(self, method: str) -> None:
        gcash = method == "gcash"
        self.ref_edit.setEnabled(gcash)
        if not gcash:
            self.ref_edit.clear()

    def _refresh_total_button(self) -> None:
        raw = self.amount_edit.text().strip()
        try:
            value = money(raw, field="amount")
        except Exception:  # noqa: BLE001 - the button is a hint only
            self.go_button.setText("Record payment")
            return
        self.go_button.setText(f"Record ₱{value:,.2f}")

    def _on_go(self) -> None:
        if (
            self.method_combo.currentText() == "gcash"
            and not self.ref_edit.text().strip()
        ):
            self.set_error("A GCash reference number is required.")
            return
        try:
            value = money(self.amount_edit.text(), field="amount")
        except Exception as exc:  # noqa: BLE001
            self.set_error(f"Amount must be a number: {exc}")
            return
        if value <= ZERO:
            self.set_error("Payment amount must be greater than zero.")
            return
        self.set_error("")
        kind = self.kind_combo.currentData() or "rental"
        if not self.show_kind:
            # Checkout settlement must never record a deposit.
            kind = "rental"
        self.submitted.emit(
            {
                "amount": value,
                "method": self.method_combo.currentText(),
                "reference_no": self.ref_edit.text().strip(),
                "note": self.note_edit.text().strip(),
                "kind": kind,
            }
        )
