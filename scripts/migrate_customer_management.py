"""Add customer-management columns, booking override columns, notes table.

USERS += birthdate, emergency_contact, source, is_active, flag_reason,
  flagged_at, flagged_by, licence_front_path, licence_back_path
  (all NULL-able except is_active, which defaults True; existing rows keep
  working, no backfill except source -> 'walk_in').
BOOKING += flag_override_note, flag_override_by, flag_override_at.
New table CUSTOMER_NOTE (staff notes with authorship).

Usage:
    python scripts/migrate_customer_management.py --database demo.db
    python scripts/migrate_customer_management.py --database demo.db --dry-run
"""
from __future__ import annotations

import argparse

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url

TABLE = "USERS"
BOOKING_TABLE = "BOOKING"
NOTE_TABLE = "CUSTOMER_NOTE"

#: Set by `main()`; injectable in tests.
engine: Engine | None = None

_NEW_USER_COLUMNS: dict[str, dict[str, str]] = {
    "mysql": {
        "birthdate": "DATE NULL",
        "emergency_contact": "VARCHAR(255) NULL",
        "source": "ENUM('walk_in','online') NULL",
        "is_active": "BOOL NOT NULL DEFAULT TRUE",
        "flag_reason": "VARCHAR(255) NULL",
        "flagged_at": "DATETIME NULL",
        "flagged_by": "INTEGER NULL",
        "licence_front_path": "VARCHAR(255) NULL",
        "licence_back_path": "VARCHAR(255) NULL",
    },
    "sqlite": {
        "birthdate": "DATE",
        "emergency_contact": "VARCHAR(255)",
        "source": "VARCHAR(10)",
        "is_active": "BOOLEAN NOT NULL DEFAULT TRUE",
        "flag_reason": "VARCHAR(255)",
        "flagged_at": "DATETIME",
        "flagged_by": "INTEGER",
        "licence_front_path": "VARCHAR(255)",
        "licence_back_path": "VARCHAR(255)",
    },
}

_NEW_BOOKING_COLUMNS: dict[str, dict[str, str]] = {
    "mysql": {
        "flag_override_note": "VARCHAR(255) NULL",
        "flag_override_by": "INTEGER NULL",
        "flag_override_at": "DATETIME NULL",
    },
    "sqlite": {
        "flag_override_note": "VARCHAR(255)",
        "flag_override_by": "INTEGER",
        "flag_override_at": "DATETIME",
    },
}

_NOTE_DDL = {
    "mysql": f"""CREATE TABLE IF NOT EXISTS {NOTE_TABLE} (
        note_id INTEGER NOT NULL AUTO_INCREMENT,
        customer_id INTEGER NOT NULL,
        author_id INTEGER NULL,
        note VARCHAR(1000) NOT NULL,
        created_at DATETIME NOT NULL,
        PRIMARY KEY (note_id),
        FOREIGN KEY(customer_id) REFERENCES USERS (user_id),
        FOREIGN KEY(author_id) REFERENCES USERS (user_id)
    )""",
    "sqlite": f"""CREATE TABLE IF NOT EXISTS {NOTE_TABLE} (
        note_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL REFERENCES USERS (user_id),
        author_id INTEGER NULL REFERENCES USERS (user_id),
        note VARCHAR(1000) NOT NULL,
        created_at DATETIME NOT NULL
    )""",
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


def _columns(table: str) -> set[str]:
    inspector = inspect(_engine())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _add_columns(table: str, specs: dict[str, str], present: set[str], dry_run: bool) -> set[str]:
    if not present and table in ("USERS", "BOOKING"):
        print(f"  ! {table} does not exist yet -- run scripts/init_db.py first")
        return present
    for column, ddl in specs.items():
        if column in present:
            print(f"  = {table}.{column:<20} already exists, skipping")
            continue
        if dry_run:
            print(f"  + {table}.{column:<20} WOULD ADD  ({ddl})")
            continue
        with _engine().begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        print(f"  + {table}.{column:<20} added       ({ddl})")
        present.add(column)
    return present


def _backfill_source(dry_run: bool) -> None:
    if "source" not in _columns(TABLE):
        print("  ~ source column not present yet; nothing to backfill")
        return
    with _engine().connect() as conn:
        pending = conn.execute(
            text(f"SELECT COUNT(*) FROM {TABLE} WHERE source IS NULL")
        ).scalar_one()
    if not pending:
        print("  = every user already has a source")
        return
    if dry_run:
        print(f"  ~ would backfill source='walk_in' on {pending} row(s)")
        return
    with _engine().begin() as conn:
        conn.execute(text(f"UPDATE {TABLE} SET source = 'walk_in' WHERE source IS NULL"))
    print(f"  ~ backfilled source='walk_in' on {pending} row(s)")


def _create_notes(dry_run: bool) -> bool:
    if NOTE_TABLE in inspect(_engine()).get_table_names():
        print(f"  = {NOTE_TABLE} already exists, skipping")
        return True
    if dry_run:
        print(f"  + {NOTE_TABLE} WOULD CREATE")
        return False
    with _engine().begin() as conn:
        conn.execute(text(_NOTE_DDL[_dialect()]))
    print(f"  + {NOTE_TABLE} created")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    url = resolve_url(args.database)
    print(f"Target: {describe(url)}")
    print()

    global engine
    engine = create_engine(url, future=True, pool_pre_ping=True)
    try:
        print("=== 1. add USERS columns ===")
        users = _add_columns(TABLE, _NEW_USER_COLUMNS[_dialect()], _columns(TABLE), args.dry_run)
        print()
        print("=== 2. backfill source ===")
        if users:
            _backfill_source(args.dry_run)
        print()
        print("=== 3. add BOOKING columns ===")
        bookings = _add_columns(
            BOOKING_TABLE, _NEW_BOOKING_COLUMNS[_dialect()], _columns(BOOKING_TABLE), args.dry_run
        )
        print()
        print("=== 4. create notes table ===")
        _create_notes(args.dry_run)
        print()
        print("=== verification ===")
        ok = True
        if args.dry_run:
            print("  ~ would change; nothing was written")
        else:
            checked_users = _columns(TABLE)
            missing = [c for c in _NEW_USER_COLUMNS[_dialect()] if c not in checked_users]
            checked_bookings = _columns(BOOKING_TABLE)
            missing += [c for c in _NEW_BOOKING_COLUMNS[_dialect()] if c not in checked_bookings]
            if NOTE_TABLE not in inspect(engine).get_table_names():
                missing.append(NOTE_TABLE)
            if missing:
                print(f"  x still missing: {', '.join(missing)}")
                ok = False
            else:
                print("  = all customer-management columns + notes table present")
    finally:
        engine.dispose()

    if args.dry_run:
        print()
        print("Dry run: nothing was changed.")
    elif not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
