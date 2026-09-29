"""Tests for the staff-schema migration.

The migration's only job is to bring a database that predates the staff rebuild
up to what the models declare. The database that needs it is the Aiven MySQL
instance, which is not something these tests should touch -- so the tests build
a deliberately *old* schema by hand, run the migration against it, and check
that the result matches what the models expect.

Building the old schema literally is the point. A test that started from
`Base.metadata.create_all` would already have every new column and the
migration would do nothing, passing without ever exercising an ALTER.
"""

from __future__ import annotations

import importlib
import sys

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

# The pre-staff-rebuild schema, transcribed from the models as they were.
# Kept as literal DDL on purpose: if it were built from the current models it
# would contain the very columns the migration adds.
OLD_SCHEMA = [
    """
    CREATE TABLE VEHICLE_CATEGORY (
        category_id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_name VARCHAR(255) NOT NULL,
        base_rate_multiplier DECIMAL(10,2) NOT NULL
    )
    """,
    """
    CREATE TABLE USERS (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name VARCHAR(255) NOT NULL,
        email VARCHAR(255) NOT NULL UNIQUE,
        phone VARCHAR(255),
        password_hash VARCHAR(255) NOT NULL,
        address VARCHAR(255) NOT NULL,
        license_number VARCHAR(12) NOT NULL UNIQUE,
        license_expiry DATE NOT NULL,
        role VARCHAR(20) NOT NULL,
        created_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE VEHICLE (
        vehicle_id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id INTEGER NOT NULL,
        make VARCHAR(255) NOT NULL,
        model VARCHAR(255) NOT NULL,
        year INTEGER NOT NULL,
        plate_number VARCHAR(7) NOT NULL UNIQUE,
        daily_rate DECIMAL(10,2) NOT NULL,
        mileage INTEGER NOT NULL,
        seats INTEGER,
        transmission VARCHAR(20),
        fuel_type VARCHAR(20),
        body_style VARCHAR(20),
        status VARCHAR(20) NOT NULL,
        created_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE BOOKING (
        booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        vehicle_id INTEGER NOT NULL,
        start_date DATE NOT NULL,
        end_date DATE NOT NULL,
        actual_return_date DATE,
        total_cost DECIMAL(10,2) NOT NULL,
        status VARCHAR(20) NOT NULL,
        created_at DATETIME NOT NULL
    )
    """,
    # paid_at NOT NULL, created_at absent entirely: the two contradictions the
    # migration exists to resolve.
    """
    CREATE TABLE PAYMENT (
        payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER NOT NULL,
        amount DECIMAL(10,2) NOT NULL,
        method VARCHAR(20) NOT NULL,
        status VARCHAR(20) NOT NULL,
        paid_at DATETIME NOT NULL,
        FOREIGN KEY(booking_id) REFERENCES BOOKING(booking_id)
    )
    """,
    # photo_url NOT NULL: forcing '' for every unphotographed rental.
    """
    CREATE TABLE INSPECTION_REPORT (
        inspection_id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER NOT NULL,
        inspected_by INTEGER NOT NULL,
        inspection_type VARCHAR(20) NOT NULL,
        mileage_reading INTEGER NOT NULL,
        fuel_level VARCHAR(20) NOT NULL,
        damage_notes VARCHAR(255),
        photo_url VARCHAR(255) NOT NULL,
        inspected_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE PENALTY (
        penalty_id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER NOT NULL,
        penalty_type VARCHAR(20) NOT NULL,
        amount DECIMAL(10,2) NOT NULL,
        description VARCHAR(255),
        created_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE MAINTENANCE_RECORD (
        maintenance_id INTEGER PRIMARY KEY AUTOINCREMENT,
        vehicle_id INTEGER NOT NULL,
        description VARCHAR(255),
        cost DECIMAL(10,2),
        start_date DATE,
        end_date DATE,
        status VARCHAR(20)
    )
    """,
    # VEHICLE_MEDIA is not touched by the staff migration and already matches
    # the model on the live database, so it is transcribed in its current form.
    # The columns here embed CarImages signed URLs and are treated as
    # credential data by the model -- do not print their contents in a failure.
    """
    CREATE TABLE VEHICLE_MEDIA (
        media_id INTEGER PRIMARY KEY AUTOINCREMENT,
        vehicle_id INTEGER NOT NULL,
        source VARCHAR(255) NOT NULL,
        view_angle VARCHAR(255) NOT NULL,
        image_url VARCHAR(255) NOT NULL,
        model_3d_url VARCHAR(255) NOT NULL,
        is_watermarked BOOLEAN,
        cached_at DATETIME NOT NULL
    )
    """,
]


