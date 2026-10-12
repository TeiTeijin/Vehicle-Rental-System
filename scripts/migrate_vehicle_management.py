"""Add vehicle-management columns and the rate-log table.

Adds to VEHICLE: color, registration_expiry, insurance_expiry, retired_at
(all NULL-able, so no backfill needed). The `retired` status needs no DDL:
the demo/live tables store status as plain VARCHAR (no CHECK constraint),
so the new enum value is app-validated only.

Creates VEHICLE_RATE_LOG (audit trail for per-vehicle and bulk rate
writes -- extensions recompute at current rates, so the log explains why
two identical extensions can cost different amounts).

Usage:
    python scripts/migrate_vehicle_management.py --database demo.db
    python scripts/migrate_vehicle_management.py --database demo.db --dry-run
"""
from __future__ import annotations

import argparse

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.models import Vehicle

TABLE = Vehicle.__tablename__
RATE_LOG_TABLE = "VEHICLE_RATE_LOG"

#: Set by `main()` to the engine it resolved from `--database`. Module-level so a
#: test can inject its own: see tests/test_migrate_vehicle_management.py.
engine: Engine | None = None

_NEW_VEHICLE_COLUMNS: dict[str, dict[str, str]] = {
    "mysql": {
        "color": "VARCHAR(50) NULL",
        "registration_expiry": "DATE NULL",
        "insurance_expiry": "DATE NULL",
        "retired_at": "DATE NULL",
    },
    "sqlite": {
        "color": "VARCHAR(50)",
        "registration_expiry": "DATE",
        "insurance_expiry": "DATE",
        "retired_at": "DATE",
    },
}

_RATE_LOG_DDL = {
    "mysql": f"""CREATE TABLE IF NOT EXISTS {RATE_LOG_TABLE} (
        rate_log_id INTEGER NOT NULL AUTO_INCREMENT,
        vehicle_id INTEGER NOT NULL,
        old_rate DECIMAL(10, 2) NOT NULL,
        new_rate DECIMAL(10, 2) NOT NULL,
        changed_by INTEGER NULL,
        changed_at DATETIME NOT NULL,
        is_bulk BOOL NOT NULL DEFAULT FALSE,
        note VARCHAR(255) NULL,
        PRIMARY KEY (rate_log_id),
        FOREIGN KEY(vehicle_id) REFERENCES VEHICLE (vehicle_id),
        FOREIGN KEY(changed_by) REFERENCES USERS (user_id)
    )""",
    "sqlite": f"""CREATE TABLE IF NOT EXISTS {RATE_LOG_TABLE} (
        rate_log_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        vehicle_id INTEGER NOT NULL REFERENCES VEHICLE (vehicle_id),
        old_rate DECIMAL(10, 2) NOT NULL,
        new_rate DECIMAL(10, 2) NOT NULL,
        changed_by INTEGER NULL REFERENCES USERS (user_id),
        changed_at DATETIME NOT NULL,
        is_bulk BOOLEAN NOT NULL DEFAULT FALSE,
        note VARCHAR(255) NULL
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


def existing_columns() -> set[str]:
    inspector = inspect(_engine())
    if TABLE not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(TABLE)}


def has_rate_log() -> bool:
    return RATE_LOG_TABLE in inspect(_engine()).get_table_names()


def add_columns(present: set[str], dry_run: bool) -> set[str]:
    if not present:
        print(f"  ! {TABLE} does not exist yet -- run scripts/init_db.py first")
        return present
    for column, ddl in _NEW_VEHICLE_COLUMNS[_dialect()].items():
        if column in present:
            print(f"  = {TABLE}.{column:<20} already exists, skipping")
            continue
        if dry_run:
            print(f"  + {TABLE}.{column:<20} WOULD ADD  ({ddl})")
            continue
        with _engine().begin() as conn:
            conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {column} {ddl}"))
        print(f"  + {TABLE}.{column:<20} added       ({ddl})")
        present.add(column)
    return present


def create_rate_log(dry_run: bool) -> bool:
    if has_rate_log():
        print(f"  = {RATE_LOG_TABLE} already exists, skipping")
        return True
    if dry_run:
        print(f"  + {RATE_LOG_TABLE} WOULD CREATE")
        return False
    with _engine().begin() as conn:
        conn.execute(text(_RATE_LOG_DDL[_dialect()]))
    print(f"  + {RATE_LOG_TABLE} created")
    return True


def verify(present: set[str]) -> bool:
    missing = [c for c in _NEW_VEHICLE_COLUMNS[_dialect()] if c not in present]
    if missing:
        print(f"  x {TABLE}.{', '.join(missing)} still missing")
        return False
    if not has_rate_log():
        print(f"  x {RATE_LOG_TABLE} still missing")
        return False
    print(f"  = {TABLE} columns + {RATE_LOG_TABLE} are present")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        required=True,
        help="Target SQLite path or SQLAlchemy URL. Required on purpose.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    url = resolve_url(args.database)
    print(f"Target: {describe(url)}")
    print()

    global engine
    engine = create_engine(url, future=True, pool_pre_ping=True)
    try:
        print("=== 1. add vehicle columns ===")
        present = add_columns(existing_columns(), args.dry_run)
        print()
        print("=== 2. create rate log table ===")
        create_rate_log(args.dry_run)
        print()
        print("=== verification ===")
        checked = existing_columns()
        ok = True
        if args.dry_run:
            pending = [c for c in _NEW_VEHICLE_COLUMNS[_dialect()] if c not in checked]
            if pending or not has_rate_log():
                print("  ~ would change; nothing was written")
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
