"""Maintenance action panels: frosted-overlay forms, never OS windows.

Same recipe as `booking_panels.py`: each panel collects the kwargs its
page action needs. Validation mistakes show inline; only the page toasts
results. No stadium shapes, no circles.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QCheckBox,
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

from app.models import Vehicle
from app.services import maintenance_service
from app.services.maintenance_service import TYPE_LABELS, TYPES
from app.staff import theme
from app.staff.cards import Caption, SectionLabel
from app.staff.feedback import describe
from app.staff.rental_wizard import _StepBase
from app.utils.money import ZERO, money, pesos


def _field_label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("fieldLabel")
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


class RecordPanel(_StepBase):
    """Log a shop stay. Overlapping bookings warn, never block."""

    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(self, context, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        self._head("Log maintenance", "Put a vehicle in the shop")
        self._error = self._error_label()
        grid = QFormLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self.vehicle_combo = QComboBox(self.card)
        self._vehicles = _vehicle_options(context)
        for _vid, label in self._vehicles:
            self.vehicle_combo.addItem(label)
        grid.addRow(_field_label("Vehicle", self.card), self.vehicle_combo)
        self.type_combo = QComboBox(self.card)
        self.type_combo.addItems([TYPE_LABELS[t] for t in TYPES])
        grid.addRow(_field_label("Type", self.card), self.type_combo)
        self.status_combo = QComboBox(self.card)
        self.status_combo.addItems(["Scheduled", "In Progress"])
        grid.addRow(_field_label("Status", self.card), self.status_combo)
        self.start_edit = QDateEdit(self.card)
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setDisplayFormat("dd MMM yyyy")
        self.start_edit.setDate(QDate.currentDate())
        grid.addRow(_field_label("Start", self.card), self.start_edit)
        self.open_check = QCheckBox("Open-ended (no end date yet)", self.card)
        self.open_check.setChecked(True)
        grid.addRow("", self.open_check)
        self.end_edit = QDateEdit(self.card)
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDisplayFormat("dd MMM yyyy")
        self.end_edit.setDate(QDate.currentDate().addDays(3))
        self.end_edit.setEnabled(False)
        self.open_check.toggled.connect(
            lambda on: self.end_edit.setEnabled(not on)
        )
        grid.addRow(_field_label("End", self.card), self.end_edit)
        self.estimate_edit = QLineEdit(self.card)
        self.estimate_edit.setPlaceholderText("0.00 (optional quote)")
        grid.addRow(_field_label("Estimate (PHP)", self.card), self.estimate_edit)
        self.odometer_edit = QSpinBox(self.card)
        self.odometer_edit.setRange(0, 9_999_999)
        self.odometer_edit.setSpecialValueText("—")
        grid.addRow(_field_label("Odometer (km)", self.card), self.odometer_edit)
        self.shop_edit = QLineEdit(self.card)
        self.shop_edit.setPlaceholderText("Shop or mechanic (optional)")
        grid.addRow(_field_label("Shop", self.card), self.shop_edit)
        self.booking_edit = QLineEdit(self.card)
        self.booking_edit.setPlaceholderText("Booking # for damage repairs (optional)")
        grid.addRow(_field_label("Booking", self.card), self.booking_edit)
        self.description_edit = QLineEdit(self.card)
        self.description_edit.setPlaceholderText("What is being done?")
        grid.addRow(_field_label("Work", self.card), self.description_edit)
        self._body.addLayout(grid)
        self.clash_note = Caption("", parent=self.card)
        self._body.addWidget(self.clash_note)
        for widget in (
            self.vehicle_combo,
            self.start_edit,
            self.end_edit,
        ):
            if hasattr(widget, "currentIndexChanged"):
                widget.currentIndexChanged.connect(self._refresh_clashes)
            else:
                widget.dateChanged.connect(self._refresh_clashes)
        self.open_check.toggled.connect(lambda _on: self._refresh_clashes())
        back, go = self._buttons("Log it")
        back.clicked.connect(self.cancelled.emit)
        go.clicked.connect(self._on_go)
        self._refresh_clashes()

    def _selection(self) -> tuple[int | None, date, date | None]:
        vid = None
        if self._vehicles:
            vid = self._vehicles[self.vehicle_combo.currentIndex()][0]
        start = self.start_edit.date().toPython()
        end = None if self.open_check.isChecked() else self.end_edit.date().toPython()
        return vid, start, end

    def _refresh_clashes(self) -> None:
        vid, start, end = self._selection()
        if vid is None:
            self.clash_note.setText("")
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
            return
        if refs:
            self.clash_note.setText(
                f"Overlaps booking(s) {refs}: reassign them first, or log anyway "
                "and the handover will refuse."
            )
        else:
            self.clash_note.setText("No booking clash in this window.")

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


class CompletePanel(_StepBase):
    """The one button staff mustn't forget: close the job, release the car."""

    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(self, description: str, estimate, parent=None) -> None:
        super().__init__(parent)
        self._head("Mark completed", description)
        self._error = self._error_label()
        if estimate is not None and estimate > ZERO:
            self._body.addWidget(
                Caption(f"Quoted {pesos(estimate)}.", parent=self.card)
            )
        grid = QFormLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self.end_edit = QDateEdit(self.card)
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDisplayFormat("dd MMM yyyy")
        self.end_edit.setDate(QDate.currentDate())
        grid.addRow(_field_label("Finished", self.card), self.end_edit)
        self.cost_edit = QLineEdit(self.card)
        if estimate is not None:
            self.cost_edit.setText(f"{estimate:,.2f}")
        self.cost_edit.setPlaceholderText("0.00")
        grid.addRow(_field_label("Final cost (PHP)", self.card), self.cost_edit)
        self.odometer_edit = QSpinBox(self.card)
        self.odometer_edit.setRange(0, 9_999_999)
        self.odometer_edit.setSpecialValueText("—")
        grid.addRow(_field_label("Odometer (km)", self.card), self.odometer_edit)
        self.due_date_check = QCheckBox("Set next due date", self.card)
        grid.addRow("", self.due_date_check)
        self.due_date_edit = QDateEdit(self.card)
        self.due_date_edit.setCalendarPopup(True)
        self.due_date_edit.setDisplayFormat("dd MMM yyyy")
        self.due_date_edit.setDate(QDate.currentDate().addDays(90))
        self.due_date_edit.setEnabled(False)
        self.due_date_check.toggled.connect(self.due_date_edit.setEnabled)
        grid.addRow(_field_label("Next due", self.card), self.due_date_edit)
        self.due_km_check = QCheckBox("Set next due odometer", self.card)
        grid.addRow("", self.due_km_check)
        self.due_km_edit = QSpinBox(self.card)
        self.due_km_edit.setRange(0, 9_999_999)
        self.due_km_edit.setEnabled(False)
        self.due_km_check.toggled.connect(self.due_km_edit.setEnabled)
        grid.addRow(_field_label("Next due (km)", self.card), self.due_km_edit)
        self._body.addLayout(grid)
        back, go = self._buttons("Mark completed")
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


