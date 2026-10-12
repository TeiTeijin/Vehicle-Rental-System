"""Grow MAINTENANCE_RECORD into the fleet service log.

What changes and why (see app/services/maintenance_service.py):

- `status` gains `in_progress` (replacing the confusing `ongoing`, which
  also means "out on rent" for bookings) and `cancelled`, so a cancelled
  job stays on the log instead of being deleted.
- `end_date` and `cost` become nullable: both stay blank until the work
  is done; `estimate_cost` carries the quote while a job is open.
- New columns: `maintenance_type`, `odometer`, `next_due_date`,
  `next_due_odometer`, `shop`, `logged_by`, `booking_id`,
  `cancel_reason`, `created_at`.

SQLite cannot MODIFY a column, so there the table is rebuilt (copy with
the status remap); MySQL uses ALTERs. Either way the run is idempotent.

Usage:
    python -m scripts.migrate_maintenance_lifecycle --database demo.db [--dry-run]
"""

from __future__ import annotations

import argparse

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.models import Maintenance_Record

TABLE = Maintenance_Record.__tablename__

#: Set by `main()` to the engine it resolved from `--database`. Module-level so a
#: test can inject its own: see tests/test_migrate_maintenance_lifecycle.py.
engine: Engine | None = None

STATUSES: tuple[str, ...] = ("scheduled", "in_progress", "completed", "cancelled")

TYPES: tuple[str, ...] = (
    "routine",
    "repair",
    "tire_brake",
    "inspection_registration",
    "cleaning",
    "damage_repair",
)

