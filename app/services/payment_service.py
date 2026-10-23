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

#: What a payment row is for. A deposit is a refundable hold against
#: damage/late return; `deposit_applied` is the ledger adjustment made at
#: check-out when part of that hold pays penalties. Neither held deposits nor
#: applied deposits are cash revenue.
KINDS = ("rental", "deposit", "deposit_applied")


@dataclass(frozen=True)
class BookingBalance:
    rental_cost: Decimal
    penalty_total: Decimal
    total_due: Decimal
    amount_paid: Decimal
    balance: Decimal
    #: Refundable deposit currently held (paid, not returned, not applied).
    deposit_held: Decimal
    #: Deposit already handed back (history rows, never touches balance).
    deposit_returned: Decimal
    #: Deposit applied to penalties at check-out (ledger, never cash/revenue).
    deposit_applied: Decimal

    @property
    def is_settled(self) -> bool:
        return self.balance <= ZERO


def amount_paid(session: Session, booking: Booking) -> Decimal:
    """Money credited against the bill: rental payments and applied deposits.

    A held security deposit is a liability, not a payment, so it is excluded
    until `settle_deposit` applies some of it to penalties.
    """
    return sum(
        (
            payment.amount
            for payment in booking.payments
            if payment.status in SETTLED_STATUSES
            and payment.kind in ("rental", "deposit_applied")
        ),
        ZERO,
    )


def deposit_applied(session: Session, booking: Booking) -> Decimal:
    """Deposit applied to penalties: `paid` ledger rows, never cash."""
    return sum(
        (
            payment.amount
            for payment in booking.payments
            if payment.kind == "deposit_applied"
            and payment.status in SETTLED_STATUSES
        ),
        ZERO,
    )


def deposit_held(session: Session, booking: Booking) -> Decimal:
    """Refundable deposit currently held.

    Deposit rows paid, minus every peso already returned (`refunded` history
    rows) and minus every peso applied to penalties (`deposit_applied` ledger
    rows) by `settle_deposit`.
    """
    paid = sum(
        (
            payment.amount
            for payment in booking.payments
            if payment.kind == "deposit" and payment.status == "paid"
        ),
        ZERO,
    )
    return money(
        paid
        - deposit_returned(session, booking)
        - deposit_applied(session, booking),
        field="deposit_held",
    )


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
    applied = money(deposit_applied(session, booking), field="deposit_applied")
    return BookingBalance(
        rental_cost=rental,
        penalty_total=penalties,
        total_due=due,
        amount_paid=paid,
        balance=money(due - paid, field="balance"),
        deposit_held=held,
        deposit_returned=given_back,
        deposit_applied=applied,
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

    A held deposit is a liability, not rental revenue. At settlement:

    - `rental_paid` is rental-kind money already received; any overpayment of
      the rental total offsets penalties first.
    - `penalties_outstanding = max(0, penalty_total - max(0, rental_paid -
      rental))`.
    - `min(held, penalties_outstanding)` is recorded as a `deposit_applied`
      ledger row. It is not cash and not revenue; it is credited against the
      bill by `amount_paid`.
    - The remainder is handed back as a `refunded` deposit history row.

    A shortfall (penalties beyond the deposit) stays OUTSTANDING: no cash is
    returned, and the booking's balance remains positive. Returns the deposit
    still held afterwards, which is always zero after a successful settlement.
    """
    held = money(deposit_held(session, booking), field="deposit_held")
    if held <= ZERO:
        return ZERO

    rental = money(booking.total_cost, field="total_cost")
    rental_paid = sum(
        (
            payment.amount
            for payment in booking.payments
            if payment.kind == "rental" and payment.status in SETTLED_STATUSES
        ),
        ZERO,
    )
    penalty_total = sum((p.amount for p in booking.penalties), ZERO)
    overpaid_rental = max(ZERO, money(rental_paid, field="rental_paid") - rental)
    penalties_outstanding = max(
        ZERO, money(penalty_total, field="penalty_total") - overpaid_rental
    )
    applied = money(min(held, penalties_outstanding), field="deposit_applied")
    refund = money(held - applied, field="deposit_refund")

    deposit_rows = [
        payment
        for payment in booking.payments
        if payment.kind == "deposit" and payment.status == "paid"
    ]
    # The legacy method enum has no non-cash adjustment value. The applied row
    # carries the method of the deposit it consumes (fallback cash) so the
    # ledger stays honest, and the note says it is not a tender.
    method = deposit_rows[0].method if deposit_rows else "cash"

    if applied > ZERO:
        shortfall = money(penalties_outstanding - applied, field="penalty_shortfall")
        note = (
            f"Deposit applied to penalties: {applied} "
            f"(ledger adjustment, not cash)"
        )
        if shortfall > ZERO:
            note += f"; {shortfall} penalty shortfall remains outstanding"
        session.add(
            Payment(
                booking=booking,
                amount=applied,
                method=method,
                kind="deposit_applied",
                status="paid",
                paid_at=None,
                note=note + ".",
            )
        )

    if refund > ZERO:
        if applied > ZERO:
            note = (
                f"Deposit change returned after {applied} applied to penalties."
            )
        else:
            note = "Clean return: deposit refunded in full."
        session.add(
            Payment(
                booking=booking,
                amount=refund,
                method="cash",
                kind="deposit",
                status="refunded",
                paid_at=None,
                note=note,
            )
        )

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


def takings(session: Session, *, from_date: datetime | None = None, to_date: datetime | None = None) -> Decimal:
    # Held deposits are not revenue: only rental-kind takings count.
    payments = list_payments(session, kind="rental", status="paid", from_date=from_date, to_date=to_date)
    return money(sum((p.amount for p in payments), ZERO), field="takings")