@pytest.fixture
def old_db(tmp_path):
    """A database with the pre-staff schema, plus a runner for the migration.

    Returns (engine, run) where calling run() runs the migration script against
    that engine. Each test gets its own file, so a migration that corrupts its
    target cannot affect the next test.

    The engine is injected rather than re-created through `app.config`, because
    reloading the config module produces a *new* declarative Base and the models
    stay registered against the old one -- which makes the rebuild step look
    for tables that are not there.
    """
    db_file = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", future=True)
    with engine.begin() as conn:
        for statement in OLD_SCHEMA:
            conn.execute(text(statement))

    migration = importlib.import_module("scripts.migrate_staff_schema")
    migration.engine = engine

    def run(*args):
        """Run the migration; return the exit code instead of raising.

        main() ends with SystemExit(1) when verification finds a problem, which
        is the behaviour a command-line user wants but would otherwise abort
        every test before it could inspect the resulting schema.
        """
        sys.argv = ["migrate_staff_schema", *args]
        try:
            migration.main()
        except SystemExit as exc:
            return exc.code
        finally:
            sys.argv = ["pytest"]
        return 0

    try:
        yield engine, run
    finally:
        engine.dispose()


def columns(engine, table) -> dict[str, dict]:
    return {c["name"]: c for c in inspect(engine).get_columns(table)}


class TestAddsColumns:
    def test_adds_payment_audit_columns(self, old_db):
        engine, run = old_db
        run()
        cols = columns(engine, "PAYMENT")
        for name in ("recorded_by", "reference_no", "note", "created_at"):
            assert name in cols, f"PAYMENT.{name} was not added"

    def test_adds_booking_audit_columns(self, old_db):
        engine, run = old_db
        run()
        cols = columns(engine, "BOOKING")
        for name in ("created_by", "cancel_reason"):
            assert name in cols, f"BOOKING.{name} was not added"

    def test_new_columns_are_nullable(self, old_db):
        # NOT NULL with no default would fail every existing INSERT.
        engine, run = old_db
        run()
        for table, name in [
            ("PAYMENT", "recorded_by"),
            ("PAYMENT", "reference_no"),
            ("PAYMENT", "note"),
            ("BOOKING", "created_by"),
            ("BOOKING", "cancel_reason"),
        ]:
            assert columns(engine, table)[name]["nullable"] is True

    def test_existing_rows_survive(self, old_db):
        engine, run = old_db
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO VEHICLE_CATEGORY (category_name, base_rate_multiplier) "
                    "VALUES ('Car', 1.0)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO VEHICLE (category_id, make, model, year, plate_number, "
                    "daily_rate, mileage, status, created_at) "
                    "VALUES (1, 'Toyota', 'Vios', 2022, 'OLD-001', 2500, 100, 'available', '2026-01-01')"
                )
            )
        run()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM VEHICLE")).scalar_one() == 1
            assert (
                conn.execute(text("SELECT plate_number FROM VEHICLE")).scalar_one()
                == "OLD-001"
            )


class TestBackfills:
    def test_backfills_payment_created_at_from_paid_at(self, old_db):
        engine, run = old_db
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO VEHICLE_CATEGORY (category_name, base_rate_multiplier) "
                    "VALUES ('Car', 1.0)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO VEHICLE (category_id, make, model, year, plate_number, "
                    "daily_rate, mileage, status, created_at) "
                    "VALUES (1, 'Toyota', 'Vios', 2022, 'OLD-001', 2500, 100, 'available', '2026-01-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO USERS (full_name, email, password_hash, address, "
                    "license_number, license_expiry, role, created_at) "
                    "VALUES ('A', 'a@x.com', 'h', 'addr', 'L-1', '2027-01-01', "
                    "'customer', '2026-01-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO BOOKING (user_id, vehicle_id, start_date, end_date, "
                    "total_cost, status, created_at) "
                    "VALUES (1, 1, '2026-09-01', '2026-09-05', 10000, 'completed', '2026-09-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO PAYMENT (booking_id, amount, method, status, paid_at) "
                    "VALUES (1, 10000, 'cash', 'paid', '2026-09-05 10:00:00')"
                )
            )
        run()
        with engine.connect() as conn:
            created = conn.execute(text("SELECT created_at FROM PAYMENT")).scalar_one()
        assert created is not None
        assert "2026-09-05" in str(created)


