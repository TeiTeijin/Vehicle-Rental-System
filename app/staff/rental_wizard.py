"""Counter rental wizard: Steps 2-5 inside the check-details overlay.

Step 1 is the vehicle details panel (photo, specs, availability checker).
Pressing Rent swaps the same frosted card to:

- Step 2 Scheduling: dates (prefilled from the checked window) + live total.
- Step 3 Licence: licence-first lookup (exact match reuses the row, so
  repeat renters never duplicate); inline create form only on no match.
- Step 4 Payment: breakdown + pay-minimum / pay-full + method + Pay.
- Step 5 Done: verdict + receipt path + Print / View in Bookings / Done.

Design language matches the details card: Inter, white 8px card on the
frosted backdrop, spec-sheet rows, 6px inputs, primary/secondary buttons,
status boxes (8px, square chips). No stadium shapes, no circles.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from PySide6.QtCore import QDate, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QDateEdit,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.models import Booking, Users, Vehicle
from app.services import booking_service, customer_service, payment_service
from app.services.booking_service import SCHEDULING_FEE
from app.services.receipt_service import generate_receipt_pdf
from app.staff import theme
from app.staff.cards import Caption, Card, MoneyNumber, SectionLabel
from app.staff.feedback import describe
from app.utils.money import money, pesos


@dataclass
class WizardState:
    vehicle_id: int
    start: QDate
    end: QDate
    days: int = 1
    total: Decimal | None = None
    deposit: Decimal | None = None
    customer_id: int | None = None
    #: Staff-typed reason for proceeding past a customer flag. Written to
    #: the booking on Pay; NULL means no flag was overridden.
    flag_override_note: str | None = None


# -- shared pieces ------------------------------------------------------------


#: Wizard-only density. The dashboard/details card recipe (26px padding,
#: 14px gaps) drowns short form rows; the wizard wraps the form instead of
#: framing empty space. One place to tune; every step inherits it.
WIZARD_PADDING = 16
WIZARD_SPACING = 8


class _StepBase(QWidget):
    """Transparent host holding the white 8px card in wizard density."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("wizardStep")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # Centering stretches: the Card is Expanding by default and would
        # inflate to the full scroll height, pooling dead air between short
        # form blocks. Hugging + centering keeps the card wrapped around its
        # content; on tall content the stretches collapse and it scrolls.
        outer.addStretch(1)
        self.card = Card(self, radius=8)
        self.card.setObjectName("vehicleDetailsCard")
        self.card.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        outer.addWidget(self.card)
        body = QVBoxLayout()
        body.setContentsMargins(
            WIZARD_PADDING, WIZARD_PADDING, WIZARD_PADDING, WIZARD_PADDING
        )
        body.setSpacing(WIZARD_SPACING)
        self.card.layout().addLayout(body)
        self._body = body
        outer.addStretch(1)

    def _head(self, step: str, title: str) -> None:
        self._body.addWidget(Caption(step, parent=self.card))
        # 16px DemiBold section-head size: the caption carries the context,
        # so the title reads as a head, not a hero.
        self._body.addWidget(SectionLabel(title, parent=self.card))

    def _error_label(self) -> QLabel:
        label = QLabel("", self.card)
        label.setObjectName("fieldError")
        label.setWordWrap(True)
        label.setVisible(False)
        self._body.addWidget(label)
        return label

    def _buttons(self, primary_text: str) -> tuple[QPushButton, QPushButton]:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addStretch(1)
        back = QPushButton("Back", self.card)
        back.setObjectName("secondaryButton")
        primary = QPushButton(primary_text, self.card)
        primary.setObjectName("primaryButton")
        row.addWidget(back)
        row.addWidget(primary)
        self._body.addLayout(row)
        return back, primary


def make_status_box(text: str, colour: str, parent: QWidget) -> QFrame:
    """8px status box with a square chip. Never a stadium, never a dot."""
    box = QFrame(parent)
    box.setObjectName("wizardBox")
    lay = QHBoxLayout(box)
    lay.setContentsMargins(10, 8, 12, 8)
    lay.setSpacing(8)
    chip = QLabel("", box)
    chip.setObjectName("wizardChip")
    chip.setFixedSize(10, 10)
    chip.setStyleSheet(f"QLabel#wizardChip {{ background: {colour}; }}")
    lay.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
    lay.addWidget(Caption(text, parent=box, colour=theme.TEXT))
    lay.addStretch(1)
    return box


