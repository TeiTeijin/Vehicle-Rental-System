"""Bring an existing database up to the customer-app schema.

Adds, safely and idempotently:

* BRANCH                    -- pickup/return counters with opening hours
* PSGC reference tables     -- province and city/municipality rows used by
                               the sign-up address form
* BOOKING online columns    -- pickup/return datetimes, branches, payment
                               method, the price breakdown and the reference

Nothing is added to VEHICLE: the customer screens read the columns the counter
already maintains (make, model, year, seats, fuel, transmission, body style,
colour, mileage) and take photos from the CarImages API, so the live catalog is
never duplicated or rewritten.

Existing counter bookings are deliberately NOT backfilled: a booking with a
NULL ``pickup_dt`` keeps the counter's date-only semantics (see
``booking_service.booking_window``). Run with --dry-run first if you like.

    python -m scripts.migrate_customer_booking
"""

from __future__ import annotations

import argparse

from sqlalchemy import inspect, text

from app.database import engine
from app.models import Booking, Branch, PsgcCityMunicipality, PsgcProvince

#: (table, column, DDL fragment). Keep in step with the Column declarations in
#: app/models/*.py -- the ENUM lists especially.
NEW_COLUMNS: list[tuple[str, str, str]] = [
    (Booking.__tablename__, "pickup_dt", "DATETIME NULL"),
    (Booking.__tablename__, "return_dt", "DATETIME NULL"),
    (Booking.__tablename__, "pickup_branch_id", "INT NULL"),
    (Booking.__tablename__, "return_branch_id", "INT NULL"),
    (Booking.__tablename__, "payment_method", "ENUM('cash','card','gcash') NULL"),
    (Booking.__tablename__, "card_last4", "VARCHAR(4) NULL"),
    (Booking.__tablename__, "reference_code", "VARCHAR(16) NULL"),
    (Booking.__tablename__, "subtotal", "DECIMAL(10,2) NULL"),
    (Booking.__tablename__, "insurance_fee", "DECIMAL(10,2) NULL"),
    (Booking.__tablename__, "service_fee", "DECIMAL(10,2) NULL"),
    (Booking.__tablename__, "vat", "DECIMAL(10,2) NULL"),
    (Booking.__tablename__, "total", "DECIMAL(10,2) NULL"),
]


def existing_columns(table: str) -> set[str]:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def ddl_for_dialect(ddl: str) -> str:
    """Translate MySQL-only DDL fragments for SQLite.

    SQLite has no ENUM type; SQLAlchemy's model creates a VARCHAR with a
    CHECK constraint, but ALTER TABLE cannot add that constraint portably.
    The column still stores the same short strings.
    """
    if engine.dialect.name.startswith("sqlite") and ddl.upper().startswith("ENUM("):
        return "VARCHAR(16) NULL"
    return ddl


def create_branch_table(dry_run: bool) -> None:
    print("=== 1. BRANCH table ===")
    if Branch.__tablename__ in inspect(engine).get_table_names():
        print(f"  = {Branch.__tablename__} already exists, skipping")
        return
    if dry_run:
        print(f"  + {Branch.__tablename__} WOULD BE CREATED")
        return
    Branch.__table__.create(engine, checkfirst=True)
    print(f"  + {Branch.__tablename__} created")


def create_reference_tables(dry_run: bool) -> None:
    """Create the PSGC tables a legacy database may not have.

    The live database already ships the full PSGC reference set; a database
    built only from the staff schema does not. Creating the empty tables is
    enough for sign-up to load; the rows remain the operator's data.
    """
    print()
    print("=== 2. PSGC reference tables ===")
    for model in (PsgcProvince, PsgcCityMunicipality):
        name = model.__tablename__
        if name in inspect(engine).get_table_names():
            print(f"  = {name} already exists, skipping")
            continue
        if dry_run:
            print(f"  + {name} WOULD BE CREATED")
            continue
        model.__table__.create(engine, checkfirst=True)
        print(f"  + {name} created")


def add_columns(dry_run: bool) -> None:
    print()
    print("=== 3. BOOKING online columns ===")
    present: dict[str, set[str]] = {}
    for table, column, ddl in NEW_COLUMNS:
        if table not in present:
            present[table] = existing_columns(table)
        if not present[table]:
            print(f"  ! {table} does not exist yet -- run scripts/init_db.py first")
            continue
        if column in present[table]:
            print(f"  = {table}.{column:<18} already exists, skipping")
            continue
        column_ddl = ddl_for_dialect(ddl)
        if dry_run:
            print(f"  + {table}.{column:<18} WOULD ADD  ({column_ddl})")
        else:
            with engine.begin() as conn:
                conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column} {column_ddl}")
                )
            print(f"  + {table}.{column:<18} added       ({column_ddl})")
        present[table].add(column)


def report() -> None:
    print()
    print("=== current state ===")
    with engine.connect() as conn:
        try:
            branches = conn.execute(
                text("SELECT COUNT(*) FROM BRANCH")
            ).scalar_one()
            print(f"  branches: {branches}")
        except Exception:  # noqa: BLE001 - the table may legitimately be absent
            print("  branches: (no BRANCH table)")
    print("  next: python -m scripts.seed_customer_catalog")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the changes without applying them",
    )
    args = parser.parse_args()

    create_branch_table(args.dry_run)
    create_reference_tables(args.dry_run)
    add_columns(args.dry_run)
    report()


if __name__ == "__main__":
    main()
