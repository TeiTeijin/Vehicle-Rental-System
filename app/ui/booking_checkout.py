"""Screen 4: pay and confirm (step 3 of 3).

The left column is the booking as it will be written (vehicle, window,
branches) plus how the customer wants to pay; the right column is the money,
computed once by app/services/pricing.py -- the same call screen 3 made, so the
two screens can never disagree.

Paying is a mock: app/services/card_validation.py checks the form (Luhn,
expiry, CVV) and answers `ok`/`not ok`. A declined card stops here, before
anything is written; a successful one (or cash, which is paid at the counter)
writes the booking through app/services/booking_service.py and shows the
reference code. The card number and CVV are dropped with the widgets -- only
`CardDetails` (brand + last four) ever reaches the database.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from app.database import SessionLocal
from app.models import Branch, Users, Vehicle
from app.services import (
    booking_service,
    card_validation,
    pricing,
    vehicle_photos,
    vehicle_service,
)
from app.services.errors import ServiceError
from app.ui import theme
from app.ui.session import BookingDraft
from app.ui.theme import (
    BOOKING_STEPS,
    TOKENS,
    divider,
    hint_label,
    icon_from,
    make_label,
    moment_text,
    overline,
    panel,
    per_day_text,
    primary_button,
    section_title,
    text_button,
    value_row,
)
from app.ui.vehicle_photo import VehiclePhoto

STEP_INDEX = 3

#: (key, title, note, icon). The keys are the service's own method names.
PAYMENT_METHODS: tuple[tuple[str, str, str, str], ...] = (
    ("card", "Credit / debit card", "Visa or Mastercard, charged now.", "card"),
    ("gcash", "GCash", "Send the payment now, keep the reference.", "gcash"),
    ("cash", "Cash at the counter", "Pay when you pick the vehicle up.", "cash"),
)


class MethodCard(QFrame):
    """One payment option. Clicking the card selects it."""

    chosen = Signal(str)

    def __init__(
        self,
        key: str,
        title: str,
        note: str,
        icon_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.key = key
        self.setObjectName("methodCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("selected", False)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(12)

        glyph = QLabel()
        glyph.setObjectName("specIcon")
        glyph.setPixmap(icon_from(icon_name, TOKENS["text_dark"], 20).pixmap(20, 20))
        glyph.setFixedSize(34, 34)
        glyph.setAlignment(Qt.AlignCenter)
        glyph.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(glyph, 0, Qt.AlignVCenter)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        title_label = make_label(title, "methodTitle")
        note_label = make_label(note, "methodNote", wrap=True)
        for label in (title_label, note_label):
            # Clicks land on the card itself, so the whole tile is the target.
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text.addWidget(title_label)
        text.addWidget(note_label)
        row.addLayout(text, 1)

        self.check = QLabel()
        self.check.setPixmap(icon_from("check", TOKENS["green"], 18).pixmap(18, 18))
        self.check.setFixedSize(18, 18)
        self.check.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.check.hide()
        row.addWidget(self.check, 0, Qt.AlignTop)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.check.setVisible(selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self.chosen.emit(self.key)


class CheckoutSuccess(theme.OverlayDialog):
    """The receipt the customer keeps: the reference code, big."""

    def __init__(self, parent: QWidget, *, reference: str, receipt: str) -> None:
        super().__init__(parent, object_name="overlayCard", card_width=440)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(make_label("Booking confirmed", "overlayTitle"), 1)
        head.addWidget(self.close_button(), 0, Qt.AlignTop)
        self.column.addLayout(head)

        tick = QLabel()
        tick.setPixmap(icon_from("check", TOKENS["green"], 34).pixmap(34, 34))
        tick.setFixedSize(34, 34)
        self.column.addWidget(tick, 0, Qt.AlignLeft)

        self.column.addWidget(
            make_label(
                "Show this reference at the counter. It is also in "
                "Bookings, under your account.",
                "overlaySubtitle",
                wrap=True,
            )
        )
        self.column.addSpacing(6)

        code_panel, code_col = panel("panelTan", spacing=4, margins=(18, 14, 18, 14))
        code_col.addWidget(overline("Reference"))
        code = make_label(reference, "pageTitle")
        code_col.addWidget(code)
        self.column.addWidget(code_panel)

        self.column.addWidget(make_label(receipt, "overlaySubtitle", wrap=True))

        self.view_button = primary_button("VIEW MY BOOKINGS")
        self.view_button.clicked.connect(self.accept)
        self.column.addWidget(self.view_button)

        keep = text_button("Keep browsing")
        keep.clicked.connect(self.reject)
        self.column.addWidget(keep, 0, Qt.AlignHCenter)


class BookingCheckoutPage(QWidget):
    """Review, pay, confirm. Emits `booked` once the booking is saved."""

    back_requested = Signal()
    booked = Signal(object)

    def __init__(
        self, draft: BookingDraft, user_id: int | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.draft = draft
        self._user_id = user_id
        self._quote = None
        self._name = ""
        self._meta = ""
        self._rate = 0
        self._ref = None
        self._pickup_branch = "\u2014"
        self._return_branch = "\u2014"
        self._moments: dict[str, tuple[QLabel, QLabel]] = {}
        self._method = draft.payment_method or "card"
        self._fields: dict[str, QLineEdit] = {}

        # ----- head -----
        self.back_button = text_button("\u2190  Change dates")
        self.back_button.setIcon(icon_from("back", TOKENS["text_dark"], 14))
        self.back_button.clicked.connect(self.back_requested)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self.back_button, 0, Qt.AlignLeft | Qt.AlignVCenter)
        head.addSpacing(24)
        head.addWidget(theme.step_strip(BOOKING_STEPS, STEP_INDEX), 1)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(14)
        left.addLayout(head)
        left.addWidget(self._booking_panel())
        left.addWidget(self._payment_panel())
        left.addStretch(1)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(14)
        right.addWidget(self._price_panel())
        right.addStretch(1)

        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(28)
        columns.addLayout(left, 3)
        columns.addLayout(right, 2)

        body = QVBoxLayout()
        body.setContentsMargins(40, 24, 40, 32)
        body.setSpacing(16)
        body.addLayout(columns)
        body.addStretch(1)

        holder = QWidget()
        holder.setObjectName("pageBody")
        holder.setLayout(body)

        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(holder)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

        self.reload()

    # ---------- construction ----------
    def _booking_panel(self) -> QWidget:
        frame, col = panel("panel", spacing=12)
        col.addWidget(section_title("Your booking"))

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        self.photo = VehiclePhoto(thumbnail=True)
        self.photo.setFixedSize(150, 96)
        row.addWidget(self.photo, 0, Qt.AlignTop)

        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(4)
        self.vehicle_name = make_label("", "gridName")
        self.vehicle_meta = make_label("", "gridMeta")
        self.vehicle_rate = make_label("", "gridPrice")
        info.addWidget(self.vehicle_name)
        info.addWidget(self.vehicle_meta)
        info.addWidget(self.vehicle_rate)
        info.addStretch(1)
        row.addLayout(info, 1)
        col.addLayout(row)

        col.addWidget(divider())
        moments = QHBoxLayout()
        moments.setContentsMargins(0, 0, 0, 0)
        moments.setSpacing(18)
        for caption, icon in (("Pickup", "map_pin"), ("Return", "route")):
            block = QVBoxLayout()
            block.setContentsMargins(0, 0, 0, 0)
            block.setSpacing(3)
            block.addWidget(overline(caption))
            value = make_label("\u2014", "valueAmount")
            value.setWordWrap(True)
            block.addWidget(value)
            branch = make_label("", "gridMeta")
            branch.setWordWrap(True)
            block.addWidget(branch)
            moments.addLayout(block, 1)
            self._moments[caption.lower()] = (value, branch)
        col.addLayout(moments)
        return frame

    def _payment_panel(self) -> QWidget:
        frame, col = panel("panel", spacing=12)
        col.addWidget(section_title("How would you like to pay?"))

        self.method_cards: dict[str, MethodCard] = {}
        for key, title, note, icon_name in PAYMENT_METHODS:
            card = MethodCard(key, title, note, icon_name)
            card.chosen.connect(self._choose_method)
            self.method_cards[key] = card
            col.addWidget(card)

        # The three forms share one slot: only the chosen method's fields show.
        self.method_forms = QStackedLayout()
        self.method_forms.setContentsMargins(0, 0, 0, 0)
        self.method_forms.addWidget(self._card_form())
        self.method_forms.addWidget(self._gcash_form())
        self.method_forms.addWidget(self._cash_form())
        col.addLayout(self.method_forms)

        self.error = hint_label("", tone="danger")
        self.error.hide()
        col.addWidget(self.error)

        self.terms = QCheckBox("I agree to the rental terms and cancellation policy")
        col.addWidget(self.terms)
        return frame

    def _card_form(self) -> QWidget:
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 4, 0, 0)
        col.setSpacing(10)

        grid = QHBoxLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        grid.addWidget(self._field("cardholder", "Name on card", "Juan Dela Cruz"), 1)
        grid.addWidget(self._field("card_number", "Card number", "4242 4242 4242 4242"), 1)
        col.addLayout(grid)

        small = QHBoxLayout()
        small.setContentsMargins(0, 0, 0, 0)
        small.setSpacing(12)
        small.addWidget(self._field("expiry", "Expiry (MM / YY)", "08 / 29"), 1)
        small.addWidget(self._field("cvv", "CVV", "123"), 1)
        small.addStretch(1)
        col.addLayout(small)

        col.addWidget(
            hint_label(
                "Test gateway: nothing is charged, and a card ending 0002 "
                "is always declined so the failure path can be tried.",
            )
        )
        self._fields["card_number"].setMaxLength(23)
        self._fields["expiry"].setMaxLength(7)
        self._fields["cvv"].setMaxLength(4)
        self._fields["card_number"].textEdited.connect(self._format_number)
        self._fields["expiry"].textEdited.connect(self._format_expiry)
        self._fields["cvv"].textEdited.connect(self._format_cvv)
        return holder

    def _gcash_form(self) -> QWidget:
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 4, 0, 0)
        col.setSpacing(10)
        col.addWidget(self._field("gcash_number", "GCash mobile number", "0917 123 4567"))
        col.addWidget(
            hint_label(
                "The mock gateway confirms the GCash payment right away and "
                "stores the reference on the booking.",
            )
        )
        return holder

    def _cash_form(self) -> QWidget:
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 4, 0, 0)
        col.setSpacing(10)
        self.cash_note = hint_label("", tone="muted")
        col.addWidget(self.cash_note)
        col.addWidget(
            hint_label(
                "The booking is held (pending) and the branch collects the "
                "total when the keys are handed over.",
            )
        )
        return holder

    def _field(self, key: str, caption: str, placeholder: str) -> QWidget:
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        col.addWidget(make_label(caption, "comboCaption"))
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        col.addWidget(edit)
        self._fields[key] = edit
        return holder

    def _price_panel(self) -> QWidget:
        frame, col = panel("panel", spacing=10, margins=(24, 22, 24, 22))
        col.addWidget(section_title("Price breakdown"))

        self._rows = {
            "rental": value_row("Rental", "\u2014", icon_name="car"),
            "insurance": value_row("Insurance", "\u2014", icon_name="shield"),
            "service": value_row("Service fee", "\u2014", icon_name="receipt"),
            "vat": value_row("VAT (12%)", "\u2014", icon_name="percent"),
        }
        for row in self._rows.values():
            col.addWidget(row)
        self._rental_caption = self._rows["rental"].findChild(QLabel, "valueCaption")
        col.addWidget(divider())
        self._rows["total"] = value_row("Total", "\u2014", strong=True)
        col.addWidget(self._rows["total"])

        self.pay_button = primary_button("CONFIRM && PAY")  # && : & is a mnemonic marker
        self.pay_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.pay_button.clicked.connect(self._confirm)
        col.addWidget(self.pay_button)
        self.pay_hint = hint_label("", tone="muted")
        col.addWidget(self.pay_hint)
        return frame

    # ---------- data ----------
    def reload(self) -> None:
        """Read the vehicle and the branches, then price the draft."""
        try:
            with SessionLocal() as session:
                vehicle = session.get(Vehicle, self.draft.vehicle_id)
                if vehicle is None:
                    self._fail("That vehicle is no longer in the fleet.")
                    return
                self._name = vehicle_service.display_name(vehicle)
                self._meta = vehicle_service.summary_line(vehicle)
                self._rate = vehicle.daily_rate
                self._ref = vehicle_photos.ref_for(vehicle)

                pickup_branch = session.get(Branch, self.draft.pickup_branch_id)
                return_branch = (
                    session.get(Branch, self.draft.return_branch_id)
                    if self.draft.return_branch_id
                    else None
                )
                drop_branch = return_branch or pickup_branch
                self._pickup_branch = pickup_branch.name if pickup_branch else "\u2014"
                self._return_branch = drop_branch.name if drop_branch else "\u2014"
        except ServiceError as exc:
            self._fail(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - the database is remote
            self._fail(f"Could not load this booking: {exc}")
            return

        self.vehicle_name.setText(self._name)
        self.vehicle_meta.setText(self._meta)
        self.vehicle_rate.setText(per_day_text(self._rate))
        if self._ref is not None:
            self.photo.set_vehicle(self._ref)

        pickup_dt, return_dt = self.draft.pickup_dt, self.draft.return_dt
        if pickup_dt is not None and return_dt is not None:
            pickup_value, pickup_branch = self._moments["pickup"]
            return_value, return_branch = self._moments["return"]
            pickup_value.setText(moment_text(pickup_dt))
            pickup_branch.setText(self._pickup_branch)
            return_value.setText(moment_text(return_dt))
            return_branch.setText(self._return_branch)

        self._choose_method(self._method)
        self._price()

    def _price(self) -> None:
        pickup_dt, return_dt = self.draft.pickup_dt, self.draft.return_dt
        if pickup_dt is None or return_dt is None:
            self._fail("Pickup and return dates are missing: go back a step.")
            return
        try:
            self._quote = pricing.quote(self._rate, pickup_dt, return_dt)
        except ServiceError as exc:
            self._fail(str(exc))
            return

        quote = self._quote
        self._rental_caption.setText(
            f"Rental ({quote.days} day{'' if quote.days == 1 else 's'} "
            f"at {theme.pesos_text(self._rate)})"
        )
        for key, text in (
            ("rental", quote.subtotal_text),
            ("insurance", quote.insurance_text),
            ("service", quote.service_fee_text),
            ("vat", quote.vat_text),
            ("total", quote.total_text),
        ):
            self._set_row(key, text)

        self.pay_button.setText(f"CONFIRM && PAY {quote.total_text}")
        self.cash_note.setText(
            f"{quote.total_text} is due at the counter when you pick up."
        )
        self.pay_hint.setText(
            "A reference code is issued as soon as the booking is saved."
        )

    def _set_row(self, key: str, text: str) -> None:
        amount = self._rows[key].findChild(QLabel, "valueAmount")
        if amount is not None:
            amount.setText(text)

    # ---------- interaction ----------
    def _format_number(self, text: str) -> None:
        self._fields["card_number"].setText(card_validation.format_number(text))

    def _format_expiry(self, text: str) -> None:
        self._fields["expiry"].setText(card_validation.format_expiry(text))

    def _format_cvv(self, text: str) -> None:
        self._fields["cvv"].setText(card_validation.format_cvv(text))

    def _choose_method(self, key: str) -> None:
        self._method = key
        for name, card in self.method_cards.items():
            card.set_selected(name == key)
        index = [name for name, _t, _n, _i in PAYMENT_METHODS].index(key)
        self.method_forms.setCurrentIndex(index)

    def _fail(self, message: str, field: str | None = None) -> None:
        self.error.setText(message)
        self.error.show()
        if field and field in self._fields:
            self._fields[field].setFocus()

    def _clear_error(self) -> None:
        self.error.hide()

    # ---------- paying ----------
    def _confirm(self) -> None:
        self._clear_error()
        if not self.terms.isChecked():
            self._fail("Please agree to the rental terms before paying.")
            return
        if self.draft.pickup_dt is None or self.draft.return_dt is None:
            self._fail("Pickup and return dates are missing: go back a step.")
            return
        if self._user_id is None:
            self._fail("Please sign in again before paying.")
            return

        details = None
        if self._method == "card":
            try:
                details = card_validation.validate_card(
                    self._fields["cardholder"].text(),
                    self._fields["card_number"].text(),
                    self._fields["expiry"].text(),
                    self._fields["cvv"].text(),
                )
            except ServiceError as exc:
                self._fail(str(exc), getattr(exc, "field", None))
                return
        if self._method == "gcash" and not card_validation.digits(
            self._fields["gcash_number"].text()
        ):
            self._fail(
                "Enter the GCash mobile number that will pay.", "gcash_number"
            )
            return

        self.pay_button.setEnabled(False)
        self.pay_button.setText("PROCESSING...")
        try:
            reference, receipt = self._write_booking(details)
        except ServiceError as exc:
            self._fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - the database is remote
            self._fail(f"Could not save the booking: {exc}")
        else:
            self.draft.payment_method = self._method
            self.draft.card_last4 = details.last4 if details else None
            self.draft.reference_code = reference
            dialog = CheckoutSuccess(
                self.window(), reference=reference, receipt=receipt
            )
            dialog.exec()
            self.booked.emit(self.draft)
        finally:
            self.pay_button.setEnabled(True)
            self._price()

    def _write_booking(self, details) -> tuple[str, str]:
        """Charge (mock), then save. Returns (reference, receipt line)."""
        method = self._method
        with SessionLocal() as session:
            vehicle = session.get(Vehicle, self.draft.vehicle_id)
            user = session.get(Users, self._user_id)
            pickup_branch = session.get(Branch, self.draft.pickup_branch_id)
            return_branch = (
                session.get(Branch, self.draft.return_branch_id)
                if self.draft.return_branch_id
                else None
            )
            if vehicle is None or user is None or pickup_branch is None:
                raise ServiceError("This booking is missing its vehicle or branch.")

            quote = pricing.quote(
                vehicle.daily_rate, self.draft.pickup_dt, self.draft.return_dt
            )
            result = card_validation.collect(method, quote.total, details)
            if not result.ok:
                # Declined: nothing is written, the customer keeps their dates.
                raise ServiceError(result.message)

            booking = booking_service.create_online_booking(
                session,
                user=user,
                vehicle=vehicle,
                pickup_dt=self.draft.pickup_dt,
                return_dt=self.draft.return_dt,
                pickup_branch=pickup_branch,
                return_branch=return_branch,
                payment_method=method,
                card_last4=details.last4 if details else None,
                payment_reference=result.reference or None,
            )
            session.commit()
            return booking.reference_code or "", result.message


__all__ = ["BOOKING_STEPS", "BookingCheckoutPage", "MethodCard", "PAYMENT_METHODS"]
