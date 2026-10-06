from __future__ import annotations

from decimal import Decimal
from functools import partial

from sqlalchemy import select

from app.models import Booking, Users
from app.services import payment_service
from app.staff.pages.base import StaffPage
from app.staff.tables import (
    ALIGN_LEFT,
    ALIGN_RIGHT,
    Column,
    LoadState,
    LoadStateTable,
)

#: Balances at or below this count as settled.
SETTLED_AT = Decimal("0.00")


def _payment_rows(context):
    with context.reading() as session:
        rows = []
        for payment in payment_service.list_payments(session):
            booking = payment.booking
            customer = booking.user if booking is not None else None
            recorded_by = session.get(Users, payment.recorded_by) if payment.recorded_by else None
            rows.append(
                (
                    payment.payment_id,
                    f"#{booking.booking_id}" if booking is not None else "-",
                    customer.full_name if customer is not None else "-",
                    f"{payment.amount:,.2f}",
                    payment.method.title(),
                    payment.status,
                    payment.paid_at.strftime("%d %b %Y %H:%M") if payment.paid_at else "Not cleared",
                    payment.reference_no or "-",
                    recorded_by.full_name if recorded_by is not None else "-",
                )
            )
        return rows


def _outstanding_rows(context):
    with context.reading() as session:
        rows = []
        bookings = session.execute(
            select(Booking).where(Booking.status.in_(("confirmed", "ongoing", "completed")))
        ).scalars().all()
        for booking in bookings:
            balance = payment_service.booking_balance(session, booking)
            if balance.balance <= SETTLED_AT:
                continue
            vehicle = booking.vehicle
            rows.append(
                (
                    f"#{booking.booking_id}",
                    booking.user.full_name,
                    f"{vehicle.plate_number} {vehicle.make} {vehicle.model}",
                    booking.status,
                    f"{balance.rental_cost:,.2f}",
                    f"{balance.penalty_total:,.2f}",
                    f"{balance.total_due:,.2f}",
                    f"{balance.amount_paid:,.2f}",
                    f"{balance.balance:,.2f}",
                )
            )
        rows.sort(key=lambda r: Decimal(r[-1].replace(",", "")), reverse=True)
        return rows


class PaymentsPage(StaffPage):
    def __init__(self, shell) -> None:
        super().__init__(
            shell,
            "Payments",
            "Everything taken, and everything still owed.",
        )

        self.takings = LoadStateTable(
            [
                Column("Ref", ALIGN_RIGHT),
                Column("Booking", ALIGN_LEFT),
                Column("Customer", ALIGN_LEFT),
                Column("Amount", ALIGN_RIGHT),
                Column("Method", ALIGN_LEFT),
                Column("Status"),
                Column("Taken at", ALIGN_LEFT),
                Column("Reference", ALIGN_LEFT),
                Column("Recorded by", ALIGN_LEFT),
            ],
            partial(_payment_rows, self.context),
            empty_message="No payments recorded yet.",
        )

        self.outstanding = LoadStateTable(
            [
                Column("Booking", ALIGN_LEFT),
                Column("Customer", ALIGN_LEFT),
                Column("Vehicle", ALIGN_LEFT),
                Column("Status"),
                Column("Rental", ALIGN_RIGHT),
                Column("Penalties", ALIGN_RIGHT),
                Column("Total due", ALIGN_RIGHT),
                Column("Paid", ALIGN_RIGHT),
                Column("Owed", ALIGN_RIGHT),
            ],
            partial(_outstanding_rows, self.context),
            empty_message="Nothing outstanding. Every rental is settled.",
        )

        self.body.addWidget(self._heading("All payments"))
        self.body.addWidget(self.takings, 2)
        self.body.addWidget(self._heading("Outstanding balances"))
        self.body.addWidget(self.outstanding, 1)

    def _heading(self, text: str):
        from PySide6.QtWidgets import QLabel

        label = QLabel(text, self)
        label.setObjectName("pageSubtitle")
        return label

    def refresh(self) -> None:
        super().refresh()
        self.takings.load()
        self.outstanding.load()

    @property
    def all_settled(self) -> bool:
        return self.outstanding.state is LoadState.EMPTY


def build_payments_page(shell) -> PaymentsPage:
    return PaymentsPage(shell)
