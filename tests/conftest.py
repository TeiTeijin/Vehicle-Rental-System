"""Shared pytest fixtures.

Every test in this suite runs against a throwaway in-memory SQLite database.
Nothing here ever reaches the developer's real DATABASE_URL (the Aiven MySQL
instance in ``.env``), which is why the environment variable below is set
*before* ``app.config`` is first imported: ``load_dotenv`` does not override
variables that are already set, so a real ``.env`` cannot leak in.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

# Must happen before any `app.*` import: app/config.py reads DATABASE_URL at
# import time and build_database_url() turns it into a live engine.
os.environ["DATABASE_URL"] = "sqlite://"
# Keep credentials out of any code path that inspects them.
os.environ.setdefault("CI_API_KEY", "test-key")
os.environ.setdefault("CI_API_SECRET", "test-secret")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (  # noqa: E402  (import order is load-bearing here)
    Booking,
    Inspection_Report,
    Maintenance_Record,
    Payment,
    Penalty,
    Users,
    Vehicle,
    Vehicle_Category,
    Vehicle_Media,
)
from app.utils.security import hash_password  # noqa: E402

TODAY = date(2026, 9, 29)


@pytest.fixture
def engine():
    """A private in-memory database, per test."""
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def session(engine) -> Session:
    with Session(engine, future=True) as session:
        yield session
        session.rollback()


# --------------------------------------------------------------------------
# Factories
#
# Defaults are chosen so a bare `make_vehicle(session, category)` produces a
# usable, available car. Tests override only the fields they care about.
# --------------------------------------------------------------------------


def _password() -> str:
    return hash_password("correct horse battery staple")


@pytest.fixture
def car_category(session) -> Vehicle_Category:
    row = Vehicle_Category(category_name="Car", base_rate_multiplier=1.00)
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def moto_category(session) -> Vehicle_Category:
    row = Vehicle_Category(category_name="Motorcycle", base_rate_multiplier=0.70)
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def customer_factory(session):
    counter = {"n": 0}

    def _make(**overrides) -> Users:
        counter["n"] += 1
        n = counter["n"]
        fields = {
            "full_name": f"Customer {n}",
            "email": f"customer{n}@example.com",
            "phone": f"0917000{n:04d}",
            "password_hash": _password(),
            "address": f"{n} Test Street",
            "license_number": f"LIC-TEST-{n:04d}",
            "license_expiry": TODAY + timedelta(days=365),
            "role": "customer",
            "created_at": datetime(2026, 1, 1, 9, 0),
        }
        fields.update(overrides)
        row = Users(**fields)
        session.add(row)
        session.flush()
        return row

    return _make


@pytest.fixture
def staff_factory(customer_factory):
    def _make(**overrides) -> Users:
        fields = {
            "full_name": "Staff User",
            "email": "staff@example.com",
            "role": "staff",
        }
        fields.update(overrides)
        return customer_factory(**fields)

    return _make


@pytest.fixture
def vehicle_factory(session):
    counter = {"n": 0}

    def _make(category: Vehicle_Category, **overrides) -> Vehicle:
        counter["n"] += 0  # plates are passed in explicitly below
        fields = {
            "make": "Toyota",
            "model": "Vios",
            "year": 2022,
            "plate_number": "AAA-000",
            "daily_rate": "2500.00",
            "mileage": 0,
            "status": "available",
            "created_at": datetime(2026, 1, 1, 9, 0),
        }
        fields.update(overrides)
        row = Vehicle(category_id=category.category_id, **fields)
        session.add(row)
        session.flush()
        return row

    return _make


@pytest.fixture
def booking_factory(session):
    def _make(
        user: Users,
        vehicle: Vehicle,
        start_date: date = TODAY,
        end_date: date | None = None,
        **overrides,
    ) -> Booking:
        fields = {
            "start_date": start_date,
            "end_date": end_date or (start_date + timedelta(days=2)),
            "actual_return_date": None,
            "total_cost": "7500.00",
            "status": "pending",
            "created_at": datetime(2026, 9, 20, 10, 0),
        }
        fields.update(overrides)
        row = Booking(
            user_id=user.user_id, vehicle_id=vehicle.vehicle_id, **fields
        )
        session.add(row)
        session.flush()
        return row

    return _make
