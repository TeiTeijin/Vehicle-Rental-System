from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

#: Every glyph is authored on this 24 grid.
GRID = 24

_STROKE = 1.6

#: Rounded-rectangle helper: an inset rect drawn as a path with corner radii.
def _round_rect(x: float, y: float, w: float, h: float, r: float) -> str:
    return (
        f"M{x + r} {y}H{x + w - r}A{r} {r} 0 0 1 {x + w} {y + r}"
        f"V{y + h - r}A{r} {r} 0 0 1 {x + w - r} {y + h}"
        f"H{x + r}A{r} {r} 0 0 1 {x} {y + h - r}"
        f"V{y + r}A{r} {r} 0 0 1 {x + r} {y}Z"
    )


def _circle(cx: float, cy: float, r: float) -> str:
    return (
        f"M{cx - r} {cy}A{r} {r} 0 1 0 {cx + r} {cy}"
        f"A{r} {r} 0 1 0 {cx - r} {cy}Z"
    )


#: name -> path data.
PATHS: dict[str, str] = {
    # -- navigation --------------------------------------------------------
    # Four squares, one filled, top-left: the dashboard tile pattern.
    "dashboard": (
        f"{_round_rect(3.5, 3.5, 7, 7, 1.6)}"
        f"{_round_rect(13.5, 3.5, 7, 7, 1.6)}"
        f"{_round_rect(3.5, 13.5, 7, 7, 1.6)}"
        f"{_round_rect(13.5, 13.5, 7, 7, 1.6)}"
    ),
    # A receipt with a zigzag bottom edge, which reads as "ticket" not "paper".
    "pos": (
        "M6 2.8h12v18.4l-2.4-1.6-2.4 1.6-2.4-1.6-2.4 1.6-2.4-1.6Z"
        "M9 8h6M9 12h6M9 16h3"
    ),
    "bookings": (
        f"{_round_rect(3.5, 5, 17, 16, 2.4)}M3.5 9.5h17"
        "M8 3v4M16 3v4M8 14h3"
    ),
    # Car: roof arc, body, two wheels.
    "vehicle": (
        "M3 16.5v-3.2l2-5.1A2.6 2.6 0 0 1 7.4 6.6h9.2a2.6 2.6 0 0 1 2.4 1.6l2 5.1"
        "v3.2M3 13.3h18M5.8 16.6h2.6M15.6 16.6h2.6M12 13.3v3.3"
    ),
    "customers": (
        f"{_circle(9.5, 8, 3.4)}"
        "M3.5 20.2a6.4 6.4 0 0 1 12 0"
        "M16 5.1a3.4 3.4 0 0 1 0 6.6M17.4 14.6a6.4 6.4 0 0 1 3.1 5.6"
    ),
    "reports": (
        "M4 20V4M4 20h16"
        "M8 20v-6M12.7 20V9M17.3 20v-9.5"
    ),
    "billing": (
        "M6.4 2.8h11.2v18.4l-1.87-1.4-1.87 1.4-1.86-1.4-1.87 1.4-1.86-1.4-1.87 1.4Z"
        "M12 7.2v6.4M14.6 8.9a2.7 2.7 0 0 0-4.7 1.8c0 2.3 4.7 1.5 4.7 3.8"
        "a2.7 2.7 0 0 1-4.7 1.8"
    ),
    "maintenance": (
        "M14.4 6.2a3.9 3.9 0 0 0 5 5l-8.8 8.8a2.8 2.8 0 0 1-4-4Z"
        "M4.6 4.6l3 3"
    ),
    "settings": (
        f"{_circle(12, 12, 3.2)}"
        "M12 2.6v2.6M12 18.8v2.6M21.4 12h-2.6M5.2 12H2.6"
        "M18.6 5.4l-1.9 1.9M7.3 16.7l-1.9 1.9M18.6 18.6l-1.9-1.9M7.3 7.3 5.4 5.4"
    ),
    "logout": (
        "M14.5 4.5h3.6a1.9 1.9 0 0 1 1.9 1.9v11.2a1.9 1.9 0 0 1-1.9 1.9h-3.6"
        "M9.6 15.6 14.2 11l-4.6-4.6M14.2 11H4.4"
    ),
    # -- dashboard actions --------------------------------------------------
    "search": f"{_circle(10.8, 10.8, 6.2)}M15.4 15.4 20 20",
    "calendar": (
        f"{_round_rect(3.5, 5, 17, 16, 2.4)}M3.5 9.6h17"
        "M8 3v4M16 3v4M7.6 13.4h2M12 13.4h2M16.4 13.4h.6"
    ),
    # Three stacked bars of differing height: the "manage this card" menu.
    "sliders": "M4 7h11M19 7h1M4 12h3M11 12h9M4 17h9M17 17h3M17 5.2v3.6M9 10.2v3.6M15 15.2v3.6",
    "dots": f"{_circle(6, 12, 1.5)}{_circle(12, 12, 1.5)}{_circle(18, 12, 1.5)}",
    "plus": "M12 5.5v13M5.5 12h13",
    # A clock with a counter-clockwise arrow, for the period selector.
    "history": (
        f"{_circle(12, 12, 8.2)}M12 7.2V12l3.4 2.2"
    ),
    "download": "M12 3.8v10.6M7.8 10.6 12 14.8l4.2-4.2M4.4 19.4h15.2",
    "up_right": "M7.4 16.6 16.6 7.4M8.8 7.4h7.8v7.8",
    "arrow_up": "M12 19.2V5.4M6.9 10.5 12 5.4l5.1 5.1",
    "arrow_down": "M12 4.8v13.8M6.9 13.5 12 18.6l5.1-5.1",
    "swap": (
        "M4.2 8.8h13.2M14.6 5.6l3 3.2-3 3.2"
        "M19.8 15.2H6.6M9.4 11.8l-3 3.2 3 3.2"
    ),
    "user": f"{_circle(12, 8.2, 3.7)}M4.9 20.3a7.1 7.1 0 0 1 14.2 0",
    # -- channels -----------------------------------------------------------
    # A person behind a counter for walk-in; a browser window for online.
    "walk_in": (
        f"{_circle(12, 7.4, 3.3)}"
        "M6.2 20.4a5.8 5.8 0 0 1 11.6 0M3.2 12.4h17.6"
    ),
    "online": (
        f"{_round_rect(2.8, 4.4, 18.4, 13.4, 2.2)}M2.8 8.6h18.4"
        f"{_circle(6.2, 6.5, 0.9)}{_circle(8.8, 6.5, 0.9)}"
        "M8 14.4h8M2.8 20.6h18.4"
    ),
    # -- payment methods ----------------------------------------------------
    "cash": (
        f"{_round_rect(2.8, 6, 18.4, 12, 2.2)}"
        f"{_circle(12, 12, 2.9)}M6.4 9.6v.01M17.6 14.4v.01"
    ),
    "card": (
        f"{_round_rect(2.8, 5.5, 18.4, 13, 2.4)}M2.8 10h18.4M6.4 14.6h3.6"
    ),
    "gcash": (
        "M8.1 16.9a4.9 4.9 0 1 0-2.7-8.7 4.9 4.9 0 0 1 4.3 8.4"
        "M12.4 6.3a5.6 5.6 0 1 1-4.1 9.7"
        "M6.3 12.4h3.4M8 11.1v2.6"
    ),
    # -- trend -------------------------------------------------------------
    "trend_up": "M4 16.6 9.4 11l3.4 3.4L20 7.2M20 7.2h-5.4M20 7.2v5.4",
    # -- misc ---------------------------------------------------------------
    "check": "M5 12.6 9.8 17.4 19 8.2",
    "close": "M6.2 6.2 17.8 17.8M17.8 6.2 6.2 17.8",
    "info": f"{_circle(12, 12, 8.6)}M12 11v5.4M12 7.6v.01",
    "warning": (
        "M10.6 3.9 2.5 18.1a1.6 1.6 0 0 0 1.4 2.4h16.2a1.6 1.6 0 0 0 1.4-2.4"
        "L13.4 3.9a1.6 1.6 0 0 0-2.8 0ZM12 9.4v4.4M12 16.8v.01"
    ),
}


def svg_source(name: str, colour: str) -> bytes:
    path = PATHS.get(name)
    if path is None:
        raise KeyError(f"no icon named {name!r}")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}" '
        f'fill="none" stroke="{colour}" stroke-width="{_STROKE}" '
        'stroke-linecap="round" stroke-linejoin="round">'
        f"<path d=\"{path}\"/></svg>"
    ).encode("utf-8")


def renderer(name: str, colour: str) -> QSvgRenderer:
    r = QSvgRenderer(QByteArray(svg_source(name, colour)))
    if not r.isValid():
        raise ValueError(f"icon {name!r} did not produce a valid SVG")
    return r


def pixmap(name: str, colour: str, size: int) -> QPixmap:
    scale = 2
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pm)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer(name, colour).render(painter, QRectF(0, 0, size * scale, size * scale))
    finally:
        painter.end()

    return pm.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def icon(name: str, colour: str, size: int = 20) -> QIcon:
    out = QIcon()
    for ratio in (1, 2):
        key = QPixmap(pixmap(name, colour, size * ratio))
        key.setDevicePixelRatio(ratio)
        out.addPixmap(key)
    return out


def known(name: str) -> bool:
    return name in PATHS
