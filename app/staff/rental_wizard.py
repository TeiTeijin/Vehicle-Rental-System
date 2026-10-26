"""Counter rental wizard: Steps 2-5 inside the check-details overlay.

Step 1 is the vehicle details panel (gallery, specs, availability checker).
Pressing Rent swaps the same frosted card to:

- Step 2 Scheduling: dates, day-by-day availability, live total.
- Step 3 Customer: licence-first lookup (exact match reuses the row, so
  repeat renters never duplicate); inline create form only on no match.
- Step 4 Payment: pay-minimum / pay-full, method, and a split summary.
- Step 5 Done: verdict, balance box, receipt row, Print / View / Done.

Every step uses the same sheet from `app.staff.rental_ui`: a progress rail,
title, two columns (content + warm recap), and a footer with Cancel rental on
the left and the primary action on the right.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QDate, QObject, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.models import Booking, Users, Vehicle
from app.services import (
    booking_service,
    counter_service,
    customer_service,
    payment_service,
    settings_service,
)
from app.services.errors import ValidationError
from app.services.receipt_service import generate_receipt_pdf
from app.staff import icons, theme
from app.staff.cards import Caption, Card, MoneyNumber, SectionLabel
from app.staff.feedback import describe
from app.staff.rental_ui import (
    MODAL_DANGER,
    MODAL_INK,
    MODAL_MUTED,
    MODAL_PADDING,
    MODAL_SURFACE,
    MODAL_WARN,
    DayStrip,
    MetaTile,
    ModalStep,
    NoticeBar,
    StatusChip,
    VehicleSummary,
)
from app.staff.widgets import confirm_modal
from app.utils.money import money, pesos


@dataclass
class WizardState:
    vehicle_id: int
    start: QDate
    end: QDate
    days: int = 1
    total: Decimal | None = None
    deposit: Decimal | None = None
    scheduling_fee: Decimal | None = None
    default_method: str = "cash"
    default_channel: str = "walk_in"
    customer_id: int | None = None
    #: Staff-typed reason for proceeding past a customer flag. Written to
    #: the booking on Pay; NULL means no flag was overridden.
    flag_override_note: str | None = None


#: Kept as aliases for callers that imported the old density constants.
WIZARD_PADDING = MODAL_PADDING
WIZARD_SPACING = 12


# -- shared bits --------------------------------------------------------------


def _friendly(error: BaseException) -> str:
    message, _field, _kind = describe(error)
    return message


def _section(text: str, parent: QWidget) -> SectionLabel:
    return SectionLabel(text, parent=parent, size=13)


def _money(
    amount,
    parent: QWidget,
    *,
    size: int = 13,
    weight: QFont.Weight = QFont.Weight.DemiBold,
) -> MoneyNumber:
    label = MoneyNumber(
        parent=parent,
        size=size,
        dim=MODAL_MUTED,
        bright=MODAL_INK,
        weight=weight,
    )
    label.set_amount(amount)
    return label


def _rule(parent: QWidget) -> QFrame:
    rule = QFrame(parent)
    rule.setObjectName("modalRule")
    rule.setFixedHeight(1)
    return rule


class _StepBase(QWidget):
    """Legacy inline-card host used by the customer form/detail panels.

    Those panels predate the counter redraw and are still swapped into the
    customer step as forms, so they keep their compact card recipe. New steps
    use `ModalStep` and the warm two-column sheet.
    """

    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("wizardStep")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addStretch(1)
        self.card = Card(self, radius=18)
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
        self._body.addWidget(SectionLabel(title, parent=self.card))

    def _error_label(self) -> QLabel:
        label = QLabel("", self.card)
        label.setObjectName("fieldError")
        label.setWordWrap(True)
        label.setVisible(False)
        self._body.addWidget(label)
        return label

    def _buttons(
        self, primary_text: str, *, cancel: bool = False
    ) -> tuple[QPushButton, QPushButton]:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        if cancel:
            leave = QPushButton("Cancel rental", self.card)
            leave.setObjectName("secondaryButton")
            leave.clicked.connect(self.cancel_requested.emit)
            row.addWidget(leave)
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
    """8px status box with a square chip (legacy inline panels)."""
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


def _legend_item(text: str, colour: str, parent: QWidget) -> QWidget:
    host = QWidget(parent)
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(5)
    chip = QLabel(host)
    chip.setObjectName("legendChip")
    chip.setFixedSize(9, 9)
    chip.setStyleSheet(
        f"QLabel#legendChip {{ background: {colour}; border-radius: 2px; }}"
    )
    row.addWidget(chip)
    label = Caption(text, parent=host, colour=MODAL_MUTED, size=10)
    row.addWidget(label)
    return host


def _date_cell(
    parent: QWidget, label: str, initial: QDate, minimum: QDate
) -> tuple[QWidget, QDateEdit]:
    host = QWidget(parent)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lay.addWidget(Caption(label, parent=host, colour=MODAL_MUTED, size=10))
    edit = QDateEdit(host)
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("dd MMM yyyy")
    edit.setDate(initial)
    edit.setMinimumDate(minimum)
    edit.setMinimumHeight(36)
    lay.addWidget(edit)
    return host, edit


def _summary_row(
    label: str,
    value,
    parent: QWidget,
    *,
    note: str | None = None,
) -> QWidget:
    row = QWidget(parent)
    row.setObjectName("rentalModalBody")
    lay = QHBoxLayout(row)
    lay.setContentsMargins(14, 10, 14, 10)
    lay.setSpacing(10)
    text = QVBoxLayout()
    text.setContentsMargins(0, 0, 0, 0)
    text.setSpacing(1)
    text.addWidget(Caption(label, parent=row, colour=MODAL_INK, size=12))
    if note:
        text.addWidget(Caption(note, parent=row, colour=MODAL_MUTED, size=10))
    lay.addLayout(text, 1)
    if isinstance(value, QWidget):
        amount = value
    else:
        amount = _money(value, row, size=13)
    lay.addWidget(amount, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


class RadioDot(QWidget):
    """A hand-painted radio ring; Qt's QSS indicator squares off."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._checked = False

    def set_checked(self, checked: bool) -> None:
        self._checked = bool(checked)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.rect().adjusted(1, 1, -1, -1)
            painter.setBrush(QColor("#FFFFFF"))
            painter.setPen(QPen(QColor("#171717" if self._checked else "#B6A791"), 1.4))
            painter.drawEllipse(rect)
            if self._checked:
                inner = rect.adjusted(5, 5, -5, -5)
                painter.setBrush(QColor("#171717"))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(inner)
        finally:
            painter.end()