class TestIndexes:
    def test_creates_the_booking_status_index(self, old_db):
        engine, run = old_db
        run()
        names = {i["name"] for i in inspect(engine).get_indexes("BOOKING")}
        assert "ix_booking_status_end" in names

    def test_creates_the_payment_index(self, old_db):
        engine, run = old_db
        run()
        names = {i["name"] for i in inspect(engine).get_indexes("PAYMENT")}
        assert "ix_payment_status_created" in names


class TestIdempotent:
    def test_running_twice_is_harmless(self, old_db):
        engine, run = old_db
        run()
        before = columns(engine, "PAYMENT")
        run()  # second time
        after = columns(engine, "PAYMENT")
        assert set(before) == set(after)

    def test_a_third_run_still_does_nothing(self, old_db):
        engine, run = old_db
        run()
        first = len(inspect(engine).get_indexes("BOOKING"))
        run()
        run()
        # An index created twice would either raise or be listed twice; either
        # way the count must not move.
        assert len(inspect(engine).get_indexes("BOOKING")) == first

    def test_dry_run_changes_nothing(self, old_db):
        engine, run = old_db
        before = set(columns(engine, "PAYMENT"))
        run("--dry-run")
        assert set(columns(engine, "PAYMENT")) == before

    def test_dry_run_reports_what_it_would_do(self, old_db, capsys):
        engine, run = old_db
        run("--dry-run")
        out = capsys.readouterr().out
        assert "WOULD ADD" in out
        assert "recorded_by" in out
        assert "nothing was changed" in out


class TestResultMatchesModels:
    def test_migrated_schema_satisfies_every_model_column(self, old_db):
        """The real assertion: models and database agree after migrating.

        Compares the migrated columns against the ORM metadata rather than a
        hand-written list, so this keeps meaning something as the models change.
        """
        from app.database import Base
        import app.models  # noqa: F401

        engine, run = old_db
        run()

        with engine.connect() as conn:
            for table in Base.metadata.sorted_tables:
                db_columns = {c["name"] for c in inspect(engine).get_columns(table.name)}
                model_columns = {c.name for c in table.columns}
                missing = model_columns - db_columns
                assert not missing, f"{table.name} is missing {sorted(missing)}"

    def test_the_staff_models_work_against_the_migrated_database(self, old_db):
        """Write a booking, a pending payment and a photo-less inspection.

        This is the test that would have caught the two NOT NULL columns: on a
        genuinely migrated database a pending payment has no paid_at and an
        unphotographed rental has no photo.
        """
        from datetime import date, datetime

        from app.models import Booking, Inspection_Report, Payment, Users, Vehicle, Vehicle_Category

        engine, run = old_db
        run()

        with Session(engine, future=True) as session:
            category = Vehicle_Category(category_name="Car", base_rate_multiplier=1.0)
            session.add(category)
            session.flush()
            user = Users(
                full_name="A", email="a@x.com", password_hash="h", address="addr",
                license_number="L-1", license_expiry=date(2027, 1, 1),
                role="customer", created_at=datetime(2026, 1, 1),
            )
            staff = Users(
                full_name="B", email="b@x.com", password_hash="h", address="addr",
                license_number="L-2", license_expiry=date(2027, 1, 1),
                role="staff", created_at=datetime(2026, 1, 1),
            )
            vehicle = Vehicle(
                category_id=category.category_id, make="Toyota", model="Vios",
                year=2022, plate_number="MIG-001", daily_rate="2500.00",
                mileage=0, status="available", created_at=datetime(2026, 1, 1),
            )
            session.add_all([user, staff, vehicle])
            session.flush()

            booking = Booking(
                user_id=user.user_id, vehicle_id=vehicle.vehicle_id,
                start_date=date(2026, 9, 1), end_date=date(2026, 9, 5),
                total_cost="10000.00", status="pending",
                created_at=datetime(2026, 1, 1), created_by=staff.user_id,
                cancel_reason=None,
            )
            session.add(booking)
            session.flush()

            # A GCash transfer that has not landed: no paid_at.
            pending = Payment(
                booking=booking, amount="2000.00", method="gcash", status="pending",
                paid_at=None, recorded_by=staff.user_id, reference_no="GC-X",
            )
            # An inspection of a rental that was never photographed: no photo.
            report = Inspection_Report(
                booking=booking, inspected_by=staff.user_id,
                inspection_type="pre-rental", mileage_reading=100, fuel_level="full",
                damage_notes=None, photo_url=None, inspected_at=datetime(2026, 1, 1),
            )
            session.add_all([pending, report])
            session.commit()

            assert pending.paid_at is None
            assert report.photo_url is None
            assert booking.created_by == staff.user_id
            assert pending.recorded_by == staff.user_id
            assert pending.created_at is not None  # defaulted, not required
