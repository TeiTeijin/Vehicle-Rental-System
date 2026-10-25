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
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import BASE_DIR, DATABASE_URL
from app.services.backup_service import (
    SNAPSHOT_MODE,
    UnsafeSnapshotPath,
    sanitize_row,
)

#: Rows per statement, matching the bulk seeder: round trips dominate.
BATCH = 2000


def _sqlite_source_path(source_url: str) -> Path | None:
    """The on-disk file behind a SQLite URL, or None for MySQL/memory."""
    url = make_url(source_url)
    if not url.drivername.startswith("sqlite"):
        return None
    if not url.database or url.database == ":memory:":
        return None
    return Path(url.database).resolve()


def _refuse_overwriting_source(source_url: str, out_path: Path) -> None:
    """A backup must never unlink or replace the database it reads."""
    source_path = _sqlite_source_path(source_url)
    if source_path is not None and out_path.resolve() == source_path:
        raise UnsafeSnapshotPath(
            "refusing to write the snapshot over its SQLite source"
        )


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
    _refuse_overwriting_source(source_url, out_path)
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
    # Snapshot still holds password hashes: create it private, not at the
    # process umask, even before the first row lands. On POSIX this is a real
    # 0600; on Windows Python only toggles the read-only bit, which is the
    # closest equivalent the platform offers.
    out_path.touch(mode=SNAPSHOT_MODE)
    os.chmod(out_path, SNAPSHOT_MODE)
    target = create_engine(f"sqlite:///{out_path}")
    _fast_sqlite(target)
    metadata.create_all(target)

    counts: dict[str, int] = {}
    with Session(source) as src, Session(target) as dst:
        # `sorted_tables` is dependency order, so parents land before
        # children even with the FK pragma off.
        for table in metadata.sorted_tables:
            rows = [dict(row) for row in src.execute(select(table)).mappings()]
            for row in rows:
                sanitize_row(table.name, row)
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

    # Guard before the failure cleanup below can unlink a source file.
    try:
        _refuse_overwriting_source(args.source_url, out_path)
    except UnsafeSnapshotPath as exc:
        parser.error(str(exc))

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
