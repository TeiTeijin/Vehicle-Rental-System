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
import os
import subprocess
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent

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

PRIVATE_FILE_MODE = 0o600


def _restrict_windows_acl(path: Path) -> None:
    """Drop inherited ACEs and leave only the current user with full control.

    Windows does not implement POSIX mode bits; ``os.chmod(0o600)`` only
    toggles the read-only attribute. The ACL is the meaningful control there,
    and ``icacls`` is present on every supported Windows install.
    """
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if not user:
        raise OSError("cannot determine the current Windows user")
    completed = subprocess.run(
        [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{user}:F",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise OSError(detail or f"icacls failed with {completed.returncode}")


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
            if os.name == "nt":
                _restrict_windows_acl(path)
            else:
                os.chmod(path, PRIVATE_FILE_MODE)
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
