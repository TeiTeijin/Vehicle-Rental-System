"""Create APP_SETTINGS + USERS.must_change_password on older databases.

New databases get both from `Base.metadata.create_all` (init_db); this
only patches files created before the Settings tab existed.

Usage:
    python -m scripts.migrate_app_settings --database demo.db [--dry-run]
"""

from __future__ import annotations

import argparse

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url

#: Set by `main()`; module-level so tests can inject their own engine.
engine: Engine | None = None

APP_SETTINGS_SQLITE = """CREATE TABLE IF NOT EXISTS APP_SETTINGS (
    key VARCHAR(64) NOT NULL PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_by INTEGER NULL,
    updated_at DATETIME NOT NULL
)"""

APP_SETTINGS_MYSQL = """CREATE TABLE IF NOT EXISTS APP_SETTINGS (
    `key` VARCHAR(64) NOT NULL PRIMARY KEY,
    value TEXT NOT NULL,
    updated_by INTEGER NULL,
    updated_at DATETIME NOT NULL
)"""


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


def existing_tables() -> set[str]:
    return set(inspect(_engine()).get_table_names())


def existing_columns(table: str) -> set[str]:
    insp = inspect(_engine())
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def ensure_settings_table(dry_run: bool) -> bool:
    if "APP_SETTINGS" in existing_tables():
        print("  = APP_SETTINGS already exists")
        return True
    if dry_run:
        print("  + APP_SETTINGS WOULD CREATE")
        return False
    dialect = _engine().dialect.name
    ddl = APP_SETTINGS_MYSQL if dialect.startswith("mysql") else APP_SETTINGS_SQLITE
    with _engine().begin() as conn:
        conn.execute(text(ddl))
    print("  + APP_SETTINGS created")
    return True


def ensure_must_change(dry_run: bool) -> bool:
    cols = existing_columns("USERS")
    if not cols:
        print("  ! USERS does not exist yet -- run scripts/init_db.py first")
        return False
    if "must_change_password" in cols:
        print("  = USERS.must_change_password already exists")
        return True
    if dry_run:
        print("  + USERS.must_change_password WOULD ADD (BOOL NOT NULL DEFAULT FALSE)")
        return False
    dialect = _engine().dialect.name
    ddl = ("BOOL NOT NULL DEFAULT FALSE"
           if dialect.startswith("mysql") else "BOOLEAN NOT NULL DEFAULT 0")
    with _engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE USERS ADD COLUMN must_change_password {ddl}"))
    print("  + USERS.must_change_password added")
    return True


def verify() -> bool:
    ok = True
    if "APP_SETTINGS" not in existing_tables():
        print("  x APP_SETTINGS still missing")
        ok = False
    else:
        print("  = APP_SETTINGS is present")
    if "must_change_password" not in existing_columns("USERS"):
        print("  x USERS.must_change_password still missing")
        ok = False
    else:
        print("  = USERS.must_change_password is present")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True,
                        help="Target SQLite path or SQLAlchemy URL.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    url = resolve_url(args.database)
    print(f"Target: {describe(url)}")

    global engine
    engine = create_engine(url, future=True, pool_pre_ping=True)
    try:
        print("=== 1. APP_SETTINGS table ===")
        ensure_settings_table(args.dry_run)
        print()
        print("=== 2. USERS.must_change_password ===")
        ensure_must_change(args.dry_run)
        print()
        print("=== verification ===")
        if args.dry_run:
            print("  ~ dry run: nothing was changed")
        elif not verify():
            raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
