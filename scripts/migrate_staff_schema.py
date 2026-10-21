from __future__ import annotations

import argparse

from sqlalchemy import inspect, text

from app.database import engine

#: table -> [(column, ddl, note)]
ADDITIONS: dict[str, list[tuple[str, str, str]]] = {
    "PAYMENT": [
        ("recorded_by", "INT NULL", "who took the money, for shift reconciliation"),
        ("reference_no", "VARCHAR(64) NULL", "GCash ref, auth code, or receipt number"),
        ("note", "VARCHAR(255) NULL", "free text; also carries refund explanations"),
        (
            "created_at",
            "DATETIME NULL",
            "did not exist; defaulted in the model and backfilled below",
        ),
    ],
    "BOOKING": [
        ("created_by", "INT NULL", "which member of staff took the booking"),
        ("cancel_reason", "VARCHAR(255) NULL", "why a booking was cancelled"),
        (
            "channel",
            "VARCHAR(8) NULL",
            "walked in at the counter or taken online; needed by the dashboard",
        ),
        (
            "flag_override_note",
            "VARCHAR(255) NULL",
            "staff-typed reason for proceeding past a customer flag. "
            "Data in scripts/migrate_customer_management.py",
        ),
        (
            "flag_override_by",
            "INT NULL",
            "who overrode the flag. Same: data lives elsewhere",
        ),
        (
            "flag_override_at",
            "DATETIME NULL",
            "when the flag was overridden. Same: data lives elsewhere",
        ),
    ],
    "USERS": [
        (
            "region_code",
            "VARCHAR(9) NULL",
            "PSGC region chosen at customer sign-up; NULL for counter/legacy rows",
        ),
        (
            "province_code",
            "VARCHAR(9) NULL",
            "PSGC province chosen at sign-up; NULL for Metro Manila or legacy rows",
        ),
        (
            "city_municipality_code",
            "VARCHAR(9) NULL",
            "PSGC city/municipality chosen at sign-up; NULL for legacy rows",
        ),
        (
            "barangay_code",
            "VARCHAR(9) NULL",
            "reserved for a barangay picker; NULL until the form asks for one",
        ),
        (
            "street_address",
            "VARCHAR(255) NULL",
            "the street line the customer typed; `address` keeps the formatted copy",
        ),
        (
            "birthdate",
            "DATE NULL",
            "needed by the renting-age check; legacy rows stay NULL until "
            "the wizard's inline fix fills them",
        ),
        (
            "emergency_contact",
            "VARCHAR(255) NULL",
            "who to call. Optional on the form",
        ),
        (
            "source",
            "VARCHAR(10) NULL",
            "walk_in or online. Backfilled by migrate_customer_management",
        ),
        (
            "is_active",
            "BOOL NOT NULL DEFAULT TRUE",
            "deactivate instead of delete; history keeps its FK",
        ),
        (
            "flag_reason",
            "VARCHAR(255) NULL",
            "staff flag / blacklist reason; NULL means unflagged",
        ),
        ("flagged_at", "DATETIME NULL", "when the flag was set"),
        ("flagged_by", "INT NULL", "which staff member set the flag"),
        (
            "licence_front_path",
            "VARCHAR(255) NULL",
            "disk path to the licence-front copy, never a BLOB",
        ),
        (
            "licence_back_path",
            "VARCHAR(255) NULL",
            "disk path to the licence-back copy, never a BLOB",
        ),
        (
            "must_change_password",
            "BOOL NOT NULL DEFAULT FALSE",
            "staff temp-password flag; False for all legacy rows",
        ),
    ],
    "VEHICLE": [
        (
            "vehicle_class",
            "VARCHAR(16) NULL",
            "size class for the New Rental filter rail. Nullable, so a row nobody "
            "has classified still loads. Filling it in is "
            "scripts/migrate_vehicle_class.py -- this only creates the column",
        ),
        (
            "engine_cc",
            "INTEGER NULL",
            "displacement, which the rail's CC buckets read. Same: schema here, "
            "data in migrate_vehicle_class",
        ),
        (
            "color",
            "VARCHAR(50) NULL",
            "paint colour for the fleet form and details",
        ),
        (
            "registration_expiry",
            "DATE NULL",
            "paperwork date for the fleet form and details",
        ),
        (
            "insurance_expiry",
            "DATE NULL",
            "paperwork date for the fleet form and details",
        ),
        (
            "retired_at",
            "DATE NULL",
            "when the vehicle was retired; NULL while in service. "
            "History-safe deactivate, like customers",
        ),
    ],
    "INSPECTION_REPORT": [
        (
            "photo_url",
            "VARCHAR(255) NULL",
            "was NOT NULL, forcing '' for rentals that were never photographed",
        ),
    ],
    "MAINTENANCE_RECORD": [
        (
            "maintenance_type",
            "VARCHAR(32) NOT NULL DEFAULT 'routine'",
            "routine, repair, tire_brake, inspection_registration, cleaning, "
            "damage_repair. Data in scripts/migrate_maintenance_lifecycle.py",
        ),
        (
            "estimate_cost",
            "DECIMAL(10,2) NULL",
            "quote while a job is open; the final lands in cost",
        ),
        (
            "odometer",
            "INTEGER NULL",
            "odometer at service, for mileage-based schedules",
        ),
        (
            "next_due_date",
            "DATE NULL",
            "next service date, stamped at completion",
        ),
        (
            "next_due_odometer",
            "INTEGER NULL",
            "next service odometer, stamped at completion",
        ),
        (
            "shop",
            "VARCHAR(128) NULL",
            "shop or mechanic who did the work",
        ),
        (
            "logged_by",
            "INT NULL",
            "staff member who logged the row, like Payments",
        ),
        (
            "booking_id",
            "INT NULL",
            "incident repairs tie back to the rental here",
        ),
        (
            "cancel_reason",
            "VARCHAR(255) NULL",
            "cancel keeps the row; the reason stays with it",
        ),
        (
            "created_at",
            "DATETIME NULL",
            "did not exist; defaulted in the model for new rows",
        ),
    ],
}

