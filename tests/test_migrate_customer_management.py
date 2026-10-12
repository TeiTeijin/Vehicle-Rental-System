"""Tests for the customer-management migration.

Builds pre-management USERS/BOOKING tables by hand, runs the migration
against a throwaway SQLite file, and checks the result. Never touches a
real database.
"""

from __future__ import annotations

import importlib
import sys

from sqlalchemy import create_engine, inspect, text

OLD_USERS = """
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
"""

OLD_BOOKING = """
CREATE TABLE BOOKING (
    booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    vehicle_id INTEGER NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    total_cost DECIMAL(10,2) NOT NULL,
    status VARCHAR(20) NOT NULL,
    created_at DATETIME NOT NULL
)
"""


def _seed_old(conn) -> None:
    conn.execute(text(OLD_USERS))
    conn.execute(text(OLD_BOOKING))
    conn.execute(
        text(
            "INSERT INTO USERS (full_name, email, password_hash, address, "
            "license_number, license_expiry, role, created_at) "
            "VALUES ('Old Customer', 'old@example.com', 'x', '1 Street', "
            "'LIC0000001', '2030-01-01', 'customer', '2026-01-01')"
        )
    )


import pytest


@pytest.fixture
def old_db(tmp_path):
    db_file = tmp_path / "legacy_customers.db"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", future=True)
    with engine.begin() as conn:
        _seed_old(conn)

    migration = importlib.import_module("scripts.migrate_customer_management")

    def run(*args):
        sys.argv = ["migrate_customer_management", "--database", str(db_file), *args]
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


def columns(engine, table) -> dict[str, dict]:
    return {c["name"]: c for c in inspect(engine).get_columns(table)}


class TestAddsColumns:
    def test_adds_user_columns(self, old_db):
        engine, run = old_db
        run()
        cols = columns(engine, "USERS")
        for name in (
            "birthdate", "emergency_contact", "source", "is_active",
            "flag_reason", "flagged_at", "flagged_by",
            "licence_front_path", "licence_back_path",
        ):
            assert name in cols, f"USERS.{name} was not added"

    def test_adds_booking_override_columns(self, old_db):
        engine, run = old_db
        run()
        cols = columns(engine, "BOOKING")
        for name in ("flag_override_note", "flag_override_by", "flag_override_at"):
            assert name in cols

    def test_creates_notes_table(self, old_db):
        engine, run = old_db
        run()
        assert "CUSTOMER_NOTE" in inspect(engine).get_table_names()

    def test_backfills_walk_in_source(self, old_db):
        engine, run = old_db
        run()
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT source FROM USERS WHERE email = 'old@example.com'")
            ).scalar_one() == "walk_in"

    def test_existing_rows_survive(self, old_db):
        engine, run = old_db
        run()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM USERS")).scalar_one() == 1


class TestIdempotent:
    def test_running_twice_changes_nothing(self, old_db):
        engine, run = old_db
        run()
        before = set(columns(engine, "USERS"))
        run()
        assert set(columns(engine, "USERS")) == before

    def test_dry_run_changes_nothing(self, old_db):
        engine, run = old_db
        before = set(columns(engine, "USERS"))
        run("--dry-run")
        assert set(columns(engine, "USERS")) == before
        assert "CUSTOMER_NOTE" not in inspect(engine).get_table_names()
