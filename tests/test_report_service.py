"""Tests for the shared reports module.

Fixed dates around conftest.TODAY (2026-09-29), so ranges are exact and
the tests never depend on the real clock. Each test builds its own world:
the engine fixture is a fresh in-memory database per test.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.models import Booking, Maintenance_Record, Payment, Penalty
from app.services import report_service
from tests.conftest import TODAY

D10 = date(2026, 9, 10)
D12 = date(2026, 9, 12)
D15 = date(2026, 9, 15)
D16 = date(2026, 9, 16)
D17 = date(2026, 9, 17)
D20 = date(2026, 9, 20)


def _noon(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time()).replace(hour=12)


def _booking(session, customer, vehicle, total="7500.00", **overrides):
    fields = {
        "start_date": D10,
        "end_date": D12,
        "actual_return_date": None,
        "total_cost": total,
        "status": "completed",
        "channel": "walk_in",
        "created_at": _noon(D10),
    }
    fields.update(overrides)
    row = Booking(user_id=customer.user_id, vehicle_id=vehicle.vehicle_id, **fields)
    session.add(row)
    session.flush()
    return row


def _pay(session, booking, amount, day, method="cash", kind="rental", staff=None):
    row = Payment(
        booking_id=booking.booking_id,
        amount=Decimal(amount),
        method=method,
        kind=kind,
        status="paid",
        paid_at=_noon(day),
        recorded_by=staff.user_id if staff else None,
        created_at=_noon(day),
    )
    session.add(row)
    session.flush()
    return row


class TestRevenueReport:
    def test_paid_money_splits_rental_first(self, session, customer, vehicle):
        booking = _booking(session, customer, vehicle)
        session.add(
            Penalty(
                booking=booking, penalty_type="late_return", amount=Decimal("1000.00"),
                description="Late.", created_at=_noon(D15),
            )
        )
        session.flush()
        _pay(session, booking, "5000.00", D15)
        _pay(session, booking, "2500.00", D16)
        _pay(session, booking, "1000.00", D17)
        report = report_service.revenue_report(session, D15, D17)
        assert report.rental == Decimal("7500.00")
        assert report.penalties == Decimal("1000.00")
        assert report.total == Decimal("8500.00")

    def test_deposits_are_never_revenue(self, session, customer, vehicle):
        booking = _booking(session, customer, vehicle)
        _pay(session, booking, "7500.00", D15)
        _pay(session, booking, "2000.00", D15, kind="deposit")
        report = report_service.revenue_report(session, D15, D15)
        assert report.total == Decimal("7500.00")

    def test_earlier_payments_fill_rental_first(self, session, customer, vehicle):
        booking = _booking(session, customer, vehicle, total="5000.00")
        session.add(
            Penalty(
                booking=booking, penalty_type="late_return", amount=Decimal("1000.00"),
                description="Late.", created_at=_noon(D15),
            )
        )
        session.flush()
        _pay(session, booking, "2000.00", D10)
        _pay(session, booking, "4000.00", D16)
        report = report_service.revenue_report(session, D15, D17)
        assert report.rental == Decimal("3000.00")
        assert report.penalties == Decimal("1000.00")

    def test_empty_range_still_lists_every_bucket(self, session):
        report = report_service.revenue_report(session, D15, D17)
        assert report.total == 0
        assert [b.label for b in report.buckets] == ["15 Sep", "16 Sep", "17 Sep"]

    def test_week_and_month_buckets_cover_the_range(self, session, customer, vehicle):
        booking = _booking(session, customer, vehicle)
        _pay(session, booking, "7500.00", D15)
        week = report_service.revenue_report(session, D15, D20, granularity="week")
        assert week.total == Decimal("7500.00")
        assert week.buckets[0].label.startswith("wk ")
        month = report_service.revenue_report(session, D15, D20, granularity="month")
        assert month.total == Decimal("7500.00")
        assert month.buckets[0].label == "Sep 2026"

    def test_method_and_channel_splits(self, session, customer, vehicle, staff):
        booking = _booking(session, customer, vehicle, channel="online")
        _pay(session, booking, "5000.00", D15, method="cash", staff=staff)
        _pay(session, booking, "2500.00", D16, method="gcash", staff=staff)
        report = report_service.revenue_report(session, D15, D16)
        assert report.methods["Cash"].total == Decimal("5000.00")
        assert report.methods["Gcash"].total == Decimal("2500.00")
        assert report.channels["Online"].total == Decimal("7500.00")
        online = report_service.revenue_report(
            session, D15, D16, channel="online"
        )
        assert online.total == Decimal("7500.00")
        walk_in = report_service.revenue_report(
            session, D15, D16, channel="walk_in"
        )
        assert walk_in.total == 0


class TestBookingsReport:
    def test_counts_averages_and_rows(self, session, customer, vehicle):
        _booking(session, customer, vehicle, status="completed",
                 created_at=_noon(D15))
        _booking(session, customer, vehicle, status="cancelled",
                 created_at=_noon(D16), channel="online")
        report = report_service.bookings_report(session, D15, D16)
        assert report.total == 2
        assert report.cancelled == 1
        assert report.walk_in == 1
        assert report.online == 1
        assert report.avg_days == 2.0
        assert report.avg_value == Decimal("7500.00")
        assert len(report.rows) == 2
        assert report.rows[0].ref.startswith("#")

    def test_does_not_silently_stop_at_500(self, session, customer, vehicle):
        for _ in range(501):
            _booking(session, customer, vehicle, created_at=_noon(D15))
        report = report_service.bookings_report(session, D15, D15)
        assert report.total == 501
        assert len(report.rows) == 501


class TestUtilizationReport:
    def test_utilization_report_query_count_is_bounded(
        self, session, customer, vehicle, car_category, vehicle_factory
    ):
        from app.staff.query_metrics import count_queries

        for index in range(20):
            vehicle_factory(
                car_category,
                plate_number=f"LOAD-{index:03d}",
            )
        with count_queries(session.get_bind()) as queries:
            rows = report_service.utilization_report(session, D10, D20)
        assert len(rows) >= 20
        assert queries.count <= 10, f"{queries.count} queries for utilization"

    def test_most_used_ranks_first(self, session, customer, vehicle,
                                   car_category, vehicle_factory):
        second = vehicle_factory(car_category, make="Honda", model="Civic",
                                 plate_number="BBB-001")
        busy = _booking(session, customer, vehicle, status="completed")
        busy.start_date, busy.end_date = D10, D20
        session.flush()
        rows = report_service.utilization_report(session, D10, D20)
        assert rows[0].plate == vehicle.plate_number
        assert rows[0].rented_days == 10
        assert rows[0].available_days == 11
        assert rows[0].utilization == pytest.approx(10 / 11)
        assert rows[-1].plate == "BBB-001"

    def test_two_day_booking_uses_rental_day_count(
        self, session, customer, vehicle,
    ):
        _booking(session, customer, vehicle, status="completed")
        rows = report_service.utilization_report(session, D10, D20)
        assert rows[0].rented_days == 2
        assert rows[0].available_days == 11

    def test_shop_days_leave_the_denominator(self, session, customer, vehicle):
        from app.services import maintenance_service

        maintenance_service.start_maintenance(
            session, vehicle, description="Fix",
            start_date=D10, end_date=D12, status="in_progress",
        )
        rows = report_service.utilization_report(session, D10, D20)
        assert rows[0].available_days == 8  # 11 days minus 3 in the shop


class TestCategoryReport:
    def test_richest_model_first(self, session, customer, vehicle,
                                 car_category, vehicle_factory):
        other = vehicle_factory(car_category, make="Honda", model="Civic",
                                plate_number="BBB-001")
        mine = _booking(session, customer, vehicle, total="7500.00")
        mine.status = "completed"
        mine.end_date = D15
        theirs = _booking(session, customer, other, total="3000.00")
        theirs.status = "completed"
        theirs.end_date = D15
        session.flush()
        _pay(session, mine, "7500.00", D16)
        _pay(session, theirs, "3000.00", D16)
        rows = report_service.category_report(session, D15, D17)
        assert rows[0].model == "Toyota Vios"
        assert rows[0].revenue == Decimal("7500.00")
        assert rows[0].rentals == 1


class TestMaintenanceCostReport:
    def test_spend_against_revenue(self, session, customer, vehicle):
        from app.services import maintenance_service

        booking = _booking(session, customer, vehicle)
        _pay(session, booking, "7500.00", D16)
        record = maintenance_service.start_maintenance(
            session, vehicle, description="Brakes", maintenance_type="tire_brake",
            start_date=D10, status="in_progress",
        )
        maintenance_service.complete_maintenance(session, record, cost=Decimal("4500.00"))
        report = report_service.maintenance_cost_report(session, D10, D20)
        assert report.spend == Decimal("4500.00")
        assert report.by_type["tire_brake"] == Decimal("4500.00")
        (row,) = report.rows
        assert row.revenue == Decimal("7500.00")
        assert row.ratio == 0.6


class TestReceivablesReport:
    def test_open_balances_oldest_first(self, session, customer, vehicle):
        first = _booking(session, customer, vehicle, status="ongoing")
        first.start_date, first.end_date = D10, D12
        second = _booking(session, customer, vehicle, status="confirmed")
        second.start_date, second.end_date = D15, D17
        session.add(
            Penalty(booking=first, penalty_type="late_return",
                    amount=Decimal("1000.00"), description="Late.",
                    created_at=_noon(D15))
        )
        session.flush()
        _pay(session, first, "8000.00", D16)
        rows = report_service.receivables_report(session, D20, today=D20)
        assert [r.ref for r in rows] == [f"#{first.booking_id}", f"#{second.booking_id}"]
        assert rows[0].owed == Decimal("500.00")
        assert rows[0].penalties_charged == Decimal("1000.00")
        assert rows[0].penalties_collected == Decimal("500.00")
        assert rows[0].age_days == 8
        assert rows[1].penalties_collected == 0

    def test_settled_rows_beyond_the_cap_do_not_hide_open_balances(
        self, session, customer, vehicle,
    ):
        for _ in range(500):
            settled = _booking(
                session, customer, vehicle, status="completed",
                start_date=D10, end_date=D10,
            )
            _pay(session, settled, "7500.00", D10)
        open_booking = _booking(
            session, customer, vehicle, status="ongoing",
            start_date=D15, end_date=D20,
        )
        rows = report_service.receivables_report(session, D20, today=D20)
        assert [r.ref for r in rows] == [f"#{open_booking.booking_id}"]


class TestCustomersReport:
    def test_cohorts_top_and_licences(self, session, customer_factory,
                                      customer, vehicle):
        customer.created_at = _noon(D15)
        session.flush()
        booking = _booking(session, customer, vehicle, created_at=_noon(D15))
        _pay(session, booking, "7500.00", D16)
        report = report_service.customers_report(session, D15, D17)
        assert report.new_count == 1
        assert report.repeat_count == 0
        assert report.top_spender == customer.full_name
        assert report.top_spent == Decimal("7500.00")
        (row,) = report.rows
        assert row.bookings == 1
        assert row.licence == "OK"


class TestStaffReport:
    def test_takings_per_cashier_per_day(self, session, customer, vehicle, staff):
        booking = _booking(session, customer, vehicle)
        _pay(session, booking, "5000.00", D15, staff=staff)
        _pay(session, booking, "2000.00", D15, kind="deposit", staff=staff)
        _pay(session, booking, "2500.00", D16, staff=staff)
        report = report_service.staff_report(session, D15, D16)
        assert report.transactions == 3
        assert report.rental == Decimal("7500.00")
        assert report.deposits == Decimal("2000.00")
        assert report.total == Decimal("9500.00")
        assert [(r.day, r.staff) for r in report.rows] == [
            (D16, "Staff User"), (D15, "Staff User"),
        ]
