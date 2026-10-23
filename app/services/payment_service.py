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

#: `pending` covers a GCash transfer still in flight.
RECORDABLE_STATUSES = ("pending", "paid", "failed")

METHODS = ("cash", "card", "gcash")

#: What a payment row is for. The deposit is a refundable hold against
#: damage/late return -- absorbed credit, never revenue.
KINDS = ("rental", "deposit")


@dataclass(frozen=True)
class BookingBalance:
    rental_cost: Decimal
    penalty_total: Decimal
    total_due: Decimal
    amount_paid: Decimal
    balance: Decimal
    #: Refundable deposit currently held (paid, not returned).
    deposit_held: Decimal
    #: Deposit already handed back (history rows, never touches balance).
    deposit_returned: Decimal

    @property
    def is_settled(self) -> bool:
        return self.balance <= ZERO


def amount_paid(session: Session, booking: Booking) -> Decimal:
    return sum(
        (payment.amount for payment in booking.payments if payment.status in SETTLED_STATUSES),
        ZERO,
    )


def deposit_held(session: Session, booking: Booking) -> Decimal:
    """Refundable deposit currently held: deposit rows paid, minus every peso
    already handed back (`refunded` history rows from `settle_deposit`)."""
    paid = sum(
        (
            payment.amount
            for payment in booking.payments
            if payment.kind == "deposit" and payment.status == "paid"
        ),
        ZERO,
    )
    return paid - deposit_returned(session, booking)


def deposit_returned(session: Session, booking: Booking) -> Decimal:
    """Deposit handed back: `refunded`-status history rows (see settle_deposit)."""
    return sum(
        (
            payment.amount
            for payment in booking.payments
            if payment.kind == "deposit" and payment.status == "refunded"
        ),
        ZERO,
    )


def booking_balance(session: Session, booking: Booking) -> BookingBalance:
    rental = money(booking.total_cost, field="total_cost")
    penalties = sum((p.amount for p in booking.penalties), ZERO)
    due = money(rental + penalties, field="total_due")
    paid = money(amount_paid(session, booking), field="amount_paid")
    held = money(deposit_held(session, booking), field="deposit_held")
    given_back = money(deposit_returned(session, booking), field="deposit_returned")
    return BookingBalance(
        rental_cost=rental,
        penalty_total=penalties,
        total_due=due,
        amount_paid=paid,
        balance=money(due - paid, field="balance"),
        deposit_held=held,
        deposit_returned=given_back,
    )


def record_payment(
    session: Session,
    booking: Booking,
    amount,
    method: str,
    *,
    kind: str = "rental",
    recorded_by: int | None = None,
    reference_no: str | None = None,
    note: str | None = None,
    status: str = "paid",
) -> Payment:
    if method not in METHODS:
        raise ValidationError(
            f"Unknown payment method: {method}. Choose one of "
            f"{', '.join(METHODS)}.",
            field="method",
        )
    if kind not in KINDS:
        raise ValidationError(
            f"Unknown payment kind: {kind}. Choose one of "
            f"{', '.join(KINDS)}.",
            field="kind",
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
        kind=kind,
        status=status,
        paid_at=datetime.now() if status == "paid" else None,
        recorded_by=recorded_by,
        reference_no=(reference_no or "").strip() or None,
        note=(note or "").strip() or None,
    )
    session.add(payment)
    session.flush()
    return payment


def settle_deposit(session: Session, booking: Booking) -> Decimal:
    """Close out the held deposit at check-out.

    Deposit rows stay `paid` forever (their value is absorbed credit through
    `amount_paid`); every peso handed back is a new `refunded`-status history
    row, so `deposit_held = paid - refunded` is always exact and the balance
    math never drifts:
    - No penalties: one history row returns the full held amount.
    - Penalties: the deposit offsets them in the balance; each tranche of
      cash physically handed back is a history row with the story in `note`.
    - Shortfall (penalties exceed the deposit): no cash back; the deposit
      rows are annotated and the remainder stays OUTSTANDING.
    Returns the deposit still held afterwards.
    """
    held = money(deposit_held(session, booking), field="deposit_held")
    if held <= ZERO:
        return ZERO
    penalties = sum((p.amount for p in booking.penalties), ZERO)
    if penalties <= ZERO:
        back = Payment(
            booking=booking,
            amount=held,
            method="cash",
            kind="deposit",
            status="refunded",
            paid_at=None,
            note="Clean return: deposit refunded in full.",
        )
        session.add(back)
        session.flush()
        return ZERO
    change = money(held - penalties, field="deposit_change")
    if change > ZERO:
        back = Payment(
            booking=booking,
            amount=change,
            method="cash",
            kind="deposit",
            status="refunded",
            paid_at=None,
            note=f"Deposit change returned: {penalties} applied to penalties.",
        )
        session.add(back)
        session.flush()
        # Still held (absorbed against the penalties): paid minus returned.
        return money(penalties, field="deposit_held")
    for payment in booking.payments:
        if payment.kind == "deposit" and payment.status == "paid":
            payment.note = (
                f"{(payment.note or '').strip()} | "
                f"APPLIED to penalties (shortfall {money(penalties - held)} remains)."
            ).strip(" |")
    session.flush()
    return ZERO


def refund_payment(
    session: Session,
    payment: Payment, *, reason: str, recorded_by: int | None = None
) -> Payment:
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
    kind: str | None = None,
    status: str | None = None,
    recorded_by: int | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> list[Payment]:
    stmt = select(Payment).options(
        joinedload(Payment.booking).joinedload(Booking.vehicle),
        joinedload(Payment.booking).joinedload(Booking.user),
        joinedload(Payment.recorder),
    )
    if method:
        stmt = stmt.where(Payment.method == method)
    if kind:
        stmt = stmt.where(Payment.kind == kind)
    if status:
        stmt = stmt.where(Payment.status == status)
    if recorded_by is not None:
        stmt = stmt.where(Payment.recorded_by == recorded_by)
    if from_date:
        stmt = stmt.where(Payment.created_at >= from_date)
    if to_date:
        stmt = stmt.where(Payment.created_at <= to_date)
    return list(session.execute(stmt.order_by(Payment.created_at.desc())).scalars().unique())


def payments_for_user(session: Session, user_id: int) -> list[Payment]:
    """One customer's payment history, newest first.

    The join is deliberate: a payment only belongs to a customer through its
    booking. It also means a row with a missing booking can never leak into
    somebody else's tab.
    """
    stmt = (
        select(Payment)
        .join(Payment.booking)
        .options(joinedload(Payment.booking).joinedload(Booking.vehicle))
        .where(Booking.user_id == user_id)
        .order_by(Payment.created_at.desc(), Payment.payment_id.desc())
    )
    return list(session.execute(stmt).scalars().unique())


def takings(session: Session, *, from_date: datetime | None = None, to_date: datetime | None = None) -> Decimal:
    # Held deposits are not revenue: only rental-kind takings count.
    payments = list_payments(session, kind="rental", status="paid", from_date=from_date, to_date=to_date)
    return money(sum((p.amount for p in payments), ZERO), field="takings")
