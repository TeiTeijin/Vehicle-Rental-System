"""Tests for the vehicle-management migration.

Builds a pre-management VEHICLE table by hand (no color / registration /
insurance / retired_at, no rate-log table), runs the migration, and checks
the result. Uses a throwaway SQLite file; never touches a real database.
"""

from __future__ import annotations

import importlib
import sys

import pytest
from sqlalchemy import create_engine, inspect, text

OLD_VEHICLE = """
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
    vehicle_class VARCHAR(10),
    engine_cc INTEGER,
    status VARCHAR(11) NOT NULL,
    created_at DATETIME NOT NULL
)
"""


def _seed_old(conn) -> None:
    conn.execute(text(OLD_VEHICLE))
    conn.execute(
        text(
            "INSERT INTO VEHICLE (category_id, make, model, year, plate_number, "
            "daily_rate, mileage, status, created_at) "
            "VALUES (1, 'Toyota', 'Vios', 2022, 'OLD-001', 2500, 1000, "
            "'available', '2026-01-01')"
        )
    )


@pytest.fixture
def old_db(tmp_path):
    db_file = tmp_path / "legacy_mgmt.db"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", future=True)
    with engine.begin() as conn:
        _seed_old(conn)

    migration = importlib.import_module("scripts.migrate_vehicle_management")

    def run(*args):
        sys.argv = ["migrate_vehicle_management", "--database", str(db_file), *args]
        try:
            migration.main()
        finally:
            sys.argv = ["pytest"]
        return engine

    try:
        yield engine, run
    finally:
        migration.engine = None
        engine.dispose()


def columns(engine, table="VEHICLE") -> dict[str, dict]:
    return {c["name"]: c for c in inspect(engine).get_columns(table)}


class TestAddsColumns:
    def test_adds_all_four_columns(self, old_db):
        engine, run = old_db
        run()
        cols = columns(engine)
        for name in ("color", "registration_expiry", "insurance_expiry", "retired_at"):
            assert name in cols, f"VEHICLE.{name} was not added"

    def test_new_columns_are_nullable(self, old_db):
        engine, run = old_db
        run()
        for name in ("color", "registration_expiry", "insurance_expiry", "retired_at"):
            assert columns(engine)[name]["nullable"] is True

    def test_creates_rate_log_table(self, old_db):
        engine, run = old_db
        run()
        assert "VEHICLE_RATE_LOG" in inspect(engine).get_table_names()
        cols = columns(engine, "VEHICLE_RATE_LOG")
        for name in ("vehicle_id", "old_rate", "new_rate", "changed_by", "changed_at", "is_bulk"):
            assert name in cols

    def test_existing_rows_survive(self, old_db):
        engine, run = old_db
        run()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM VEHICLE")).scalar_one() == 1
            assert (
                conn.execute(
                    text("SELECT mileage FROM VEHICLE WHERE plate_number = 'OLD-001'")
                ).scalar_one()
                == 1000
            )


class TestIdempotent:
    def test_running_twice_changes_nothing(self, old_db):
        engine, run = old_db
        run()
        before = set(columns(engine))
        run()
        assert set(columns(engine)) == before

    def test_dry_run_changes_nothing(self, old_db):
        engine, run = old_db
        before = set(columns(engine))
        run("--dry-run")
        assert set(columns(engine)) == before
        assert "VEHICLE_RATE_LOG" not in inspect(engine).get_table_names()
