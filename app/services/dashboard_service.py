from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Booking, Payment
from app.services.booking_service import CHANNELS
from app.utils.money import ZERO, money

#: How many weeks the activity heatmap shows.
HEATMAP_WEEKS = 16

#: Days offered by the picker, plus one so the oldest has a prior day.
REVENUE_DAYS = 14

#: Statuses that mean money actually arrived.
_MONEY_IN = ("paid",)

#: Order statuses that never happened and must not be counted.
_VOID_STATUSES = ("cancelled",)


@dataclass(frozen=True)
class ChannelTotals:
    walk_in: int = 0
    online: int = 0
    unknown: int = 0

    @property
    def total(self) -> int:
        return self.walk_in + self.online

    def get(self, channel: str | None) -> int:
        if channel == "walk_in":
            return self.walk_in
        if channel == "online":
            return self.online
        return self.unknown

    def as_rows(self) -> list[tuple[str, int]]:
        return [(name, self.get(name)) for name in CHANNELS]

    @property
    def unattributed(self) -> int:
        return self.unknown


@dataclass(frozen=True)
class MonthSales:
    month: date  # first of the month
    label: str
    total: Decimal = ZERO
    #: The month's share of the annual target.
    target: Decimal = ZERO


@dataclass
class DashboardFigures:
    today: date
    revenue_today: Decimal = ZERO
    revenue_yesterday: Decimal = ZERO
    #: Revenue per calendar day over the picker's window; every day present.
    revenue_days: dict = field(default_factory=dict)  # date -> Decimal
    recent: list = field(default_factory=list)  # list[TransactionRow]
    channels: ChannelTotals = field(default_factory=ChannelTotals)
    orders_per_day: dict = field(default_factory=dict)  # date -> int
    month_sales: list = field(default_factory=list)  # list[MonthSales]

    @property
    def revenue_ytd(self) -> Decimal:
        return money(sum((m.total for m in self.month_sales), ZERO), field="ytd")

    @property
    def revenue_change_pct(self) -> float | None:
        if not self.revenue_yesterday:
            return None
        delta = self.revenue_today - self.revenue_yesterday
        return float(delta / self.revenue_yesterday * 100)


@dataclass(frozen=True)
class TransactionRow:
    customer: str
    channel: str | None
    method: str
    amount: Decimal  # always positive; `refunded` carries the sign
    refunded: bool
    #: `payment.status`: only ``paid`` is money that actually arrived.
    status: str


def orders_between(
    session: Session,
    from_date: date,
    to_date: date,
) -> ChannelTotals:
    if to_date < from_date:
        return ChannelTotals()
    rows = session.execute(
        select(Booking.channel, func.count(Booking.booking_id))
        .where(
            Booking.created_at >= datetime.combine(from_date, datetime.min.time()),
            Booking.created_at <= datetime.combine(to_date, datetime.max.time()),
            Booking.status.notin_(_VOID_STATUSES),
        )
        .group_by(Booking.channel)
    ).all()
    counts = {channel: int(n) for channel, n in rows}
    return ChannelTotals(
        walk_in=counts.get("walk_in", 0),
        online=counts.get("online", 0),
        unknown=counts.get(None, 0),
    )


def orders_per_day(
    session: Session,
    weeks: int = HEATMAP_WEEKS,
    *,
    today: date | None = None,
) -> dict[date, int]:
    today = today or date.today()
    first = _monday_of(today) - timedelta(weeks=weeks - 1)
    span = (today - first).days + 1
    buckets: dict[date, int] = {first + timedelta(days=i): 0 for i in range(span)}

    rows = session.execute(
        select(func.date(Booking.created_at), func.count(Booking.booking_id))
        .where(
            Booking.created_at <= datetime.combine(today, datetime.max.time()),
            Booking.status.notin_(_VOID_STATUSES),
        )
        .group_by(func.date(Booking.created_at))
    ).all()

    for raw_day, count in rows:
        day = raw_day if isinstance(raw_day, date) else date.fromisoformat(str(raw_day))
        if day in buckets:
            buckets[day] += int(count)
    return buckets


def month_sales(
    session: Session,
    year: int,
    *,
    until: date | None = None,
) -> list[MonthSales]:
    until = until or date.today()
    first = date(year, 1, 1)

    rows = session.execute(
        select(
            func.date(Payment.paid_at).label("day"),
            func.sum(Payment.amount).label("total"),
        )
        .where(
            Payment.status.in_(_MONEY_IN),
            # One revenue definition everywhere (see report_service):
            # money received for the rental itself. Refundable deposits
            # are holds, never revenue.
            Payment.kind == "rental",
            Payment.paid_at >= datetime.combine(first, datetime.min.time()),
            Payment.paid_at <= datetime.combine(until, datetime.max.time()),
        )
        .group_by(func.date(Payment.paid_at))
    ).all()

    by_month: dict[int, object] = defaultdict(lambda: ZERO)
    for raw_day, total in rows:
        day = raw_day if isinstance(raw_day, date) else date.fromisoformat(str(raw_day))
        by_month[day.month] += total or ZERO

    months: list[MonthSales] = []
    for number in range(1, 13):
        months.append(
            MonthSales(
                month=date(year, number, 1),
                label=date(year, number, 1).strftime("%b"),
                total=money(by_month[number], field="month_sales"),
            )
        )
    return months


