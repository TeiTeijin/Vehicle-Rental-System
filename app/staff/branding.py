"""The RentWheels wordmark image.

The brand ships as a single raster PNG rather than an SVG glyph in
:mod:`app.staff.icons`, because it is a drawn logo and not an outline path: the
icon set's tinting trick (recolour the stroke at paint time) has nothing to
recolour here.

``rentwheels-logo-light-bg`` is the dark-ink version of the mark, meant for a
light surface -- the sidebar's white panel. Scaling happens here rather than in
the widget so the source is read once and the aspect ratio is never something a
caller has to remember.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

#: Where the brand assets live.
ASSETS = Path(__file__).resolve().parent / "assets"

#: The dark wordmark, for use on a light background.
WORDMARK_LIGHT = ASSETS / "rentwheels-logo-light-bg.png"


def wordmark_pixmap(width: int) -> QPixmap:
    """The wordmark scaled to `width` px, aspect ratio preserved.

    Returns a null `QPixmap` if the asset is missing rather than raising: a
    missing logo should leave the sidebar without a wordmark, not stop the one
    screen the whole branch signs in through. The caller can fall back.
    """
    source = QPixmap(str(WORDMARK_LIGHT))
    if source.isNull() or width <= 0:
        return source
    return source.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
