"""Registering the bundled typefaces with Qt.

A ``font-family`` in a Qt stylesheet is a *request*, not a command. If the
family was never handed to ``QFontDatabase`` the widget silently renders in
whatever Qt decides is closest, which is how a stylesheet can look correct in
the source and wrong on screen.

The TTFs live in ``app/ui/fonts`` because that is where they were first
bundled. This module is the only thing that should reach into that folder, so
there is one answer to "is the right face loaded" instead of one per entry
point. ``load_fonts`` is idempotent: calling it twice is free, so a caller that
is not sure whether someone else has already run it does not have to care.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFontDatabase

#: The single copy of the bundled faces, shared by both windows.
FONTS_DIR = Path(__file__).resolve().parent.parent / "ui" / "fonts"

#: Family name the stylesheets ask for by string.
BUNDLED_FAMILY = "Inter 18pt"

_loaded = False


def load_fonts() -> bool:
    """Register every bundled ``.ttf``. Returns whether the family is usable.

    Requires a ``QApplication`` to already exist -- ``QFontDatabase`` is not
    available before one does. Returns False rather than raising when the
    files are missing, because a missing font should degrade the typography,
    not stop the app from starting.
    """
    global _loaded
    if _loaded:
        return BUNDLED_FAMILY in QFontDatabase.families()

    for font_file in sorted(FONTS_DIR.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(font_file))
    _loaded = True

    return BUNDLED_FAMILY in QFontDatabase.families()
