# Local database backup security

Every local SQLite snapshot made by the staff app or the backup CLI must be
safe to keep on a developer machine.

## What is sanitized

`VEHICLE_MEDIA.image_url` and `VEHICLE_MEDIA.model_3d_url` contain signed
CarImages URLs with `api_key=` in the query string. Both backup paths blank
those columns before the snapshot is exposed:

- **Settings → Backup database** copies the selected SQLite file through
  `app.services.backup_service.create_sanitized_sqlite_snapshot`.
- **`python -m scripts.backup_live_db`** sanitizes each row before inserting
  it into the fresh snapshot.

The source database is never modified. The snapshot keeps its schema, row
counts, and non-secret media metadata so restore tests still work.

## Permissions

Snapshots still contain password hashes. They are created owner-only
(`0600` on POSIX; the closest Windows ACL equivalent), and a failed
sanitization deletes the partial copy instead of leaving an unsafe file.

## Old snapshots

Snapshots created **before this change** contain the old `api_key=` value
verbatim. Delete them rather than keeping or sharing them, including old
files in the Settings export folder. If any such snapshot was shared, rotate
the CarImages API key.

`backup.db`, `.env`, `ca.pem`, and `demo.db` must remain local and must not be
committed. The CLI refuses to write a snapshot over its own SQLite source.
