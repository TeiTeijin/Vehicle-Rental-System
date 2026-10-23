"""Widen PAYMENT.kind to include 'deposit_applied'.

`scripts/migrate_payment_kind.py` created the kind enum as
('rental', 'deposit'). Task 4 adds a third value, written when a held deposit
is applied to penalties at check-out (see payment_service.settle_deposit).
Held deposits remain liabilities and applied deposits remain ledger
adjustments: this migration only widens the column; it never rewrites a
historical payment row.

Usage:
    python -m scripts.migrate_deposit_applied --database demo.db [--dry-run]
"""

from __future__ import annotations

import argparse
import re

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.models import Payment

TABLE = Payment.__tablename__

#: Set by `main()` to the engine it resolved from `--database`. Module-level so a
#: test can inject its own: see tests/test_migrate_deposit_applied.py, which
#: points this at a throwaway SQLite file rather than at whatever DATABASE_URL
#: says.
engine: Engine | None = None

#: The enum this migration expects to find before widening it.
OLD_KINDS: tuple[str, ...] = ("rental", "deposit")

#: The widened enum, in the order app/models/payment.py lists them.
KINDS: tuple[str, ...] = ("rental", "deposit", "deposit_applied")

_NEW_COLUMNS: dict[str, dict[str, str]] = {
    "mysql": {
        "kind": (
            f"ENUM({','.join(repr(c) for c in KINDS)}) "
            "NOT NULL DEFAULT 'rental'"
        ),
    },
    "sqlite": {
        "kind": (
            f"VARCHAR(16) NOT NULL DEFAULT 'rental' CHECK (kind IN "
            f"({', '.join(repr(c) for c in KINDS)}))"
        ),
    },
}

#: SQLite stores the SQL text that created the table, including its CHECK
#: constraint, so widening an enum there means rebuilding that one table.
_KIND_CHECK = re.compile(r"CHECK\s*\(\s*kind\s+IN\s*\(([^)]*)\)", re.IGNORECASE)


def column_ddl(dialect: str) -> dict[str, str]:
    return dict(_NEW_COLUMNS.get(dialect, _NEW_COLUMNS["sqlite"]))


def kind_ddl(dialect: str) -> str:
    return column_ddl(dialect)["kind"]


def alter_statement(dialect: str) -> str:
    """The MySQL DDL that widens the column in place."""
    return f"ALTER TABLE {TABLE} MODIFY COLUMN kind {kind_ddl(dialect)}"


def resolve_url(database: str) -> str:
    if "://" in database:
        return database
    return f"sqlite:///{database}"


def describe(url: str) -> str:
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "<unparseable target>"


def _engine() -> Engine:
    if engine is None:
        raise RuntimeError("no target engine; call main() or set `engine` directly")
    return engine


def _dialect() -> str:
    return _engine().dialect.name


def existing_columns() -> set[str]:
    inspector = inspect(_engine())
    if TABLE not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(TABLE)}


def _sqlite_create_sql() -> str | None:
    with _engine().connect() as conn:
        return conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = :name"
            ),
            {"name": TABLE},
        ).scalar()


def _sqlite_kinds(sql: str) -> tuple[str, ...] | None:
    match = _KIND_CHECK.search(sql)
    if match is None:
        return None
    return tuple(re.findall(r"'([^']*)'", match.group(1)))


def current_kinds() -> tuple[str, ...] | None:
    """The values the database currently enforces, or None if unconstrained.

    MySQL's inspector exposes the native ENUM values directly. SQLite has no
    enum type, so its CHECK constraint (if any) is read back from the table's
    stored CREATE statement; a table built from the ORM has no such CHECK and
    therefore already accepts any string.
    """
    inspector = inspect(_engine())
    if TABLE not in inspector.get_table_names():
        return None
    columns = {column["name"]: column for column in inspector.get_columns(TABLE)}
    column = columns.get("kind")
    if column is None:
        return None
    enums = getattr(column["type"], "enums", None)
    if enums:
        return tuple(enums)
    if _dialect() == "sqlite":
        sql = _sqlite_create_sql()
        if sql:
            return _sqlite_kinds(sql)
    return None


def _widen_sqlite_create(sql: str) -> str:
    values = ", ".join(repr(kind) for kind in KINDS)
    return _KIND_CHECK.sub(
        lambda match: match.group(0).replace(match.group(1), values),
        sql,
    )


