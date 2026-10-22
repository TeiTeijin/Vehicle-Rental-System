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

os.environ["DATABASE_URL"] = "sqlite://"
# Keys start empty: no test session may reach the CarImages API (it is
# externally down and anything cached in .env must not leak into tests).
os.environ["CI_API_KEY"] = ""
os.environ["CI_API_SECRET"] = ""

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
# Offline media / no-network harness
# --------------------------------------------------------------------------


class NetworkAccessBlocked(AssertionError):
    """Raised by the offline-media harness when a test reaches the network."""


@pytest.fixture(autouse=True)
def offline_media(monkeypatch, tmp_path):
    """Keep every test off the network.

    - `media_service.get_or_fetch_media` returns no media rows, so GUI
      builders never spawn workers that would fetch anything.
    - `requests.get`/`requests.post` as used by the CarImages client raise
      ``NetworkAccessBlocked`` (an ``AssertionError``) and every blocked
      call is recorded. Two things make the no-network guarantee real:
      the immediate raise kills accidental calls where a test can see
      them, and the teardown assertion below fails the test even when a
      worker swallowed the exception.
    - the on-disk image cache is redirected into ``tmp_path``.

    A test that intentionally exercises media behaviour shadows
    `offline_media` with its own module-level fixture.
    """
    from app.services import carimages_client, image_cache, media_service

    blocked: list[tuple[str, tuple, dict]] = []

    def _blocked(method):
        def _wrapper(*args, **kwargs):
            blocked.append((method, args, kwargs))
            raise NetworkAccessBlocked(
                "tests must never call the CarImages API: "
                f"requests.{method}({args!r}, {kwargs!r})"
            )

        return _wrapper

    monkeypatch.setattr(media_service, "get_or_fetch_media", lambda *a, **k: [])
    monkeypatch.setattr(carimages_client.requests, "get", _blocked("get"))
    monkeypatch.setattr(carimages_client.requests, "post", _blocked("post"))
    monkeypatch.setattr(image_cache, "CACHE_DIR", tmp_path / "image-cache")
    yield
    assert not blocked, (
        f"{len(blocked)} network call(s) attempted while CarImages is offline; "
        "patch the client instead of reaching the API: "
        f"{blocked[:3]}"
    )


@pytest.fixture(scope="session")
def qapp():
    """Shared QApplication; bounded pool teardown at session end."""
    from PySide6.QtWidgets import QApplication

    from app.staff.workers import shutdown_pools

    app = QApplication.instance() or QApplication([])
    yield app
    shutdown_pools(1000)


# --------------------------------------------------------------------------
# Factories
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
            "license_number": f"LIC{n:08d}",
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
        counter["n"] += 1
        n = counter["n"]
        fields = {
            "make": "Toyota",
            "model": "Vios",
            "year": 2022,
            "plate_number": f"AAA-{n:03d}",
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


# --------------------------------------------------------------------------
# Ready-made instances
# --------------------------------------------------------------------------


@pytest.fixture
def customer(customer_factory) -> Users:
    return customer_factory()


@pytest.fixture
def staff(staff_factory) -> Users:
    return staff_factory()


@pytest.fixture
def admin(customer_factory) -> Users:
    return customer_factory(
        full_name="Branch Admin", email="admin@example.com", role="admin"
    )


@pytest.fixture
def vehicle(car_category, vehicle_factory) -> Vehicle:
    """One available Toyota Vios at 2,500/day."""
    return vehicle_factory(car_category)


@pytest.fixture
def second_vehicle(car_category, vehicle_factory) -> Vehicle:
    return vehicle_factory(
        car_category, make="Honda", model="Civic", plate_number="BBB-001"
    )


@pytest.fixture
def moto(moto_category, vehicle_factory) -> Vehicle:
    return vehicle_factory(
        moto_category, make="Honda", model="Click 125i", plate_number="MMP-001"
    )