def make_choice_button(text: str, parent: QWidget) -> QPushButton:
    button = QPushButton(text, parent)
    button.setObjectName("wizardChoice")
    button.setCheckable(True)
    return button


def _friendly(error: BaseException) -> str:
    message, _field, _kind = describe(error)
    return message


# -- Step 2: scheduling --------------------------------------------------------


class SchedulePanel(_StepBase):
    continued = Signal(QDate, QDate)
    backed = Signal()

    def __init__(self, context, state: WizardState, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        self._state = state
        self._head("Step 2 of 4 — Scheduling", "When is it out?")
        self._error = self._error_label()

        with context.reading() as session:
            vehicle = session.get(Vehicle, state.vehicle_id)
            if vehicle is None:
                self._missing = True
                self._body.addWidget(Caption("That vehicle no longer exists.", parent=self.card))
                back, self.next_button = self._buttons("Continue")
                back.clicked.connect(self.backed.emit)
                self.next_button.setEnabled(False)
                return
            self._missing = False
            name = f"{vehicle.make} {vehicle.model}".strip()
            sub = f"{vehicle.year} · {vehicle.plate_number}"
            rate = money(vehicle.daily_rate or 0)

        self._body.addWidget(SectionLabel(name, parent=self.card))
        self._body.addWidget(Caption(sub, parent=self.card))

        grid = QFormLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        today = QDate.currentDate()
        self.start_edit = QDateEdit(self.card)
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setDisplayFormat("dd MMM yyyy")
        self.start_edit.setDate(state.start)
        self.start_edit.setMinimumDate(today)
        self.end_edit = QDateEdit(self.card)
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDisplayFormat("dd MMM yyyy")
        self.end_edit.setDate(state.end)
        self.end_edit.setMinimumDate(state.start)
        self.start_edit.dateChanged.connect(self._on_start_changed)
        self.start_edit.dateChanged.connect(self._refresh_quote)
        self.end_edit.dateChanged.connect(self._refresh_quote)
        grid.addRow(Caption("Pick-up", parent=self.card), self.start_edit)
        grid.addRow(Caption("Return", parent=self.card), self.end_edit)
        self._body.addLayout(grid)

        total_row = QHBoxLayout()
        total_row.setContentsMargins(0, 0, 0, 0)
        total_row.setSpacing(6)
        self.total = MoneyNumber(parent=self.card)
        self.total.set_colours(dim=theme.MUTED, bright=theme.TEXT)
        total_row.addWidget(self.total)
        self.total_caption = Caption("", parent=self.card)
        total_row.addWidget(self.total_caption)
        total_row.addStretch(1)
        self._body.addLayout(total_row)
        self._body.addWidget(
            Caption(
                f"Includes {pesos(SCHEDULING_FEE)} non-refundable scheduling fee. "
                "Same-day return counts as 1 day.",
                parent=self.card,
            )
        )

        back, self.next_button = self._buttons("Continue")
        back.clicked.connect(self.backed.emit)
        self.next_button.clicked.connect(self._on_continue)
        self._refresh_quote()

    def _on_start_changed(self, day: QDate) -> None:
        self.end_edit.setMinimumDate(day)
        if self.end_edit.date() < day:
            self.end_edit.setDate(day)

    def _refresh_quote(self) -> None:
        if self._missing:
            return
        try:
            with self._context.reading() as session:
                vehicle = session.get(Vehicle, self._state.vehicle_id)
                days, total = booking_service.quote_for(
                    session,
                    vehicle,
                    self.start_edit.date().toPython(),
                    self.end_edit.date().toPython(),
                )
        except Exception as exc:  # noqa: BLE001 - advisory preview only
            self.total.set_amount(0)
            self.total_caption.setText(_friendly(exc))
            self.next_button.setEnabled(False)
            return
        self._days, self._total = days, total
        self.total.set_amount(total)
        self.total_caption.setText(f"for {days} day{'s' if days != 1 else ''}")
        self.next_button.setEnabled(True)

    def _on_continue(self) -> None:
        self.continued.emit(self.start_edit.date(), self.end_edit.date())


# -- Step 3: licence -----------------------------------------------------------


class LicencePanel(_StepBase):
    customer_chosen = Signal(int)
    backed = Signal()

    def __init__(self, context, state: WizardState, parent=None, *, preset_customer_id: int | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._state = state
        self._picked: int | None = None
        self._flagged = False
        self._head("Step 3 of 4 — Customer", "Whose licence is this?")
        self._error = self._error_label()

        finder = QHBoxLayout()
        finder.setContentsMargins(0, 0, 0, 0)
        finder.setSpacing(8)
        self.licence_edit = QLineEdit(self.card)
        self.licence_edit.setPlaceholderText("Licence number on the card")
        self.find_button = QPushButton("Find", self.card)
        self.find_button.setObjectName("secondaryButton")
        self.find_button.clicked.connect(self._on_find)
        self.licence_edit.returnPressed.connect(self._on_find)
        finder.addWidget(self.licence_edit, 1)
        finder.addWidget(self.find_button)
        self._body.addLayout(finder)
        self._body.addWidget(
            Caption("No licence, no rental.", parent=self.card)
        )

        self.result_host = QVBoxLayout()
        self.result_host.setContentsMargins(0, 0, 0, 0)
        self.result_host.setSpacing(8)
        self._body.addLayout(self.result_host)

        # Flag banner + required override reason. Shown only for flagged
        # customers; Continue stays disabled until a reason is typed.
        self._flag_banner_host = QVBoxLayout()
        self._flag_banner_host.setContentsMargins(0, 0, 0, 0)
        self._flag_banner_host.setSpacing(8)
        self._body.addLayout(self._flag_banner_host)
        self.override_edit = QLineEdit(self.card)
        self.override_edit.setPlaceholderText("Override reason (required for flagged customer)")
        self.override_edit.textChanged.connect(lambda _t: self._refresh_continue())
        self.override_edit.setVisible(False)
        self._body.addWidget(self.override_edit)

        # Inline birthdate fix for legacy rows: block once, fix on the spot.
        self._dob_host = QWidget(self.card)
        self._dob_host.setObjectName("wizardStep")
        dob_lay = QVBoxLayout(self._dob_host)
        dob_lay.setContentsMargins(0, 0, 0, 0)
        dob_lay.setSpacing(6)
        dob_lay.addWidget(Caption("Birthdate is missing: add it to check the renting age.", parent=self._dob_host))
        dob_row = QHBoxLayout()
        dob_row.setContentsMargins(0, 0, 0, 0)
        dob_row.setSpacing(8)
        self.dob_edit = QDateEdit(self._dob_host)
        self.dob_edit.setCalendarPopup(True)
        self.dob_edit.setDisplayFormat("dd MMM yyyy")
        self.dob_edit.setDate(QDate.currentDate().addYears(-21))
        self.dob_edit.setMaximumDate(QDate.currentDate())
        self.dob_save = QPushButton("Save & re-check", self._dob_host)
        self.dob_save.setObjectName("secondaryButton")
        self.dob_save.clicked.connect(self._on_save_dob)
        dob_row.addWidget(self.dob_edit, 1)
        dob_row.addWidget(self.dob_save)
        dob_lay.addLayout(dob_row)
        self._dob_host.setVisible(False)
        self._body.addWidget(self._dob_host)

        self._form = QWidget(self.card)
        self._form.setObjectName("wizardStep")
        self._form_lay = QVBoxLayout(self._form)
        self._form_lay.setContentsMargins(0, 0, 0, 0)
        self._form_lay.setSpacing(6)
        self._form.setVisible(False)
        self._body.addWidget(self._form)
        self._form_panel = None

        back, self.next_button = self._buttons("Continue to payment")
        back.clicked.connect(self.backed.emit)
        self.next_button.clicked.connect(self._on_continue)
        self.next_button.setEnabled(False)

        if preset_customer_id is not None:
            with self._context.reading() as session:
                preset = session.get(Users, preset_customer_id)
                if preset is not None:
                    self.licence_edit.setText(preset.license_number or "")
                    self._on_find()

    def _clear_results(self) -> None:
        while self.result_host.count():
            item = self.result_host.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        while self._flag_banner_host.count():
            item = self._flag_banner_host.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def _refresh_continue(self) -> None:
        if self._picked is None:
            self.next_button.setEnabled(False)
            return
        if self._flagged and not self.override_edit.text().strip():
            self.next_button.setEnabled(False)
            return
        self.next_button.setEnabled(True)

    def _on_find(self) -> None:
        self._clear_results()
        self._form.setVisible(False)
        self._dob_host.setVisible(False)
        self.override_edit.setVisible(False)
        self.override_edit.clear()
        self._picked = None
        self._flagged = False
        self.next_button.setEnabled(False)
        code = self.licence_edit.text().strip()
        if not code:
            self._fail("Type the licence number first.")
            return
        with self._context.reading() as session:
            customer = customer_service.find_by_licence(session, code)
            if customer is None:
                self._show_create(code)
                return
            rentals = (
                session.query(Booking)
                .filter(Booking.user_id == customer.user_id)
                .count()
            )
            verdict = customer_service.eligibility_for(
                session, customer,
                self._state.start.toPython(), self._state.end.toPython(),
            )
            for warning in verdict["warnings"]:
                self.result_host.addWidget(Caption(warning, parent=self.card))
            if verdict["blocked"]:
                if verdict["needs_dob"]:
                    self._dob_host.setVisible(True)
                self._fail("; ".join(verdict["reasons"]))
                return
            box = make_status_box(
                f"{customer.full_name} · {rentals} rental{'s' if rentals != 1 else ''}",
                theme.OK,
                self.card,
            )
            self.result_host.addWidget(box)
            if verdict["flagged"]:
                self._flagged = True
                self._flag_banner_host.addWidget(
                    make_status_box(
                        f"Flagged customer: {verdict['flag_reason']}",
                        theme.DANGER,
                        self.card,
                    )
                )
                self.override_edit.setVisible(True)
            self._picked = customer.user_id
            self._error.setVisible(False)
            self._refresh_continue()

    def _on_save_dob(self) -> None:
        try:
            with self._context.session() as session:
                customer = customer_service.find_by_licence(
                    session, self.licence_edit.text()
                )
                if customer is None:
                    self._fail("Find the customer first.")
                    return
                customer_service.update_customer(
                    session, customer,
                    birthdate=self.dob_edit.date().toPython(),
                )
        except Exception as exc:  # noqa: BLE001 - inline, nothing committed
            self._fail(_friendly(exc))
            return
        self._on_find()

    def _show_create(self, licence: str) -> None:
        from app.staff.customer_panels import CustomerFormPanel

        while self._form_lay.count():
            item = self._form_lay.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._form_panel = CustomerFormPanel(
            self._context, None, preset_licence=licence, parent=self._form
        )
        self._form_panel.submitted.connect(self._on_create_values)
        self._form_panel.cancelled.connect(lambda: self._form.setVisible(False))
        self._form_lay.addWidget(self._form_panel)
        self._form.setVisible(True)
        self._error.setVisible(False)
        self.result_host.addWidget(
            Caption(
                "No account for that licence yet: take the customer details once.",
                parent=self.card,
            )
        )

    def _on_create_values(self, values: dict) -> None:
        try:
            with self._context.session() as session:
                customer = customer_service.create_customer(
                    session,
                    full_name=values["full_name"],
                    email=values["email"],
                    phone=values["phone"],
                    address=values["address"],
                    license_number=values["license_number"],
                    license_expiry=values["license_expiry"],
                    birthdate=values.get("birthdate"),
                    emergency_contact=values.get("emergency_contact"),
                    source=values.get("source") or "walk_in",
                )
                verdict = customer_service.eligibility_for(
                    session, customer,
                    self._state.start.toPython(), self._state.end.toPython(),
                )
                if verdict["blocked"]:
                    raise RuntimeError("; ".join(verdict["reasons"]))
                picked = customer.user_id
                name = customer.full_name
        except Exception as exc:  # noqa: BLE001 - inline, nothing committed
            self._fail(_friendly(exc))
            return
        self._clear_results()
        self._form.setVisible(False)
        box = make_status_box(f"{name} · first rental", theme.OK, self.card)
        self.result_host.addWidget(box)
        if values.get("front_src") or values.get("back_src"):
            try:
                with self._context.session() as session:
                    customer = session.get(Users, picked)
                    for side, key in (("front", "front_src"), ("back", "back_src")):
                        if values.get(key):
                            path = customer_service.store_licence_doc(
                                customer.license_number, side, values[key]
                            )
                            if side == "front":
                                customer.licence_front_path = path
                            else:
                                customer.licence_back_path = path
            except Exception:  # noqa: BLE001 - docs are best-effort
                pass
        self._picked = picked
        self._flagged = False
        self._error.setVisible(False)
        self._refresh_continue()

    def _on_continue(self) -> None:
        if self._picked is None:
            return
        if self._flagged and not self.override_edit.text().strip():
            self._fail("Type the override reason: flagged rentals need one on record.")
            return
        self._state.flag_override_note = (
            self.override_edit.text().strip() if self._flagged else None
        )
        self.customer_chosen.emit(self._picked)

    def _fail(self, message: str) -> None:
        self._error.setText(message)
        self._error.setVisible(True)


# -- Step 4: payment -----------------------------------------------------------


class PaymentPanel(_StepBase):
    paid = Signal(dict)
    backed = Signal()

    def __init__(self, context, state: WizardState, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        self._state = state
        self._head("Step 4 of 4 — Payment", "How much today?")
        self._error = self._error_label()

        total = state.total or money(0)
        deposit = state.deposit or money(0)
        minimum = min(SCHEDULING_FEE, total) if total > 0 else SCHEDULING_FEE

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        form.addRow(
            Caption(f"Rental ({state.days} day{'s' if state.days != 1 else ''})", parent=self.card),
            self._amount_label(total),
        )
        form.addRow(
            Caption("Security deposit (refundable hold)", parent=self.card),
            self._amount_label(deposit),
        )
        self._body.addLayout(form)
        self._body.addWidget(
            Caption(
                f"Includes {pesos(SCHEDULING_FEE)} non-refundable scheduling fee. "
                "Pay the minimum now and the rest at pick-up, or everything up front.",
                parent=self.card,
            )
        )

        self.choice_group = QButtonGroup(self)
        self.min_button = make_choice_button(
            f"Pay minimum — {pesos(minimum + deposit)} (fee + deposit)", self.card
        )
        self.full_button = make_choice_button(
            f"Pay in full — {pesos(total + deposit)}", self.card
        )
        self.choice_group.addButton(self.min_button)
        self.choice_group.addButton(self.full_button)
        self.min_button.setChecked(True)
        self.min_button.clicked.connect(self._refresh_pay_button)
        self.full_button.clicked.connect(self._refresh_pay_button)
        self._body.addWidget(self.min_button)
        self._body.addWidget(self.full_button)
        self._minimum, self._full = minimum, total

        self._body.addWidget(SectionLabel("Method", parent=self.card))
        self.method_group = QButtonGroup(self)
        methods = QHBoxLayout()
        methods.setContentsMargins(0, 0, 0, 0)
        methods.setSpacing(8)
        self.method_buttons: dict[str, QPushButton] = {}
        for method, chip_colour, label in (
            ("cash", theme.METHOD_DOT_COLOURS["cash"], "Cash"),
            ("gcash", theme.METHOD_DOT_COLOURS["gcash"], "GCash"),
            ("card", theme.METHOD_DOT_COLOURS["card"], "Card"),
        ):
            cell = QHBoxLayout()
            cell.setContentsMargins(0, 0, 0, 0)
            cell.setSpacing(6)
            chip = QLabel("", self.card)
            chip.setObjectName("wizardChip")
            chip.setFixedSize(12, 12)
            chip.setStyleSheet(f"QLabel#wizardChip {{ background: {chip_colour}; }}")
            cell.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
            button = make_choice_button(label, self.card)
            self.method_group.addButton(button)
            cell.addWidget(button, 1)
            methods.addLayout(cell, 1)
            self.method_buttons[method] = button
        self.method_buttons["cash"].setChecked(True)
        self._body.addLayout(methods)

        ref_row = QFormLayout()
        ref_row.setContentsMargins(0, 0, 0, 0)
        ref_row.setSpacing(6)
        self.ref_edit = QLineEdit(self.card)
        self.ref_edit.setPlaceholderText("Required for GCash")
        ref_row.addRow(Caption("Reference no.", parent=self.card), self.ref_edit)
        self._body.addLayout(ref_row)
        for button in self.method_buttons.values():
            button.clicked.connect(self._sync_ref)
        self._sync_ref()

        back, self.pay_button = self._buttons("Pay")
        back.clicked.connect(self.backed.emit)
        self.pay_button.clicked.connect(self._on_pay)
        self._refresh_pay_button()

    def _amount_label(self, value) -> QLabel:
        label = MoneyNumber(parent=self.card)
        label.set_colours(dim=theme.MUTED, bright=theme.TEXT)
        label.set_amount(value)
        return label

    def _selection(self) -> tuple[str, Decimal]:
        if self.full_button.isChecked():
            return "full", self._full
        return "minimum", self._minimum

    def _method(self) -> str:
        for method, button in self.method_buttons.items():
            if button.isChecked():
                return method
        return "cash"

    def _sync_ref(self) -> None:
        gcash = self._method() == "gcash"
        self.ref_edit.setEnabled(gcash)
        if not gcash:
            self.ref_edit.clear()

    def _refresh_pay_button(self) -> None:
        _choice, rental = self._selection()
        deposit = self._state.deposit or money(0)
        self.pay_button.setText(f"Pay {pesos(rental + deposit)}")

    def _on_pay(self) -> None:
        _choice, rental = self._selection()
        method = self._method()
        if method == "gcash" and not self.ref_edit.text().strip():
            self._error.setText("A GCash reference number is required.")
            self._error.setVisible(True)
            return
        self._error.setVisible(False)
        self.paid.emit(
            {
                "choice": _choice,
                "rental_amount": rental,
                "method": method,
                "reference_no": self.ref_edit.text().strip(),
            }
        )

    def fail(self, message: str) -> None:
        self._error.setText(message)
        self._error.setVisible(True)


# -- Step 5: done --------------------------------------------------------------


class DonePanel(_StepBase):
    view_bookings = Signal()
    finished = Signal()

    def __init__(
        self,
        *,
        booking_id: int,
        total,
        paid_amount,
        balance,
        deposit,
        receipt_path: str,
        confirmed: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        settled = balance <= 0
        self._head("Done", f"Rental #{booking_id} is booked")
        verdict = make_status_box(
            "Paid in full — ready for pick-up"
            if settled
            else f"Balance due at pick-up: {pesos(balance)}",
            theme.OK if settled else theme.WARN,
            self.card,
        )
        self._body.addWidget(verdict)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        form.addRow(Caption("Rental total", parent=self.card), self._amount(total))
        form.addRow(Caption("Paid today", parent=self.card), self._amount(paid_amount))
        form.addRow(
            Caption("Deposit held (refundable)", parent=self.card),
            self._amount(deposit),
        )
        if confirmed:
            form.addRow(
                Caption("Status", parent=self.card),
                Caption("Confirmed", parent=self.card, colour=theme.TEXT),
            )
        self._body.addLayout(form)
        self._body.addWidget(Caption(f"Receipt: {receipt_path}", parent=self.card))

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addStretch(1)
        self.print_button = QPushButton("Print receipt", self.card)
        self.print_button.setObjectName("secondaryButton")
        self.print_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(receipt_path))
        )
        self.view_button = QPushButton("View in Bookings", self.card)
        self.view_button.setObjectName("secondaryButton")
        self.view_button.clicked.connect(self.view_bookings.emit)
        self.done_button = QPushButton("Done", self.card)
        self.done_button.setObjectName("primaryButton")
        self.done_button.clicked.connect(self.finished.emit)
        row.addWidget(self.print_button)
        row.addWidget(self.view_button)
        row.addWidget(self.done_button)
        self._body.addLayout(row)

    def _amount(self, value) -> QLabel:
        label = MoneyNumber(parent=self.card)
        label.set_colours(dim=theme.MUTED, bright=theme.TEXT)
        label.set_amount(value)
        return label


# -- controller ----------------------------------------------------------------


class RentalWizard:
    """Owns the wizard state and swaps step panels in the overlay scroll.

    The frosted backdrop is rendered once for the details panel and left
    alone: the page behind never changes between steps.
    """

    def __init__(
        self,
        context,
        overlay,
        vehicle_id: int,
        start: QDate,
        end: QDate,
        *,
        navigate=None,
        notify=None,
        customer_id: int | None = None,
    ) -> None:
        self._context = context
        self._overlay = overlay
        self._navigate = navigate
        self._notify = notify
        self.state = WizardState(
            vehicle_id=vehicle_id, start=start, end=end,
            customer_id=customer_id,
        )
        self._details = None
        self._current = None

    def start(self) -> None:
        self._swap(SchedulePanel(self._context, self.state, self._overlay.scroll))
        self._current.continued.connect(self._on_scheduled)
        self._current.backed.connect(self._back_to_details)

    # -- swapping ------------------------------------------------------

    def _swap(self, widget: QWidget):
        if self._details is None:
            self._details = self._overlay.scroll.takeWidget()
        else:
            old = self._overlay.scroll.takeWidget()
            if old is not None and old is not self._details:
                old.deleteLater()
        self._overlay.scroll.setWidget(widget)
        self._overlay.scroll.verticalScrollBar().setValue(0)
        self._current = widget
        return widget

    def _back_to_details(self) -> None:
        if self._details is not None:
            old = self._overlay.scroll.takeWidget()
            if old is not None:
                old.deleteLater()
            self._overlay.scroll.setWidget(self._details)
            self._current = self._details
            self._details = None

    # -- steps ---------------------------------------------------------

    def _on_scheduled(self, start: QDate, end: QDate) -> None:
        self.state.start, self.state.end = start, end
        try:
            with self._context.reading() as session:
                vehicle = session.get(Vehicle, self.state.vehicle_id)
                days, total = booking_service.quote_for(
                    session, vehicle, start.toPython(), end.toPython()
                )
                deposit = booking_service.deposit_for_vehicle(vehicle)
        except Exception as exc:  # noqa: BLE001 - inline, stay on step
            self._current._error.setText(_friendly(exc))
            self._current._error.setVisible(True)
            return
        self.state.days, self.state.total, self.state.deposit = days, total, deposit
        panel = LicencePanel(
            self._context, self.state, self._overlay.scroll,
            preset_customer_id=self.state.customer_id,
        )
        self._swap(panel)
        panel.customer_chosen.connect(self._on_customer)
        panel.backed.connect(self._back_to_schedule)

    def _back_to_schedule(self) -> None:
        panel = SchedulePanel(self._context, self.state, self._overlay.scroll)
        self._swap(panel)
        panel.continued.connect(self._on_scheduled)
        panel.backed.connect(self._back_to_details)

    def _on_customer(self, customer_id: int) -> None:
        self.state.customer_id = customer_id
        panel = PaymentPanel(self._context, self.state, self._overlay.scroll)
        self._swap(panel)
        panel.paid.connect(self._on_pay)
        panel.backed.connect(self._back_to_licence)

    def _back_to_licence(self) -> None:
        panel = LicencePanel(
            self._context, self.state, self._overlay.scroll,
            preset_customer_id=self.state.customer_id,
        )
        self._swap(panel)
        panel.customer_chosen.connect(self._on_customer)
        panel.backed.connect(self._back_to_schedule)

    def _on_pay(self, values: dict) -> None:
        panel = self._current
        try:
            staff_id = self._context.user.user_id
            with self._context.session() as session:
                user = session.get(Users, self.state.customer_id)
                vehicle = session.get(Vehicle, self.state.vehicle_id)
                booking, confirmed = booking_service.complete_counter_rental(
                    session,
                    user=user,
                    vehicle=vehicle,
                    start_date=self.state.start.toPython(),
                    end_date=self.state.end.toPython(),
                    rental_amount=values["rental_amount"],
                    method=values["method"],
                    staff_id=staff_id,
                    reference_no=values.get("reference_no") or None,
                    flag_override_note=self.state.flag_override_note,
                    flag_override_by=staff_id,
                )
                balance = payment_service.booking_balance(session, booking)
                receipt_path = generate_receipt_pdf(session, booking)
                result = {
                    "booking_id": booking.booking_id,
                    "total": balance.total_due,
                    "paid_amount": balance.amount_paid,
                    "balance": balance.balance,
                    "deposit": balance.deposit_held,
                    "receipt_path": receipt_path,
                    "confirmed": confirmed,
                }
        except Exception as exc:  # noqa: BLE001 - inline, nothing committed
            panel.fail(_friendly(exc))
            return
        done = DonePanel(parent=self._overlay.scroll, **result)
        self._swap(done)
        done.view_bookings.connect(self._on_view_bookings)
        done.finished.connect(self._on_finished)
        if self._notify:
            self._notify(f"Rental #{result['booking_id']} is booked.")

    def _on_view_bookings(self) -> None:
        self._overlay.hide_overlay()
        if self._navigate:
            self._navigate("bookings")

    def _on_finished(self) -> None:
        self._overlay.hide_overlay()
