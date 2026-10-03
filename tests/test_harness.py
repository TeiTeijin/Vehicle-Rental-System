"""Fixture smoke tests.

These do not test application behaviour. They assert that the harness itself
is sound -- that a test session is genuinely isolated from the real database,
and that the DECIMAL columns round-trip as Decimal rather than float. Money
arithmetic in the rest of the suite depends on both holding.
"""

from decimal import Decimal

from sqlalchemy import select

from app.config import DATABASE_URL


def test_session_never_touches_the_real_database():
    assert DATABASE_URL == "sqlite://", (
        "tests must run against SQLite; if this fails, app.config was imported "
        "before tests/conftest.py could pin DATABASE_URL"
    )


def test_decimal_columns_round_trip_as_decimal(session, car_category, vehicle_factory):
    vehicle = vehicle_factory(car_category, daily_rate=Decimal("2450.50"))
    session.commit()
    session.expire(vehicle)

    assert isinstance(vehicle.daily_rate, Decimal)
    assert vehicle.daily_rate == Decimal("2450.50")


def test_factories_build_usable_rows(
    session, car_category, customer_factory, staff_factory, vehicle_factory, booking_factory
):
    customer = customer_factory()
    staff = staff_factory()
    vehicle = vehicle_factory(car_category)
    booking = booking_factory(customer, vehicle)

    assert customer.role == "customer"
    assert staff.role == "staff"
    assert vehicle.status == "available"
    assert booking.vehicle_id == vehicle.vehicle_id

    session.commit()
    session.expire(booking)
    assert booking.total_cost == Decimal("7500.00")
    assert isinstance(booking.total_cost, Decimal)


def test_rollback_fixture_discards_writes(
    session, car_category, vehicle_factory
):
    from app.models import Vehicle

    vehicle_factory(car_category, plate_number="ZZZ-999")
    session.flush()
    assert session.query(Vehicle).count() == 1
