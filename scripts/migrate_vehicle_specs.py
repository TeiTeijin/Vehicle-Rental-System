"""Add hero spec columns to VEHICLE, backfill them, and seed one SUV.

Idempotent: every step checks current state first, so re-running is safe.

    python -m scripts.migrate_vehicle_specs --dry-run
    python -m scripts.migrate_vehicle_specs
"""

from __future__ import annotations

import argparse
from datetime import datetime

from sqlalchemy import text

from app.database import engine
from app.models import Vehicle

TABLE = Vehicle.__tablename__

# (column, MySQL ENUM/SCALAR DDL fragment)
NEW_COLUMNS: list[tuple[str, str]] = [
    ("seats", "INT NULL"),
    ("transmission", "ENUM('Automatic','Manual') NULL"),
    ("fuel_type", "ENUM('Petrol','Diesel','Electric','Hybrid') NULL"),
    (
        "body_style",
        "ENUM('SUV','Sedan','Hatchback','MPV','Pickup','Underbone','Scooter') NULL",
    ),
]

# Spec values below are hand-written assumptions, NOT manufacturer data.
# Correct them if you have the real figures.
BACKFILL: dict[tuple[str, str], dict] = {
    ("Toyota", "Vios"): dict(seats=5, transmission="Automatic", fuel_type="Petrol", body_style="Sedan"),
    ("Honda", "Civic"): dict(seats=5, transmission="Manual", fuel_type="Petrol", body_style="Sedan"),
    ("Toyota", "Camry"): dict(seats=5, transmission="Automatic", fuel_type="Hybrid", body_style="Sedan"),
    ("Honda", "Click 125i"): dict(seats=2, transmission="Automatic", fuel_type="Petrol", body_style="Underbone"),
    ("Yamaha", "NMAX 155"): dict(seats=2, transmission="Automatic", fuel_type="Petrol", body_style="Scooter"),
}

SHOWCASE_SUV = dict(
    make="Toyota",
    model="Fortuner",
    year=2023,
    plate_number="FOR1234",
    daily_rate="4500.00",
    mileage=15000,
    seats=7,
    transmission="Automatic",
    fuel_type="Diesel",
    body_style="SUV",
)


def existing_columns() -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
            ),
            {"table": TABLE},
        )
        return {r[0] for r in rows}


def add_columns(present: set[str], dry_run: bool) -> set[str]:
    for column, ddl in NEW_COLUMNS:
        if column in present:
            print(f"  = {TABLE}.{column:<12} already exists, skipping")
            continue
        sql = f"ALTER TABLE {TABLE} ADD COLUMN {column} {ddl}"
        if dry_run:
            print(f"  + {TABLE}.{column:<12} WOULD ADD  ({ddl})")
        else:
            with engine.begin() as conn:
                conn.execute(text(sql))
            print(f"  + {TABLE}.{column:<12} added       ({ddl})")
        present.add(column)
    return present


def car_category_id() -> int:
    with engine.connect() as conn:
        value = conn.execute(
            text("SELECT category_id FROM VEHICLE_CATEGORY WHERE category_name = 'Car'")
        ).scalar()
    if value is None:
        raise SystemExit("no 'Car' row in VEHICLE_CATEGORY - seed the database first")
    return int(value)


def backfill(dry_run: bool) -> None:
    sql = (
        "UPDATE VEHICLE v JOIN VEHICLE_CATEGORY c ON c.category_id = v.category_id "
        "SET v.seats = :seats, v.transmission = :transmission, "
        "v.fuel_type = :fuel_type, v.body_style = :body_style "
        "WHERE v.make = :make AND v.model = :model AND v.seats IS NULL"
    )
    for make, model in BACKFILL:
        spec = BACKFILL[(make, model)]
        if dry_run:
            print(f"  ~ would backfill {make} {model:<11} {spec}")
            continue
        with engine.begin() as txn:
            result = txn.execute(text(sql), dict(spec, make=make, model=model))
        affected = result.rowcount
        if affected:
            print(f"  ~ backfilled {make} {model:<11} {spec}")
        else:
            print(f"  = {make} {model:<11} already has specs, skipping")


def seed_suv(dry_run: bool) -> None:
    with engine.connect() as conn:
        exists = conn.execute(
            text(f"SELECT vehicle_id FROM {TABLE} WHERE plate_number = :plate"),
            {"plate": SHOWCASE_SUV["plate_number"]},
        ).first()
    if exists:
        print(f"  * {SHOWCASE_SUV['make']} {SHOWCASE_SUV['model']} already present (id={exists[0]}), skipping")
        return

    payload = dict(SHOWCASE_SUV)
    if dry_run:
        print(f"  + would insert {payload['make']} {payload['model']} {payload['year']} plate={payload['plate_number']}")
        return

    payload["category_id"] = car_category_id()
    payload["status"] = "available"
    payload["created_at"] = datetime.now()
    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {TABLE} "
                "(category_id, make, model, year, plate_number, daily_rate, mileage, "
                "seats, transmission, fuel_type, body_style, status, created_at) "
                "VALUES (:category_id, :make, :model, :year, :plate_number, :daily_rate, "
                ":mileage, :seats, :transmission, :fuel_type, :body_style, :status, :created_at)"
            ),
            payload,
        )
    print(f"  + inserted {payload['make']} {payload['model']} {payload['year']} plate={payload['plate_number']}")


def show_table() -> None:
    present = existing_columns()
    spec_cols = [c for c, _ in NEW_COLUMNS if c in present]
    wanted = ["vehicle_id", "make", "model", "year", "plate_number", "daily_rate", *spec_cols, "status"]
    select = ", ".join(f"v.{c}" for c in wanted)

    with engine.connect() as conn:
        rows = conn.execute(
            text(f"SELECT {select} FROM {TABLE} v ORDER BY v.year DESC, v.vehicle_id")
        ).fetchall()

    if not spec_cols:
        print("  (spec columns not present yet - run without --dry-run to add them)")

    pad = {c: max(len(c), 5) for c in spec_cols}
    header = f"  {'id':<4}{'vehicle':<24}{'plate':<10}{'rate':>9}  " + "".join(
        f"{c:<{pad[c] + 2}}" for c in spec_cols
    ) + "status"
    print()
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in rows:
        row = dict(zip(wanted, r))
        name = f"{row['make']} {row['model']} {row['year']}"
        spec = "".join(
            f"{str(row[c] or '-'):<{pad[c] + 2}}" for c in spec_cols
        )
        print(
            f"  {row['vehicle_id']:<4}{name:<24}{row['plate_number']:<10}"
            f"{float(row['daily_rate']):>9,.2f}  {spec}{row['status']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print changes without applying them")
    args = parser.parse_args()

    print("=== 1. add columns ===")
    add_columns(existing_columns(), args.dry_run)

    print()
    print("=== 2. backfill existing rows ===")
    backfill(args.dry_run)

    print()
    print("=== 3. seed showcase SUV ===")
    seed_suv(args.dry_run)

    print()
    print("=== current VEHICLE table ===")
    show_table()


if __name__ == "__main__":
    main()
