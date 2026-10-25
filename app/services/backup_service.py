"""Shared rules for local database backups.

CarImages signed URLs carry `api_key=` in their query string, so a raw copy
of `VEHICLE_MEDIA` is a credential leak. Every backup path -- the CLI in
`scripts/backup_live_db.py` and the Settings "Backup database" button -- uses
this module so the copy has the same blanked URL columns and the same
owner-only permissions.

Nothing here touches the source database: the CLI sanitizes rows on their way
into a fresh SQLite file, and `create_sanitized_sqlite_snapshot` copies the
file first and rewrites only the copy.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

#: Columns that are credentials, not data. CarImages signatures are short
#: lived and their URLs carry `api_key=`, so a copied URL is a leaked key
#: that also expires before a restore test can use it.
SANITIZED_COLUMNS: dict[str, tuple[str, ...]] = {
    "VEHICLE_MEDIA": ("image_url", "model_3d_url"),
}

#: Snapshots still contain password hashes: owner-only, always.
SNAPSHOT_MODE = 0o600


class SnapshotError(RuntimeError):
    """A snapshot could not be made safe, so it must not be kept."""


class UnsafeSnapshotPath(ValueError):
    """The destination would overwrite the source being backed up."""


def sanitize_row(table_name: str, row: dict) -> None:
    """Blank credential-bearing values in one row before it is inserted."""
    columns = SANITIZED_COLUMNS.get(table_name, ())
    if not columns:
        return
    for column in columns:
        if column in row:
            row[column] = ""


def sanitize_sqlite_copy(path: Path) -> None:
    """Blank media URLs in an *already-made* SQLite copy.

    The update alone can leave the old value behind in free pages, so the
    file is vacuumed afterwards; tests inspect the raw bytes, not just rows.
    A table that exists without either URL column is not safe to certify:
    raise instead of silently returning an unsanitized snapshot.
    """
    path = Path(path)
    try:
        connection = sqlite3.connect(path)
        connection.isolation_level = None
        try:
            connection.execute("PRAGMA secure_delete=ON")
            present = {
                row[1]
                for row in connection.execute('PRAGMA table_info("VEHICLE_MEDIA")')
            }
            if not present:
                raise SnapshotError("snapshot has no VEHICLE_MEDIA table")
            targets = [
                column
                for column in SANITIZED_COLUMNS["VEHICLE_MEDIA"]
                if column in present
            ]
            if not targets:
                raise SnapshotError(
                    "snapshot has no media URL columns to sanitize"
                )
            assignments = ", ".join(f'"{column}" = ?' for column in targets)
            connection.execute("BEGIN")
            connection.execute(
                f'UPDATE "VEHICLE_MEDIA" SET {assignments}',
                ("",) * len(targets),
            )
            connection.execute("COMMIT")
            connection.execute("VACUUM")
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise SnapshotError(f"cannot sanitize snapshot: {exc}") from exc


def create_sanitized_sqlite_snapshot(
    source: str | Path, destination: str | Path
) -> Path:
    """Copy a SQLite file, sanitize the copy, and fail without leaving it."""
    source = Path(source)
    destination = Path(destination)
    if source.resolve() == destination.resolve():
        raise UnsafeSnapshotPath("refusing to overwrite the source database")
    if not source.is_file():
        raise SnapshotError(f"source database not found: {source}")
    if destination.exists():
        raise SnapshotError(f"destination already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Create the destination owner-only before any bytes are written, so
        # a raw credential is never briefly readable under the process umask.
        fd = os.open(
            destination,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            SNAPSHOT_MODE,
        )
        os.close(fd)
        with source.open("rb") as src, destination.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        os.chmod(destination, SNAPSHOT_MODE)
        sanitize_sqlite_copy(destination)
    except BaseException:
        if destination.is_file():
            destination.unlink(missing_ok=True)
        raise
    return destination