_NEW_COLUMNS: dict[str, dict[str, str]] = {
    "mysql": {
        "maintenance_type": f"ENUM({','.join(repr(c) for c in TYPES)}) NOT NULL DEFAULT 'routine'",
        "estimate_cost": "DECIMAL(10,2) NULL",
        "odometer": "INTEGER NULL",
        "next_due_date": "DATE NULL",
        "next_due_odometer": "INTEGER NULL",
        "shop": "VARCHAR(128) NULL",
        "logged_by": "INTEGER NULL",
        "booking_id": "INTEGER NULL",
        "cancel_reason": "VARCHAR(255) NULL",
        "created_at": "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "sqlite": {
        "maintenance_type": (
            f"VARCHAR(32) NOT NULL DEFAULT 'routine' CHECK (maintenance_type IN "
            f"({', '.join(repr(c) for c in TYPES)}))"
        ),
        "estimate_cost": "DECIMAL(10,2)",
        "odometer": "INTEGER",
        "next_due_date": "DATE",
        "next_due_odometer": "INTEGER",
        "shop": "VARCHAR(128)",
        "logged_by": "INTEGER",
        "booking_id": "INTEGER",
        "cancel_reason": "VARCHAR(255)",
        "created_at": "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
}

_NEW_COLUMNS_SQLITE_REBUILD = """
CREATE TABLE MAINTENANCE_RECORD_NEW (
    maintenance_id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL,
    description VARCHAR(255) NOT NULL,
    maintenance_type VARCHAR(32) NOT NULL DEFAULT 'routine'
        CHECK (maintenance_type IN
        ('routine', 'repair', 'tire_brake', 'inspection_registration',
         'cleaning', 'damage_repair')),
    cost DECIMAL(10,2),
    estimate_cost DECIMAL(10,2),
    start_date DATE NOT NULL,
    end_date DATE,
    status VARCHAR(20) NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled', 'in_progress', 'completed', 'cancelled')),
    odometer INTEGER,
    next_due_date DATE,
    next_due_odometer INTEGER,
    shop VARCHAR(128),
    logged_by INTEGER,
    booking_id INTEGER,
    cancel_reason VARCHAR(255),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_REBUILD_COPY = """
INSERT INTO MAINTENANCE_RECORD_NEW
    (maintenance_id, vehicle_id, description, maintenance_type,
     cost, estimate_cost, start_date, end_date, status,
     odometer, next_due_date, next_due_odometer, shop, logged_by,
     booking_id, cancel_reason, created_at)
SELECT maintenance_id, vehicle_id, description, 'routine',
     cost, NULL, start_date, end_date,
     CASE status WHEN 'ongoing' THEN 'in_progress' ELSE status END,
     NULL, NULL, NULL, NULL, NULL,
     NULL, NULL, CURRENT_TIMESTAMP
FROM MAINTENANCE_RECORD
"""


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


def _column_nullable(name: str) -> bool | None:
    for column in inspect(_engine()).get_columns(TABLE):
        if column["name"] == name:
            return column.get("nullable", True)
    return None


def _ongoing_rows() -> int:
    with _engine().connect() as conn:
        return (
            conn.execute(
                text(f"SELECT COUNT(*) FROM {TABLE} WHERE status = 'ongoing'")
            ).scalar()
            or 0
        )


def needs_rebuild() -> bool:
    """True when the table still has the old shape: new columns missing,
    `ongoing` rows present, or end_date/cost still NOT NULL."""
    present = existing_columns()
    if not present:
        return False
    if any(col not in present for col in column_ddl("sqlite")):
        return True
    if _ongoing_rows():
        return True
    if _column_nullable("end_date") is False:
        return True
    if _column_nullable("cost") is False:
        return True
    return False


def rebuild_sqlite(dry_run: bool) -> bool:
    if _engine().dialect.name != "sqlite":
        return False
    if not needs_rebuild():
        print(f"  = {TABLE} already has the lifecycle shape, skipping rebuild")
        return False
    if dry_run:
        print(f"  + {TABLE} WOULD REBUILD (new columns, nullable end_date/cost, "
              "ongoing -> in_progress)")
        return True
    with _engine().begin() as conn:
        conn.execute(text(_NEW_COLUMNS_SQLITE_REBUILD))
        conn.execute(text(_REBUILD_COPY))
        conn.execute(text(f"DROP TABLE {TABLE}"))
        conn.execute(text(f"ALTER TABLE {TABLE}_NEW RENAME TO {TABLE}"))
    print(f"  + {TABLE} rebuilt (new columns, nullable end_date/cost, "
          "ongoing -> in_progress)")
    return True


def add_columns(present: set[str], dry_run: bool) -> set[str]:
    if not present:
        print(f"  ! {TABLE} does not exist yet -- run scripts/init_db.py first")
        return present
    for column, ddl in column_ddl(_engine().dialect.name).items():
        if column in present:
            print(f"  = {TABLE}.{column:<16} already exists, skipping")
            continue
        if dry_run:
            print(f"  + {TABLE}.{column:<16} WOULD ADD  ({ddl})")
            continue
        with _engine().begin() as conn:
            conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {column} {ddl}"))
        print(f"  + {TABLE}.{column:<16} added       ({ddl})")
        present.add(column)
    return present


def relax_mysql(dry_run: bool) -> None:
    if _engine().dialect.name != "mysql":
        return
    alters = [
        f"MODIFY status ENUM({','.join(repr(c) for c in STATUSES)}) "
        "NOT NULL DEFAULT 'scheduled'",
        "MODIFY end_date DATE NULL",
        "MODIFY cost DECIMAL(10,2) NULL",
    ]
    for clause in alters:
        if dry_run:
            print(f"  + {TABLE} WOULD ALTER ({clause})")
            continue
        with _engine().begin() as conn:
            conn.execute(text(f"ALTER TABLE {TABLE} {clause}"))
        print(f"  + {TABLE} altered     ({clause})")


def remap_status(dry_run: bool) -> int:
    rows = _ongoing_rows()
    verb = "would remap" if dry_run else "remapped"
    print(f"  ~ {verb} ongoing -> in_progress on {rows} row(s)")
    if rows and not dry_run:
        with _engine().begin() as conn:
            conn.execute(
                text(f"UPDATE {TABLE} SET status = 'in_progress' "
                     "WHERE status = 'ongoing'")
            )
    return rows


def verify(present: set[str]) -> bool:
    ok = True
    for column in column_ddl("sqlite"):
        if column not in present:
            print(f"  x {TABLE}.{column} still missing")
            ok = False
    if ok:
        print(f"  = {TABLE} has every lifecycle column")
    if _ongoing_rows():
        print(f"  x {TABLE} still has 'ongoing' rows")
        ok = False
    else:
        print(f"  = no 'ongoing' rows remain")
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
        if _engine().dialect.name == "sqlite":
            print("=== 1. rebuild (sqlite cannot MODIFY columns) ===")
            rebuild_sqlite(args.dry_run)

        print()
        print("=== 2. add columns ===")
        present = add_columns(existing_columns(), args.dry_run)

        print()
        print("=== 3. relax nullability (mysql) ===")
        relax_mysql(args.dry_run)

        print()
        print("=== 4. remap ongoing -> in_progress ===")
        if present:
            remap_status(args.dry_run)
        else:
            print(f"  ! {TABLE} does not exist yet")

        print()
        print("=== verification ===")
        checked = existing_columns()
        ok = True
        if args.dry_run:
            print("  ~ dry run: nothing was changed")
        else:
            ok = verify(checked) if checked else False
    finally:
        engine.dispose()

    if args.dry_run:
        print()
        print("Dry run: nothing was changed.")
    elif not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