#: Columns to index, for the queries the staff screens run constantly.
INDEXES: list[tuple[str, str, list[str]]] = [
    # The Today tab's two queries are both on status, one of them also on
    # end_date. Without these they are a full scan of BOOKING.
    ("BOOKING", "ix_booking_status_end", ["status", "end_date"]),
    # The Payments tab filters by status and created_at every refresh.
    ("PAYMENT", "ix_payment_status_created", ["status", "created_at"]),
    # The dashboard's activity heatmap groups BOOKING by created_at across
    # sixteen weeks, and its channel chart groups the same column by channel.
    # Both are a full scan of BOOKING without this.
    ("BOOKING", "ix_booking_created_at", ["created_at"]),
    # check_licence and the Customers tab both look a user up by licence.
    ("USERS", "ix_users_license_number", ["license_number"]),
]


def _inspector():
    return inspect(engine)


def _columns(table: str) -> set[str]:
    if table not in _inspector().get_table_names():
        return set()
    return {c["name"] for c in _inspector().get_columns(table)}


def _indexes(table: str) -> set[str]:
    if table not in _inspector().get_table_names():
        return set()
    names = set()
    for index in _inspector().get_indexes(table):
        names.add(index["name"])
    for index in _inspector().get_unique_constraints(table):
        if index.get("name"):
            names.add(index["name"])
    return names


def _dialect() -> str:
    return engine.dialect.name


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------


def add_columns(dry_run: bool) -> None:
    for table, columns in ADDITIONS.items():
        if table not in _inspector().get_table_names():
            print(f"  ! {table} does not exist -- run scripts/init_db.py first")
            continue
        present = _columns(table)
        for name, ddl, note in columns:
            if name in present:
                print(f"  = {table}.{name:<12} already exists")
                continue
            if dry_run:
                print(f"  + {table}.{name:<12} WOULD ADD {ddl}")
                print(f"      {note}")
                continue
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
            print(f"  + {table}.{name:<12} added  ({ddl})")
            print(f"      {note}")


