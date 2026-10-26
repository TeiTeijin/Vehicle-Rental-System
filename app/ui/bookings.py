"""The Bookings tab: one customer's rentals, behind four filter pills.

Rows come from app/services/booking_service.py (`bookings_for`), which is also
what decides which stored status belongs in which pill -- the screen never
re-implements the buckets. A booking made on screen 4 lands here the moment the
router switches to this tab.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.database import SessionLocal
from app.models import Booking, Users
from app.services import booking_service, vehicle_photos, vehicle_service
from app.services.errors import ServiceError
from app.ui import theme
from app.ui.session import Identity
from app.ui.theme import (
    hint_label,
    make_label,
    moment_text,
    pill,
    pill_group,
    section_title,
    status_chip,
    text_button,
)
from app.ui.vehicle_photo import VehiclePhoto

#: (bucket key, pill caption), in the order the design lists them.
FILTERS: tuple[tuple[str, str], ...] = (
    ("all", "All"),
    ("upcoming", "Upcoming"),
    ("completed", "Completed"),
    ("cancelled", "Cancelled"),
)

#: 'PENDING' -- the chip colour each stored status gets.
STATUS_TONES = {
    "pending": "amber",
    "confirmed": "green",
    "ongoing": "dark",
    "completed": "muted",
    "cancelled": "red",
}

#: What the status actually means for the customer.
STATUS_NOTES = {
    "pending": "Reserved - payment is collected at pickup.",
    "confirmed": "Paid and confirmed. Bring your licence to the counter.",
    "ongoing": "Out on rent right now.",
    "completed": "Returned. Thank you for renting with us.",
    "cancelled": "Cancelled - this booking no longer holds the vehicle.",
}


@dataclass(frozen=True)
class Row:
    """A booking reduced to what the card draws."""

    booking_id: int
    reference: str
    status: str
    vehicle_id: int | None
    vehicle_name: str
    meta: str
    window: tuple[datetime, datetime]
    total: str
    payment: str
    ref: object


def _payment_label(booking: Booking) -> str:
    method = booking.payment_method
    if method == "card":
        return f"Card {('ending ' + booking.card_last4) if booking.card_last4 else ''}".strip()
    if method == "gcash":
        return "GCash"
    if method == "cash":
        return "Cash at the counter"
    return "Counter payment"


class BookingCard(QFrame):
    """One booking: reference, when, how much -- and the vehicle."""

    open_requested = Signal(int)

    def __init__(self, row: Row, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("bookingCard")
        self.row = row

        col = QVBoxLayout(self)
        col.setContentsMargins(18, 16, 18, 16)
        col.setSpacing(8)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        head.addWidget(make_label(row.reference, "bookingRef"), 0)
        head.addWidget(
            status_chip(row.status, STATUS_TONES.get(row.status, "muted")), 0, Qt.AlignVCenter
        )
        head.addStretch(1)
        head.addWidget(make_label(row.total, "bookingTotal"), 0, Qt.AlignRight)
        col.addLayout(head)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)

        self.photo = VehiclePhoto(thumbnail=True)
        self.photo.setFixedSize(120, 78)
        body.addWidget(self.photo, 0, Qt.AlignTop)
        if row.ref is not None:
            self.photo.set_vehicle(row.ref)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(4)
        text.addWidget(make_label(row.vehicle_name, "gridName"))
        text.addWidget(make_label(row.meta, "gridMeta"))
        start, end = row.window
        text.addWidget(
            make_label(f"{moment_text(start)}  \u2192  {moment_text(end)}", "bookingWindow")
        )
        text.addWidget(make_label(row.payment, "bookingMeta"))
        note = STATUS_NOTES.get(row.status)
        if note:
            text.addWidget(hint_label(note, "amber" if row.status == "pending" else "muted"))
        text.addStretch(1)
        body.addLayout(text, 1)

        if row.vehicle_id is not None:
            button = text_button("View vehicle")
            button.clicked.connect(
                lambda _checked=False, vid=self.row.vehicle_id: self.open_requested.emit(vid)
            )
            body.addWidget(button, 0, Qt.AlignTop | Qt.AlignRight)

        col.addLayout(body)


class BookingsPage(QWidget):
    """The tab. `set_user` re-reads from the database; `focus_bucket` picks a pill."""

    vehicle_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self._identity: Identity | None = None
        self._bucket = "all"
        self._cards: list[BookingCard] = []

        self.title = section_title("Your bookings")
        self.summary = hint_label("", tone="muted")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.title, 1)
        header.addWidget(self.summary, 0, Qt.AlignBottom)

        self.pill_group_widget = QFrame()
        self.pill_group_widget.setObjectName("pillRow")
        pills_row = QHBoxLayout(self.pill_group_widget)
        pills_row.setContentsMargins(0, 0, 0, 0)
        pills_row.setSpacing(10)
        self.pills: dict[str, object] = {}
        buttons = []
        for key, caption in FILTERS:
            button = pill(caption)
            button.clicked.connect(lambda _checked, k=key: self.focus_bucket(k))
            self.pills[key] = button
            buttons.append(button)
            pills_row.addWidget(button)
        pills_row.addStretch(1)
        self._pill_group = pill_group(buttons)  # type: ignore[arg-type]
        self.pills["all"].setChecked(True)  # type: ignore[attr-defined]

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
        body.addWidget(self.pill_group_widget)
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

        self._set_empty_text()

    # ---------- state ----------
    def set_user(self, identity: Identity | None) -> None:
        """Whose bookings these are. None (a guest) shows the sign-in prompt."""
        self._identity = identity
        self.reload()

    def focus_bucket(self, bucket: str) -> None:
        if bucket not in dict(FILTERS):
            return
        self._bucket = bucket
        button = self.pills.get(bucket)
        if button is not None:
            button.setChecked(True)  # type: ignore[attr-defined]
        self.reload()

    # ---------- data ----------
    def reload(self) -> None:
        self._clear_cards()
        self.error.hide()

        if self._identity is None:
            self.summary.setText("")
            self.empty.setText(
                "Sign in from the top bar to see your bookings here."
            )
            self.empty.show()
            return

        rows: list[Row] = []
        try:
            with SessionLocal() as session:
                user = session.get(Users, self._identity.user_id)
                if user is None:
                    raise ServiceError("That account no longer exists.")
                for booking in booking_service.bookings_for(
                    session, user, bucket=self._bucket
                ):
                    vehicle = booking.vehicle
                    start, end = booking_service.booking_window(booking)
                    rows.append(
                        Row(
                            booking_id=booking.booking_id,
                            reference=booking_service.booking_reference(booking),
                            status=booking.status or "pending",
                            vehicle_id=vehicle.vehicle_id if vehicle else None,
                            vehicle_name=(
                                vehicle_service.display_name(vehicle)
                                if vehicle
                                else "Vehicle no longer in the fleet"
                            ),
                            meta=(
                                vehicle_service.summary_line(vehicle)
                                if vehicle
                                else ""
                            ),
                            window=(start, end),
                            total=theme.pesos_text(booking.total or booking.total_cost),
                            payment=_payment_label(booking),
                            ref=vehicle_photos.ref_for(vehicle) if vehicle else None,
                        )
                    )
        except ServiceError as exc:
            self._fail(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - the database is remote
            self._fail(f"Could not load your bookings: {exc}")
            return

        for row in rows:
            card = BookingCard(row)
            card.open_requested.connect(self.vehicle_selected)
            self.list_layout.addWidget(card)
            self._cards.append(card)

        count = len(rows)
        self.summary.setText(
            f"{count} booking{'' if count == 1 else 's'}"
            if self._bucket != "all"
            else f"{count} booking{'' if count == 1 else 's'} in total"
        )
        if not rows:
            self._set_empty_text()
            self.empty.show()
        else:
            self.empty.hide()

    def _set_empty_text(self) -> None:
        caption = dict(FILTERS).get(self._bucket, "All")
        self.empty.setText(
            "Nothing here yet. Book a vehicle and it will show up on this tab."
            if self._bucket == "all"
            else f"No {caption.lower()} bookings right now."
        )

    def _clear_cards(self) -> None:
        for card in self._cards:
            self.list_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

    def _fail(self, message: str) -> None:
        self.empty.hide()
        self.error.setText(message)
        self.error.show()


__all__ = ["BookingsPage", "FILTERS", "Row"]
