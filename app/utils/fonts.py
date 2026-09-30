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

from PySide6.QtGui import QFont, QFontDatabase

#: The single copy of the bundled faces, shared by both windows.
FONTS_DIR = Path(__file__).resolve().parent.parent / "ui" / "fonts"

#: Family name the stylesheets ask for by string.
BUNDLED_FAMILY = "Inter 18pt"

_loaded = False


def load_fonts() -> bool:
    """Register every bundled ``.ttf`` and make it the application font.

    Returns whether the family is usable.

    Requires a ``QApplication`` to already exist -- ``QFontDatabase`` is not
    available before one does. Returns False rather than raising when the
    files are missing, because a missing font should degrade the typography,
    not stop the app from starting.

    Setting the application font is the belt to the stylesheet's braces. A
    ``font-family`` rule is a *request*: register the faces but leave the app
    font alone and anything the stylesheet does not reach -- a context menu, a
    native-drawn tooltip, a widget created before ``setStyleSheet`` -- quietly
    renders in the system default. With the app font set, Inter is what the
    process draws with even if a rule is missing or mistyped.
    """
    global _loaded
    if not _loaded:
        for font_file in sorted(FONTS_DIR.glob("*.ttf")):
            QFontDatabase.addApplicationFont(str(font_file))
        _loaded = True

    usable = BUNDLED_FAMILY in QFontDatabase.families()
    if usable:
        from PySide6.QtWidgets import QApplication

        QApplication.setFont(QFont(BUNDLED_FAMILY))
    return usable
