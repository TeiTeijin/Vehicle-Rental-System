"""Tests for the PAYMENT.kind -> deposit_applied migration.

The suite must not touch the real database, so each test builds a deliberately
*old* PAYMENT table by hand -- the shape `scripts/migrate_payment_kind.py`
leaves behind -- runs the migration against that engine, and checks the result.

The migration takes its target from `--database` rather than importing the
project's module-level engine: the engine is injected, so nothing in the suite
can reach a real database by accident.
"""

from __future__ import annotations

import importlib
import sys

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
    created_at DATETIME NOT NULL,
    kind VARCHAR(16) NOT NULL DEFAULT 'rental'
        CHECK (kind IN ('rental', 'deposit'))
)
"""

OLD_INDEX = (
    "CREATE INDEX ix_payment_status_created "
    "ON PAYMENT (status, created_at)"
)


def _seed_old_payments(conn) -> None:
    conn.execute(text(OLD_PAYMENT_TABLE))
    conn.execute(text(OLD_INDEX))
    conn.execute(
        text(
            "INSERT INTO PAYMENT "
            "(booking_id, amount, method, status, kind, paid_at, note, created_at) "
            "VALUES (1, 2500.00, 'cash', 'paid', 'rental', "
            "'2026-09-01 10:00:00', 'Counter payment', '2026-09-01 10:00:00'), "
            "       (1, 2000.00, 'gcash', 'paid', 'deposit', "
            "'2026-09-01 10:05:00', 'Refundable security deposit', "
            "'2026-09-01 10:05:00')"
        )
    )


@pytest.fixture
def migration():
    module = importlib.import_module("scripts.migrate_deposit_applied")
    yield module
    module.engine = None


@pytest.fixture
def old_db(tmp_path, migration):
    path = tmp_path / "old_payments.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    with engine.begin() as conn:
        _seed_old_payments(conn)
    migration.engine = engine
    yield engine
    engine.dispose()


def payment_rows(engine) -> list[tuple]:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT payment_id, booking_id, amount, method, status, kind, "
                "paid_at, note, created_at FROM PAYMENT ORDER BY payment_id"
            )
        ).all()


class TestWidenKind:
    def test_widens_the_sqlite_check_constraint(self, old_db, migration):
        assert migration.current_kinds() == ("rental", "deposit")
        assert migration.widen_kind(migration.existing_columns(), dry_run=False)
        assert migration.current_kinds() == (
            "rental",
            "deposit",
            "deposit_applied",
        )

    def test_historical_rows_are_untouched(self, old_db, migration):
        before = payment_rows(old_db)
        migration.widen_kind(migration.existing_columns(), dry_run=False)
        assert payment_rows(old_db) == before

    def test_indexes_survive_the_rebuild(self, old_db, migration):
        migration.widen_kind(migration.existing_columns(), dry_run=False)
        names = {index["name"] for index in inspect(old_db).get_indexes("PAYMENT")}
        assert "ix_payment_status_created" in names

    def test_applied_deposit_rows_are_accepted(self, old_db, migration):
        migration.widen_kind(migration.existing_columns(), dry_run=False)
        with old_db.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO PAYMENT "
                    "(booking_id, amount, method, status, kind, created_at) "
                    "VALUES (1, 500.00, 'cash', 'paid', 'deposit_applied', "
                    "'2026-09-02 10:00:00')"
                )
            )
            kinds = conn.execute(
                text("SELECT kind FROM PAYMENT WHERE booking_id = 1 ORDER BY payment_id")
            ).all()
        assert kinds == [("rental",), ("deposit",), ("deposit_applied",)]

    def test_unknown_kind_is_still_rejected(self, old_db, migration):
        import sqlalchemy.exc

        migration.widen_kind(migration.existing_columns(), dry_run=False)
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            with old_db.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO PAYMENT "
                        "(booking_id, amount, method, status, kind, created_at) "
                        "VALUES (2, 1.00, 'cash', 'paid', 'tip', "
                        "'2026-09-02 10:00:00')"
                    )
                )

    def test_rerun_is_idempotent(self, old_db, migration, capsys):
        migration.widen_kind(migration.existing_columns(), dry_run=False)
        assert migration.widen_kind(migration.existing_columns(), dry_run=False)
        assert "already accepts" in capsys.readouterr().out

    def test_dry_run_changes_nothing(self, old_db, migration, capsys):
        assert migration.widen_kind(migration.existing_columns(), dry_run=True)
        assert migration.current_kinds() == ("rental", "deposit")
        assert "WOULD BE WIDENED" in capsys.readouterr().out

    def test_missing_table_is_reported(self, tmp_path, migration, capsys):
        engine = create_engine(f"sqlite:///{tmp_path}/empty.db", future=True)
        migration.engine = engine
        try:
            assert not migration.widen_kind(migration.existing_columns(), dry_run=False)
            assert "does not exist" in capsys.readouterr().out
        finally:
            engine.dispose()

    def test_missing_kind_column_points_at_the_first_migration(
        self, tmp_path, migration, capsys
    ):
        path = tmp_path / "no_kind.db"
        engine = create_engine(f"sqlite:///{path}", future=True)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE PAYMENT (payment_id INTEGER PRIMARY KEY)"))
        migration.engine = engine
        try:
            assert not migration.widen_kind(migration.existing_columns(), dry_run=False)
            assert "migrate_payment_kind" in capsys.readouterr().out
        finally:
            engine.dispose()

    def test_unexpected_enum_is_refused(self, tmp_path, migration, capsys):
        path = tmp_path / "unexpected.db"
        engine = create_engine(f"sqlite:///{path}", future=True)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE PAYMENT (payment_id INTEGER PRIMARY KEY, "
                    "kind VARCHAR(16) CHECK (kind IN ('rental', 'deposit', 'legacy')))"
                )
            )
        migration.engine = engine
        try:
            assert not migration.widen_kind(migration.existing_columns(), dry_run=False)
            assert "refusing to guess" in capsys.readouterr().out
        finally:
            engine.dispose()

    def test_unconstrained_sqlite_table_is_already_wide_enough(
        self, tmp_path, migration, capsys
    ):
        """A snapshot made from the ORM has no CHECK; SQLite accepts the value."""
        path = tmp_path / "orm_snapshot.db"
        engine = create_engine(f"sqlite:///{path}", future=True)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE PAYMENT (payment_id INTEGER PRIMARY KEY, "
                    "kind VARCHAR(7) NOT NULL DEFAULT 'rental')"
                )
            )
        migration.engine = engine
        try:
            assert migration.current_kinds() is None
            assert migration.widen_kind(migration.existing_columns(), dry_run=False)
            assert "no SQLite CHECK" in capsys.readouterr().out
        finally:
            engine.dispose()


class TestVerify:
    def test_verify_passes_after_migration(self, old_db, migration):
        migration.widen_kind(migration.existing_columns(), dry_run=False)
        assert migration.verify(migration.existing_columns()) is True

    def test_verify_fails_before_migration(self, old_db, migration):
        assert migration.verify(migration.existing_columns()) is False


class TestDefinitions:
    def test_mysql_ddl_lists_all_three_values_and_keeps_the_default(
        self, migration
    ):
        ddl = migration.kind_ddl("mysql")
        assert "'rental'" in ddl and "'deposit'" in ddl
        assert "'deposit_applied'" in ddl
        assert "DEFAULT 'rental'" in ddl

    def test_mysql_alter_compiles_from_the_model_enum(self, migration):
        from sqlalchemy.dialects import mysql

        from app.models import Payment

        model_ddl = Payment.kind.type.compile(dialect=mysql.dialect())
        assert model_ddl == "ENUM('rental','deposit','deposit_applied')"
        assert migration.alter_statement("mysql") == (
            "ALTER TABLE PAYMENT MODIFY COLUMN kind " + model_ddl +
            " NOT NULL DEFAULT 'rental'"
        )

    def test_kinds_match_the_model(self, migration):
        from app.models import Payment

        assert migration.KINDS == ("rental", "deposit", "deposit_applied")
        allowed = set(Payment.kind.property.columns[0].type.enums)
        assert allowed == set(migration.KINDS)

    def test_resolve_url_accepts_paths_and_urls(self, migration):
        assert migration.resolve_url("demo.db") == "sqlite:///demo.db"
        assert migration.resolve_url("sqlite:///x.db") == "sqlite:///x.db"


class TestMain:
    def test_main_migrates_a_database_file(self, tmp_path):
        db_file = tmp_path / "legacy.db"
        engine = create_engine(f"sqlite:///{db_file}", future=True)
        with engine.begin() as conn:
            _seed_old_payments(conn)

        migration = importlib.import_module("scripts.migrate_deposit_applied")
        sys.argv = ["migrate_deposit_applied", "--database", str(db_file)]
        try:
            migration.main()
        finally:
            sys.argv = ["pytest"]
            migration.engine = None
            engine.dispose()

        check = create_engine(f"sqlite:///{db_file}", future=True)
        try:
            migration.engine = check
            assert migration.current_kinds() == (
                "rental",
                "deposit",
                "deposit_applied",
            )
        finally:
            migration.engine = None
            check.dispose()
