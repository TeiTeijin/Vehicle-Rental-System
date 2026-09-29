"""Payments: recording money, and working out what is still owed.

`receipt_service.print_receipt` had its own inline balance calculation, and
`booking_service.check_out` had another. They disagreed: receipt_service
counted a payment as paid whenever `status == "paid"` but summed over a
relationship that could only hold one row, and neither shared the penalty
total with the amount actually recorded. A customer could be told they owed
PHP 0 on the screen and handed a receipt for PHP 1,000.

Everything now goes through `booking_balance`. A screen that wants to show a
figure calls that, and gets the same answer the receipt does.

Nothing here talks to a payment gateway. GCash, card and cash are all recorded
the same way -- as an assertion by staff that money changed hands -- and the
`reference_no` column is where the proof goes. That is an honest model of what
this application can actually know.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Booking, Payment
from app.services.errors import StateError, ValidationError
from app.utils.money import ZERO, money, sum_money

#: The only statuses that mean money actually arrived.
SETTLED_STATUSES = ("paid",)

#: Statuses a new payment record may be created with. `paid` is the normal one
#; `pending` covers a GCash transfer still in flight.
RECORDABLE_STATUSES = ("pending", "paid", "failed")

METHODS = ("cash", "card", "gcash")


@dataclass(frozen=True)
class BookingBalance:
    """What a booking owes and what has been paid against it."""

    rental_cost: Decimal
    penalty_total: Decimal
    total_due: Decimal
    amount_paid: Decimal
    balance: Decimal

    @property
    def is_settled(self) -> bool:
        return self.balance <= ZERO


def amount_paid(session: Session, booking: Booking) -> Decimal:
    """Total actually collected against a booking.

    Only `paid` rows count. A `pending` GCash transfer and a `failed` card
    attempt are both money that has not arrived, and a `refunded` row is money
    that left again -- summing any of them into the takings figure is exactly
    the kind of error that gets caught by an auditor.
    """
    return sum(
        (payment.amount for payment in booking.payments if payment.status in SETTLED_STATUSES),
        ZERO,
    )


def booking_balance(session: Session, booking: Booking) -> BookingBalance:
    """The authoritative balance for a booking.

    Penalties count as owed but not as paid, so a late return shows up as a
    balance the customer still has to settle at the counter.
    """
    rental = money(booking.total_cost, field="total_cost")
    penalties = sum((p.amount for p in booking.penalties), ZERO)
    due = money(rental + penalties, field="total_due")
    paid = money(amount_paid(session, booking), field="amount_paid")
    return BookingBalance(
        rental_cost=rental,
        penalty_total=penalties,
        total_due=due,
        amount_paid=paid,
        balance=money(due - paid, field="balance"),
    )


def record_payment(
    session: Session,
    booking: Booking,
    amount,
    method: str,
    *,
    recorded_by: int | None = None,
    reference_no: str | None = None,
    note: str | None = None,
    status: str = "paid",
) -> Payment:
    """Record a payment against a booking.

    Overpayment is allowed but has to be deliberate: a `field_amount` that
    exceeds the balance is almost always a typo, and silently absorbing it
    loses the difference. Pass the figure you mean; the caller decides whether
    the excess becomes a refund.
    """
    if method not in METHODS:
        raise ValidationError(
            f"Unknown payment method: {method}. Choose one of "
            f"{', '.join(METHODS)}.",
            field="method",
        )
    if status not in RECORDABLE_STATUSES:
        raise ValidationError(
            f"A payment cannot be recorded as '{status}'.", field="status"
        )
    if booking.status == "cancelled":
        raise StateError(
            f"Booking #{booking.booking_id} is cancelled; it cannot take a payment."
        )

    value = money(amount, field="amount")
    if value <= ZERO:
        raise ValidationError(
            "Payment amount must be greater than zero.", field="amount"
        )

    payment = Payment(
        booking=booking,
        amount=value,
        method=method,
        status=status,
        paid_at=datetime.now() if status == "paid" else None,
        recorded_by=recorded_by,
        reference_no=(reference_no or "").strip() or None,
        note=(note or "").strip() or None,
    )
    session.add(payment)
    session.flush()
    return payment


def refund_payment(
    session: Session,
    payment: Payment, *, reason: str, recorded_by: int | None = None
) -> Payment:
    """Reverse a settled payment.

    Implemented by marking the original row `refunded` rather than by deleting
    it or writing a negative row. A refund that erases the payment it reverses
    leaves no record that the money ever moved, which is the one thing a drawer
    reconciliation needs.
    """
    if payment.status != "paid":
        raise StateError(
            f"Payment #{payment.payment_id} is '{payment.status}', so it cannot "
            "be refunded."
        )
    if not (reason or "").strip():
        raise ValidationError("A refund needs a reason.", field="reason")

    payment.status = "refunded"
    note = (payment.note or "").strip()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    payment.note = (
        f"{note} | REFUNDED {stamp}: {reason.strip()}"
        if note
        else f"REFUNDED {stamp}: {reason.strip()}"
    )
    session.flush()
    return payment


def list_payments(
    session: Session,
    *,
    method: str | None = None,
    status: str | None = None,
    recorded_by: int | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> list[Payment]:
    """Payments newest first, for the Payments tab and end-of-shift drawer count."""
    stmt = select(Payment).options(
        joinedload(Payment.booking).joinedload(Booking.vehicle),
        joinedload(Payment.recorder),
    )
    if method:
        stmt = stmt.where(Payment.method == method)
    if status:
        stmt = stmt.where(Payment.status == status)
    if recorded_by is not None:
        stmt = stmt.where(Payment.recorded_by == recorded_by)
    if from_date:
        stmt = stmt.where(Payment.created_at >= from_date)
    if to_date:
        stmt = stmt.where(Payment.created_at <= to_date)
    return list(session.execute(stmt.order_by(Payment.created_at.desc())).scalars().unique())


def takings(session: Session, *, from_date: datetime | None = None, to_date: datetime | None = None) -> Decimal:
    """Cash actually collected in a window, for the Payments tab header."""
    payments = list_payments(session, status="paid", from_date=from_date, to_date=to_date)
    return money(sum((p.amount for p in payments), ZERO), field="takings")
