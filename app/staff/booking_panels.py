"""Bookings action panels: frosted-overlay forms, never OS windows.

Each panel is a wizard-style step (transparent host + white 8px card) that
collects the kwargs `BookingsPage.run()` needs. Validation mistakes show
inline; only the page toasts results. No stadium shapes, no circles.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from PySide6.QtCore import QDate, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFormLayout,
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
from app.staff.cards import Caption, SectionLabel
from app.staff.feedback import describe
from app.staff.rental_wizard import _StepBase
from app.staff.widgets import toast
from app.utils.money import ZERO, money, pesos


def _field_label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("fieldLabel")
    return label


def _friendly(error: BaseException) -> str:
    message, _field, _kind = describe(error)
    return message


class CheckInPanel(_StepBase):
    submitted = Signal(dict)
    cancelled = Signal()
    collect_requested = Signal()

    def __init__(
        self,
        parent=None,
        *,
        current_mileage: int = 0,
        outstanding: Decimal = ZERO,
    ) -> None:
        super().__init__(parent)
        self._head("Check in", "Release the vehicle")
        self._error = self._error_label()
        self._floor = int(current_mileage or 0)
        self._outstanding = money(outstanding, field="outstanding")
        if self._outstanding > ZERO:
            self._body.addWidget(
                Caption(
                    f"Outstanding {pesos(self._outstanding)}: record payment "
                    "before releasing the vehicle.",
                    parent=self.card,
                )
            )
        grid = QFormLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self.mileage_edit = QSpinBox(self.card)
        self.mileage_edit.setRange(0, 9_999_999)
        self.mileage_edit.setValue(self._floor)
        grid.addRow(_field_label("Odometer (km)", self.card), self.mileage_edit)
        self.fuel_combo = QComboBox(self.card)
        self.fuel_combo.addItems(list(FUEL_LEVELS))
        self.fuel_combo.setCurrentText("full")
        grid.addRow(_field_label("Fuel level", self.card), self.fuel_combo)
        self.damage_edit = QLineEdit(self.card)
        self.damage_edit.setPlaceholderText("Scratches, dents… (optional)")
        grid.addRow(_field_label("Damage notes", self.card), self.damage_edit)
        self._body.addLayout(grid)
        back, go = self._buttons("Check in")
        self.go_button = go
        back.clicked.connect(self.cancelled.emit)
        go.clicked.connect(self._on_go)
        self.collect_button = QPushButton("Record payment first", self.card)
        self.collect_button.setObjectName("secondaryButton")
        self.collect_button.setVisible(self._outstanding > ZERO)
        self.collect_button.clicked.connect(self.collect_requested.emit)
        self._body.addWidget(self.collect_button)
        if self._outstanding > ZERO:
            self.go_button.setEnabled(False)

    def _on_go(self) -> None:
        if self._outstanding > ZERO:
            self._error.setText(
                "Collect the outstanding balance before releasing the vehicle."
            )
            self._error.setVisible(True)
            return
        mileage = int(self.mileage_edit.value())
        if mileage < 0:
            self._error.setText("Mileage cannot be negative.")
            self._error.setVisible(True)
            return
        if mileage < self._floor:
            self._error.setText(
                f"Odometer reading of {mileage:,} km is below the "
                f"{self._floor:,} km on record."
            )
            self._error.setVisible(True)
            return
        if self.fuel_combo.currentText() not in FUEL_LEVELS:
            self._error.setText("Choose a fuel level.")
            self._error.setVisible(True)
            return
        self._error.setVisible(False)
        self.submitted.emit(
            {
                "mileage": mileage,
                "fuel_level": self.fuel_combo.currentText(),
                "damage_notes": self.damage_edit.text().strip(),
            }
        )


class CheckOutPanel(_StepBase):
    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(self, outstanding: Decimal, parent=None, *, pickup_mileage: int = 0) -> None:
        super().__init__(parent)
        self._head("Check out", "Take the vehicle back")
        self._error = self._error_label()
        self._floor = int(pickup_mileage or 0)
        self._body.addWidget(
            Caption(
                f"Pickup odometer {self._floor:,} km: the return reading "
                "cannot be below this.",
                parent=self.card,
            )
        )
        if outstanding > ZERO:
            self._body.addWidget(
                Caption(
                    f"Outstanding {pesos(outstanding)}: closing needs "
                    "settlement first.",
                    parent=self.card,
                )
            )
        grid = QFormLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self.mileage_edit = QSpinBox(self.card)
        self.mileage_edit.setRange(0, 9_999_999)
        self.mileage_edit.setValue(self._floor)
        grid.addRow(_field_label("Odometer (km)", self.card), self.mileage_edit)
        self.fuel_combo = QComboBox(self.card)
        self.fuel_combo.addItems(list(FUEL_LEVELS))
        self.fuel_combo.setCurrentText("full")
        grid.addRow(_field_label("Fuel level", self.card), self.fuel_combo)
        self.damage_edit = QLineEdit(self.card)
        self.damage_edit.setPlaceholderText("Scratches, dents… (optional)")
        grid.addRow(_field_label("Damage notes", self.card), self.damage_edit)
        self.charge_edit = QLineEdit(self.card)
        self.charge_edit.setPlaceholderText("0.00 (optional)")
        grid.addRow(
            _field_label("Damage charge (PHP)", self.card), self.charge_edit
        )
        self._body.addLayout(grid)
        back, go = self._buttons("Check out")
        back.clicked.connect(self.cancelled.emit)
        go.clicked.connect(self._on_go)

    def _on_go(self) -> None:
        mileage = int(self.mileage_edit.value())
        if mileage < self._floor:
            self._error.setText(
                f"Return odometer reading of {mileage:,} km is below the "
                f"{self._floor:,} km recorded at pick-up."
            )
            self._error.setVisible(True)
            return
        if self.fuel_combo.currentText() not in FUEL_LEVELS:
            self._error.setText("Choose a fuel level.")
            self._error.setVisible(True)
            return
        raw = self.charge_edit.text().strip()
        charge = None
        if raw:
            try:
                charge = money(raw, field="damage_charge")
            except Exception:  # noqa: BLE001 - inline below
                self._error.setText("Damage charge must be a number.")
                self._error.setVisible(True)
                return
            if charge <= ZERO:
                charge = None
        self._error.setVisible(False)
        self.submitted.emit(
            {
                "mileage": self.mileage_edit.value(),
                "fuel_level": self.fuel_combo.currentText(),
                "damage_notes": self.damage_edit.text().strip(),
                "damage_charge": charge,
            }
        )


class PaymentPanel(_StepBase):
    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(
        self,
        outstanding: Decimal,
        parent=None,
        *,
        heading: str = "Record payment",
        total: Decimal | None = None,
        paid: Decimal | None = None,
        show_kind: bool = False,
    ) -> None:
        super().__init__(parent)
        self.heading = heading
        self._head(heading, "Take money against this booking")
        self._error = self._error_label()
        if total is not None and paid is not None:
            self._body.addWidget(
                Caption(
                    f"Total {pesos(total)}"
                    f" · Paid {pesos(paid)}"
                    f" · Owed {pesos(outstanding if outstanding > ZERO else ZERO)}",
                    parent=self.card,
                )
            )
        elif outstanding > ZERO:
            self._body.addWidget(
                Caption(
                    f"Outstanding: {pesos(outstanding)}", parent=self.card
                )
            )
        else:
            self._body.addWidget(
                Caption("Nothing owed: this booking is paid in full.", parent=self.card)
            )
        grid = QFormLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self.kind_combo: QComboBox | None = None
        if show_kind:
            self.kind_combo = QComboBox(self.card)
            self.kind_combo.addItem("Rental payment", "rental")
            self.kind_combo.addItem("Security deposit", "deposit")
            grid.addRow(_field_label("Payment for", self.card), self.kind_combo)
        self.amount_edit = QLineEdit(self.card)
        self.amount_edit.setText(f"{max(outstanding, ZERO):,.2f}")
        grid.addRow(_field_label("Amount (PHP)", self.card), self.amount_edit)
        self.method_combo = QComboBox(self.card)
        self.method_combo.addItems(list(payment_service.METHODS))
        grid.addRow(_field_label("Method", self.card), self.method_combo)
        self.ref_edit = QLineEdit(self.card)
        self.ref_edit.setPlaceholderText("GCash ref (required for GCash)")
        grid.addRow(_field_label("Reference no.", self.card), self.ref_edit)
        self.note_edit = QLineEdit(self.card)
        grid.addRow(_field_label("Note", self.card), self.note_edit)
        self._body.addLayout(grid)
        back, go = self._buttons("Record payment")
        back.clicked.connect(self.cancelled.emit)
        go.clicked.connect(self._on_go)
        self.method_combo.currentTextChanged.connect(self._sync_ref)
        self._sync_ref(self.method_combo.currentText())

    def _sync_ref(self, method: str) -> None:
        gcash = method == "gcash"
        self.ref_edit.setEnabled(gcash)
        if not gcash:
            self.ref_edit.clear()

    def _on_go(self) -> None:
        if self.method_combo.currentText() == "gcash" and not self.ref_edit.text().strip():
            self._error.setText("A GCash reference number is required.")
            self._error.setVisible(True)
            return
        try:
            value = money(self.amount_edit.text(), field="amount")
        except Exception as exc:  # noqa: BLE001
            self._error.setText(f"Amount must be a number: {exc}")
            self._error.setVisible(True)
            return
        if value <= ZERO:
            self._error.setText("Payment amount must be greater than zero.")
            self._error.setVisible(True)
            return
        self._error.setVisible(False)
        self.submitted.emit(
            {
                "amount": value,
                "method": self.method_combo.currentText(),
                "reference_no": self.ref_edit.text().strip(),
                "note": self.note_edit.text().strip(),
                "kind": (
                    self.kind_combo.currentData()
                    if self.kind_combo is not None
                    else "rental"
                ),
            }
        )


class ExtendPanel(_StepBase):
    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(self, context, booking_id: int, current_end: date, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        self._booking_id = booking_id
        self._head("Extend booking", "Push the return date forward")
        self._error = self._error_label()
        grid = QFormLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self.end_edit = QDateEdit(self.card)
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDisplayFormat("dd MMM yyyy")
        first = QDate(current_end.year, current_end.month, current_end.day).addDays(1)
        self.end_edit.setDate(first)
        self.end_edit.setMinimumDate(first)
        self.end_edit.dateChanged.connect(self._refresh_preview)
        grid.addRow(_field_label("New return date", self.card), self.end_edit)
        self._body.addLayout(grid)
        self.preview = Caption("", parent=self.card)
        self._body.addWidget(self.preview)
        back, go = self._buttons("Extend")
        back.clicked.connect(self.cancelled.emit)
        go.clicked.connect(
            lambda: self.submitted.emit(
                {"new_end_date": self.end_edit.date().toPython()}
            )
        )
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        try:
            with self._context.reading() as session:
                booking = session.get(Booking, self._booking_id)
                old, new = booking_service.extension_quote(
                    session, booking, self.end_edit.date().toPython()
                )
        except Exception as exc:  # noqa: BLE001 - advisory preview only
            self.preview.setText(_friendly(exc))
            return
        self.preview.setText(
            f"Was {pesos(old)} → now {pesos(new)} (+{pesos(new - old)} due)."
        )


class ReasonPanel(_StepBase):
    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._head("Cancel booking", "Why is this being cancelled?")
        self._error = self._error_label()
        grid = QFormLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self.reason_edit = QLineEdit(self.card)
        self.reason_edit.setPlaceholderText("Why is this being cancelled?")
        grid.addRow(_field_label("Reason", self.card), self.reason_edit)
        self._body.addLayout(grid)
        back, go = self._buttons("Continue")
        back.clicked.connect(self.cancelled.emit)
        go.clicked.connect(self._on_go)

    def _on_go(self) -> None:
        if not self.reason_edit.text().strip():
            self._error.setText("A reason is required to cancel.")
            self._error.setVisible(True)
            return
        self._error.setVisible(False)
        self.submitted.emit({"reason": self.reason_edit.text().strip()})


class BookingDetailsPanel(_StepBase):
    closed = Signal()

    def __init__(self, context, booking_id: int, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        self._booking_id = booking_id
        self._head("Booking", f"Booking #{booking_id}")
        self._error = self._error_label()
        self._sections: list = []
        self._load()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addStretch(1)
        self.print_button = QPushButton("Print receipt", self.card)
        self.print_button.setObjectName("secondaryButton")
        self.print_button.clicked.connect(self._on_print)
        self.close_button = QPushButton("Close", self.card)
        self.close_button.setObjectName("primaryButton")
        self.close_button.clicked.connect(self.closed.emit)
        row.addWidget(self.print_button)
        row.addWidget(self.close_button)
        self._body.addLayout(row)
        self._rows: list = []

    def _section(self, title: str, lines: list[str]) -> None:
        self._body.addWidget(SectionLabel(title, parent=self.card))
        for line in lines:
            self._body.addWidget(Caption(line, parent=self.card))

    def _load(self) -> None:
        with self._context.reading() as session:
            booking = session.get(Booking, self._booking_id)
            if booking is None:
                self._section("Missing", ["That booking no longer exists."])
                self.print_button.setEnabled(False)
                return
            user = booking.user
            vehicle = booking.vehicle
            balance = payment_service.booking_balance(session, booking)
            self._section(
                "Booking",
                [
                    f"#{booking.booking_id} · "
                    f"{theme.status_label(booking.status)} · "
                    f"{booking.channel or 'walk-in'}",
                    f"{booking.start_date:%d %b %Y} → {booking.end_date:%d %b %Y}",
                ],
            )
            self._section(
                "Customer",
                [
                    f"{user.full_name} · {user.email}",
                    f"{user.phone or '-'} · Licence {user.license_number}",
                ],
            )
            self._section(
                "Vehicle",
                [
                    f"{vehicle.make} {vehicle.model} ({vehicle.year})",
                    f"Plate {vehicle.plate_number} · "
                    f"{theme.status_label(vehicle.status)}",
                ],
            )
            payments = [
                f"{pesos(p.amount)} via {p.method} ({p.status})"
                for p in booking.payments
            ] or ["No payments recorded."]
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
            inspections = [
                f"{r.inspection_type} · {r.mileage_reading:,} km · "
                f"fuel {r.fuel_level} · {r.damage_notes or 'no damage'}"
                for r in booking.inspections
            ] or ["No inspections yet."]
            self._section("Inspections", inspections)
            penalties = [
                f"{p.penalty_type}: {pesos(p.amount)} · {p.description}"
                for p in booking.penalties
            ] or ["No penalties."]
            self._section("Penalties", penalties)

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
            self._error.setText(_friendly(exc))
            self._error.setVisible(True)
            return
        if auto_open:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            toast(self, f"Receipt saved: {path}")
