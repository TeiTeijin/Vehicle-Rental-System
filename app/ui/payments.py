"""The Payments tab: one customer's receipts, newest first.

Rows come from `payment_service.payments_for_user`, which joins each payment
to the booking that owns it. That join is the whole rule behind this screen:
a customer sees money recorded against their own rentals, and nothing else.

The tab is deliberately not a second showroom. A customer with no payments --
or no booking yet -- sees an empty state, not a shelf of cars they have not
rented. Vehicle names and photos appear only alongside real payment rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.database import SessionLocal
from app.models import Payment, Users
from app.services import (
    booking_service,
    payment_service,
    vehicle_photos,
    vehicle_service,
)
from app.services.errors import ServiceError
from app.ui import theme
from app.ui.session import Identity
from app.ui.theme import (
    hint_label,
    make_label,
    moment_text,
    section_title,
    status_chip,
    text_button,
)
from app.ui.vehicle_photo import VehiclePhoto

#: 'PAID' -- the chip colour each stored payment status gets.
STATUS_TONES = {
    "pending": "amber",
    "paid": "green",
    "failed": "red",
    "refunded": "muted",
}

KIND_LABELS = {
    "rental": "Rental payment",
    "deposit": "Security deposit",
}

STATUS_NOTES = {
    "pending": "Waiting for the branch to confirm this payment.",
    "failed": "This attempt did not go through; no money was taken.",
    "refunded": "This amount was returned to you.",
}


@dataclass(frozen=True)
class Row:
    """A payment reduced to what the card draws."""

    payment_id: int
    vehicle_id: int | None
    vehicle_name: str
    refs: str
    amount: str
    details: str
    when: str
    status: str
    note: str
    ref: object


def _method_label(payment: Payment) -> str:
    method = payment.method
    if method == "card":
        last4 = payment.booking.card_last4 if payment.booking else None
        return f"Card ending {last4}" if last4 else "Card"
    if method == "gcash":
        return "GCash"
    if method == "cash":
        return "Cash at the counter"
    return "Counter payment"


def _when_label(payment: Payment) -> str:
    stamp = payment.paid_at or payment.created_at
    if stamp is None:
        return "Date unavailable"
    lead = {
        "paid": "Paid",
        "refunded": "Paid",
        "failed": "Attempted",
        "pending": "Started",
    }.get(payment.status or "", "Recorded")
    return f"{lead} {moment_text(stamp)}"


def _note(payment: Payment) -> str:
    if payment.status in STATUS_NOTES:
        return STATUS_NOTES[payment.status]
    if payment.kind == "deposit":
        return "Refundable deposit held against damage or late return."
    return ""


def _row_for(payment: Payment) -> Row:
    booking = payment.booking
    vehicle = booking.vehicle if booking else None
    reference = (
        booking_service.booking_reference(booking) if booking else "Booking removed"
    )
    payment_ref = (payment.reference_no or "").strip()
    refs = f"Booking {reference}"
    if payment_ref:
        refs = f"{refs}  \u00b7  Ref {payment_ref}"
    return Row(
        payment_id=payment.payment_id,
        vehicle_id=vehicle.vehicle_id if vehicle else None,
        vehicle_name=(
            vehicle_service.display_name(vehicle)
            if vehicle
            else "Vehicle no longer in the fleet"
        ),
        refs=refs,
        amount=theme.total_text(payment.amount),
        details=(
            f"{KIND_LABELS.get(payment.kind or '', 'Payment')}  \u00b7  "
            f"{_method_label(payment)}"
        ),
        when=_when_label(payment),
        status=payment.status or "pending",
        note=_note(payment),
        ref=vehicle_photos.ref_for(vehicle) if vehicle else None,
    )


class PaymentCard(QFrame):
    """One payment: what was paid, for which rental, and when."""

    open_requested = Signal(int)

    def __init__(self, row: Row, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("paymentCard")
        self.row = row

        col = QVBoxLayout(self)
        col.setContentsMargins(18, 16, 18, 16)
        col.setSpacing(8)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        head.addWidget(make_label(row.vehicle_name, "gridName"), 1)
        head.addWidget(
            status_chip(row.status, STATUS_TONES.get(row.status, "muted")),
            0,
            Qt.AlignVCenter,
        )
        col.addLayout(head)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)

        if row.ref is not None:
            photo = VehiclePhoto(thumbnail=True)
            photo.setFixedSize(120, 78)
            photo.set_vehicle(row.ref)
            body.addWidget(photo, 0, Qt.AlignTop)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(4)
        text.addWidget(make_label(row.amount, "paymentAmount"))
        text.addWidget(make_label(row.details, "bookingMeta"))
        text.addWidget(make_label(row.when, "bookingWindow"))
        text.addWidget(make_label(row.refs, "bookingMeta"))
        if row.note:
            text.addWidget(hint_label(row.note, "muted"))
        text.addStretch(1)
        body.addLayout(text, 1)

        if row.vehicle_id is not None:
            button = text_button("View vehicle")
            button.clicked.connect(
                lambda _checked=False, vid=self.row.vehicle_id: self.open_requested.emit(vid)
            )
            body.addWidget(button, 0, Qt.AlignTop | Qt.AlignRight)

        col.addLayout(body)


class PaymentsPage(QWidget):
    """The tab. `set_user` re-reads the signed-in customer's history."""

    vehicle_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self._identity: Identity | None = None
        self._cards: list[PaymentCard] = []

        self.title = section_title("Your payments")
        self.summary = hint_label("", tone="muted")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.title, 1)
        header.addWidget(self.summary, 0, Qt.AlignBottom)

        self.error = hint_label("", tone="danger")
        self.error.hide()

        self.empty = hint_label("", tone="muted")
        self.empty.setObjectName("emptyState")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(140)
        self.empty.hide()

        self.list_layout = QVBoxLayout()
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(12)

        body = QVBoxLayout()
        body.setContentsMargins(40, 28, 40, 32)
        body.setSpacing(16)
        body.addLayout(header)
        body.addWidget(self.error)
        body.addWidget(self.empty)
        body.addLayout(self.list_layout)
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

        self.empty.setText(
            "No payments recorded yet. A receipt appears here once a booking "
            "is paid."
        )

    # ---------- state ----------
    def set_user(self, identity: Identity | None) -> None:
        """Whose receipts these are. None (a guest) shows the sign-in prompt."""
        self._identity = identity
        self.reload()

    # ---------- data ----------
    def reload(self) -> None:
        self._clear_cards()
        self.error.hide()

        if self._identity is None:
            self.summary.setText("")
            self.empty.setText(
                "Sign in from the top bar to see your payments here."
            )
            self.empty.show()
            return

        rows: list[Row] = []
        try:
            with SessionLocal() as session:
                user = session.get(Users, self._identity.user_id)
                if user is None:
                    raise ServiceError("That account no longer exists.")
                rows = [
                    _row_for(payment)
                    for payment in payment_service.payments_for_user(
                        session, user.user_id
                    )
                ]
        except ServiceError as exc:
            self._fail(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - the database is remote
            self._fail(f"Could not load your payments: {exc}")
            return

        for row in rows:
            card = PaymentCard(row)
            card.open_requested.connect(self.vehicle_selected)
            self.list_layout.addWidget(card)
            self._cards.append(card)

        count = len(rows)
        self.summary.setText(f"{count} payment{'' if count == 1 else 's'}")
        if rows:
            self.empty.hide()
        else:
            self.empty.setText(
                "No payments recorded yet. A receipt appears here once a "
                "booking is paid."
            )
            self.empty.show()

    def _clear_cards(self) -> None:
        for card in self._cards:
            self.list_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

    def _fail(self, message: str) -> None:
        self.summary.setText("")
        self.empty.hide()
        self.error.setText(message)
        self.error.show()


__all__ = ["PaymentCard", "PaymentsPage", "STATUS_TONES", "Row"]
