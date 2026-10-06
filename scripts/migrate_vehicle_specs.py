from __future__ import annotations

import argparse
from datetime import datetime

from sqlalchemy import inspect, text, update

from app.database import engine
from app.models import Vehicle

TABLE = Vehicle.__tablename__

# Column -> DDL fragment. The ENUM lists must stay in step with the Column
# declarations in app/models/vehicle.py.
NEW_COLUMNS: list[tuple[str, str]] = [
    ("seats", "INT NULL"),
    ("transmission", "ENUM('Automatic','Manual') NULL"),
    ("fuel_type", "ENUM('Petrol','Diesel','Electric','Hybrid') NULL"),
    (
        "body_style",
        "ENUM('SUV','Sedan','Hatchback','MPV','Pickup','Underbone','Scooter') NULL",
    ),
]

# These are hand-written assumptions, NOT manufacturer data.
# Correct them if you have the real figures.
BACKFILL: dict[tuple[str, str], dict] = {
    ("Toyota", "Vios"): dict(seats=5, transmission="Automatic", fuel_type="Petrol", body_style="Sedan"),
    ("Honda", "Civic"): dict(seats=5, transmission="Manual", fuel_type="Petrol", body_style="Sedan"),
    ("Toyota", "Camry"): dict(seats=5, transmission="Automatic", fuel_type="Hybrid", body_style="Sedan"),
    ("Honda", "Click 125i"): dict(seats=2, transmission="Automatic", fuel_type="Petrol", body_style="Underbone"),
    ("Yamaha", "NMAX 155"): dict(seats=2, transmission="Automatic", fuel_type="Petrol", body_style="Scooter"),
}


def existing_columns() -> set[str]:
    inspector = inspect(engine)
    if TABLE not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(TABLE)}


def add_columns(present: set[str], dry_run: bool) -> set[str]:
    if not present:
        print(f"  ! {TABLE} does not exist yet -- run scripts/init_db.py first")
        return present

    for column, ddl in NEW_COLUMNS:
        if column in present:
            print(f"  = {TABLE}.{column:<12} already exists, skipping")
            continue
        if dry_run:
            print(f"  + {TABLE}.{column:<12} WOULD ADD  ({ddl})")
        else:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {column} {ddl}"))
            print(f"  + {TABLE}.{column:<12} added       ({ddl})")
        present.add(column)
    return present


def backfill(present: set[str], dry_run: bool) -> None:
    missing = [column for column, _ in NEW_COLUMNS if column not in present]
    if missing:
        print(f"  ! spec columns not present ({', '.join(missing)}); nothing to backfill")
        return

    for (make, model), spec in BACKFILL.items():
        if dry_run:
            print(f"  ~ would backfill {make} {model:<11} {spec}")
            continue

        # A Core UPDATE rather than MySQL's UPDATE...JOIN, so this runs on any
        # backend. Only touches rows that still have no specs.
        stmt = (
            update(Vehicle)
            .where(
                Vehicle.make == make,
                Vehicle.model == model,
                Vehicle.seats.is_(None),
            )
            .values(**spec)
        )
        with engine.begin() as conn:
            affected = conn.execute(stmt).rowcount
        if affected:
            print(f"  ~ backfilled {make} {model:<11} {spec}")
        else:
            print(f"  = {make} {model:<11} already has specs, skipping")


def show_table(present: set[str]) -> None:
    spec_cols = [column for column, _ in NEW_COLUMNS if column in present]
    wanted = ["vehicle_id", "make", "model", "year", "plate_number", "daily_rate", *spec_cols, "status"]

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT {', '.join(wanted)} FROM {TABLE} ORDER BY year DESC, vehicle_id"
            )
        ).fetchall()

    if not rows:
        print("  (no vehicles yet -- run scripts/seed_data.py)")
        return

    header = f"  {'id':<4}{'vehicle':<24}{'plate':<10}{'rate':>9}  status"
    print()
    print(header)
    print("  " + "-" * (len(header) - 2))
    for row in rows:
        record = dict(zip(wanted, row))
        name = f"{record['make']} {record['model']} {record['year']}"
        specs = "  ".join(
            f"{column}={record[column]}" for column in spec_cols if record[column] is not None
        )
        print(
            f"  {record['vehicle_id']:<4}{name:<24}{record['plate_number']:<10}"
            f"{float(record['daily_rate']):>9,.2f}  {record['status']}"
            + (f"   [{specs}]" if specs else "")
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print changes without applying them",
    )
    args = parser.parse_args()

    print("=== 1. add columns ===")
    present = add_columns(existing_columns(), args.dry_run)

    print()
    print("=== 2. backfill existing rows ===")
    backfill(present, args.dry_run)

    print()
    print(f"=== current {TABLE} table ===")
    show_table(present)


if __name__ == "__main__":
    main()
