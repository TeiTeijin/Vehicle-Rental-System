from __future__ import annotations

import argparse
import re

from sqlalchemy import Engine, create_engine, inspect, select, text
from sqlalchemy.engine import make_url

from app.models import Vehicle

TABLE = Vehicle.__tablename__

#: Set by `main()` to the engine it resolved from `--database`. Module-level so a
#: test can inject its own: see tests/test_migrate_vehicle_class.py, which points
#: this at a throwaway SQLite file rather than at whatever DATABASE_URL says.
engine: Engine | None = None

#: The values the model allows, in the order app/models/vehicle.py lists them.
CLASSES: tuple[str, ...] = (
    "small",
    "medium",
    "suv",
    "van",
    "pickup",
    "truck",
    "motorcycle",
)

_NEW_COLUMNS: dict[str, dict[str, str]] = {
    "mysql": {
        "vehicle_class": f"ENUM({','.join(repr(c) for c in CLASSES)}) NULL",
        "engine_cc": "INT NULL",
    },
    "sqlite": {
        "vehicle_class": (
            f"VARCHAR(16) NULL CHECK (vehicle_class IN "
            f"({', '.join(repr(c) for c in CLASSES)}))"
        ),
        "engine_cc": "INTEGER NULL",
    },
}


def column_ddl(dialect: str) -> dict[str, str]:
    return dict(_NEW_COLUMNS.get(dialect, _NEW_COLUMNS["sqlite"]))


def new_columns(dialect: str) -> list[tuple[str, str]]:
    return list(column_ddl(dialect).items())

#: body_style -> vehicle_class. Absent classes get no backfill; see the module
#: docstring.
BODY_STYLE_TO_CLASS: dict[str, str] = {
    "Hatchback": "small",
    "Sedan": "small",
    "MPV": "van",
    "SUV": "suv",
    "Pickup": "pickup",
    "Underbone": "motorcycle",
    "Scooter": "motorcycle",
}

MIN_CC = 50
MAX_CC = 2000


# --------------------------------------------------------------------------
# Target resolution
# --------------------------------------------------------------------------


def resolve_url(database: str) -> str:
    if "://" in database:
        return database
    return f"sqlite:///{database}"


def describe(url: str) -> str:
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "<unparseable target>"


# --------------------------------------------------------------------------
# Inspection
# --------------------------------------------------------------------------


def _dialect() -> str:
    return _engine().dialect.name


def existing_columns() -> set[str]:
    inspector = inspect(_engine())
    if TABLE not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(TABLE)}


def _engine() -> Engine:
    if engine is None:
        raise RuntimeError("no target engine; call main() or set `engine` directly")
    return engine


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


def parse_cc(model: str | None) -> int | None:
    if not model:
        return None
    best: int | None = None
    for token in re.findall(r"\d+", model):
        value = int(token)
        if MIN_CC <= value <= MAX_CC:
            best = value
    return best


def class_for_body_style(body_style: str | None) -> str | None:
    if not body_style:
        return None
    return BODY_STYLE_TO_CLASS.get(body_style)


def plan_backfill(rows) -> tuple[list[tuple[int, str]], list[tuple[int, int]]]:
    class_updates: list[tuple[int, str]] = []
    cc_updates: list[tuple[int, int]] = []

    for vehicle_id, model, body_style, existing_class, existing_cc in rows:
        mapped = class_for_body_style(body_style)

        if existing_class is None and mapped is not None:
            class_updates.append((vehicle_id, mapped))

        if existing_cc is None and (mapped == "motorcycle" or existing_class == "motorcycle"):
            cc = parse_cc(model)
            if cc is not None:
                cc_updates.append((vehicle_id, cc))

    return class_updates, cc_updates


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------


def add_columns(present: set[str], dry_run: bool) -> set[str]:
    if not present:
        print(f"  ! {TABLE} does not exist yet -- run scripts/init_db.py first")
        return present

    for column, ddl in new_columns(_dialect()):
        if column in present:
            print(f"  = {TABLE}.{column:<14} already exists, skipping")
            continue
        if dry_run:
            print(f"  + {TABLE}.{column:<14} WOULD ADD  ({ddl})")
            continue
        with _engine().begin() as conn:
            conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {column} {ddl}"))
        print(f"  + {TABLE}.{column:<14} added       ({ddl})")
        present.add(column)
    return present


