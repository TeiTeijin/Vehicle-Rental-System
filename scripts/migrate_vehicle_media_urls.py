"""Widen VEHICLE_MEDIA image_url / model_3d_url so signed URLs fit.

CarImages signs an image URL with the API key and a rotating signature; the
result routinely runs past the old ``VARCHAR(255)``. MySQL truncates or
rejects it, which leaves a vehicle with a broken photo. Both columns become
``TEXT`` here, matching ``app/models/vehicle_media.py``.

SQLite cannot MODIFY a column, so there the table is rebuilt (copy, drop,
rename) exactly like ``migrate_maintenance_lifecycle.py``. Rows survive.

Usage:
    python scripts/migrate_vehicle_media_urls.py --database demo.db
    python scripts/migrate_vehicle_media_urls.py --database demo.db --dry-run
"""
from __future__ import annotations

import argparse

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url

TABLE = "VEHICLE_MEDIA"
WIDE_COLUMNS = ("image_url", "model_3d_url")

#: Every column of the current model. Used only by the SQLite rebuild.
_NEW_TABLE_SQLITE = f"""
CREATE TABLE {TABLE}_NEW (
    media_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL,
    source VARCHAR(255) NOT NULL,
    view_angle VARCHAR(255) NOT NULL,
    image_url TEXT NOT NULL,
    model_3d_url TEXT NOT NULL,
    is_watermarked BOOLEAN,
    cached_at DATETIME NOT NULL,
    FOREIGN KEY(vehicle_id) REFERENCES VEHICLE (vehicle_id)
)
"""

_REBUILD_COPY = f"""
INSERT INTO {TABLE}_NEW
    (media_id, vehicle_id, source, view_angle, image_url,
     model_3d_url, is_watermarked, cached_at)
SELECT media_id, vehicle_id, source, view_angle, image_url,
     model_3d_url, is_watermarked, cached_at
FROM {TABLE}
"""

#: Set by `main()`; injectable in tests.
engine: Engine | None = None


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


def table_exists() -> bool:
    return TABLE in inspect(_engine()).get_table_names()


def columns() -> dict[str, object]:
    if not table_exists():
        return {}
    return {
        column["name"]: column["type"]
        for column in inspect(_engine()).get_columns(TABLE)
    }


def is_wide(column_type: object | None) -> bool:
    """True for TEXT and other unbounded strings. VARCHAR(255) is not."""
    if column_type is None:
        return False
    return getattr(column_type, "length", None) is None


def needs_widening(present: dict[str, object]) -> bool:
    """True only when the table exists and a URL column is still narrow.

    An empty mapping means the table is absent (fresh database). That is not
    a widening job: `verify()` explains it, and no DDL may run.
    """
    if not present:
        return False
    return any(not is_wide(present.get(name)) for name in WIDE_COLUMNS)


def needs_rebuild() -> bool:
    return needs_widening(columns())


def rebuild_sqlite(dry_run: bool) -> bool:
    if _dialect() != "sqlite":
        return False
    if not needs_rebuild():
        print(f"  = {TABLE} URL columns are already TEXT, skipping rebuild")
        return False
    if dry_run:
        print(f"  + {TABLE} WOULD REBUILD (image_url, model_3d_url -> TEXT)")
        return True
    with _engine().begin() as conn:
        conn.execute(text(_NEW_TABLE_SQLITE))
        conn.execute(text(_REBUILD_COPY))
        conn.execute(text(f"DROP TABLE {TABLE}"))
        conn.execute(text(f"ALTER TABLE {TABLE}_NEW RENAME TO {TABLE}"))
    print(f"  + {TABLE} rebuilt (image_url, model_3d_url -> TEXT)")
    return True


def widen_mysql(dry_run: bool) -> None:
    if _dialect() != "mysql":
        return
    present = columns()
    if not present:
        # Missing table: `verify()` prints the "run scripts/init_db.py" hint.
        # Issuing ALTER here would crash with an opaque driver traceback.
        return
    if not needs_widening(present):
        print(f"  = {TABLE} URL columns are already TEXT, skipping")
        return
    for column in WIDE_COLUMNS:
        if is_wide(present.get(column)):
            print(f"  = {TABLE}.{column:<16} already TEXT, skipping")
            continue
        clause = f"MODIFY COLUMN {column} TEXT NOT NULL"
        if dry_run:
            print(f"  + {TABLE} WOULD ALTER ({clause})")
            continue
        with _engine().begin() as conn:
            conn.execute(text(f"ALTER TABLE {TABLE} {clause}"))
        print(f"  + {TABLE} altered     ({clause})")


def verify() -> bool:
    present = columns()
    if not present:
        print(f"  ! {TABLE} does not exist yet -- run scripts/init_db.py first")
        return False
    missing = [name for name in WIDE_COLUMNS if not is_wide(present.get(name))]
    if missing:
        print(f"  x still narrow: {', '.join(missing)}")
        return False
    print(f"  = {TABLE} accepts long signed URLs ({', '.join(WIDE_COLUMNS)} are TEXT)")
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
        print()
        if _dialect() == "sqlite":
            print("=== 1. rebuild (sqlite cannot MODIFY columns) ===")
            rebuild_sqlite(args.dry_run)
        else:
            print("=== 1. widen URL columns ===")
            widen_mysql(args.dry_run)

        print()
        print("=== verification ===")
        if args.dry_run:
            print("  ~ would change; nothing was written")
            ok = True
        else:
            ok = verify()
    finally:
        engine.dispose()

    if args.dry_run:
        print()
        print("Dry run: nothing was changed.")
    elif not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
