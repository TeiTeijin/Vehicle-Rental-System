"""Tests for the workshop lifecycle.

One start/complete/cancel path, shared by Fleet and Maintenance: the
vehicle status follows the log, clashes warn instead of blocking, and
cancel keeps the row on the log.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.models import Maintenance_Record, Vehicle
from app.services import booking_service, maintenance_service
from app.services.errors import ConflictError, StateError, ValidationError
from tests.conftest import TODAY


def _car(session, customer_factory, car_category, vehicle_factory, **overrides):
    return vehicle_factory(car_category, **overrides)


class TestStart:
    def test_in_progress_puts_the_car_in_the_shop(
        self, session, customer, vehicle
    ):
        record = maintenance_service.start_maintenance(
            session, vehicle, description="Brake pads", status="in_progress"
        )
        assert record.status == "in_progress"
        assert record.end_date is None
        assert record.cost is None
        assert vehicle.status == "maintenance"

    def test_a_future_schedule_leaves_the_car_rentable(
        self, session, customer, vehicle
    ):
        from datetime import date as _date

        start = _date.today() + timedelta(days=30)
        maintenance_service.start_maintenance(
            session, vehicle, description="Service",
            start_date=start, end_date=start + timedelta(days=2),
        )
        assert vehicle.status == "available"
        booking_service.create_booking(
            session, customer, vehicle, TODAY, TODAY + timedelta(days=2)
        )

    def test_an_arrived_schedule_holds_the_car(
        self, session, customer, vehicle
    ):
        maintenance_service.start_maintenance(
            session, vehicle, description="Service",
            start_date=TODAY - timedelta(days=1),
            end_date=TODAY + timedelta(days=2),
        )
        assert vehicle.status == "maintenance"

    def test_overlapping_bookings_warn_through_the_clash_list(
        self, session, customer, vehicle
    ):
        booking = booking_service.create_booking(
            session, customer, vehicle, TODAY, TODAY + timedelta(days=3)
        )
        record = maintenance_service.start_maintenance(
            session, vehicle, description="Urgent fix",
            start_date=TODAY, end_date=TODAY + timedelta(days=2),
        )
        assert record.maintenance_id is not None
        clashes = maintenance_service.booking_conflicts(
            session, vehicle, TODAY, TODAY + timedelta(days=2)
        )
        assert [b.booking_id for b in clashes] == [booking.booking_id]

    def test_a_rented_car_stays_rented(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(
            session, customer, vehicle, TODAY, TODAY + timedelta(days=3)
        )
        booking_service.check_in(session, booking, staff, 1000, "full")
        maintenance_service.start_maintenance(
            session, vehicle, description="Fix", status="in_progress"
        )
        assert vehicle.status == "rented"

    def test_unknown_type_is_refused(self, session, vehicle):
        with pytest.raises(ValidationError, match="maintenance type"):
            maintenance_service.start_maintenance(
                session, vehicle, description="X", maintenance_type="warp_drive"
            )

    def test_blank_description_is_refused(self, session, vehicle):
        with pytest.raises(ValidationError, match="Describe"):
            maintenance_service.start_maintenance(session, vehicle, description="  ")


class TestComplete:
    def test_completion_releases_the_car(
        self, session, customer, vehicle
    ):
        record = maintenance_service.start_maintenance(
            session, vehicle, description="Brake pads",
            estimate_cost=Decimal("4500.00"), status="in_progress",
        )
        maintenance_service.complete_maintenance(
            session, record, cost=Decimal("4700.00"), odometer=15000,
            next_due_date=TODAY + timedelta(days=90),
        )
        assert record.status == "completed"
        assert record.cost == Decimal("4700.00")
        assert vehicle.status == "available"
        assert vehicle.mileage == 15000

    def test_completion_needs_a_final_cost(self, session, vehicle):
        record = maintenance_service.start_maintenance(
            session, vehicle, description="Brake pads", status="in_progress"
        )
        with pytest.raises(ValidationError, match="final cost"):
            maintenance_service.complete_maintenance(session, record)

    def test_a_second_open_job_keeps_the_car(self, session, vehicle):
        first = maintenance_service.start_maintenance(
            session, vehicle, description="One", status="in_progress"
        )
        maintenance_service.start_maintenance(
            session, vehicle, description="Two", status="in_progress"
        )
        maintenance_service.complete_maintenance(session, first, cost=100)
        assert vehicle.status == "maintenance"

    def test_completed_rows_cannot_be_completed_again(self, session, vehicle):
        record = maintenance_service.start_maintenance(
            session, vehicle, description="One", status="in_progress"
        )
        maintenance_service.complete_maintenance(session, record, cost=100)
        with pytest.raises(StateError, match="completed"):
            maintenance_service.complete_maintenance(session, record, cost=100)


class TestCancel:
    def test_cancel_keeps_the_row_and_releases_the_car(
        self, session, vehicle
    ):
        record = maintenance_service.start_maintenance(
            session, vehicle, description="One", status="in_progress"
        )
        maintenance_service.cancel_maintenance(session, record, reason="No parts")
        assert record.status == "cancelled"
        assert record.cancel_reason == "No parts"
        assert vehicle.status == "available"

    def test_cancel_needs_a_live_record(self, session, vehicle):
        record = maintenance_service.start_maintenance(
            session, vehicle, description="One", status="in_progress"
        )
        maintenance_service.complete_maintenance(session, record, cost=100)
        with pytest.raises(StateError, match="completed"):
            maintenance_service.cancel_maintenance(session, record)


class TestOdometer:
    def test_start_rejects_a_reading_below_the_current_mileage(
        self, session, vehicle
    ):
        from datetime import date

        vehicle.mileage = 10_000
        session.flush()
        with pytest.raises(ValidationError, match="below the 10,000 km"):
            maintenance_service.start_maintenance(
                session,
                vehicle,
                description="Service",
                start_date=date.today(),
                odometer=9_000,
            )

    def test_start_allows_a_historical_reading(self, session, vehicle):
        from datetime import date

        vehicle.mileage = 10_000
        session.flush()
        record = maintenance_service.start_maintenance(
            session,
            vehicle,
            description="Backfilled service",
            start_date=date.today() - timedelta(days=30),
            end_date=date.today() - timedelta(days=29),
            odometer=5_000,
        )
        assert record.odometer == 5_000

    def test_completion_rejects_a_reading_below_the_start_reading(
        self, session, vehicle
    ):
        from datetime import date

        record = maintenance_service.start_maintenance(
            session,
            vehicle,
            description="Service",
            start_date=date.today(),
            status="in_progress",
            odometer=10_200,
        )
        with pytest.raises(ValidationError, match="when the job started"):
            maintenance_service.complete_maintenance(
                session, record, cost=100, odometer=10_100
            )

    def test_completion_rejects_a_reading_below_the_current_mileage(
        self, session, vehicle
    ):
        from datetime import date

        record = maintenance_service.start_maintenance(
            session,
            vehicle,
            description="Service",
            start_date=date.today(),
            status="in_progress",
            odometer=10_200,
        )
        vehicle.mileage = 10_500
        session.flush()
        with pytest.raises(ValidationError, match="below the 10,500 km"):
            maintenance_service.complete_maintenance(
                session, record, cost=100, odometer=10_400
            )


class TestOpenEndedWorkshop:
    def test_booking_reports_the_open_ended_window(self, session, customer, vehicle):
        from datetime import date

        start = date.today()
        maintenance_service.start_maintenance(
            session,
            vehicle,
            description="Open job",
            start_date=start,
            status="in_progress",
        )
        with pytest.raises(ConflictError, match="from"):
            booking_service.create_booking(
                session,
                customer,
                vehicle,
                start + timedelta(days=1),
                start + timedelta(days=3),
            )

    def test_extension_reports_the_open_ended_window(
        self, session, customer, vehicle
    ):
        from datetime import date

        start = date.today()
        booking = booking_service.create_booking(
            session,
            customer,
            vehicle,
            start + timedelta(days=1),
            start + timedelta(days=3),
        )
        maintenance_service.start_maintenance(
            session,
            vehicle,
            description="Open job",
            start_date=start,
            status="in_progress",
        )
        with pytest.raises(ConflictError, match="from"):
            booking_service.extend_booking(
                session, booking, start + timedelta(days=5)
            )


class TestDueSoonAndSpend:
    def test_due_soon_flags_dates_and_mileage(
        self, session, customer, vehicle
    ):
        record = maintenance_service.start_maintenance(
            session, vehicle, description="Service", status="in_progress"
        )
        vehicle.mileage = 10000
        maintenance_service.complete_maintenance(
            session, record, cost=100,
            next_due_date=TODAY + timedelta(days=3),
            next_due_odometer=20000,
        )
        due = maintenance_service.due_soon(session, on_date=TODAY)
        assert len(due) == 1
        _car, _record, note = due[0]
        assert "3 day" in note
        record.next_due_date = None
        session.flush()
        vehicle.mileage = 19900
        due = maintenance_service.due_soon(session, on_date=TODAY)
        assert "km" in due[0][2]

    def test_due_soon_reads_the_configured_windows(self, session, vehicle):
        from app.services import settings_service

        record = maintenance_service.start_maintenance(
            session, vehicle, description="Service", status="in_progress"
        )
        vehicle.mileage = 10000
        maintenance_service.complete_maintenance(
            session,
            record,
            cost=100,
            next_due_date=TODAY + timedelta(days=20),
            next_due_odometer=12000,
        )
        assert maintenance_service.due_soon(session, on_date=TODAY) == []

        settings_service.set_value(session, "maint_due_days", "30")
        due = maintenance_service.due_soon(session, on_date=TODAY)
        assert len(due) == 1 and "20 day" in due[0][2]

        record.next_due_date = None
        record.next_due_odometer = 10300
        session.flush()
        settings_service.set_value(session, "maint_due_km", "200")
        assert maintenance_service.due_soon(session, on_date=TODAY) == []
        settings_service.set_value(session, "maint_due_km", "400")
        due = maintenance_service.due_soon(session, on_date=TODAY)
        assert len(due) == 1 and "300 km" in due[0][2]

    def test_spend_counts_final_costs_only(self, session, vehicle):
        open_job = maintenance_service.start_maintenance(
            session, vehicle, description="Open",
            estimate_cost=Decimal("9999.00"), status="in_progress",
        )
        done = maintenance_service.start_maintenance(
            session, vehicle, description="Done", status="in_progress"
        )
        maintenance_service.complete_maintenance(session, done, cost=Decimal("500.00"))
        total, by_type, by_vehicle = maintenance_service.spend_summary(session)
        assert total == Decimal("500.00")
        assert open_job.cost is None