class PaymentOption(QFrame):
    """A radio card: title + explanation on the left, amount on the right."""

    def __init__(
        self,
        title: str,
        subtitle: str,
        amount,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("paymentOption")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(10)

        self.radio = QRadioButton(self)
        self.radio.setObjectName("paymentRadio")
        self.radio.hide()
        self.dot = RadioDot(self)
        layout.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        self.title_label = Caption(
            title, parent=self, colour=MODAL_INK, size=13,
            weight=QFont.Weight.Bold,
        )
        self.title_label.setObjectName("paymentTitle")
        self.subtitle_label = Caption(
            subtitle, parent=self, colour=MODAL_MUTED, size=11
        )
        self.subtitle_label.setObjectName("paymentSub")
        self.subtitle_label.setWordWrap(True)
        text.addWidget(self.title_label)
        text.addWidget(self.subtitle_label)
        layout.addLayout(text, 1)

        self.amount_label = _money(amount, self, size=16)
        layout.addWidget(self.amount_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.radio.toggled.connect(self._on_toggled)

    def _on_toggled(self, checked: bool) -> None:
        self.dot.set_checked(checked)
        self.setProperty("selected", checked)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            self.radio.setChecked(True)
        super().mousePressEvent(event)


# -- Step 2: scheduling --------------------------------------------------------


class SchedulePanel(ModalStep):
    continued = Signal(QDate, QDate)
    backed = Signal()

    def __init__(self, context, state: WizardState, parent=None) -> None:
        super().__init__(context, parent)
        self._context = context
        self._state = state
        self._days = state.days
        self._total = state.total
        self._missing = False
        self._head(1, "When is it out?")
        self._error = self._error_label()

        left, recap = self._two_columns("Total for 1 day")
        self.recap = recap
        # The title already says "Total for N days"; the reference puts the
        # big figure straight under it and the fee below.
        recap.rule.hide()
        recap.key_label.hide()

        with context.reading() as session:
            vehicle = session.get(Vehicle, state.vehicle_id)
            if vehicle is None:
                self._missing = True
                left.addWidget(
                    NoticeBar(
                        "That vehicle no longer exists.",
                        tone="danger",
                        parent=self.content_host,
                    )
                )
                self.start_edit = QDateEdit(self.content_host)
                self.start_edit.setDate(state.start)
                self.start_edit.hide()
                self.end_edit = QDateEdit(self.content_host)
                self.end_edit.setDate(state.end)
                self.end_edit.hide()
                self.total = recap.key_amount
                self.total_caption = Caption("", parent=self.content_host)
                self.fee_caption = Caption("", parent=self.content_host)
                self.conflict_label = QLabel("", self.content_host)
                self.conflict_banner = NoticeBar("", parent=self.content_host)
                self.conflict_banner.hide()
                self.day_strip = DayStrip(self.content_host)
                self.day_strip.hide()
                back, self.next_button = self._buttons("Continue")
                back.clicked.connect(self.backed.emit)
                self.next_button.setEnabled(False)
                return
            self._vehicle = {
                "make": vehicle.make or "",
                "model": vehicle.model or "",
                "year": int(vehicle.year or 0),
                "plate": vehicle.plate_number or "",
            }

        self.vehicle_card = VehicleSummary(
            self._vehicle["make"],
            self._vehicle["model"],
            self._vehicle["year"],
            self._vehicle["plate"],
            parent=self.content_host,
            action_text="Change",
        )
        self.vehicle_card.action.clicked.connect(self.backed.emit)
        left.addWidget(self.vehicle_card)

        dates = QHBoxLayout()
        dates.setContentsMargins(0, 0, 0, 0)
        dates.setSpacing(8)
        today = QDate.currentDate()
        start_host, self.start_edit = _date_cell(
            self.content_host, "Pick-up", state.start, today
        )
        end_host, self.end_edit = _date_cell(
            self.content_host, "Return", state.end, state.start
        )
        dates.addWidget(start_host, 1)
        self.duration_chip = QLabel("1 day", self.content_host)
        self.duration_chip.setObjectName("plateChip")
        self.duration_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.duration_chip.setFixedHeight(24)
        dates.addWidget(self.duration_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        dates.addWidget(end_host, 1)
        left.addLayout(dates)

        left.addWidget(_section("Availability", self.content_host))
        self.day_strip = DayStrip(self.content_host)
        left.addWidget(self.day_strip)

        # A cream strip gives the legend text (especially "Your dates") its
        # own background instead of sitting bare on the white sheet.
        self.legend_host = QFrame(self.content_host)
        self.legend_host.setObjectName("wizardLegend")
        legend = QHBoxLayout(self.legend_host)
        legend.setContentsMargins(10, 6, 10, 6)
        legend.setSpacing(14)
        legend.addWidget(
            _legend_item("Your dates", MODAL_INK, self.legend_host)
        )
        legend.addWidget(
            _legend_item("Confirmed booking", "#9CC5A7", self.legend_host)
        )
        legend.addWidget(
            _legend_item("Pending booking", "#EACF9F", self.legend_host)
        )
        legend.addWidget(
            _legend_item("Free", MODAL_SURFACE, self.legend_host)
        )
        legend.addStretch(1)
        left.addWidget(self.legend_host)

        self.conflict_banner = NoticeBar("", parent=self.content_host)
        self.conflict_label = self.conflict_banner.label
        self.conflict_banner.hide()
        left.addWidget(self.conflict_banner)

        # Compatibility labels: the old tests read these even though the
        # visible numbers now live in the recap column.
        self.total = recap.key_amount
        self.total_caption = Caption("", parent=self.content_host, size=11)
        self.total_caption.hide()
        self.fee_caption = Caption("", parent=self.content_host, size=11)
        self.fee_caption.hide()

        self.fee_amount = _money(0, recap, size=12)
        recap.add_extra("Scheduling fee, non-refundable", self.fee_amount)
        recap.set_note(
            "Included in the total above. Same-day return counts as 1 day."
        )

        self.start_edit.dateChanged.connect(self._on_start_changed)
        self.start_edit.dateChanged.connect(self._refresh_quote)
        self.end_edit.dateChanged.connect(self._refresh_quote)

        back, self.next_button = self._buttons("Continue")
        back.clicked.connect(self.backed.emit)
        self.next_button.clicked.connect(self._on_continue)
        self._refresh_quote()

    def _on_start_changed(self, day: QDate) -> None:
        self.end_edit.setMinimumDate(day)
        if self.end_edit.date() < day:
            self.end_edit.setDate(day)

    def _preview_range(self, start: date, end: date) -> tuple[date, date]:
        today = date.today()
        first = today if start <= today + timedelta(days=45) else start - timedelta(days=7)
        last = max(end, start + timedelta(days=12))
        return first, last

    def _refresh_quote(self) -> None:
        if self._missing:
            return
        start = self.start_edit.date().toPython()
        end = self.end_edit.date().toPython()
        if end < start:
            end = start
        self.duration_chip.setText(
            f"{max(1, (end - start).days)} day"
            if max(1, (end - start).days) == 1
            else f"{max(1, (end - start).days)} days"
        )

        preview_first, preview_last = self._preview_range(start, end)
        states: dict[date, str] = {}
        conflict = None
        quote = None
        quote_error = None
        fee = self._state.scheduling_fee
        daily_rate = money(0)
        try:
            with self._context.reading() as session:
                vehicle = session.get(Vehicle, self._state.vehicle_id)
                daily_rate = vehicle.daily_rate or money(0)
                states = booking_service.availability_states(
                    session, vehicle, preview_first, preview_last
                )
                display_end = end if end > start else start + timedelta(days=1)
                for booking in booking_service.active_bookings(session, vehicle):
                    booked_end = booking.end_date or (
                        booking.start_date + timedelta(days=1)
                    )
                    if booking.start_date < display_end and start < booked_end:
                        conflict = {
                            "start": booking.start_date,
                            "end": booking.end_date,
                            "status": booking.status,
                            "id": booking.booking_id,
                            "customer": (
                                booking.user.full_name if booking.user else "a customer"
                            ),
                        }
                        break
                if fee is None:
                    fee = settings_service.scheduling_fee(session)
                try:
                    quote = counter_service.quote(session, vehicle, start, end)
                except Exception as exc:  # noqa: BLE001 - shown below
                    quote_error = exc
        except Exception as exc:  # noqa: BLE001 - advisory preview only
            quote_error = exc

        if quote is not None:
            days, total = quote.days, quote.total
        elif conflict is not None:
            # The quote path deliberately refuses a clash. Keep the figure
            # honest with the live daily rate and let the warning own the
            # step: Continue is disabled anyway.
            days = max(1, (end - start).days)
            total = daily_rate * days
        else:
            self.recap.set_title("Total")
            self.total.set_amount(0)
            self.total_caption.setText(
                _friendly(quote_error) if quote_error else "Total unavailable."
            )
            self.fee_caption.setText("")
            self.fee_amount.set_amount(0)
            self.next_button.setEnabled(False)
            self.conflict_banner.hide()
            if states:
                self.day_strip.set_range(
                    preview_first, preview_last, states, selected=(start, end)
                )
            return

        self._days, self._total = days, total
        day_word = "day" if days == 1 else "days"
        self.recap.set_title(f"Total for {days} {day_word}")
        self.recap.set_key("Total", total)
        self.total.set_amount(total)
        self.total_caption.setText(f"for {days} {day_word}")
        self.fee_amount.set_amount(fee if fee is not None else money(0))
        fee_note = (
            f"Includes {pesos(fee if fee is not None else money(0))} "
            "non-refundable scheduling fee. Same-day return counts as 1 day."
        )
        self.fee_caption.setText(fee_note)

        self.day_strip.set_range(
            preview_first, preview_last, states, selected=(start, end)
        )

        if conflict is not None:
            self.conflict_label.setText(
                "This vehicle is already booked "
                f"{conflict['start']:%d %b} \u2192 "
                f"{conflict['end']:%d %b} "
                f"({conflict['customer']} \u00b7 #{conflict['id']})."
            )
            self.conflict_banner.setProperty(
                "tone", "warn" if conflict["status"] == "pending" else "danger"
            )
            self.conflict_banner.style().unpolish(self.conflict_banner)
            self.conflict_banner.style().polish(self.conflict_banner)
            self.conflict_banner.show()
            self.next_button.setEnabled(False)
        else:
            self.conflict_banner.hide()
            self.next_button.setEnabled(True)

    def _on_continue(self) -> None:
        self.continued.emit(self.start_edit.date(), self.end_edit.date())


# -- Step 3: customer ----------------------------------------------------------


class LicencePanel(ModalStep):
    customer_chosen = Signal(int)
    backed = Signal()

    def __init__(
        self,
        context,
        state: WizardState,
        parent=None,
        *,
        preset_customer_id: int | None = None,
    ) -> None:
        super().__init__(context, parent)
        self._context = context
        self._state = state
        self._picked: int | None = None
        self._flagged = False
        self.customer_card: QFrame | None = None
        self.customer_name: QLabel | None = None
        self.validity_chip: QLabel | None = None
        self.past_rentals_label: QLabel | None = None
        self._head(2, "Whose licence is this?")
        self._error = self._error_label()

        left, recap = self._two_columns("Rental so far")
        self.recap = recap

        with context.reading() as session:
            vehicle = session.get(Vehicle, state.vehicle_id)
            name = f"{vehicle.make} {vehicle.model}".strip() if vehicle else "Vehicle"
            make = vehicle.make or "" if vehicle else ""
            model = vehicle.model or "" if vehicle else ""
            year = int(vehicle.year or 0) if vehicle else 0
            plate = vehicle.plate_number or "" if vehicle else ""
        recap.add_widget(
            VehicleSummary(make, model, year, plate, parent=recap)
        )
        recap.add_row("Pick-up", f"{state.start.toString('dd MMM yyyy')}")
        recap.add_row("Return", f"{state.end.toString('dd MMM yyyy')}")
        recap.add_row(
            "Duration",
            f"{state.days} day" if state.days == 1 else f"{state.days} days",
        )
        recap.set_key("Total", state.total if state.total is not None else money(0))

        left.addWidget(Caption("Licence number", parent=self.content_host, size=10))
        finder = QHBoxLayout()
        finder.setContentsMargins(0, 0, 0, 0)
        finder.setSpacing(8)
        self.licence_edit = QLineEdit(self.content_host)
        self.licence_edit.setPlaceholderText("Licence number on the card")
        self.licence_edit.setMinimumHeight(38)
        self.find_button = QPushButton("Find", self.content_host)
        self.find_button.setObjectName("primaryButton")
        self.find_button.setMinimumHeight(38)
        self.find_button.setMinimumWidth(64)
        self.find_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.find_button.clicked.connect(self._on_find)
        self.licence_edit.returnPressed.connect(self._on_find)
        finder.addWidget(self.licence_edit, 1)
        finder.addWidget(self.find_button)
        left.addLayout(finder)

        self.notice = NoticeBar(
            "A valid licence is required. No licence, no rental.",
            icon="info",
            parent=self.content_host,
        )
        left.addWidget(self.notice)

        self.add_link_row = QWidget(self.content_host)
        self.add_link_row.setObjectName("rentalModalBody")
        add_row = QHBoxLayout(self.add_link_row)
        add_row.setContentsMargins(0, 0, 0, 0)
        add_row.setSpacing(4)
        add_row.addWidget(
            Caption(
                "Customer not registered yet?", parent=self.add_link_row,
                colour=MODAL_MUTED, size=12,
            )
        )
        self.add_customer_link = QPushButton("Add new customer", self.add_link_row)
        self.add_customer_link.setObjectName("linkButton")
        self.add_customer_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_customer_link.clicked.connect(self._on_add_customer)
        add_row.addWidget(self.add_customer_link)
        add_row.addStretch(1)
        left.addWidget(self.add_link_row)

        self.result_host = QVBoxLayout()
        self.result_host.setContentsMargins(0, 0, 0, 0)
        self.result_host.setSpacing(8)
        left.addLayout(self.result_host)

        self.search_again_row = QWidget(self.content_host)
        self.search_again_row.setObjectName("rentalModalBody")
        again = QHBoxLayout(self.search_again_row)
        again.setContentsMargins(0, 0, 0, 0)
        again.setSpacing(4)
        again.addWidget(
            Caption(
                "Not this person?", parent=self.search_again_row,
                colour=MODAL_MUTED, size=12,
            )
        )
        self.search_again_link = QPushButton("Search again", self.search_again_row)
        self.search_again_link.setObjectName("linkButton")
        self.search_again_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_again_link.clicked.connect(self._on_search_again)
        again.addWidget(self.search_again_link)
        again.addStretch(1)
        self.search_again_row.hide()
        left.addWidget(self.search_again_row)

        # Flag banner + required override reason.
        self._flag_banner_host = QVBoxLayout()
        self._flag_banner_host.setContentsMargins(0, 0, 0, 0)
        self._flag_banner_host.setSpacing(8)
        left.addLayout(self._flag_banner_host)
        self.override_edit = QLineEdit(self.content_host)
        self.override_edit.setPlaceholderText(
            "Override reason (required for flagged customer)"
        )
        self.override_edit.textChanged.connect(lambda _t: self._refresh_continue())
        self.override_edit.setVisible(False)
        left.addWidget(self.override_edit)

        # Inline birthdate fix for legacy rows: block once, fix on the spot.
        self._dob_host = QWidget(self.content_host)
        self._dob_host.setObjectName("wizardStep")
        dob_lay = QVBoxLayout(self._dob_host)
        dob_lay.setContentsMargins(0, 0, 0, 0)
        dob_lay.setSpacing(6)
        dob_lay.addWidget(
            Caption(
                "Birthdate is missing: add it to check the renting age.",
                parent=self._dob_host,
            )
        )
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
        left.addWidget(self._dob_host)

        self._form = QWidget(self.content_host)
        self._form.setObjectName("wizardStep")
        self._form_lay = QVBoxLayout(self._form)
        self._form_lay.setContentsMargins(0, 0, 0, 0)
        self._form_lay.setSpacing(6)
        self._form.setVisible(False)
        left.addWidget(self._form)
        self._form_panel = None
        left.addStretch(1)

        back, self.next_button = self._buttons(
            "Continue to payment", hint="Find a customer to continue"
        )
        back.clicked.connect(self.backed.emit)
        self.next_button.clicked.connect(self._on_continue)
        self.next_button.setEnabled(False)

        if preset_customer_id is not None:
            with self._context.reading() as session:
                preset = session.get(Users, preset_customer_id)
                if preset is not None:
                    self.licence_edit.setText(preset.license_number or "")
                    self._on_find()

    # -- result state --------------------------------------------------

    def _clear_results(self) -> None:
        for host in (self.result_host, self._flag_banner_host):
            while host.count():
                item = host.takeAt(0)
                if item.widget() is not None:
                    item.widget().hide()
                    item.widget().deleteLater()
        self.customer_card = None
        self.customer_name = None
        self.validity_chip = None
        self.past_rentals_label = None

    def _refresh_continue(self) -> None:
        if self._picked is None:
            self.next_button.setEnabled(False)
            self.hint_label.setText("Find a customer to continue")
            self.hint_label.show()
            return
        if self._flagged and not self.override_edit.text().strip():
            self.next_button.setEnabled(False)
            self.hint_label.setText("Add an override reason to continue")
            self.hint_label.show()
            return
        self.next_button.setEnabled(True)
        self.hint_label.hide()

    def _snapshot(self, session, customer, rentals: int) -> dict:
        return {
            "id": customer.user_id,
            "name": customer.full_name,
            "licence": customer.license_number or "",
            "phone": customer.phone,
            "rentals": rentals,
            "expiry": customer.license_expiry,
        }

    def _show_customer(self, snapshot: dict) -> None:
        self._clear_results()
        self.notice.hide()
        self.add_link_row.hide()
        self.search_again_row.show()

        card = QFrame(self.content_host)
        card.setObjectName("customerCard")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(14, 12, 14, 12)
        card_lay.setSpacing(10)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        avatar = QLabel(card)
        avatar.setObjectName("customerAvatar")
        avatar.setFixedSize(36, 36)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        initials = "".join(
            part[0] for part in (snapshot["name"] or "").split()[:2]
        ).upper() or "?"
        avatar.setText(initials)
        head.addWidget(avatar)
        identity = QVBoxLayout()
        identity.setContentsMargins(0, 0, 0, 0)
        identity.setSpacing(1)
        self.customer_name = Caption(
            snapshot["name"], parent=card, colour=MODAL_INK, size=15,
            weight=QFont.Weight.Bold,
        )
        self.customer_name.setObjectName("customerName")
        licence = Caption(
            f"Licence {snapshot['licence']}", parent=card,
            colour=MODAL_MUTED, size=11,
        )
        licence.setObjectName("customerLicence")
        identity.addWidget(self.customer_name)
        identity.addWidget(licence)
        head.addLayout(identity, 1)
        if snapshot["expiry"] is not None:
            self.validity_chip = StatusChip(
                f"Valid until {snapshot['expiry']:%d %b %Y}",
                tone="ok",
                parent=card,
            )
            head.addWidget(self.validity_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        card_lay.addLayout(head)

        stats = QHBoxLayout()
        stats.setContentsMargins(0, 0, 0, 0)
        stats.setSpacing(7)
        phone = MetaTile("Phone", snapshot["phone"] or "\u2014", card)
        rentals = MetaTile("Past rentals", str(snapshot["rentals"]), card)
        covers = MetaTile("Licence covers", "Return date", card)
        self.past_rentals_label = rentals.value_label
        stats.addWidget(phone, 1)
        stats.addWidget(rentals, 1)
        stats.addWidget(covers, 1)
        card_lay.addLayout(stats)

        self.result_host.addWidget(card)
        self.customer_card = card
        self._picked = snapshot["id"]
        self._flagged = False
        self._error.setVisible(False)
        self._refresh_continue()

    def _on_find(self) -> None:
        self._clear_results()
        self._form.setVisible(False)
        self._dob_host.setVisible(False)
        self.override_edit.setVisible(False)
        self.override_edit.clear()
        self._picked = None
        self._flagged = False
        self.next_button.setEnabled(False)
        self.notice.show()
        self.search_again_row.hide()
        self.add_link_row.show()
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
                session,
                customer,
                self._state.start.toPython(),
                self._state.end.toPython(),
                expiring_days=settings_service.get_int(
                    session, "licence_expiring_days"
                ),
            )
            snapshot = self._snapshot(session, customer, rentals)
            warnings = list(verdict["warnings"])
            reasons = list(verdict["reasons"])
            blocked = bool(verdict["blocked"])
            flagged = bool(verdict["flagged"])
            flag_reason = verdict["flag_reason"]
            needs_dob = bool(verdict["needs_dob"])

        for warning in warnings:
            self.result_host.addWidget(
                Caption(warning, parent=self.content_host, colour=MODAL_WARN)
            )
        if blocked:
            if needs_dob:
                self._dob_host.setVisible(True)
            self._fail("; ".join(reasons))
            return

        self._show_customer(snapshot)
        if flagged:
            self._flagged = True
            self._flag_banner_host.addWidget(
                NoticeBar(
                    f"Flagged customer: {flag_reason}",
                    tone="danger",
                    parent=self.content_host,
                )
            )
            self.override_edit.setVisible(True)
            self._refresh_continue()

    def _on_search_again(self) -> None:
        self.licence_edit.clear()
        self._clear_results()
        self.search_again_row.hide()
        self.add_link_row.show()
        self.notice.show()
        self._form.setVisible(False)
        self._picked = None
        self.next_button.setEnabled(False)
        self._refresh_continue()
        self.licence_edit.setFocus()

    def _on_add_customer(self) -> None:
        self._show_create(self.licence_edit.text().strip())

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
                    session,
                    customer,
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
            self._context, None, preset_licence=licence, parent=self._form,
            inline=True,
        )
        self._form_panel.submitted.connect(self._on_create_values)
        self._form_panel.cancelled.connect(lambda: self._form.setVisible(False))
        self._form_lay.addWidget(self._form_panel)
        self._form.setVisible(True)
        self._error.setVisible(False)
        self.result_host.addWidget(
            Caption(
                "No account for that licence yet: take the customer details once.",
                parent=self.content_host,
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
                    session,
                    customer,
                    self._state.start.toPython(),
                    self._state.end.toPython(),
                    expiring_days=settings_service.get_int(
                        session, "licence_expiring_days"
                    ),
                )
                if verdict["blocked"]:
                    raise ValidationError("; ".join(verdict["reasons"]))
                snapshot = self._snapshot(session, customer, 0)
                snapshot["temporary_password"] = getattr(
                    customer, "temporary_password", ""
                )
        except Exception as exc:  # noqa: BLE001 - inline, nothing committed
            self._fail(_friendly(exc))
            return
        self._form.setVisible(False)
        self._show_customer(snapshot)
        if snapshot.get("temporary_password"):
            self.result_host.addWidget(
                Caption(
                    f"Temporary password: {snapshot['temporary_password']} \u2014 "
                    "give it to the customer",
                    parent=self.content_host,
                )
            )
        if values.get("front_src") or values.get("back_src"):
            try:
                with self._context.session() as session:
                    customer = session.get(Users, snapshot["id"])
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


class PaymentPanel(ModalStep):
    paid = Signal(dict)
    backed = Signal()

    def __init__(self, context, state: WizardState, parent=None) -> None:
        super().__init__(context, parent)
        self._context = context
        self._state = state
        total = state.total if state.total is not None else money(0)
        deposit = state.deposit if state.deposit is not None else money(0)
        fee = (
            state.scheduling_fee
            if state.scheduling_fee is not None
            else booking_service.SCHEDULING_FEE
        )
        minimum = fee
        self._minimum, self._full = minimum, total
        self.minimum_total = minimum + deposit
        self.full_total = total + deposit

        vehicle_name = "Vehicle"
        customer_name = "Customer"
        if context is not None:
            with context.reading() as session:
                vehicle = session.get(Vehicle, state.vehicle_id)
                customer = (
                    session.get(Users, state.customer_id)
                    if state.customer_id is not None
                    else None
                )
                vehicle_name = (
                    f"{vehicle.make} {vehicle.model}".strip()
                    if vehicle
                    else vehicle_name
                )
                customer_name = customer.full_name if customer else customer_name

        subtitle = (
            f"{customer_name} \u00b7 {vehicle_name} \u00b7 "
            f"{state.start.toString('dd MMM')} \u2192 "
            f"{state.end.toString('dd MMM')}"
        )
        self._head(3, "How much today?", subtitle)
        self._error = self._error_label()
        left, recap = self._two_columns("Payment summary")
        self.recap = recap

        left.addWidget(_section("Amount", self.content_host))
        self.min_option = PaymentOption(
            "Pay minimum",
            f"Fee + deposit \u00b7 {pesos(total - fee)} at pick-up",
            minimum + deposit,
            parent=self.content_host,
        )
        self.full_option = PaymentOption(
            "Pay in full",
            "Nothing left to pay at pick-up",
            total + deposit,
            parent=self.content_host,
        )
        self.choice_group = QButtonGroup(self)
        self.choice_group.setExclusive(True)
        self.choice_group.addButton(self.min_option.radio)
        self.choice_group.addButton(self.full_option.radio)
        self.min_button = self.min_option.radio
        self.full_button = self.full_option.radio
        self.min_button.setChecked(True)
        self.min_button.toggled.connect(self._refresh_pay_button)
        self.full_button.toggled.connect(self._refresh_pay_button)
        left.addWidget(self.min_option)
        left.addWidget(self.full_option)
        self.fee_caption = Caption(
            f"Includes {pesos(fee)} non-refundable scheduling fee.",
            parent=self.content_host,
            colour=MODAL_MUTED,
            size=11,
        )
        # The fee already sits inside the recap row; keep the label for the
        # data contract without repeating it in the visual column.
        self.fee_caption.hide()
        left.addWidget(self.fee_caption)

        left.addWidget(_section("Method", self.content_host))
        self.method_group = QButtonGroup(self)
        self.method_group.setExclusive(True)
        methods = QHBoxLayout()
        methods.setContentsMargins(0, 0, 0, 0)
        methods.setSpacing(8)
        self.method_buttons: dict[str, QToolButton] = {}
        for key, label in (("cash", "Cash"), ("gcash", "GCash"), ("card", "Card")):
            button = QToolButton(self.content_host)
            button.setObjectName("methodChoice")
            button.setText(label)
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIcon(
                icons.icon(key, theme.METHOD_DOT_COLOURS[key], 22)
            )
            button.setIconSize(QSize(22, 22))
            button.setMinimumHeight(74)
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.method_group.addButton(button)
            methods.addWidget(button, 1)
            self.method_buttons[key] = button
        left.addLayout(methods)
        initial_method = (
            state.default_method
            if state.default_method in self.method_buttons
            else "cash"
        )
        self.method_buttons[initial_method].setChecked(True)
        for button in self.method_buttons.values():
            button.clicked.connect(self._sync_ref)

        self.ref_label = Caption(
            "Reference no. \u00b7 Not needed for cash",
            parent=self.content_host,
            colour=MODAL_MUTED,
            size=10,
        )
        left.addWidget(self.ref_label)
        self.ref_edit = QLineEdit(self.content_host)
        self.ref_edit.setPlaceholderText("Enter reference number")
        self.ref_edit.setMinimumHeight(36)
        left.addWidget(self.ref_edit)
        left.addStretch(1)
        self._sync_ref()

        # Recap: rows before the divider, the key due-now figure after it,
        # and the pickup balance at the bottom.
        self.due_now = recap.key_amount
        self.due_at_pickup = _money(0, recap, size=12)
        recap.add_row(
            f"Rental \u00b7 {state.days} "
            f"{'day' if state.days == 1 else 'days'}\n"
            f"Includes {pesos(fee)} non-refundable fee",
            _money(total, recap, size=12),
        )
        recap.add_row(
            "Security deposit\nRefundable hold",
            _money(deposit, recap, size=12),
        )
        recap.add_row("Total", _money(total + deposit, recap, size=12))
        recap.set_key("Due now", minimum + deposit)
        recap.add_extra("Due at pick-up", self.due_at_pickup)
        recap.set_note("The deposit is returned when the vehicle comes back.")

        back, self.pay_button = self._buttons(
            f"Pay {pesos(minimum + deposit)}"
        )
        back.clicked.connect(self.backed.emit)
        self.pay_button.clicked.connect(self._on_pay)
        self._refresh_pay_button()

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
        self.ref_label.setText(
            "Reference no. \u00b7 Required for GCash"
            if gcash
            else "Reference no. \u00b7 Not needed for cash"
        )
        if not gcash:
            self.ref_edit.clear()

    def _refresh_pay_button(self) -> None:
        _choice, rental = self._selection()
        deposit = (
            self._state.deposit
            if self._state.deposit is not None
            else money(0)
        )
        total = self._state.total if self._state.total is not None else money(0)
        due_now = rental + deposit
        due_at_pickup = total + deposit - due_now
        self.due_now.set_amount(due_now)
        self.due_at_pickup.set_amount(due_at_pickup)
        self.pay_button.setText(f"Pay {pesos(due_now)}")

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


class DonePanel(ModalStep):
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
        receipt_path: str | None,
        confirmed: bool,
        receipt_error: str | None = None,
        customer_name: str | None = None,
        vehicle_make: str | None = None,
        vehicle_model: str | None = None,
        vehicle_year: int | None = None,
        vehicle_plate: str | None = None,
        start=None,
        end=None,
        parent=None,
    ) -> None:
        super().__init__(None, parent)
        self.receipt_path = receipt_path
        self.receipt_error = receipt_error
        settled = balance <= 0

        leading = QFrame(self.header_host)
        leading.setObjectName("doneCheck")
        leading.setFixedSize(36, 36)
        lead_lay = QVBoxLayout(leading)
        lead_lay.setContentsMargins(0, 0, 0, 0)
        glyph = QLabel("\u2713", leading)
        glyph.setObjectName("doneCheckGlyph")
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lead_lay.addWidget(glyph)

        self._head(
            4,
            f"Rental #{booking_id} is booked",
            "Give the customer their receipt before they leave.",
            status=("Confirmed" if confirmed else "Pending", "ok" if confirmed else "warn"),
            leading=leading,
        )

        left, recap = self._two_columns("Booking details")
        self.recap = recap

        balance_box = QFrame(self.content_host)
        balance_box.setObjectName("balanceBox")
        box = QHBoxLayout(balance_box)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(12)
        balance_label = Caption(
            "Paid in full" if settled else "Balance due at pick-up",
            parent=balance_box,
            colour=MODAL_INK,
            size=13,
            weight=QFont.Weight.Bold,
        )
        box.addWidget(balance_label, 1)
        self.balance_amount = _money(balance, balance_box, size=32)
        box.addWidget(self.balance_amount, 0, Qt.AlignmentFlag.AlignVCenter)
        left.addWidget(balance_box)

        summary = QFrame(self.content_host)
        summary.setObjectName("summaryCard")
        summary_lay = QVBoxLayout(summary)
        summary_lay.setContentsMargins(0, 0, 0, 0)
        summary_lay.setSpacing(0)
        self.rental_total_amount = _money(total, summary)
        self.paid_amount = _money(paid_amount, summary)
        self.deposit_amount = _money(deposit, summary)
        summary_lay.addWidget(
            _summary_row("Rental total", self.rental_total_amount, summary)
        )
        summary_lay.addWidget(_rule(summary))
        summary_lay.addWidget(
            _summary_row("Paid today", self.paid_amount, summary)
        )
        summary_lay.addWidget(_rule(summary))
        summary_lay.addWidget(
            _summary_row(
                "Deposit held",
                self.deposit_amount,
                summary,
                note="Refundable",
            )
        )
        left.addWidget(summary)

        self.receipt_row = QFrame(self.content_host)
        self.receipt_row.setObjectName("receiptRow")
        receipt = QHBoxLayout(self.receipt_row)
        receipt.setContentsMargins(12, 10, 12, 10)
        receipt.setSpacing(10)
        icon = QLabel(self.receipt_row)
        icon.setObjectName("receiptIcon")
        icon.setFixedSize(32, 32)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(icons.pixmap("pos", MODAL_MUTED, 16))
        receipt.addWidget(icon)
        receipt_text = QVBoxLayout()
        receipt_text.setContentsMargins(0, 0, 0, 0)
        receipt_text.setSpacing(1)
        if receipt_path is None:
            self.receipt_name = Caption(
                "Receipt not available",
                parent=self.receipt_row,
                colour=MODAL_INK,
                size=12,
                weight=QFont.Weight.DemiBold,
            )
            warning = (
                "Receipt could not be generated"
                + (f": {receipt_error}" if receipt_error else ".")
                + " The booking is saved."
            )
            self.receipt_note = Caption(
                warning, parent=self.receipt_row, colour=MODAL_DANGER, size=11
            )
        else:
            self.receipt_name = Caption(
                Path(receipt_path).name,
                parent=self.receipt_row,
                colour=MODAL_INK,
                size=12,
                weight=QFont.Weight.DemiBold,
            )
            self.receipt_note = Caption(
                "Saved in the receipts folder",
                parent=self.receipt_row,
                colour=MODAL_MUTED,
                size=11,
            )
        self.receipt_name.setObjectName("receiptName")
        self.receipt_note.setObjectName("receiptNote")
        self.receipt_note.setWordWrap(True)
        receipt_text.addWidget(self.receipt_name)
        receipt_text.addWidget(self.receipt_note)
        receipt.addLayout(receipt_text, 1)
        left.addWidget(self.receipt_row)
        left.addStretch(1)

        if vehicle_make or vehicle_model:
            recap.add_widget(
                VehicleSummary(
                    vehicle_make or "",
                    vehicle_model or "",
                    vehicle_year,
                    vehicle_plate or "",
                    parent=recap,
                )
            )
        if customer_name:
            recap.add_row("Customer", customer_name)
        if start is not None:
            recap.add_row("Pick-up", f"{start:%d %b %Y}")
        if end is not None:
            recap.add_row("Return", f"{end:%d %b %Y}")
        recap.set_key_visible(False)
        recap.set_note("")

        while self.footer_layout.count():
            item = self.footer_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.footer_layout.addStretch(1)
        self.print_button = QPushButton("Print receipt", self.footer)
        self.print_button.setObjectName("secondaryButton")
        self.print_button.setMinimumHeight(38)
        self.print_button.setCursor(Qt.CursorShape.PointingHandCursor)
        if receipt_path is None:
            self.print_button.setEnabled(False)
        else:
            self.print_button.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(receipt_path))
            )
        self.view_button = QPushButton("View in Bookings", self.footer)
        self.view_button.setObjectName("secondaryButton")
        self.view_button.setMinimumHeight(38)
        self.view_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.view_button.clicked.connect(self.view_bookings.emit)
        self.done_button = QPushButton("Done", self.footer)
        self.done_button.setObjectName("primaryButton")
        self.done_button.setMinimumHeight(38)
        self.done_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.done_button.clicked.connect(self.finished.emit)
        self.footer_layout.addWidget(self.print_button)
        self.footer_layout.addWidget(self.view_button)
        self.footer_layout.addWidget(self.done_button)
        self.footer.setVisible(True)