#: New tables, per dialect (the ADDITIONS above only cover columns).
TABLES: dict[str, dict[str, str]] = {
    "CUSTOMER_NOTE": {
        "mysql": """CREATE TABLE IF NOT EXISTS CUSTOMER_NOTE (
        note_id INTEGER NOT NULL AUTO_INCREMENT,
        customer_id INTEGER NOT NULL,
        author_id INTEGER NULL,
        note VARCHAR(1000) NOT NULL,
        created_at DATETIME NOT NULL,
        PRIMARY KEY (note_id),
        FOREIGN KEY(customer_id) REFERENCES USERS (user_id),
        FOREIGN KEY(author_id) REFERENCES USERS (user_id)
    )""",
        "sqlite": """CREATE TABLE IF NOT EXISTS CUSTOMER_NOTE (
        note_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL REFERENCES USERS (user_id),
        author_id INTEGER NULL REFERENCES USERS (user_id),
        note VARCHAR(1000) NOT NULL,
        created_at DATETIME NOT NULL
    )""",
    },
    "VEHICLE_RATE_LOG": {
        "mysql": """CREATE TABLE IF NOT EXISTS VEHICLE_RATE_LOG (
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
        "sqlite": """CREATE TABLE IF NOT EXISTS VEHICLE_RATE_LOG (
        rate_log_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        vehicle_id INTEGER NOT NULL REFERENCES VEHICLE (vehicle_id),
        old_rate DECIMAL(10, 2) NOT NULL,
        new_rate DECIMAL(10, 2) NOT NULL,
        changed_by INTEGER NULL REFERENCES USERS (user_id),
        changed_at DATETIME NOT NULL,
        is_bulk BOOLEAN NOT NULL DEFAULT FALSE,
        note VARCHAR(255) NULL
    )""",
    },
    "APP_SETTINGS": {
        "mysql": """CREATE TABLE IF NOT EXISTS APP_SETTINGS (
        `key` VARCHAR(64) NOT NULL PRIMARY KEY,
        value TEXT NOT NULL,
        updated_by INTEGER NULL,
        updated_at DATETIME NOT NULL
    )""",
        "sqlite": """CREATE TABLE IF NOT EXISTS APP_SETTINGS (
        key VARCHAR(64) NOT NULL PRIMARY KEY,
        value TEXT NOT NULL DEFAULT '',
        updated_by INTEGER NULL,
        updated_at DATETIME NOT NULL
    )""",
    },
}


def create_tables(dry_run: bool) -> None:
    dialect = "mysql" if _dialect().startswith("mysql") else "sqlite"
    for table, ddls in TABLES.items():
        if table in _inspector().get_table_names():
            print(f"  = {table:<16} already exists")
            continue
        if dry_run:
            print(f"  + {table:<16} WOULD CREATE")
            continue
        with engine.begin() as conn:
            conn.execute(text(ddls[dialect]))
        print(f"  + {table:<16} created")


def backfill_created_at(dry_run: bool) -> None:
    if "PAYMENT" not in _inspector().get_table_names():
        return
    if "created_at" not in _columns("PAYMENT"):
        return

    with engine.connect() as conn:
        nulls = conn.execute(
            text("SELECT COUNT(*) FROM PAYMENT WHERE created_at IS NULL")
        ).scalar_one()
    if not nulls:
        print("  = PAYMENT.created_at  no NULL rows to backfill")
        return
    if dry_run:
        print(f"  ~ PAYMENT.created_at  WOULD backfill {nulls} row(s) from paid_at")
        return

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE PAYMENT SET created_at = paid_at "
                "WHERE created_at IS NULL AND paid_at IS NOT NULL"
            )
        )
        # MySQL forbids naming the updated table in a subquery (error 1093),
        # so read the floor first and write it back as a literal.
        floor = conn.execute(text("SELECT MIN(created_at) FROM PAYMENT")).scalar()
        if floor is not None:
            conn.execute(
                text("UPDATE PAYMENT SET created_at = :floor WHERE created_at IS NULL"),
                {"floor": floor},
            )
    print(f"  ~ PAYMENT.created_at  backfilled {nulls} row(s)")


def _rebuild_sqlite_table(table: str, dry_run: bool) -> bool:
    from sqlalchemy.schema import CreateTable

    import app.models  # noqa: F401  (registers the tables on Base)
    from app.database import Base

    if table not in Base.metadata.tables:
        print(f"  ! {table} is not a known model table; cannot rebuild")
        return False

    target = Base.metadata.tables[table]
    existing = [c["name"] for c in _inspector().get_columns(table)]
    ddl = str(CreateTable(target).compile(engine)).strip()

    if dry_run:
        print(f"  ~ {table} WOULD be rebuilt from the model definition")
        print(f"      to drop the NOT NULL on its constrained columns")
        return True

    scratch = f"_rebuild_{table.lower()}"
    keep = ", ".join(existing)
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} RENAME TO {scratch}"))
        # Foreign keys are not enforced by default in SQLite, but a PRAGMA that
        # leaves them off is how a rebuild silently drops references. Turn
        # enforcement on for the swap, then restore the caller's setting.
        conn.execute(text(f"PRAGMA foreign_keys=OFF"))
        conn.execute(text(ddl))
        conn.execute(
            text(f"INSERT INTO {table} ({keep}) SELECT {keep} FROM {scratch}")
        )
        conn.execute(text(f"DROP TABLE {scratch}"))
        conn.execute(text("PRAGMA foreign_keys=ON"))
    print(f"  ~ {table} rebuilt from the model definition")
    return True


def relax_paid_at(dry_run: bool) -> None:
    _relax_column("PAYMENT", "paid_at", "DATETIME NULL", dry_run)


def relax_photo_url(dry_run: bool) -> None:
    _relax_column("INSPECTION_REPORT", "photo_url", "VARCHAR(255) NULL", dry_run)


def _relax_column(table: str, column: str, target_ddl: str, dry_run: bool) -> None:
    if table not in _inspector().get_table_names():
        print(f"  ! {table} does not exist -- run scripts/init_db.py first")
        return
    if column not in _columns(table):
        print(f"  ! {table}.{column} does not exist; skipped")
        return

    nullable = {
        c["name"]: c.get("nullable", True) for c in _inspector().get_columns(table)
    }.get(column)
    if nullable:
        print(f"  = {table}.{column:<12} already nullable")
        return

    dialect = _dialect()
    if dialect == "mysql":
        if dry_run:
            print(f"  + {table}.{column:<12} WOULD DROP NOT NULL")
            return
        with engine.begin() as conn:
            conn.execute(
                text(f"ALTER TABLE {table} MODIFY COLUMN {column} {target_ddl}")
            )
        print(f"  + {table}.{column:<12} dropped NOT NULL")
    elif dialect == "sqlite":
        _rebuild_sqlite_table(table, dry_run)
    else:
        print(f"  ! {table}.{column} cannot be altered on {dialect}; skipped")


def add_indexes(dry_run: bool) -> None:
    for table, name, columns in INDEXES:
        if table not in _inspector().get_table_names():
            continue
        if any(c not in _columns(table) for c in columns):
            continue
        if name in _indexes(table):
            print(f"  = {name:<28} already exists")
            continue
        if dry_run:
            print(f"  + {name:<28} WOULD CREATE on {table} ({', '.join(columns)})")
            continue
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"CREATE INDEX {name} ON {table} "
                    f"({', '.join(columns)})"
                )
            )
        print(f"  + {name:<28} created on {table} ({', '.join(columns)})")


def verify() -> bool:
    print()
    print("=== verification ===")
    ok = True
    enforced = _dialect() == "mysql"

    expected = {
        "PAYMENT": ["recorded_by", "reference_no", "note", "created_at"],
        "BOOKING": [
            "created_by", "cancel_reason", "channel",
            "flag_override_note", "flag_override_by", "flag_override_at",
        ],
        "USERS": [
            "region_code", "province_code", "city_municipality_code",
            "barangay_code", "street_address",
            "birthdate", "emergency_contact", "source", "is_active",
            "flag_reason", "flagged_at", "flagged_by",
            "licence_front_path", "licence_back_path", "must_change_password",
        ],
        "VEHICLE": [
            "color", "registration_expiry", "insurance_expiry", "retired_at",
        ],
    }
    for table, columns in expected.items():
        present = _columns(table)
        for column in columns:
            if column not in present:
                print(f"  x {table}.{column} is missing")
                ok = False
    for table in ("CUSTOMER_NOTE", "VEHICLE_RATE_LOG", "APP_SETTINGS"):
        if table not in _inspector().get_table_names():
            print(f"  x {table} table is missing")
            ok = False

    # The one that bites hardest: a staff app that cannot write a pending
    # payment cannot record a GCash transfer that has not landed yet.
    paid_at_nullable = {
        c["name"]: c.get("nullable", True) for c in _inspector().get_columns("PAYMENT")
    }.get("paid_at")
    if paid_at_nullable is False:
        print("  x PAYMENT.paid_at is still NOT NULL (pending/failed payments will fail)")
        ok = False
    else:
        print("  = PAYMENT.paid_at is nullable")

    if ok:
        print("  all staff-schema changes are in place")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="print changes without applying them")
    args = parser.parse_args()

    if not args.dry_run:
        print("Taking a backup of a real database is worth doing before this.")
        print("It only ever adds columns, so it cannot lose data -- but a")
        print("`mysqldump` takes a minute and removes all doubt.")
        print()

    print(f"Target: {_dialect()} at {engine.url.render_as_string(hide_password=True)}")
    print()

    print("=== 1. add columns ===")
    add_columns(args.dry_run)

    print()
    print("=== 2. backfill ===")
    backfill_created_at(args.dry_run)

    print()
    print("=== 3. relax NOT NULL ===")
    relax_paid_at(args.dry_run)
    relax_photo_url(args.dry_run)

    print()
    print("=== 4. indexes ===")
    add_indexes(args.dry_run)

    print()
    print("=== 5. new tables ===")
    create_tables(args.dry_run)

    print()
    if args.dry_run:
        print("Dry run: nothing was changed.")
    elif not verify():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