def _read_rows(present: set[str]):
    fields = ["vehicle_id", "model", "body_style"]
    for column in ("vehicle_class", "engine_cc"):
        if column in present:
            fields.append(column)
        else:
            fields.append("NULL")

    with _engine().connect() as conn:
        return conn.execute(
            text(f"SELECT {', '.join(fields)} FROM {TABLE}")
        ).all()


def backfill(present: set[str], dry_run: bool, planned: set[str] | None = None) -> dict[str, int]:
    counts = {"vehicle_class": 0, "engine_cc": 0}

    # An empty column set means the table itself is gone, which `add_columns` has
    # already reported. `planned` would otherwise paper over that and send a
    # SELECT at a table that does not exist.
    if not present:
        return counts

    missing = [name for name in column_ddl(_dialect()) if name not in (planned or present)]
    if missing:
        print(f"  ! columns not present ({', '.join(missing)}); nothing to backfill")
        return counts

    rows = _read_rows(present)
    class_updates, cc_updates = plan_backfill(rows)

    if not dry_run:
        with _engine().begin() as conn:
            for vehicle_id, mapped in class_updates:
                conn.execute(
                    text(
                        f"UPDATE {TABLE} SET vehicle_class = :value "
                        "WHERE vehicle_id = :id AND vehicle_class IS NULL"
                    ),
                    {"value": mapped, "id": vehicle_id},
                )
            for vehicle_id, cc in cc_updates:
                conn.execute(
                    text(
                        f"UPDATE {TABLE} SET engine_cc = :value "
                        "WHERE vehicle_id = :id AND engine_cc IS NULL"
                    ),
                    {"value": cc, "id": vehicle_id},
                )

    counts["vehicle_class"] = len(class_updates)
    counts["engine_cc"] = len(cc_updates)

    verb = "would backfill" if dry_run else "backfilled"
    print(f"  ~ {verb} vehicle_class on {counts['vehicle_class']} row(s)")
    print(f"  ~ {verb} engine_cc on {counts['engine_cc']} row(s)")
    if not class_updates and not cc_updates:
        print("  = every row already has both columns populated")
    return counts


def show_distribution(present: set[str]) -> None:
    if "vehicle_class" not in present:
        print(f"  ! {TABLE}.vehicle_class is not present; no distribution to show")
        return
    with _engine().connect() as conn:
        class_rows = conn.execute(
            text(
                f"SELECT COALESCE(vehicle_class, '(null)'), COUNT(*) FROM {TABLE} "
                "GROUP BY vehicle_class ORDER BY 2 DESC"
            )
        ).all()
        cc_rows = conn.execute(
            text(
                f"SELECT COUNT(*), COUNT(engine_cc) FROM {TABLE} "
                "WHERE vehicle_class = 'motorcycle'"
            )
        ).first()

    print()
    print("  vehicle_class distribution:")
    for name, count in class_rows:
        print(f"    {name:<14}{count:>5}")

    if cc_rows:
        total, with_cc = cc_rows
        print()
        print(f"  motorcycles: {total}, of which {with_cc} have engine_cc")
        if total and not with_cc:
            print("    ! no motorcycle matched a cc in its model name")


def verify(present: set[str]) -> bool:
    missing = [name for name in column_ddl(_dialect()) if name not in present]
    if missing:
        print(f"  x {TABLE}.{', '.join(missing)} still missing")
        return False
    print(f"  = {TABLE}.vehicle_class and {TABLE}.engine_cc are both present")
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
        print("Taking a backup of a real database is worth doing before this.")
        print("It only ever adds columns and fills blanks, so it cannot lose")
        print("data -- but a mysqldump removes all doubt.")
    print()

    global engine
    engine = create_engine(url, future=True, pool_pre_ping=True)

    try:
        print("=== 1. add columns ===")
        present = add_columns(existing_columns(), args.dry_run)

        print()
        print("=== 2. backfill existing rows ===")
        planned = set(present) | set(column_ddl(_dialect()))
        backfill(present, args.dry_run, planned=planned)

        print()
        print(f"=== {TABLE} distribution ===")
        show_distribution(present)

        print()
        print("=== verification ===")
        # Re-inspected rather than reusing `present`: on a real run that is the
        # only honest check, since add_columns may have reported success on a
        # table the database quietly ignored.
        checked = existing_columns()
        ok = True
        if args.dry_run:
            pending = [name for name in column_ddl(_dialect()) if name not in checked]
            if pending:
                print(f"  ~ would add {', '.join(pending)}; nothing was changed")
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
