"""Open a 3D model in a separate preview process.

WebEngine has crashed the main staff process on this machine (SIGSEGV while
creating/showing QWebEngineView), so the staff app must never instantiate one.
This module only starts the child process; it deliberately imports nothing
from QtWebEngine.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess

#: The child module that owns the WebEngine window.
PREVIEW_MODULE = "app.staff.model_preview_window"


def launch_model_preview(glb_path: Path, label: str) -> bool:
    """Start the external 3D preview for `glb_path`.

    Returns True when the child process was started, False when the model is
    missing or the process cannot be launched. A failed preview must never
    affect the staff window: the caller just shows a quiet message.
    """
    path = Path(glb_path)
    if not path.is_file():
        return False
    arguments = ["-m", PREVIEW_MODULE, str(path), str(label)]
    try:
        result = QProcess.startDetached(sys.executable, arguments)
    except Exception:  # noqa: BLE001 - a preview must never break the desk
        return False
    if isinstance(result, tuple):
        return bool(result[0])
    return bool(result)


__all__ = ["PREVIEW_MODULE", "launch_model_preview"]