def recent_transactions(
    session: Session, limit: int = 5
) -> list[TransactionRow]:
    from sqlalchemy.orm import joinedload

    rows = (
        session.execute(
            select(Payment)
            .options(
                joinedload(Payment.booking).joinedload(Booking.user),
            )
            .order_by(Payment.created_at.desc(), Payment.payment_id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )

    out: list[TransactionRow] = []
    for payment in rows:
        booking = payment.booking
        customer = booking.user if booking is not None else None
        out.append(
            TransactionRow(
                customer=customer.full_name if customer is not None else "-",
                channel=booking.channel if booking is not None else None,
                method=payment.method,
                amount=payment.amount,
                refunded=payment.status == "refunded",
                status=payment.status,
            )
        )
    return out


def revenue_on(session: Session, day: date) -> Decimal:
    total = session.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status.in_(_MONEY_IN),
            Payment.kind == "rental",
            Payment.paid_at >= datetime.combine(day, datetime.min.time()),
            Payment.paid_at <= datetime.combine(day, datetime.max.time()),
        )
    ).scalar_one()
    return money(total or 0, field="revenue_on")


def revenue_by_day(
    session: Session,
    from_date: date,
    to_date: date,
) -> dict[date, Decimal]:
    if to_date < from_date:
        return {}
    rows = session.execute(
        select(func.date(Payment.paid_at), func.sum(Payment.amount))
        .where(
            Payment.status.in_(_MONEY_IN),
            Payment.kind == "rental",
            Payment.paid_at >= datetime.combine(from_date, datetime.min.time()),
            Payment.paid_at <= datetime.combine(to_date, datetime.max.time()),
        )
        .group_by(func.date(Payment.paid_at))
    ).all()

    span = (to_date - from_date).days + 1
    buckets: dict[date, Decimal] = {
        from_date + timedelta(days=i): ZERO for i in range(span)
    }
    for raw_day, total in rows:
        day = raw_day if isinstance(raw_day, date) else date.fromisoformat(str(raw_day))
        if day in buckets:
            buckets[day] = money(total or 0, field="revenue_by_day")
    return buckets


def revenue_and_months(
    session: Session,
    today: date,
) -> tuple[dict[date, Decimal], list[MonthSales]]:
    """Revenue-by-day and month-to-date in one payment query.

    The dashboard used to run `revenue_by_day` and `month_sales` separately;
    both group the same PAYMENT rows by day. One read now feeds both cards.
    """
    first = date(today.year, 1, 1)
    rows = session.execute(
        select(func.date(Payment.paid_at), func.sum(Payment.amount))
        .where(
            Payment.status.in_(_MONEY_IN),
            Payment.kind == "rental",
            Payment.paid_at >= datetime.combine(first, datetime.min.time()),
            Payment.paid_at <= datetime.combine(today, datetime.max.time()),
        )
        .group_by(func.date(Payment.paid_at))
    ).all()

    start = today - timedelta(days=REVENUE_DAYS)
    revenue_days: dict[date, Decimal] = {
        start + timedelta(days=i): ZERO for i in range(REVENUE_DAYS + 1)
    }
    by_month: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for raw_day, total in rows:
        day = (
            raw_day
            if isinstance(raw_day, date)
            else date.fromisoformat(str(raw_day))
        )
        amount = money(total or 0, field="revenue_by_day")
        by_month[day.month] += amount
        if day in revenue_days:
            revenue_days[day] = amount

    months = [
        MonthSales(
            month=date(today.year, number, 1),
            label=date(today.year, number, 1).strftime("%b"),
            total=money(by_month[number], field="month_sales"),
        )
        for number in range(1, 13)
    ]
    return revenue_days, months


def orders_and_days(
    session: Session,
    today: date,
    weeks: int = HEATMAP_WEEKS,
) -> tuple[ChannelTotals, dict[date, int]]:
    """Channel totals and the daily heatmap in one booking query."""
    first = _monday_of(today) - timedelta(weeks=weeks - 1)
    span = (today - first).days + 1
    buckets: dict[date, int] = {
        first + timedelta(days=i): 0 for i in range(span)
    }
    rows = session.execute(
        select(
            func.date(Booking.created_at),
            Booking.channel,
            func.count(Booking.booking_id),
        )
        .where(
            Booking.created_at >= datetime.combine(first, datetime.min.time()),
            Booking.created_at <= datetime.combine(today, datetime.max.time()),
            Booking.status.notin_(_VOID_STATUSES),
        )
        .group_by(func.date(Booking.created_at), Booking.channel)
    ).all()

    counts: dict[str | None, int] = defaultdict(int)
    for raw_day, channel, count in rows:
        day = (
            raw_day
            if isinstance(raw_day, date)
            else date.fromisoformat(str(raw_day))
        )
        number = int(count)
        counts[channel] += number
        if day in buckets:
            buckets[day] += number

    return (
        ChannelTotals(
            walk_in=counts.get("walk_in", 0),
            online=counts.get("online", 0),
            unknown=counts.get(None, 0),
        ),
        buckets,
    )


def collect(
    session: Session,
    *,
    today: date | None = None,
    weeks: int = HEATMAP_WEEKS,
    recent: int = 5,
    monthly: bool = True,
) -> DashboardFigures:
    today = today or date.today()

    figures = DashboardFigures(today=today)
    figures.revenue_days, months = revenue_and_months(session, today)
    figures.revenue_today = figures.revenue_days.get(today, ZERO)
    figures.revenue_yesterday = figures.revenue_days.get(
        today - timedelta(days=1), ZERO
    )

    if weeks > 0:
        figures.channels, figures.orders_per_day = orders_and_days(
            session, today, weeks
        )

    if recent > 0:
        figures.recent = recent_transactions(session, recent)

    if monthly:
        figures.month_sales = months

    return figures


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())
