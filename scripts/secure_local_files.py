"""Restrict local secret files to their owner.

`.env` holds the live database password and CarImages keys; `ca.pem` is the
TLS client key; the `.db` copies carry customer licences and payment history.
On a shared machine a world-readable checkout is a credential leak.

Run:
    python scripts/secure_local_files.py
    python scripts/secure_local_files.py --root /path/to/project

Missing files are reported, not treated as failures: a fresh checkout has
no `.env`, no `demo.db`, and no `backup.db`, and the script must still be
safe to run. Symlinks are skipped rather than followed, so a link cannot be
used to change the target's mode. Directories are left alone.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

#: The documented invocation is `python scripts/secure_local_files.py`, which
#: puts `scripts/` (not the repo root) on sys.path. Make the app package
#: importable before the shared helper import below.
DEFAULT_ROOT = Path(__file__).resolve().parent.parent
if str(DEFAULT_ROOT) not in sys.path:
    sys.path.insert(0, str(DEFAULT_ROOT))

from app.utils.file_security import (  # noqa: E402
    PRIVATE_FILE_MODE,
    restrict_to_owner,
)


#: Local files that must never be group/world readable.
SECRET_FILES = (
    ".env",
    "ca.pem",
    "backup.db",
    "demo.db",
    "rental.db",
    # Generated with the signed CarImages URL interpolated in (see .gitignore).
    "gallery.html",
)

def secure(
    root: str | Path, names: tuple[str, ...] = SECRET_FILES
) -> list[tuple[str, str, Path]]:
    """chmod every existing secret under `root` to 0600.

    Returns ``(name, status, path)`` where status is ``secured``, ``missing``,
    ``skipped-symlink``, ``skipped-not-a-file`` or ``error: ...``. Never
    raises for a missing or unreadable file: the caller reports and moves on.
    """
    base = Path(root)
    results: list[tuple[str, str, Path]] = []
    for name in names:
        path = base / name
        if path.is_symlink():
            results.append((name, "skipped-symlink", path))
            continue
        if not path.exists():
            results.append((name, "missing", path))
            continue
        if not path.is_file():
            results.append((name, "skipped-not-a-file", path))
            continue
        try:
            restrict_to_owner(path)
        except OSError as exc:
            results.append((name, f"error: {exc}", path))
            continue
        results.append((name, "secured", path))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help="project folder holding the local secrets (default: repo root)",
    )
    args = parser.parse_args()

    root = Path(args.root)
    print(f"Securing local files under: {root}")
    results = secure(root)
    for name, status, path in results:
        print(f"  {status:<18} {path}")
    secured = sum(1 for _name, status, _path in results if status == "secured")
    print(f"\n{secured} file(s) set to {PRIVATE_FILE_MODE:o}.")
    # Missing files are normal on a fresh checkout; never fail the command.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
