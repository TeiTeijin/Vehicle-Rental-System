"""Add guarantor columns and allow online accounts without a licence number.

USERS += guarantor_first_name, guarantor_last_name, guarantor_mobile (all
NULL-able). ``license_number`` becomes NULL-able because online sign-up now
collects a guarantor instead; the branch records the physical licence when the
customer picks a vehicle up. Existing unique indexes are left in place --
MySQL permits multiple NULL values in a UNIQUE index.

Usage:
    python -m scripts.migrate_guarantor_profile --database demo.db
    python -m scripts.migrate_guarantor_profile            # reads DATABASE_URL
    python -m scripts.migrate_guarantor_profile --dry-run
"""
from __future__ import annotations

import argparse

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url

TABLE = "USERS"

#: Set by `main()`; injectable in tests.
engine: Engine | None = None

_NEW_COLUMNS: dict[str, dict[str, str]] = {
    "mysql": {
        "guarantor_first_name": "VARCHAR(120) NULL",
        "guarantor_last_name": "VARCHAR(120) NULL",
        "guarantor_mobile": "VARCHAR(20) NULL",
    },
    "sqlite": {
        "guarantor_first_name": "VARCHAR(120)",
        "guarantor_last_name": "VARCHAR(120)",
        "guarantor_mobile": "VARCHAR(20)",
    },
}


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
    name = _engine().dialect.name
    return "mysql" if name.startswith("mysql") else "sqlite"


def _columns(table: str = TABLE) -> set[str]:
    inspector = inspect(_engine())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _column_info(table: str = TABLE) -> dict[str, dict]:
    inspector = inspect(_engine())
    if table not in inspector.get_table_names():
        return {}
    return {column["name"]: column for column in inspector.get_columns(table)}


def _add_columns(present: set[str], dry_run: bool) -> set[str]:
    if not present:
        print(f"  ! {TABLE} does not exist yet -- run scripts/init_db.py first")
        return present
    for column, ddl in _NEW_COLUMNS[_dialect()].items():
        if column in present:
            print(f"  = {TABLE}.{column:<22} already exists, skipping")
            continue
        if dry_run:
            print(f"  + {TABLE}.{column:<22} WOULD ADD  ({ddl})")
            continue
        with _engine().begin() as conn:
            conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {column} {ddl}"))
        print(f"  + {TABLE}.{column:<22} added       ({ddl})")
        present.add(column)
    return present


def _licence_nullable(dry_run: bool) -> bool:
    """Make USERS.license_number NULL-able where the database can do it.

    SQLite cannot change a column's nullability in place. New SQLite databases
    get the right shape from ``Base.metadata.create_all``; for an old SQLite
    file the migration reports what is still required and returns False.
    """
    info = _column_info().get("license_number")
    if info is None:
        print(f"  ! {TABLE}.license_number does not exist")
        return False
    if info.get("nullable", True):
        print(f"  = {TABLE}.license_number already NULL-able, skipping")
        return True
    if _dialect() == "sqlite":
        print(
            "  ! SQLite cannot change license_number to NULL-able in place.\n"
            "    Create the database with the current model, or rebuild the\n"
            "    table manually. The new guarantor columns were still added."
        )
        return False
    if dry_run:
        print(
            f"  ~ {TABLE}.license_number WOULD BECOME NULL-able "
            "(VARCHAR(12) NULL)"
        )
        return True
    with _engine().begin() as conn:
        conn.execute(
            text(
                f"ALTER TABLE {TABLE} "
                "MODIFY COLUMN license_number VARCHAR(12) NULL"
            )
        )
    print(f"  ~ {TABLE}.license_number is now NULL-able")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.database:
        url = resolve_url(args.database)
    else:
        from app.config import DATABASE_URL

        url = DATABASE_URL
    print(f"Target: {describe(url)}")
    print()

    global engine
    engine = create_engine(url, future=True, pool_pre_ping=True)
    try:
        print("=== 1. add USERS guarantor columns ===")
        users = _add_columns(_columns(), args.dry_run)
        print()
        print("=== 2. make the licence number optional for online sign-up ===")
        licence_ok = _licence_nullable(args.dry_run)
        print()
        print("=== verification ===")
        if args.dry_run:
            print("  ~ would change; nothing was written")
            ok = True
        else:
            missing = [c for c in _NEW_COLUMNS[_dialect()] if c not in _columns()]
            if missing:
                print(f"  x still missing: {', '.join(missing)}")
                ok = False
            elif not licence_ok:
                print(
                    "  ! guarantor columns are present; license_number still "
                    "requires a value on this database"
                )
                ok = False
            else:
                print("  = guarantor profile columns + optional licence present")
                ok = True
    finally:
        engine.dispose()

    if args.dry_run:
        print()
        print("Dry run: nothing was changed.")
    elif not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
