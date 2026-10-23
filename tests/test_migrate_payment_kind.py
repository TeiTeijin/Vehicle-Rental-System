"""Tests for the PAYMENT.kind migration.

Same contract as the other migrations: the suite must not touch the real
database, so each test builds a deliberately *old* PAYMENT table by hand --
the shape it had before the refundable-deposit feature -- runs the migration
against that engine, and checks the result.

The migration takes its target from `--database` rather than importing the
project's module-level engine: the engine is injected, so nothing in the
suite can reach a real database by accident.
"""

from __future__ import annotations

import importlib

import pytest
from sqlalchemy import create_engine, inspect, text

OLD_PAYMENT_TABLE = """
CREATE TABLE PAYMENT (
    payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL,
    amount DECIMAL(10,2) NOT NULL,
    method VARCHAR(20) NOT NULL DEFAULT 'cash',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    paid_at DATETIME,
    recorded_by INTEGER,
    reference_no VARCHAR(64),
    note VARCHAR(255),
    created_at DATETIME NOT NULL
)
"""


@pytest.fixture
def migration():
    module = importlib.import_module("scripts.migrate_payment_kind")
    yield module
    module.engine = None


@pytest.fixture
def old_db(tmp_path, migration):
    path = tmp_path / "old_payments.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    with engine.begin() as conn:
        conn.execute(text(OLD_PAYMENT_TABLE))
        conn.execute(
            text(
                "INSERT INTO PAYMENT (booking_id, amount, method, status, created_at) "
                "VALUES (1, 2500.00, 'cash', 'paid', '2026-09-01 10:00:00'), "
                "       (1, 2000.00, 'gcash', 'paid', '2026-09-01 10:05:00')"
            )
        )
    migration.engine = engine
    yield engine
    engine.dispose()


class TestAddColumn:
    def test_adds_kind_with_rental_default(self, old_db, migration):
        present = migration.existing_columns()
        assert "kind" not in present
        migration.add_column(present, dry_run=False)
        assert "kind" in migration.existing_columns()

    def test_existing_rows_read_as_rental(self, old_db, migration):
        migration.add_column(migration.existing_columns(), dry_run=False)
        with old_db.connect() as conn:
            kinds = conn.execute(text("SELECT DISTINCT kind FROM PAYMENT")).all()
        assert kinds == [("rental",)]

    def test_rerun_is_idempotent(self, old_db, migration, capsys):
        migration.add_column(migration.existing_columns(), dry_run=False)
        migration.add_column(migration.existing_columns(), dry_run=False)
        assert "already exists" in capsys.readouterr().out

    def test_dry_run_changes_nothing(self, old_db, migration):
        migration.add_column(migration.existing_columns(), dry_run=True)
        assert "kind" not in migration.existing_columns()

    def test_missing_table_is_reported(self, tmp_path, migration, capsys):
        engine = create_engine(f"sqlite:///{tmp_path}/empty.db", future=True)
        migration.engine = engine
        try:
            migration.add_column(migration.existing_columns(), dry_run=False)
            assert "does not exist" in capsys.readouterr().out
        finally:
            engine.dispose()


class TestBackfill:
    def test_backfill_counts_nulls(self, old_db, migration):
        # A MySQL-style NULL (SQLite filled the default on ALTER, so fake one).
        migration.add_column(migration.existing_columns(), dry_run=False)
        assert migration.backfill({"kind"}, dry_run=False) == 0

    def test_backfill_without_column_reports(self, old_db, migration, capsys):
        assert migration.backfill(set(), dry_run=False) == 0
        assert "not present" in capsys.readouterr().out


class TestVerify:
    def test_verify_passes_after_migration(self, old_db, migration):
        migration.add_column(migration.existing_columns(), dry_run=False)
        assert migration.verify(migration.existing_columns()) is True

    def test_verify_fails_before_migration(self, old_db, migration):
        assert migration.verify(migration.existing_columns()) is False

    def test_deposit_rows_survive_the_constraint(self, old_db, migration):
        migration.add_column(migration.existing_columns(), dry_run=False)
        with old_db.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO PAYMENT (booking_id, amount, method, status, kind, created_at) "
                    "VALUES (2, 2000.00, 'cash', 'paid', 'deposit', '2026-09-02 10:00:00')"
                )
            )
            kinds = conn.execute(
                text("SELECT kind FROM PAYMENT WHERE booking_id = 2")
            ).all()
        assert kinds == [("deposit",)]

    def test_unknown_kind_is_rejected(self, old_db, migration):
        import sqlalchemy.exc

        migration.add_column(migration.existing_columns(), dry_run=False)
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            with old_db.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO PAYMENT (booking_id, amount, method, status, kind, created_at) "
                        "VALUES (2, 1.00, 'cash', 'paid', 'tip', '2026-09-02 10:00:00')"
                    )
                )


class TestInspectionHelpers:
    def test_column_ddl_matches_the_historical_enum(self, migration):
        """This migration is history: it must keep writing the *old* enum.

        A later migration (`migrate_deposit_applied`) widens the live column;
        this script's job is still to add the two-value column that existed at
        the time, so its tests must not follow the current model enum.
        """
        ddl = migration.column_ddl("sqlite")["kind"]
        assert "'rental'" in ddl and "'deposit'" in ddl
        assert "deposit_applied" not in ddl
        assert migration.KINDS == ("rental", "deposit")

    def test_resolve_url_accepts_paths_and_urls(self, migration):
        assert migration.resolve_url("demo.db") == "sqlite:///demo.db"
        assert migration.resolve_url("sqlite:///x.db") == "sqlite:///x.db"