def _sqlite_rebuild() -> None:
    """Rebuild PAYMENT so its kind CHECK constraint includes the new value.

    SQLite cannot alter a column in place. The table, its rows and its indexes
    are copied, so historical payment rows survive byte-for-byte; only the
    CHECK expression changes.
    """
    inspector = inspect(_engine())
    columns = [column["name"] for column in inspector.get_columns(TABLE)]
    with _engine().connect() as conn:
        create_sql = _sqlite_create_sql()
        index_sqls = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type IN ('index', 'trigger') "
                    "AND tbl_name = :name AND sql IS NOT NULL"
                ),
                {"name": TABLE},
            )
            if row[0]
        ]
    if not create_sql:
        raise RuntimeError(f"{TABLE} has no stored CREATE statement to widen")

    widened_sql = _widen_sqlite_create(create_sql)
    if widened_sql == create_sql:
        raise RuntimeError(f"could not find kind's CHECK constraint in {TABLE}")

    old_table = f"{TABLE}__deposit_applied_old"
    column_list = ", ".join(f'"{column}"' for column in columns)
    with _engine().begin() as conn:
        conn.execute(text(f'ALTER TABLE "{TABLE}" RENAME TO "{old_table}"'))
        conn.execute(text(widened_sql))
        conn.execute(
            text(
                f'INSERT INTO "{TABLE}" ({column_list}) '
                f'SELECT {column_list} FROM "{old_table}"'
            )
        )
        conn.execute(text(f'DROP TABLE "{old_table}"'))
        for sql in index_sqls:
            conn.execute(text(sql))


def widen_kind(present: set[str], dry_run: bool) -> bool:
    if not present:
        print(f"  ! {TABLE} does not exist yet -- run scripts/init_db.py first")
        return False
    if "kind" not in present:
        print(f"  ! {TABLE}.kind does not exist -- run migrate_payment_kind first")
        return False

    kinds = current_kinds()
    if kinds is not None:
        if "deposit_applied" in kinds:
            print(f"  = {TABLE}.kind already accepts 'deposit_applied', skipping")
            return True
        if tuple(kinds) != OLD_KINDS:
            print(
                f"  x {TABLE}.kind is {tuple(kinds)}, expected {OLD_KINDS}; "
                "refusing to guess"
            )
            return False
        print(f"  = confirmed old enum {OLD_KINDS}")
    elif _dialect() == "sqlite":
        print(
            f"  = {TABLE}.kind has no SQLite CHECK constraint; it already "
            "accepts 'deposit_applied'"
        )
        return True

    ddl = kind_ddl(_dialect())
    if dry_run:
        print(f"  ~ {TABLE}.kind WOULD BE WIDENED ({ddl})")
        return True

    if _dialect() == "sqlite":
        _sqlite_rebuild()
    else:
        with _engine().begin() as conn:
            conn.execute(text(alter_statement(_dialect())))
    print(f"  + {TABLE}.kind widened ({ddl})")
    return True


def verify(present: set[str]) -> bool:
    if "kind" not in present:
        print(f"  x {TABLE}.kind is missing")
        return False
    kinds = current_kinds()
    if kinds is None:
        if _dialect() == "sqlite":
            print(f"  = {TABLE}.kind has no CHECK constraint; accepts every value")
            return True
        print(f"  x {TABLE}.kind is not an enum")
        return False
    if "deposit_applied" not in kinds:
        print(f"  x {TABLE}.kind is still {tuple(kinds)}")
        return False
    print(f"  = {TABLE}.kind accepts {tuple(kinds)}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--database",
        required=True,
        help=(
            "Target SQLite path or SQLAlchemy URL. Required on purpose: this "
            "project's default DATABASE_URL is the live Aiven instance."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print changes without applying them",
    )
    args = parser.parse_args()

    url = resolve_url(args.database)
    print(f"Target: {describe(url)}")

    if not args.dry_run:
        print("Take a fresh backup before applying this to a live database.")
        print("It only widens the enum and never rewrites payment rows.")
    print()

    global engine
    engine = create_engine(url, future=True, pool_pre_ping=True)

    try:
        print("=== 1. widen PAYMENT.kind ===")
        ok = widen_kind(existing_columns(), args.dry_run)

        print()
        print("=== verification ===")
        checked = existing_columns()
        if args.dry_run:
            if "kind" not in checked:
                print("  ~ would widen kind; nothing was changed")
        else:
            ok = verify(checked) and ok
    finally:
        engine.dispose()

    if args.dry_run:
        print()
        print("Dry run: nothing was changed.")
    elif not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
