"""Tests for the maintenance-lifecycle migration.

Same contract as the other migrations: the suite must not touch the real
database, so each test builds a deliberately *old* MAINTENANCE_RECORD table
by hand -- the shape it had before the service-log feature -- runs the
migration against that engine, and checks the result.
"""

from __future__ import annotations

import importlib

import pytest
from sqlalchemy import create_engine, text

OLD_MAINTENANCE_TABLE = """
CREATE TABLE MAINTENANCE_RECORD (
    maintenance_id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL,
    description VARCHAR(255) NOT NULL,
    cost DECIMAL(10,2) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'scheduled'
)
"""


@pytest.fixture
def migration():
    module = importlib.import_module("scripts.migrate_maintenance_lifecycle")
    yield module
    module.engine = None


@pytest.fixture
def old_db(tmp_path, migration):
    path = tmp_path / "old_shop.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    with engine.begin() as conn:
        conn.execute(text(OLD_MAINTENANCE_TABLE))
        conn.execute(
            text(
                "INSERT INTO MAINTENANCE_RECORD "
                "(vehicle_id, description, cost, start_date, end_date, status) "
                "VALUES (1, 'Brake pads', 4500.00, '2026-09-01', '2026-09-03', 'ongoing'), "
                "       (1, 'Oil change', 3000.00, '2026-08-01', '2026-08-02', 'completed')"
            )
        )
    migration.engine = engine
    yield engine
    engine.dispose()


class TestRebuild:
    def test_rebuild_adds_the_lifecycle_columns(self, old_db, migration):
        assert "maintenance_type" not in migration.existing_columns()
        migration.rebuild_sqlite(dry_run=False)
        present = migration.existing_columns()
        for column in (
            "maintenance_type", "estimate_cost", "odometer",
            "next_due_date", "next_due_odometer", "shop", "logged_by",
            "booking_id", "cancel_reason", "created_at",
        ):
            assert column in present

    def test_rebuild_remaps_ongoing_to_in_progress(self, old_db, migration):
        migration.rebuild_sqlite(dry_run=False)
        with old_db.connect() as conn:
            statuses = conn.execute(
                text("SELECT DISTINCT status FROM MAINTENANCE_RECORD")
            ).all()
            assert ("ongoing",) not in statuses
            assert ("in_progress",) in statuses
            assert ("completed",) in statuses

    def test_rebuild_keeps_the_old_rows(self, old_db, migration):
        migration.rebuild_sqlite(dry_run=False)
        with old_db.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM MAINTENANCE_RECORD")
            ).scalar()
        assert count == 2

    def test_end_date_and_cost_accept_nulls_afterwards(self, old_db, migration):
        migration.rebuild_sqlite(dry_run=False)
        with old_db.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO MAINTENANCE_RECORD "
                    "(vehicle_id, description, start_date, status) "
                    "VALUES (2, 'Open job', '2026-10-01', 'scheduled')"
                )
            )

    def test_rerun_is_idempotent(self, old_db, migration, capsys):
        migration.rebuild_sqlite(dry_run=False)
        migration.rebuild_sqlite(dry_run=False)
        assert "already has the lifecycle shape" in capsys.readouterr().out

    def test_dry_run_changes_nothing(self, old_db, migration):
        migration.rebuild_sqlite(dry_run=True)
        assert "maintenance_type" not in migration.existing_columns()

    def test_missing_table_is_safe(self, tmp_path, migration):
        engine = create_engine(f"sqlite:///{tmp_path}/empty.db", future=True)
        migration.engine = engine
        try:
            assert migration.needs_rebuild() is False
            assert migration.rebuild_sqlite(dry_run=False) is False
        finally:
            engine.dispose()


class TestHelpers:
    def test_column_ddl_matches_model_values(self, migration):
        from app.models import Maintenance_Record

        ddl = migration.column_ddl("sqlite")["maintenance_type"]
        assert "'damage_repair'" in ddl
        allowed = set(Maintenance_Record.maintenance_type.property.columns[0].type.enums)
        assert allowed == set(migration.TYPES)
        status_allowed = set(Maintenance_Record.status.property.columns[0].type.enums)
        assert status_allowed == set(migration.STATUSES)

    def test_remap_counts_ongoing_rows(self, old_db, migration):
        migration.rebuild_sqlite(dry_run=False)
        assert migration.remap_status(dry_run=False) == 0
