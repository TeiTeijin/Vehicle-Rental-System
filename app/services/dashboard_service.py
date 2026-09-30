"""Aggregates for the staff dashboard.

Separate from ``booking_service`` because none of these are part of the
rental lifecycle -- they read history and hand back shapes for a chart, and
nothing in the app books a car because of them. They live here rather than in
``app/staff/pages/dashboard.py`` so that a query is testable without a widget
and so the same question gets one answer wherever it is asked.

Two conventions worth stating once, because both are easy to get subtly wrong:

**Revenue is grouped on ``PAYMENT.paid_at``, not ``created_at``.** These two
days ago: revenue is when the money arrived, so a deposit taken at the counter
today for a rental that ended last month is last month's revenue. This is a
deliberate difference from ``payment_service.takings``, which the Payments tab
uses -- there "taken today" is the useful question ("how much did I bank?"),
and it deliberately counts by ``created_at``. Both are right for their screen;
they should not be confused for each other.

**A booking's channel can be NULL.** The column was added after the fact, so
rows that predate it have no value. Those are counted as *unknown*, never
folded into walk-in -- assuming a walk-in would invent history the branch
never recorded.

**The heatmap clips at now.** The seeder places a booking's ``created_at``
slightly before its start date, which pushes some rows past today. An order
placed tomorrow is not activity, so future-dated rows are dropped rather than
plotted.
"""

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

#: How many weeks the activity heatmap shows. Sixteen weeks is about the widest
#: that still fits a card without the cells going narrower than the gap between
#: them.
HEATMAP_WEEKS = 16

#: Statuses that mean money actually arrived. Only these are revenue -- a
#: pending transfer and a declined card are both money that has not arrived,
#: and a refund is money that left again.
_MONEY_IN = ("paid",)

#: Order statuses that never happened, and so should not be counted as orders
#: anywhere on the dashboard.
_VOID_STATUSES = ("cancelled",)


@dataclass(frozen=True)
class ChannelTotals:
    """How the branch's orders split, and what is left unattributed."""

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
        """(channel, count) in a fixed order, for drawing.

        Ordered by CHANNELS rather than by magnitude so the two curves keep
        their colours and their places on the axis when the numbers cross over.
        """
        return [(name, self.get(name)) for name in CHANNELS]

    @property
    def unattributed(self) -> int:
        """Rows the branch never recorded a channel for."""
        return self.unknown


@dataclass(frozen=True)
class MonthSales:
    """Takings for one calendar month, in the order the axis wants them."""

    month: date  # first of the month
    label: str
    total: Decimal = ZERO
    #: The month's share of the annual target, for the target line to compare
    #: against. Zero until the dashboard scales it in.
    target: Decimal = ZERO


@dataclass
class DashboardFigures:
    """Everything the dashboard shows, read in one pass.

    Built by :func:`collect` so the cards cannot disagree with each other: two
    cards each running their own query would show two different "todays" if a
    refresh straddled midnight.
    """

    today: date
    revenue_today: Decimal = ZERO
    revenue_yesterday: Decimal = ZERO
    recent: list = field(default_factory=list)  # list[TransactionRow]
    channels: ChannelTotals = field(default_factory=ChannelTotals)
    orders_per_day: dict = field(default_factory=dict)  # date -> int
    month_sales: list = field(default_factory=list)  # list[MonthSales]

    @property
    def revenue_ytd(self) -> Decimal:
        return money(sum((m.total for m in self.month_sales), ZERO), field="ytd")

    @property
    def revenue_change_pct(self) -> float | None:
        """Percent change against yesterday, or None when it cannot be said.

        None rather than zero when yesterday was empty: a jump from nothing to
        something is not "no change", and drawing a flat 0% would say something
        untrue about the branch.
        """
        if not self.revenue_yesterday:
            return None
        delta = self.revenue_today - self.revenue_yesterday
        return float(delta / self.revenue_yesterday * 100)


