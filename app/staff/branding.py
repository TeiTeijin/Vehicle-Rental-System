from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

#: Where the brand assets live.
ASSETS = Path(__file__).resolve().parent / "assets"

#: The dark wordmark, for use on a light background.
WORDMARK_LIGHT = ASSETS / "rentwheels-logo-light-bg.png"


def wordmark_pixmap(width: int) -> QPixmap:
    source = QPixmap(str(WORDMARK_LIGHT))
    if source.isNull() or width <= 0:
        return source
    return source.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
