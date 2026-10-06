from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase

#: The single copy of the bundled faces, shared by both windows.
FONTS_DIR = Path(__file__).resolve().parent.parent / "ui" / "fonts"

#: Family name the stylesheets ask for by string.
BUNDLED_FAMILY = "Inter 18pt"

_loaded = False


def load_fonts() -> bool:
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
