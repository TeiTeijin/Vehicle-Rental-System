"""Add PAYMENT.kind ('rental' | 'deposit') and backfill existing rows.

The refundable security deposit is recorded as a deposit-kind payment row
(see payment_service.settle_deposit). Every payment written before this
column existed was rental money, so the backfill marks them all `rental`.

Usage:
    python -m scripts.migrate_payment_kind --database demo.db [--dry-run]
"""

from __future__ import annotations

import argparse

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.models import Payment

TABLE = Payment.__tablename__

#: Set by `main()` to the engine it resolved from `--database`. Module-level so a
#: test can inject its own: see tests/test_migrate_payment_kind.py, which points
#: this at a throwaway SQLite file rather than at whatever DATABASE_URL says.
engine: Engine | None = None

#: The values the model allows, in the order app/models/payment.py lists them.
KINDS: tuple[str, ...] = ("rental", "deposit")

_NEW_COLUMNS: dict[str, dict[str, str]] = {
    "mysql": {
        "kind": f"ENUM({','.join(repr(c) for c in KINDS)}) NOT NULL DEFAULT 'rental'",
    },
    "sqlite": {
        "kind": (
            f"VARCHAR(16) NOT NULL DEFAULT 'rental' CHECK (kind IN "
            f"({', '.join(repr(c) for c in KINDS)}))"
        ),
    },
}


def column_ddl(dialect: str) -> dict[str, str]:
    return dict(_NEW_COLUMNS.get(dialect, _NEW_COLUMNS["sqlite"]))


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


def existing_columns() -> set[str]:
    inspector = inspect(_engine())
    if TABLE not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(TABLE)}


def add_column(present: set[str], dry_run: bool) -> set[str]:
    if not present:
        print(f"  ! {TABLE} does not exist yet -- run scripts/init_db.py first")
        return present
    column, ddl = next(iter(column_ddl(_engine().dialect.name).items()))
    if column in present:
        print(f"  = {TABLE}.{column:<14} already exists, skipping")
        return present
    if dry_run:
        print(f"  + {TABLE}.{column:<14} WOULD ADD  ({ddl})")
        return present
    with _engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {column} {ddl}"))
    print(f"  + {TABLE}.{column:<14} added       ({ddl})")
    present.add(column)
    return present


def backfill(present: set[str], dry_run: bool) -> int:
    if not present or "kind" not in present:
        print("  ! kind is not present; nothing to backfill")
        return 0
    with _engine().connect() as conn:
        rows = conn.execute(
            text(f"SELECT COUNT(*) FROM {TABLE} WHERE kind IS NULL")
        ).scalar() or 0
    verb = "would backfill" if dry_run else "backfilled"
    print(f"  ~ {verb} kind='rental' on {rows} row(s)")
    if rows and not dry_run:
        with _engine().begin() as conn:
            conn.execute(text(f"UPDATE {TABLE} SET kind = 'rental' WHERE kind IS NULL"))
    if not rows:
        print("  = every row already has a kind")
    return rows


def verify(present: set[str]) -> bool:
    if "kind" not in present:
        print(f"  x {TABLE}.kind still missing")
        return False
    print(f"  = {TABLE}.kind is present")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
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

    global engine
    engine = create_engine(url, future=True, pool_pre_ping=True)

    try:
        print("=== 1. add column ===")
        present = add_column(existing_columns(), args.dry_run)

        print()
        print("=== 2. backfill existing rows ===")
        backfill(present, args.dry_run)

        print()
        print("=== verification ===")
        checked = existing_columns()
        ok = True
        if args.dry_run:
            if "kind" not in checked:
                print("  ~ would add kind; nothing was changed")
        else:
            ok = verify(checked)
    finally:
        engine.dispose()

    if args.dry_run:
        print()
        print("Dry run: nothing was changed.")
    elif not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