# -- controller ----------------------------------------------------------------


class RentalWizard(QObject):
    """Owns the wizard state and swaps step panels in the overlay scroll.

    The frosted backdrop is rendered once for the details panel and left
    alone: the page behind never changes between steps.
    """

    cancelled = Signal()
    finished = Signal()

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
        super().__init__()
        self._context = context
        self._overlay = overlay
        self._navigate = navigate
        self._notify = notify
        self.state = WizardState(
            vehicle_id=vehicle_id,
            start=start,
            end=end,
            customer_id=customer_id,
        )
        #: Booking written by this run; View in Bookings focuses this row.
        self.last_booking_id: int | None = None
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
        cancel = getattr(widget, "cancel_requested", None)
        if cancel is not None:
            cancel.connect(self._on_cancel)
        return widget

    def _back_to_details(self) -> None:
        if self._details is not None:
            old = self._overlay.scroll.takeWidget()
            if old is not None:
                old.deleteLater()
            self._overlay.scroll.setWidget(self._details)
            self._current = self._details
            self._details = None
            self._overlay.scroll.verticalScrollBar().setValue(0)

    # -- steps ---------------------------------------------------------

    def _on_scheduled(self, start: QDate, end: QDate) -> None:
        self.state.start, self.state.end = start, end
        try:
            with self._context.reading() as session:
                vehicle = session.get(Vehicle, self.state.vehicle_id)
                q = counter_service.quote(
                    session, vehicle, start.toPython(), end.toPython()
                )
        except Exception as exc:  # noqa: BLE001 - inline, stay on step
            self._current._error.setText(_friendly(exc))
            self._current._error.setVisible(True)
            return
        self.state.days = q.days
        self.state.total = q.total
        self.state.deposit = q.deposit
        self.state.scheduling_fee = q.scheduling_fee
        self.state.default_method = q.default_method
        self.state.default_channel = q.default_channel
        panel = LicencePanel(
            self._context,
            self.state,
            self._overlay.scroll,
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
            self._context,
            self.state,
            self._overlay.scroll,
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
                    channel=self.state.default_channel,
                    flag_override_note=self.state.flag_override_note,
                    flag_override_by=staff_id,
                    fee_floor=self.state.scheduling_fee,
                    deposit=self.state.deposit,
                )
                balance = payment_service.booking_balance(session, booking)
                result = {
                    "booking_id": booking.booking_id,
                    "total": balance.total_due,
                    "paid_amount": balance.amount_paid,
                    "balance": balance.balance,
                    "deposit": balance.deposit_held,
                    "receipt_path": None,
                    "receipt_error": None,
                    "confirmed": confirmed,
                    "customer_name": user.full_name if user else None,
                    "vehicle_make": vehicle.make if vehicle else None,
                    "vehicle_model": vehicle.model if vehicle else None,
                    "vehicle_year": int(vehicle.year or 0) if vehicle else None,
                    "vehicle_plate": vehicle.plate_number if vehicle else None,
                    "start": booking.start_date,
                    "end": booking.end_date,
                }
        except Exception as exc:  # noqa: BLE001 - inline, nothing committed
            panel.fail(_friendly(exc))
            return

        self.last_booking_id = result["booking_id"]

        # The write transaction is closed and committed: a receipt failure can
        # no longer roll the rental back. Read the booking afresh so the PDF
        # never renders from a session mid-commit.
        receipt_path, receipt_error = self._generate_receipt_safely(
            result["booking_id"]
        )
        result["receipt_path"] = receipt_path
        result["receipt_error"] = receipt_error
        done = DonePanel(parent=self._overlay.scroll, **result)
        self._swap(done)
        done.view_bookings.connect(self._on_view_bookings)
        done.finished.connect(self._on_finished)
        if self._notify:
            self._notify(f"Rental #{result['booking_id']} is booked.")

    def _generate_receipt_safely(
        self, booking_id: int
    ) -> tuple[str | None, str | None]:
        """Render the receipt after the booking is committed.

        Returns ``(path, None)`` on success or ``(None, error)`` on failure.
        Never raises: by the time this runs the rental is already saved, so a
        dead export folder must not look like a failed booking.
        """
        try:
            with self._context.reading() as session:
                booking = session.get(Booking, booking_id)
                if booking is None:
                    return (
                        None,
                        f"Booking #{booking_id} could not be reloaded.",
                    )
                return generate_receipt_pdf(session, booking), None
        except Exception as exc:  # noqa: BLE001 - the booking is already saved
            detail = str(exc).strip() or _friendly(exc)
            return None, detail

    def _on_cancel(self) -> None:
        if not confirm_modal(
            self._overlay,
            "Discard this rental?",
            detail="The customer, dates, and payment choice will be lost.",
            destructive=True,
            confirm_text="Discard rental",
        ):
            return
        self._overlay.set_dismissible(True)
        self._overlay.hide_overlay()
        self.cancelled.emit()

    def _on_view_bookings(self) -> None:
        booking_id = self.last_booking_id
        self._overlay.set_dismissible(True)
        self._overlay.hide_overlay()
        self.finished.emit()
        if self._navigate:
            self._navigate("bookings", booking_id)

    def _on_finished(self) -> None:
        self._overlay.set_dismissible(True)
        self._overlay.hide_overlay()
        self.finished.emit()