class CancelPanel(_StepBase):
    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._head("Cancel record", "The row stays on the log.")
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


class HistoryPanel(_StepBase):
    closed = Signal()

    def __init__(self, context, vehicle_id: int, parent=None) -> None:
        super().__init__(parent)
        from app.services.maintenance_service import history_for, type_label

        self._context = context
        with context.reading() as session:
            vehicle = session.get(Vehicle, vehicle_id)
            if vehicle is None:
                self._head("History", "Vehicle not found")
                self._body.addWidget(
                    Caption("That vehicle no longer exists.", parent=self.card)
                )
            else:
                self._head(
                    "History",
                    f"{vehicle.plate_number} {vehicle.make} {vehicle.model}",
                )
                records, spent = history_for(session, vehicle)
                self._body.addWidget(
                    Caption(
                        f"{len(records)} record(s) · {pesos(spent)} spent.",
                        parent=self.card,
                    )
                )
                for record in records:
                    when = f"{record.start_date:%d %b %Y}"
                    if record.end_date:
                        when += f" → {record.end_date:%d %b %Y}"
                    amount = (
                        pesos(record.cost)
                        if record.cost is not None
                        else (
                            f"est. {pesos(record.estimate_cost)}"
                            if record.estimate_cost is not None
                            else "no cost yet"
                        )
                    )
                    self._body.addWidget(
                        SectionLabel(
                            f"{type_label(record.maintenance_type)} · {record.status}",
                            parent=self.card,
                        )
                    )
                    lines = [f"{when} · {amount}", record.description]
                    if record.shop:
                        lines.append(f"Shop: {record.shop}")
                    if record.next_due_date or record.next_due_odometer:
                        due = "Next due: "
                        bits = []
                        if record.next_due_date:
                            bits.append(f"{record.next_due_date:%d %b %Y}")
                        if record.next_due_odometer:
                            bits.append(f"{record.next_due_odometer:,} km")
                        lines.append(due + " / ".join(bits))
                    for line in lines:
                        self._body.addWidget(Caption(line, parent=self.card))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addStretch(1)
        self.close_button = QPushButton("Close", self.card)
        self.close_button.setObjectName("primaryButton")
        self.close_button.clicked.connect(self.closed.emit)
        row.addWidget(self.close_button)
        self._body.addLayout(row)
