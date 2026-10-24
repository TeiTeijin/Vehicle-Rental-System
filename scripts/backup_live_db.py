"""Copy the live database into a local SQLite snapshot.

The Settings page's Backup button can copy a SQLite file, but the real
branch database is hosted MySQL -- this is the missing piece. It is
SELECT-only on the source, and it writes a fresh, complete copy that the
app can open offline:

    python -m scripts.backup_live_db                 # -> backup.db
    python -m scripts.backup_live_db --out /tmp/x.db

    python -m app.staff --backup-file backup.db      # open the snapshot

The snapshot is credential data -- password hashes still need to restore a
working login -- so it stays on this machine: `*.db` is gitignored and the
file is chmod 0600. Signed media URLs are *not* copied: they embed the
CarImages API key and are useless after they expire, so those columns are
blanked (the app refetches them). If this snapshot or the machine was ever
shared, rotate the CarImages key.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from sqlalchemy import MetaData, create_engine, event, select
from sqlalchemy.orm import Session

from app.config import BASE_DIR, DATABASE_URL

#: Rows per statement, matching the bulk seeder: round trips dominate.
BATCH = 2000

#: Columns whose values are credentials, not data. Signed CarImages URLs
#: embed the API key, so the snapshot keeps the row (restore tests and FKs
#: still see it) but blanks the value. A blank URL reads as stale and is
#: refetched when the app is online.
CREDENTIAL_COLUMNS: dict[str, tuple[str, ...]] = {
    "VEHICLE_MEDIA": ("image_url", "model_3d_url"),
}

PRIVATE_FILE_MODE = 0o600


def _sanitize_rows(table_name: str, rows: list[dict]) -> list[dict]:
    """Drop credential-bearing values before they ever reach the target."""
    columns = CREDENTIAL_COLUMNS.get(table_name)
    if not columns:
        return rows
    return [
        {**row, **{column: "" for column in columns if column in row}}
        for row in rows
    ]


def _source_label(url: str) -> str:
    """Host + database only: never print credentials."""
    return url.split("@", 1)[-1]


def _fast_sqlite(target) -> None:
    """A snapshot is rebuilt on every run: durability of the intermediate
    file buys nothing, and the pragmas cut write time severalfold."""

    @event.listens_for(target, "connect")
    def _pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=MEMORY")
        cursor.execute("PRAGMA synchronous=OFF")
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()


def copy_database(source_url: str, out_path: Path) -> dict[str, int]:
    source = create_engine(source_url, pool_pre_ping=True)

    # The target schema mirrors the SOURCE, not the ORM models: the live
    # database is the truth about itself (its MAINTENANCE_RECORD.created_at
    # is nullable even though the model says NOT NULL), and a copy that
    # cannot hold a faithful row is not a backup.
    metadata = MetaData()
    metadata.reflect(bind=source)

    # MySQL scopes index names per table; SQLite scopes them per database,
    # so two `vehicle_id` indexes collide at CREATE INDEX. Rename only the
    # duplicates, keeping the name recognizable.
    seen: set[str] = set()
    for table in metadata.sorted_tables:
        for index in table.indexes:
            if index.name in seen:
                index.name = f"{table.name}_{index.name}"
            seen.add(index.name)

    # MySQL-only types (ENUM, TINYINT) do not compile for SQLite; the
    # generic equivalents store the same values.
    for table in metadata.sorted_tables:
        for column in table.columns:
            column.type = column.type.as_generic()

    if out_path.exists():
        out_path.unlink()
    target = create_engine(f"sqlite:///{out_path}")
    _fast_sqlite(target)
    metadata.create_all(target)
    # Restrict before customer data lands in the file. On POSIX this is a
    # real 0600; on Windows Python only toggles the read-only bit, which is
    # the closest equivalent the platform offers.
    os.chmod(out_path, PRIVATE_FILE_MODE)

    counts: dict[str, int] = {}
    with Session(source) as src, Session(target) as dst:
        # `sorted_tables` is dependency order, so parents land before
        # children even with the FK pragma off.
        for table in metadata.sorted_tables:
            rows = _sanitize_rows(
                table.name,
                [dict(row) for row in src.execute(select(table)).mappings()],
            )
            for start in range(0, len(rows), BATCH):
                dst.execute(table.insert(), rows[start : start + BATCH])
            dst.commit()
            counts[table.name] = len(rows)
            print(f"  {table.name:22s} {len(rows):>6d}", flush=True)

    source.dispose()
    target.dispose()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--source-url",
        default=DATABASE_URL,
        help="Read from this database instead of DATABASE_URL.",
    )
    parser.add_argument(
        "--out",
        default="backup.db",
        help="Snapshot file to write (relative paths land in the project root).",
    )
    args = parser.parse_args()

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = BASE_DIR / out_path

    print(f"source: {_source_label(args.source_url)}")
    print(f"target: {out_path}")
    started = time.monotonic()
    try:
        counts = copy_database(args.source_url, out_path)
    except BaseException:
        # A failed run must not leave a half-copy that looks like a backup.
        if out_path.exists():
            out_path.unlink()
        raise

    size = out_path.stat().st_size
    print(
        f"Done. {sum(counts.values())} rows, {size / 1024:.0f} KB, "
        f"{time.monotonic() - started:.1f}s."
    )


if __name__ == "__main__":
    main()
