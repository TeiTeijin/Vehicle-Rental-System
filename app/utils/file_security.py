"""Filesystem permissions for local files that carry credentials or PII.

POSIX mode bits do not mean the same thing on Windows: ``os.chmod(0o600)``
only toggles the read-only attribute. The ACL is the real control there, so
callers use :func:`restrict_to_owner` instead of a bare ``chmod``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

PRIVATE_FILE_MODE = 0o600


def restrict_windows_acl(path: str | Path) -> None:
    """Drop inherited ACEs and leave only the current user with full control.

    ``icacls`` ships with every supported Windows install. The user name comes
    from the environment because the process runs as the signed-in clerk.
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


def restrict_to_owner(path: str | Path) -> None:
    """Make one file readable and writable only by its owner.

    On Windows this replaces inherited ACLs with a single current-user ACE;
    elsewhere it applies the conventional ``0600`` mode.
    """
    target = Path(path)
    if os.name == "nt":
        restrict_windows_acl(target)
    else:
        os.chmod(target, PRIVATE_FILE_MODE)
