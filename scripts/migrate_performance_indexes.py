"""Create the composite indexes used by the staff performance work.

This migration is additive and safe to re-run: it only creates indexes, skips
tables or columns that are not present, and never rewrites rows. It requires an
explicit target so it cannot accidentally touch the live database from the
project's default URL.

Usage:
    python -m scripts.migrate_performance_indexes --database demo.db [--dry-run]
    python -m scripts.migrate_performance_indexes --database mysql+pymysql://...
"""

from __future__ import annotations

import argparse

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url

#: (table, index name, columns), in the order the reports and pages use them.
#:
#: Keep this list additive. Existing indexes with a different name but the
#: same column order are treated as equivalent, so a hand-created index does
#: not get a duplicate.
INDEXES: list[tuple[str, str, list[str]]] = [
    # Takings aggregate and the Payments ledger's paid/kind filter.
    ("PAYMENT", "ix_payment_status_kind_paid", ["status", "kind", "paid_at"]),
    # Outstanding balances and booking_balance's per-booking payment lookup.
    ("PAYMENT", "ix_payment_booking_status_kind", ["booking_id", "status", "kind"]),
    # Fleet's next-due query and booking overlap checks.
    ("BOOKING", "ix_booking_vehicle_status_end", ["vehicle_id", "status", "end_date"]),
    # Bookings list: status filter plus newest-first ordering.
    ("BOOKING", "ix_booking_status_created", ["status", "created_at"]),
    # Fleet maintenance blocking and maintenance history lookups.
    (
        "MAINTENANCE_RECORD",
        "ix_maintenance_vehicle_status_dates",
        ["vehicle_id", "status", "start_date", "end_date"],
    ),
    # Inspections page newest-first list.
    ("INSPECTION_REPORT", "ix_inspection_inspected_at", ["inspected_at"]),
    # Customer/staff role filters plus newest-first ordering.
    ("USERS", "ix_users_role_created", ["role", "created_at"]),
]

#: Set by `main()` to the engine resolved from `--database`. Tests inject their
#: own engine here rather than relying on the project's live URL.
engine: Engine | None = None


def _engine() -> Engine:
    if engine is None:
        raise RuntimeError("no target engine; call main() or set `engine` directly")
    return engine


def resolve_url(database: str) -> str:
    if "://" in database:
        return database
    return f"sqlite:///{database}"


def describe(url: str) -> str:
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "<unparseable target>"


def create_statement(table: str, name: str, columns: list[str]) -> str:
    return f"CREATE INDEX {name} ON {table} ({', '.join(columns)})"


def _tables() -> set[str]:
    return set(inspect(_engine()).get_table_names())


def _columns(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {column["name"] for column in inspect(_engine()).get_columns(table)}


def _index_columns(table: str) -> dict[str, tuple[str, ...]]:
    """Index name -> ordered column tuple, including unique constraints."""
    if table not in _tables():
        return {}
    found: dict[str, tuple[str, ...]] = {}
    inspector = inspect(_engine())
    for index in inspector.get_indexes(table):
        found[index["name"]] = tuple(index.get("column_names") or ())
    for unique in inspector.get_unique_constraints(table):
        if unique.get("name"):
            found[unique["name"]] = tuple(unique.get("column_names") or ())
    return found


def add_indexes(dry_run: bool) -> bool:
    """Create the missing indexes. Return True if at least one target is usable."""
    tables = _tables()
    usable = False
    for table, name, columns in INDEXES:
        if table not in tables:
            print(f"  ! {table} does not exist -- skipping {name}")
            continue
        missing = [column for column in columns if column not in _columns(table)]
        if missing:
            print(
                f"  ! {table} is missing {', '.join(missing)} -- skipping {name}"
            )
            continue
        usable = True
        existing = _index_columns(table)
        if name in existing:
            print(f"  = {name:<36} already exists")
            continue
        wanted = tuple(columns)
        equivalent = next(
            (index_name for index_name, index_columns in existing.items()
             if index_columns == wanted),
            None,
        )
        if equivalent is not None:
            print(
                f"  = {name:<36} equivalent index {equivalent} already exists"
            )
            continue
        if dry_run:
            print(
                f"  + {name:<36} WOULD CREATE on {table} "
                f"({', '.join(columns)})"
            )
            continue
        with _engine().begin() as conn:
            conn.execute(text(create_statement(table, name, columns)))
        print(f"  + {name:<36} created on {table} ({', '.join(columns)})")
    return usable


def verify() -> bool:
    ok = True
    for table, name, columns in INDEXES:
        if table not in _tables():
            print(f"  x {table} does not exist")
            ok = False
            continue
        if any(column not in _columns(table) for column in columns):
            print(f"  x {table} is missing a column for {name}")
            ok = False
            continue
        existing = _index_columns(table)
        if name in existing or tuple(columns) in existing.values():
            print(f"  = {name:<36} present")
        else:
            print(f"  x {name:<36} missing")
            ok = False
    return ok


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
        print("=== create performance indexes ===")
        usable = add_indexes(args.dry_run)
        if not usable:
            print(
                "No target table has all of its required columns; refusing "
                "to guess at a different schema."
            )
            raise SystemExit(1)
        print()
        print("=== verification ===")
        if args.dry_run:
            print("  ~ dry run; nothing was changed")
        elif not verify():
            raise SystemExit(1)
    finally:
        engine.dispose()

    if args.dry_run:
        print()
        print("Dry run: nothing was changed.")


if __name__ == "__main__":
    main()
