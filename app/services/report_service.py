"""Period reports: aggregate money and booking figures over a date range.

Everything here is aggregate SQL (sums grouped by day), so a report over
months costs a handful of queries -- safe to run synchronously on the GUI
thread, unlike the per-row ledger loaders. No schema change: the figures
derive from PAYMENT / BOOKING / PENALTY rows that already exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import (
    Booking,
    Maintenance_Record,
    Payment,
    Penalty,
    Users,
    Vehicle,
    Vehicle_Category,
)
from app.services import payment_service
from app.services.dashboard_service import ChannelTotals, orders_between
from app.utils.money import ZERO, money


@dataclass(frozen=True)
class DayRow:
    day: date
    collections: Decimal
    bookings: int


@dataclass(frozen=True)
class PeriodReport:
    from_date: date
    to_date: date
    #: Rental money taken (paid, rental-kind). Held deposits are not revenue.
    collections: Decimal
    #: Refundable deposits taken (paid, deposit-kind).
    deposits: Decimal
    #: Penalties raised (all types) in the period.
    penalties: Decimal
    bookings: ChannelTotals
    #: Money still owed across all open history (not period-bound).
    outstanding: Decimal
    days: tuple[DayRow, ...] = ()


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day, datetime.min.time()),
        datetime.combine(day, datetime.max.time()),
    )


def collections_between(session: Session, from_date: date, to_date: date) -> Decimal:
    return payment_service.takings(
        session,
        from_date=datetime.combine(from_date, datetime.min.time()),
        to_date=datetime.combine(to_date, datetime.max.time()),
    )


def deposits_between(session: Session, from_date: date, to_date: date) -> Decimal:
    payments = payment_service.list_payments(
        session,
        kind="deposit",
        status="paid",
        from_date=datetime.combine(from_date, datetime.min.time()),
        to_date=datetime.combine(to_date, datetime.max.time()),
    )
    return money(sum((p.amount for p in payments), ZERO), field="deposits")


def penalties_between(session: Session, from_date: date, to_date: date) -> Decimal:
    start, _ = _day_bounds(from_date)
    _, end = _day_bounds(to_date)
    total = session.execute(
        select(func.coalesce(func.sum(Penalty.amount), 0)).where(
            Penalty.created_at >= start,
            Penalty.created_at <= end,
        )
    ).scalar_one()
    return money(total or 0, field="penalties")


def collections_by_day(
    session: Session, from_date: date, to_date: date
) -> dict[date, Decimal]:
    """Rental money taken per day (paid, rental-kind): deposits excluded."""
    if to_date < from_date:
        return {}
    start, _ = _day_bounds(from_date)
    _, end = _day_bounds(to_date)
    rows = session.execute(
        select(func.date(Payment.paid_at), func.sum(Payment.amount))
        .where(
            Payment.status.in_(("paid",)),
            Payment.kind == "rental",
            Payment.paid_at >= start,
            Payment.paid_at <= end,
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
            buckets[day] = money(total or 0, field="collections_by_day")
    return buckets


def bookings_per_day(
    session: Session, from_date: date, to_date: date
) -> dict[date, int]:
    if to_date < from_date:
        return {}
    start, _ = _day_bounds(from_date)
    _, end = _day_bounds(to_date)
    rows = session.execute(
        select(func.date(Booking.created_at), func.count(Booking.booking_id))
        .where(
            Booking.created_at >= start,
            Booking.created_at <= end,
            Booking.status.notin_(("cancelled",)),
        )
        .group_by(func.date(Booking.created_at))
    ).all()
    span = (to_date - from_date).days + 1
    buckets: dict[date, int] = {
        from_date + timedelta(days=i): 0 for i in range(span)
    }
    for raw_day, count in rows:
        day = raw_day if isinstance(raw_day, date) else date.fromisoformat(str(raw_day))
        if day in buckets:
            buckets[day] += int(count)
    return buckets


def outstanding_total(session: Session) -> Decimal:
    """Money still owed: rental + penalties minus what arrived, over every
    booking that was never cancelled. Clamped at zero so overpayments net
    out instead of printing as negative debt."""
    rental = session.execute(
        select(func.coalesce(func.sum(Booking.total_cost), 0)).where(
            Booking.status.notin_(("cancelled",))
        )
    ).scalar_one()
    penalties = session.execute(
        select(func.coalesce(func.sum(Penalty.amount), 0))
        .select_from(Penalty)
        .join(Booking, Penalty.booking_id == Booking.booking_id)
        .where(Booking.status.notin_(("cancelled",)))
    ).scalar_one()
    paid = session.execute(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .select_from(Payment)
        .join(Booking, Payment.booking_id == Booking.booking_id)
        .where(
            Booking.status.notin_(("cancelled",)),
            Payment.status.in_(("paid",)),
        )
    ).scalar_one()
    total = money(rental or 0) + money(penalties or 0) - money(paid or 0)
    return money(max(total, ZERO), field="outstanding")


def build_report(session: Session, from_date: date, to_date: date) -> PeriodReport:
    if to_date < from_date:
        raise ValueError("The report cannot end before it starts.")
    collections = collections_by_day(session, from_date, to_date)
    counts = bookings_per_day(session, from_date, to_date)
    days = tuple(
        DayRow(day=day, collections=collections[day], bookings=counts[day])
        for day in sorted(counts)
    )
    return PeriodReport(
        from_date=from_date,
        to_date=to_date,
        collections=collections_between(session, from_date, to_date),
        deposits=deposits_between(session, from_date, to_date),
        penalties=penalties_between(session, from_date, to_date),
        bookings=orders_between(session, from_date, to_date),
        outstanding=outstanding_total(session),
        days=days,
    )


# --------------------------------------------------------------------------
# Revenue: one definition shared with the Dashboard.
#
# Revenue = money received (`status=paid`, by `paid_at`), rental-kind only:
# refundable deposits are holds, never revenue. Payments are not earmarked,
# so each booking's paid money is allocated rental-first: paid fills the
# rental total before a peso counts toward penalties. The scheduling fee
# rides with rental income (it is part of the rental total).
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RevenueSlice:
    rental: Decimal = ZERO
    penalties: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return money(self.rental + self.penalties, field="revenue_slice")


@dataclass(frozen=True)
class RevenueBucket:
    key: date
    label: str
    rental: Decimal
    penalties: Decimal
    methods: dict = None  # method -> RevenueSlice
    channels: dict = None  # channel -> RevenueSlice

    @property
    def total(self) -> Decimal:
        return money(self.rental + self.penalties, field="revenue_bucket")


@dataclass(frozen=True)
class RevenueReport:
    from_date: date
    to_date: date
    granularity: str
    rental: Decimal
    penalties: Decimal
    methods: dict  # method -> RevenueSlice
    channels: dict  # channel -> RevenueSlice
    buckets: tuple = ()

    @property
    def total(self) -> Decimal:
        return money(self.rental + self.penalties, field="revenue_report")


def _bucket_key(day: date, granularity: str) -> date:
    if granularity == "week":
        return day - timedelta(days=day.weekday())
    if granularity == "month":
        return day.replace(day=1)
    return day


def _bucket_label(key: date, granularity: str) -> str:
    if granularity == "week":
        return f"wk {key:%d %b}"
    if granularity == "month":
        return key.strftime("%b %Y")
    return key.strftime("%d %b")


def _bucket_keys(from_date: date, to_date: date, granularity: str) -> list[date]:
    keys: list[date] = []
    seen: set[date] = set()
    day = from_date
    while day <= to_date:
        key = _bucket_key(day, granularity)
        if key not in seen:
            seen.add(key)
            keys.append(key)
        day += timedelta(days=1)
    return keys


def revenue_report(
    session: Session,
    from_date: date,
    to_date: date,
    granularity: str = "day",
    channel: str | None = None,
) -> RevenueReport:
    """Money received in a period, split rental vs penalties.

    Allocation walks each booking's paid payments oldest-first against its
    rental total, then its penalties; each payment's portions land on its
    own paid date, method, and channel. Three aggregate queries, no N+1.
    """
    if granularity not in ("day", "week", "month"):
        raise ValueError(f"Unknown granularity: {granularity}.")
    if to_date < from_date:
        raise ValueError("The report cannot end before it starts.")
    start, _ = _day_bounds(from_date)
    _, end = _day_bounds(to_date)

    payments = list(
        session.execute(
            select(Payment)
            .options(joinedload(Payment.booking))
            .where(
                Payment.status == "paid",
                Payment.kind == "rental",
                Payment.paid_at >= start,
                Payment.paid_at <= end,
            )
            .order_by(Payment.paid_at, Payment.payment_id)
        ).scalars()
    )
    if channel is not None:
        payments = [
            p for p in payments
            if (p.booking.channel if p.booking else None) == channel
        ]

    booking_ids = list({p.booking_id for p in payments})
    earlier = {
        row[0]: money(row[1] or 0)
        for row in session.execute(
            select(Payment.booking_id, func.sum(Payment.amount)).where(
                Payment.status == "paid",
                Payment.kind == "rental",
                Payment.booking_id.in_(booking_ids),
                Payment.paid_at < start,
            ).group_by(Payment.booking_id)
        ).all()
    }
    penalty_due = {
        row[0]: money(row[1] or 0)
        for row in session.execute(
            select(Penalty.booking_id, func.sum(Penalty.amount)).where(
                Penalty.booking_id.in_(booking_ids)
            ).group_by(Penalty.booking_id)
        ).all()
    }
    rental_due = {}
    for payment in payments:
        booking = payment.booking
        if booking is not None and booking.booking_id not in rental_due:
            rental_due[booking.booking_id] = money(booking.total_cost, field="total_cost")

    buckets: dict[date, dict] = {
        key: {"rental": ZERO, "penalties": ZERO, "methods": {}, "channels": {}}
        for key in _bucket_keys(from_date, to_date, granularity)
    }
    methods: dict[str, RevenueSlice] = {}
    channels: dict[str, RevenueSlice] = {}
    filled: dict[int, list[Decimal]] = {}

    def _add(holder: dict, name: str, rental: Decimal, penalties: Decimal) -> None:
        current = holder.get(name, RevenueSlice())
        holder[name] = RevenueSlice(
            rental=money(current.rental + rental),
            penalties=money(current.penalties + penalties),
        )

    for payment in payments:
        booking = payment.booking
        if booking is None:
            continue
        due_rental = rental_due.get(booking.booking_id, ZERO)
        due_penalty = penalty_due.get(booking.booking_id, ZERO)
        if booking.booking_id not in filled:
            earlier_paid = earlier.get(booking.booking_id, ZERO)
            filled[booking.booking_id] = [
                min(earlier_paid, due_rental),
                min(max(ZERO, earlier_paid - due_rental), due_penalty),
            ]
        fill_rental, fill_penalty = filled[booking.booking_id]
        amount = money(payment.amount)
        rental_part = min(amount, max(ZERO, due_rental - fill_rental))
        penalty_part = min(amount - rental_part, max(ZERO, due_penalty - fill_penalty))
        filled[booking.booking_id] = [fill_rental + rental_part, fill_penalty + penalty_part]

        paid_day = payment.paid_at.date() if payment.paid_at else from_date
        key = _bucket_key(max(from_date, min(paid_day, to_date)), granularity)
        bucket = buckets[key]
        bucket["rental"] += rental_part
        bucket["penalties"] += penalty_part
        _add(bucket["methods"], payment.method, rental_part, penalty_part)
        _add(
            bucket["channels"],
            {"walk_in": "Walk-in", "online": "Online"}.get(booking.channel or "", "—"),
            rental_part, penalty_part,
        )
        _add(methods, payment.method.title(), rental_part, penalty_part)
        _add(
            channels,
            {"walk_in": "Walk-in", "online": "Online"}.get(booking.channel or "", "—"),
            rental_part, penalty_part,
        )

    ordered = tuple(
        RevenueBucket(
            key=key,
            label=_bucket_label(key, granularity),
            rental=money(cell["rental"]),
            penalties=money(cell["penalties"]),
            methods=cell["methods"],
            channels=cell["channels"],
        )
        for key, cell in buckets.items()
    )
    rental_total = money(sum((b.rental for b in ordered), ZERO))
    penalty_total = money(sum((b.penalties for b in ordered), ZERO))
    return RevenueReport(
        from_date=from_date, to_date=to_date, granularity=granularity,
        rental=rental_total, penalties=penalty_total,
        methods=methods, channels=channels, buckets=ordered,
    )


# --------------------------------------------------------------------------
# Bookings: what was booked in a period, and the rows behind the numbers.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BookingRow:
    ref: str
    customer: str
    vehicle: str
    dates: str
    status: str
    channel: str
    total: Decimal


@dataclass(frozen=True)
class BookingsReport:
    from_date: date
    to_date: date
    total: int = 0
    by_status: dict = None  # status -> count
    walk_in: int = 0
    online: int = 0
    cancelled: int = 0
    avg_days: float = 0.0
    avg_value: Decimal = ZERO
    rows: tuple = ()


def bookings_report(
    session: Session,
    from_date: date,
    to_date: date,
    channel: str | None = None,
) -> BookingsReport:
    """Bookings created in a period: counts, averages, and the rows."""
    if to_date < from_date:
        raise ValueError("The report cannot end before it starts.")
    start, _ = _day_bounds(from_date)
    _, end = _day_bounds(to_date)
    stmt = (
        select(Booking)
        .options(
            joinedload(Booking.user),
            joinedload(Booking.vehicle),
        )
        .where(Booking.created_at >= start, Booking.created_at <= end)
        .order_by(Booking.created_at.desc())
    )
    if channel is not None:
        stmt = stmt.where(Booking.channel == channel)
    bookings = list(session.execute(stmt).scalars())

    by_status: dict[str, int] = {}
    walk_in = online = cancelled = 0
    spans: list[int] = []
    values: list[Decimal] = []
    rows = []
    for booking in bookings:
        by_status[booking.status] = by_status.get(booking.status, 0) + 1
        if booking.channel == "walk_in":
            walk_in += 1
        elif booking.channel == "online":
            online += 1
        if booking.status == "cancelled":
            cancelled += 1
        spans.append(max(1, (booking.end_date - booking.start_date).days))
        values.append(money(booking.total_cost, field="total_cost"))
        vehicle = booking.vehicle
        rows.append(
            BookingRow(
                ref=f"#{booking.booking_id}",
                customer=booking.user.full_name if booking.user else "-",
                vehicle=(
                    f"{vehicle.plate_number} {vehicle.make} {vehicle.model}"
                    if vehicle
                    else "-"
                ),
                dates=f"{booking.start_date:%d %b} → {booking.end_date:%d %b %Y}",
                status=booking.status,
                channel={"walk_in": "Walk-in", "online": "Online"}.get(
                    booking.channel or "", "—"
                ),
                total=money(booking.total_cost, field="total_cost"),
            )
        )
    return BookingsReport(
        from_date=from_date,
        to_date=to_date,
        total=len(bookings),
        by_status=by_status,
        walk_in=walk_in,
        online=online,
        cancelled=cancelled,
        avg_days=round(sum(spans) / len(spans), 1) if spans else 0.0,
        avg_value=money(sum(values, ZERO) / len(values)) if values else ZERO,
        rows=tuple(rows),
    )


# --------------------------------------------------------------------------
# Utilization: rented days ÷ available days per vehicle, ranked.
# Uses the Fleet tab's own definition (maintenance and retired days
# excluded) via fleet_service.vehicle_stats, so the two cannot disagree.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class UtilizationRow:
    vehicle_id: int
    plate: str
    label: str
    category: str
    rented_days: int
    available_days: int
    utilization: float | None  # None when there was nothing available
    revenue: Decimal
    shop_cost: Decimal


def revenue_by_vehicle(
    session: Session, from_date: date, to_date: date
) -> dict[int, Decimal]:
    """Rental money received per vehicle (paid, rental-kind, by paid date)."""
    start, _ = _day_bounds(from_date)
    _, end = _day_bounds(to_date)
    totals: dict[int, Decimal] = {}
    for vehicle_id, total in session.execute(
        select(Booking.vehicle_id, func.sum(Payment.amount))
        .join(Payment, Payment.booking_id == Booking.booking_id)
        .where(
            Payment.status == "paid",
            Payment.kind == "rental",
            Payment.paid_at >= start,
            Payment.paid_at <= end,
        )
        .group_by(Booking.vehicle_id)
    ).all():
        totals[vehicle_id] = money(total or 0, field="revenue_by_vehicle")
    return totals


def vehicle_categories(session: Session) -> list[str]:
    return [
        row[0]
        for row in session.execute(
            select(Vehicle_Category.category_name).order_by(
                Vehicle_Category.category_name
            )
        ).all()
    ]


def utilization_report(
    session: Session,
    from_date: date,
    to_date: date,
    category: str | None = None,
) -> tuple[UtilizationRow, ...]:
    """Per vehicle, most used first; never-available vehicles sink."""
    if to_date < from_date:
        raise ValueError("The report cannot end before it starts.")
    from app.services import fleet_service

    vehicles = fleet_service.list_vehicles(
        session, category_name=category, with_maintenance=False
    )
    if not vehicles:
        return ()
    stats_by_vehicle = fleet_service.vehicle_stats_batch(
        session, vehicles, from_date=from_date, to_date=to_date
    )
    takings = revenue_by_vehicle(session, from_date, to_date)
    vehicle_ids = [vehicle.vehicle_id for vehicle in vehicles]
    shop_costs = dict(
        session.execute(
            select(
                Maintenance_Record.vehicle_id,
                func.coalesce(func.sum(Maintenance_Record.cost), 0),
            )
            .where(
                Maintenance_Record.vehicle_id.in_(vehicle_ids),
                Maintenance_Record.status == "completed",
                Maintenance_Record.start_date >= from_date,
                Maintenance_Record.start_date <= to_date,
            )
            .group_by(Maintenance_Record.vehicle_id)
        ).all()
    )
    rows = []
    for vehicle in vehicles:
        stats = stats_by_vehicle[vehicle.vehicle_id]
        rows.append(
            UtilizationRow(
                vehicle_id=vehicle.vehicle_id,
                plate=vehicle.plate_number,
                label=f"{vehicle.plate_number} {vehicle.make} {vehicle.model}",
                category=(
                    vehicle.category.category_name
                    if vehicle.category is not None
                    else "—"
                ),
                rented_days=stats["rented_days"],
                available_days=stats["available_days"],
                utilization=stats["utilization"],
                revenue=takings.get(vehicle.vehicle_id, ZERO),
                shop_cost=money(
                    shop_costs.get(vehicle.vehicle_id, 0),
                    field="maintenance_cost",
                ),
            )
        )
    rows.sort(
        key=lambda r: (r.utilization is None, -(r.utilization or 0.0), r.plate)
    )
    return tuple(rows)


# --------------------------------------------------------------------------
# Category and model performance: what earns the most.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryRow:
    category: str
    model: str
    revenue: Decimal
    rentals: int


def category_report(
    session: Session, from_date: date, to_date: date
) -> tuple[CategoryRow, ...]:
    """Revenue and completed rentals per category/model, richest first."""
    if to_date < from_date:
        raise ValueError("The report cannot end before it starts.")
    start, _ = _day_bounds(from_date)
    _, end = _day_bounds(to_date)
    takings = revenue_by_vehicle(session, from_date, to_date)
    vehicles = {
        car.vehicle_id: car
        for car in session.execute(
            select(Vehicle).options(joinedload(Vehicle.category))
        ).scalars()
    }
    rentals: dict[tuple[str, str], int] = {}
    for (vehicle_id,) in session.execute(
        select(Booking.vehicle_id).where(
            Booking.status == "completed",
            Booking.end_date >= from_date,
            Booking.end_date <= to_date,
        )
    ).all():
        car = vehicles.get(vehicle_id)
        key = (
            (car.category.category_name if car and car.category else "—"),
            f"{car.make} {car.model}" if car else "—",
        )
        rentals[key] = rentals.get(key, 0) + 1
    takings_by_model: dict[tuple[str, str], Decimal] = {}
    for vehicle_id, total in takings.items():
        car = vehicles.get(vehicle_id)
        key = (
            (car.category.category_name if car and car.category else "—"),
            f"{car.make} {car.model}" if car else "—",
        )
        takings_by_model[key] = takings_by_model.get(key, ZERO) + total
    rows = [
        CategoryRow(
            category=key[0], model=key[1], revenue=total,
            rentals=rentals.get(key, 0),
        )
        for key, total in takings_by_model.items()
    ]
    rows.sort(key=lambda r: (-r.revenue, r.category, r.model))
    return tuple(rows)


# --------------------------------------------------------------------------
# Maintenance cost: spend by vehicle and type, against revenue.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MaintenanceCostRow:
    plate: str
    label: str
    spend: Decimal
    revenue: Decimal
    ratio: float | None  # spend ÷ revenue; None when revenue is zero


@dataclass(frozen=True)
class MaintenanceCostReport:
    spend: Decimal
    by_type: dict  # maintenance_type -> Decimal
    rows: tuple = ()


def maintenance_cost_report(
    session: Session, from_date: date, to_date: date
) -> MaintenanceCostReport:
    """Completed-job spend in a period, per vehicle against its revenue."""
    if to_date < from_date:
        raise ValueError("The report cannot end before it starts.")
    from app.services import maintenance_service

    spend, by_type, _by_vehicle = maintenance_service.spend_summary(
        session, from_date=from_date, to_date=to_date
    )
    takings = revenue_by_vehicle(session, from_date, to_date)
    per_vehicle: dict[int, Decimal] = {}
    for vehicle_id, total in session.execute(
        select(
            Maintenance_Record.vehicle_id,
            func.sum(Maintenance_Record.cost),
        ).where(
            Maintenance_Record.status == "completed",
            Maintenance_Record.cost.is_not(None),
            Maintenance_Record.start_date >= from_date,
            Maintenance_Record.start_date <= to_date,
        ).group_by(Maintenance_Record.vehicle_id)
    ).all():
        per_vehicle[vehicle_id] = money(total or 0, field="maintenance_cost")
    vehicles = {
        car.vehicle_id: car
        for car in session.execute(select(Vehicle)).scalars()
    }
    rows = []
    for vehicle_id, cost in per_vehicle.items():
        car = vehicles.get(vehicle_id)
        revenue = takings.get(vehicle_id, ZERO)
        rows.append(
            MaintenanceCostRow(
                plate=car.plate_number if car else "—",
                label=(
                    f"{car.plate_number} {car.make} {car.model}"
                    if car
                    else "—"
                ),
                spend=cost,
                revenue=revenue,
                ratio=(float(cost / revenue) if revenue > 0 else None),
            )
        )
    rows.sort(key=lambda r: (-r.spend, r.plate))
    return MaintenanceCostReport(spend=spend, by_type=by_type, rows=tuple(rows))


# --------------------------------------------------------------------------
# Receivables: who still owes, how old the debt is, penalty position.
# A snapshot of now (not period-bound): the range only bounds which
# bookings qualify (due by the period end). Shares booking_balance math
# through the same paid-covers-rental-first allocation as revenue.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ReceivableRow:
    ref: str
    customer: str
    vehicle: str
    status: str
    due: date | None
    age_days: int
    owed: Decimal
    penalties_charged: Decimal
    penalties_collected: Decimal


def receivables_report(
    session: Session,
    to_date: date,
    *,
    overdue_only: bool = False,
    today: date | None = None,
) -> tuple[ReceivableRow, ...]:
    """Open balances on bookings due by `to_date`, oldest debt first."""
    from app.services import payment_service

    day = today or date.today()
    bookings = list(
        session.execute(
            select(Booking)
            .options(
                joinedload(Booking.user),
                joinedload(Booking.vehicle),
                selectinload(Booking.payments),
                selectinload(Booking.penalties),
            )
            .where(
                Booking.status.notin_(("cancelled",)),
                Booking.end_date <= to_date,
            )
            .order_by(Booking.end_date)
        ).scalars()
    )
    rows = []
    for booking in bookings:
        balance = payment_service.booking_balance(session, booking)
        if balance.balance <= ZERO:
            continue
        age = (day - booking.end_date).days
        if overdue_only and age <= 0:
            continue
        paid = balance.amount_paid
        rental = balance.rental_cost
        collected_rental = min(paid, rental)
        collected_penalty = min(
            max(ZERO, paid - rental), balance.penalty_total
        )
        vehicle = booking.vehicle
        rows.append(
            ReceivableRow(
                ref=f"#{booking.booking_id}",
                customer=booking.user.full_name if booking.user else "-",
                vehicle=(
                    f"{vehicle.plate_number} {vehicle.make} {vehicle.model}"
                    if vehicle
                    else "-"
                ),
                status=booking.status,
                due=booking.end_date,
                age_days=age,
                owed=balance.balance,
                penalties_charged=balance.penalty_total,
                penalties_collected=collected_penalty,
            )
        )
    rows.sort(key=lambda r: (-r.age_days, -r.owed))
    return tuple(rows)


# --------------------------------------------------------------------------
# Customers: new and repeat faces, top spenders, licence issues.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CustomerRow:
    customer: str
    bookings: int
    spent: Decimal
    licence: str


@dataclass(frozen=True)
class CustomersReport:
    new_count: int = 0
    new_names: tuple = ()
    repeat_count: int = 0
    licence_issues: tuple = ()  # customer names
    top_spender: str = "-"
    top_spent: Decimal = ZERO
    rows: tuple = ()


def customers_report(
    session: Session, from_date: date, to_date: date
) -> CustomersReport:
    """Cohorts and spend for a period; licence horizon is 30 days past it."""
    if to_date < from_date:
        raise ValueError("The report cannot end before it starts.")
    start, _ = _day_bounds(from_date)
    _, end = _day_bounds(to_date)

    new = [
        user.full_name
        for user in session.execute(
            select(Users).where(
                Users.role == "customer",
                Users.created_at >= start,
                Users.created_at <= end,
            ).order_by(Users.created_at)
        ).scalars()
    ]
    counts: dict[int, int] = {}
    for user_id, _booking_id in session.execute(
        select(Booking.user_id, func.count(Booking.booking_id)).where(
            Booking.created_at >= start,
            Booking.created_at <= end,
            Booking.status.notin_(("cancelled",)),
        ).group_by(Booking.user_id)
    ).all():
        counts[user_id] = int(_booking_id)
    spent: dict[int, Decimal] = {}
    for user_id, total in session.execute(
        select(Booking.user_id, func.sum(Payment.amount))
        .join(Payment, Payment.booking_id == Booking.booking_id)
        .where(
            Payment.status == "paid",
            Payment.kind == "rental",
            Payment.paid_at >= start,
            Payment.paid_at <= end,
        )
        .group_by(Booking.user_id)
    ).all():
        spent[user_id] = money(total or 0, field="customer_spend")

    horizon = to_date + timedelta(days=30)
    users = {
        user.user_id: user
        for user in session.execute(
            select(Users).where(Users.user_id.in_(list(counts) or [0]))
        ).scalars()
    }
    licence_issues = sorted(
        user.full_name
        for user in users.values()
        if user.license_expiry is not None and user.license_expiry <= horizon
    )
    rows = []
    for user_id, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        user = users.get(user_id)
        expiry = user.license_expiry if user else None
        if expiry is None:
            licence = "—"
        elif expiry <= to_date:
            licence = f"EXPIRED {expiry:%d %b %Y}"
        elif expiry <= horizon:
            licence = f"expires {expiry:%d %b %Y}"
        else:
            licence = "OK"
        rows.append(
            CustomerRow(
                customer=user.full_name if user else "-",
                bookings=count,
                spent=spent.get(user_id, ZERO),
                licence=licence,
            )
        )
    repeat = sum(1 for user_id in counts if _lifetime_bookings(session, user_id) > 1)
    top = max(rows, key=lambda r: r.spent, default=None)
    return CustomersReport(
        new_count=len(new),
        new_names=tuple(new[:10]),
        repeat_count=repeat,
        licence_issues=tuple(licence_issues),
        top_spender=top.customer if top else "-",
        top_spent=top.spent if top else ZERO,
        rows=tuple(rows),
    )


def _lifetime_bookings(session: Session, user_id: int) -> int:
    return session.execute(
        select(func.count(Booking.booking_id)).where(
            Booking.user_id == user_id,
            Booking.status.notin_(("cancelled",)),
        )
    ).scalar_one()


# --------------------------------------------------------------------------
# Staff: money taken per cashier per day -- the end-of-day close.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StaffRow:
    day: date
    staff: str
    transactions: int
    rental: Decimal
    deposits: Decimal

    @property
    def total(self) -> Decimal:
        return money(self.rental + self.deposits, field="staff_total")


@dataclass(frozen=True)
class StaffReport:
    transactions: int = 0
    rental: Decimal = ZERO
    deposits: Decimal = ZERO
    rows: tuple = ()

    @property
    def total(self) -> Decimal:
        return money(self.rental + self.deposits, field="staff_report")


def staff_report(
    session: Session, from_date: date, to_date: date
) -> StaffReport:
    """Paid money grouped by day and cashier, newest first."""
    if to_date < from_date:
        raise ValueError("The report cannot end before it starts.")
    start, _ = _day_bounds(from_date)
    _, end = _day_bounds(to_date)
    figures = session.execute(
        select(
            func.date(Payment.paid_at),
            Payment.recorded_by,
            func.count(Payment.payment_id),
            func.sum(case((Payment.kind == "rental", Payment.amount), else_=0)),
            func.sum(case((Payment.kind == "deposit", Payment.amount), else_=0)),
        )
        .where(
            Payment.status == "paid",
            Payment.paid_at >= start,
            Payment.paid_at <= end,
        )
        .group_by(func.date(Payment.paid_at), Payment.recorded_by)
        .order_by(func.date(Payment.paid_at).desc())
    ).all()
    recorders = {
        user.user_id: user.full_name
        for user in session.execute(
            select(Users).where(
                Users.user_id.in_(
                    [row[1] for row in figures if row[1] is not None] or [0]
                )
            )
        ).scalars()
    }
    rows = []
    for raw_day, recorder_id, count, rental, deposits in figures:
        day = (
            raw_day
            if isinstance(raw_day, date)
            else date.fromisoformat(str(raw_day))
        )
        rows.append(
            StaffRow(
                day=day,
                staff=recorders.get(recorder_id, "—"),
                transactions=int(count),
                rental=money(rental or 0, field="staff_rental"),
                deposits=money(deposits or 0, field="staff_deposits"),
            )
        )
    return StaffReport(
        transactions=sum(r.transactions for r in rows),
        rental=money(sum((r.rental for r in rows), ZERO)),
        deposits=money(sum((r.deposits for r in rows), ZERO)),
        rows=tuple(rows),
    )