@dataclass(frozen=True)
class TransactionRow:
    """One line of the recent-transactions card, already display-ready.

    Built inside the reading session that loaded it -- an ORM object handed back
    out of ``context.reading()`` raises ``DetachedInstanceError`` the moment
    the caller reads an attribute, because the session rolls back and expires
    everything it touched on the way out.
    """

    customer: str
    channel: str | None
    method: str
    amount: Decimal  # always positive; `refunded` carries the sign
    refunded: bool


def orders_between(
    session: Session,
    from_date: date,
    to_date: date,
) -> ChannelTotals:
    """Count real orders placed in a window, split by channel."""
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
    """Orders placed per day for the last `weeks`, oldest first.

    Gaps are present as zeroes rather than absent, because the heatmap draws a
    fixed grid and a missing key is not the same as a quiet Tuesday.
    """
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
    """Takings per month for one year, oldest first.

    Grouped on ``PAYMENT.paid_at``, not ``created_at``: revenue is when the
    money arrived. Months with no takings are still returned as zeroes so the
    line has a point to sit on.
    """
    until = until or date.today()
    first = date(year, 1, 1)

    rows = session.execute(
        select(
            func.date(Payment.paid_at).label("day"),
            func.sum(Payment.amount).label("total"),
        )
        .where(
            Payment.status.in_(_MONEY_IN),
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
    """The most recently recorded payments, newest first.

    Eager-loads the customer in one join rather than letting each row trigger
    its own lazy load -- for five rows that is five extra queries, on a
    dashboard that already refreshes on a timer.
    """
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
                # The sign is a rendering decision made from status: the column
                # never goes negative, because `record_payment` refuses a
                # non-positive amount.
                amount=payment.amount,
                refunded=payment.status == "refunded",
            )
        )
    return out


def revenue_on(session: Session, day: date) -> Decimal:
    """Revenue earned on one calendar day -- money that landed that day.

    Aggregated in the database rather than by loading payments and summing in
    Python: this runs on every dashboard refresh, which is every thirty
    seconds, and the Payments tab's own query would also drag the whole day's
    rows and their vehicles across the wire to add up four numbers.

    Does not delegate to ``payment_service.takings``, which counts by
    ``created_at`` for the Payments tab. See the module docstring.
    """
    total = session.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status.in_(_MONEY_IN),
            Payment.paid_at >= datetime.combine(day, datetime.min.time()),
            Payment.paid_at <= datetime.combine(day, datetime.max.time()),
        )
    ).scalar_one()
    return money(total or 0, field="revenue_on")


def collect(
    session: Session,
    *,
    today: date | None = None,
    weeks: int = HEATMAP_WEEKS,
    recent: int = 5,
    monthly: bool = True,
) -> DashboardFigures:
    """Read every dashboard figure against one `today`.

    `weeks`, `recent` and `monthly` switch off the aggregates that only the
    cards need. A staff member's refresh passes `weeks=0, recent=0,
    monthly=False` so the three expensive queries are never issued at all --
    not issued and discarded, which would still cost the round trip.
    """
    today = today or date.today()

    figures = DashboardFigures(
        today=today,
        revenue_today=revenue_on(session, today),
        revenue_yesterday=revenue_on(session, today - timedelta(days=1)),
    )

    if weeks > 0:
        first = _monday_of(today) - timedelta(weeks=weeks - 1)
        figures.channels = orders_between(session, first, today)
        figures.orders_per_day = orders_per_day(session, weeks, today=today)

    if recent > 0:
        figures.recent = recent_transactions(session, recent)

    if monthly:
        figures.month_sales = month_sales(session, today.year, until=today)

    return figures


def _monday_of(day: date) -> date:
    """The Monday of `day`'s week. Weeks start Monday because a rental shop
    is busiest at the weekend, and Sunday-first would split those in half."""
    return day - timedelta(days=day.weekday())
