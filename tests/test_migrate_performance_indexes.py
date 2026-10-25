"""Tests for the composite performance indexes migration.

The suite must never touch the live MySQL database, so every test injects a
throwaway SQLite engine. The migration is intentionally additive: it only
creates indexes, skips tables/columns that are not there, and never rewrites
data.
"""

from __future__ import annotations

import importlib
import sys

import pytest
from sqlalchemy import create_engine, inspect, text

from app.database import Base

EXPECTED = {
    "PAYMENT": {
        "ix_payment_status_kind_paid",
        "ix_payment_booking_status_kind",
    },
    "BOOKING": {
        "ix_booking_vehicle_status_end",
        "ix_booking_status_created",
    },
    "MAINTENANCE_RECORD": {"ix_maintenance_vehicle_status_dates"},
    "INSPECTION_REPORT": {"ix_inspection_inspected_at"},
    "USERS": {"ix_users_role_created"},
}


@pytest.fixture
def migration():
    module = importlib.import_module("scripts.migrate_performance_indexes")
    yield module
    module.engine = None


@pytest.fixture
def db(tmp_path, migration):
    engine = create_engine(f"sqlite:///{tmp_path / 'performance.db'}", future=True)
    Base.metadata.create_all(engine)
    migration.engine = engine
    yield engine
    engine.dispose()


def _index_names(engine, table: str) -> set[str]:
    return {index["name"] for index in inspect(engine).get_indexes(table)}


class TestCreateIndexes:
    def test_creates_every_composite_index(self, db, migration):
        assert migration.add_indexes(dry_run=False) is True
        for table, names in EXPECTED.items():
            assert names <= _index_names(db, table)

    def test_creates_the_expected_columns(self, db, migration):
        migration.add_indexes(dry_run=False)
        by_name = {
            index["name"]: tuple(index["column_names"])
            for table in EXPECTED
            for index in inspect(db).get_indexes(table)
        }
        assert by_name["ix_payment_status_kind_paid"] == (
            "status",
            "kind",
            "paid_at",
        )
        assert by_name["ix_booking_vehicle_status_end"] == (
            "vehicle_id",
            "status",
            "end_date",
        )
        assert by_name["ix_maintenance_vehicle_status_dates"] == (
            "vehicle_id",
            "status",
            "start_date",
            "end_date",
        )

    def test_rerun_is_idempotent(self, db, migration, capsys):
        assert migration.add_indexes(dry_run=False) is True
        before = {table: _index_names(db, table) for table in EXPECTED}
        assert migration.add_indexes(dry_run=False) is True
        after = {table: _index_names(db, table) for table in EXPECTED}
        assert after == before
        assert "already exists" in capsys.readouterr().out

    def test_dry_run_changes_nothing(self, db, migration, capsys):
        assert migration.add_indexes(dry_run=True) is True
        for table, names in EXPECTED.items():
            assert not (names & _index_names(db, table))
        assert "WOULD CREATE" in capsys.readouterr().out

    def test_equivalent_index_is_not_duplicated(self, db, migration, capsys):
        with db.begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX legacy_payment_lookup "
                    "ON PAYMENT (status, kind, paid_at)"
                )
            )
        migration.add_indexes(dry_run=False)
        assert "ix_payment_status_kind_paid" not in _index_names(db, "PAYMENT")
        assert "legacy_payment_lookup" in _index_names(db, "PAYMENT")
        assert "equivalent" in capsys.readouterr().out


class TestMissingSchema:
    def test_empty_database_is_skipped_not_changed(self, tmp_path, migration, capsys):
        engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}", future=True)
        migration.engine = engine
        try:
            assert migration.add_indexes(dry_run=False) is False
            assert "does not exist" in capsys.readouterr().out
        finally:
            engine.dispose()

    def test_missing_column_skips_only_that_index(self, tmp_path, migration, capsys):
        engine = create_engine(f"sqlite:///{tmp_path / 'partial.db'}", future=True)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE PAYMENT (payment_id INTEGER PRIMARY KEY, "
                    "status VARCHAR(20), kind VARCHAR(16))"
                )
            )
        migration.engine = engine
        try:
            assert migration.add_indexes(dry_run=False) is False
            assert "paid_at" in capsys.readouterr().out
            assert "ix_payment_status_kind_paid" not in _index_names(
                engine, "PAYMENT"
            )
        finally:
            engine.dispose()


class TestCommandLine:
    def test_main_refuses_an_empty_database(
        self, tmp_path, migration, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "migrate_performance_indexes",
                "--database",
                str(tmp_path / "empty.db"),
            ],
        )
        with pytest.raises(SystemExit) as exc:
            migration.main()
        assert exc.value.code == 1
        assert "refusing to guess" in capsys.readouterr().out


class TestDefinitions:
    def test_create_statement_is_plain_sql(self, migration):
        assert migration.create_statement(
            "PAYMENT",
            "ix_payment_status_kind_paid",
            ["status", "kind", "paid_at"],
        ) == (
            "CREATE INDEX ix_payment_status_kind_paid ON PAYMENT "
            "(status, kind, paid_at)"
        )

    def test_indexes_cover_every_hot_filter(self, migration):
        assert {
            (table, tuple(columns))
            for table, _name, columns in migration.INDEXES
        } == {
            ("PAYMENT", ("status", "kind", "paid_at")),
            ("PAYMENT", ("booking_id", "status", "kind")),
            ("BOOKING", ("vehicle_id", "status", "end_date")),
            ("BOOKING", ("status", "created_at")),
            (
                "MAINTENANCE_RECORD",
                ("vehicle_id", "status", "start_date", "end_date"),
            ),
            ("INSPECTION_REPORT", ("inspected_at",)),
            ("USERS", ("role", "created_at")),
        }
