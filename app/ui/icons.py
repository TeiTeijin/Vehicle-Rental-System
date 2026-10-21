"""Icons for the customer screens, drawn on the same 24 grid as the counter's.

Same idea as app/staff/icons.py: one path per name, stroked in whatever colour
the caller asks for, rendered through QSvgRenderer so they stay crisp on any
display scale. No image files to ship or keep in step.

    icon("calendar", colour="#1a1a1a", size=18)   -> QIcon
    pixmap("cash", colour="#1a1a1a", size=20)     -> QPixmap
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

#: Every glyph is authored on this 24 grid.
GRID = 24

_STROKE = 1.6


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
    "back": "M14.6 5.6 8.2 12l6.4 6.4",
    "forward": "M9.4 5.6 15.8 12l-6.4 6.4",
    "arrow_left": "M19 12H5.6M10.6 6.6 5.2 12l5.4 5.4",
    "arrow_right": "M5 12h13.4M13.4 6.6l5.4 5.4-5.4 5.4",
    "search": f"{_circle(10.8, 10.8, 6.2)}M15.4 15.4 20 20",
    # Car: roof arc, body, two wheels.
    "car": (
        "M3 16.5v-3.2l2-5.1A2.6 2.6 0 0 1 7.4 6.6h9.2a2.6 2.6 0 0 1 2.4 1.6l2 5.1"
        "v3.2M3 13.3h18M5.8 16.6h2.6M15.6 16.6h2.6M12 13.3v3.3"
    ),
    "calendar": (
        f"{_round_rect(3.5, 5, 17, 16, 2.4)}M3.5 9.6h17"
        "M8 3v4M16 3v4M7.6 13.4h2M12 13.4h2M16.4 13.4h.6"
    ),
    "clock": f"{_circle(12, 12, 8.2)}M12 7.2V12l3.4 2.2",
    "bookings": (
        f"{_round_rect(3.5, 5, 17, 16, 2.4)}M3.5 9.5h17"
        "M8 3v4M16 3v4M8 14h3"
    ),
    # -- spec grid ---------------------------------------------------------
    # Two heads and a shoulder line: passenger count.
    "seats": (
        f"{_circle(9, 8.4, 3.1)}"
        "M3.6 19.6a5.6 5.6 0 0 1 10.8 0"
        f"{_circle(17.4, 9.6, 2.3)}"
        "M15.4 14.4a4.6 4.6 0 0 1 5 4"
    ),
    # Gear stick: two cogs, the larger one driving.
    "transmission": (
        f"{_circle(12, 6.6, 2.6)}"
        "M12 9.2v5.4M6.6 17.4h10.8M4.4 15.4l2.2 2v2.8M19.6 15.4l-2.2 2v2.8"
    ),
    # Fuel pump: body, hose, filler.
    "fuel": (
        f"{_round_rect(4.6, 3.6, 8.4, 16.8, 1.6)}M7.4 7.6h2.8"
        "M13 8.4h3.4v9.2a2.4 2.4 0 0 0 4.8 0V8.4l-1.8-2.2"
    ),
    # Body style: a hatchback outline with a wheel.
    "body": (
        "M3.4 15.4v-3.1l2.2-4.6A2.2 2.2 0 0 1 7.6 6.4h6.6a2.2 2.2 0 0 1 1.9 1.2"
        "l2.5 4.8M3.4 12.3h17.2v3.1M6.6 18.6h2.8M14.6 18.6h2.8"
    ),
    "mileage": (
        f"{_circle(12, 12, 8.4)}M12 12l4.2-3.4M8.6 12h.01M15.4 12h.01M12 15.4h.01"
    ),
    # -- booking / summary -------------------------------------------------
    "map_pin": (
        "M12 21.2s6.6-6.2 6.6-10.6a6.6 6.6 0 1 0-13.2 0C5.4 15 12 21.2 12 21.2Z"
        f"{_circle(12, 10.4, 2.4)}"
    ),
    "route": (
        f"{_circle(6.4, 6.4, 2.4)}{_circle(17.6, 17.6, 2.4)}"
        "M8.8 6.4h6.6a2.6 2.6 0 0 1 0 5.2H8.6a2.6 2.6 0 0 0 0 5.2h6.6"
    ),
    "shield": (
        "M12 3.2 5 6v5.4c0 4.4 2.9 7.6 7 9.4 4.1-1.8 7-5 7-9.4V6Z"
        "M9.2 12.2l2 2 3.6-4"
    ),
    "receipt": (
        "M6 3.4h12v17.2l-2-1.4-2 1.4-2-1.4-2 1.4-2-1.4-2 1.4Z"
        "M9 8.4h6M9 12.4h6"
    ),
    "percent": f"{_circle(8, 8, 2.6)}{_circle(16, 16, 2.6)}M17.6 6.4 6.4 17.6",
    "swap": (
        "M4.2 8.8h13.2M14.6 5.6l3 3.2-3 3.2"
        "M19.8 15.2H6.6M9.4 11.8l-3 3.2 3 3.2"
    ),
    # -- payment -----------------------------------------------------------
    "card": (
        f"{_round_rect(2.8, 5.5, 18.4, 13, 2.4)}M2.8 10h18.4M6.4 14.6h3.6"
    ),
    "cash": (
        f"{_round_rect(2.8, 6, 18.4, 12, 2.2)}"
        f"{_circle(12, 12, 2.9)}M6.4 9.6v.01M17.6 14.4v.01"
    ),
    "gcash": (
        "M8.1 16.9a4.9 4.9 0 1 0-2.7-8.7 4.9 4.9 0 0 1 4.3 8.4"
        "M12.4 6.3a5.6 5.6 0 1 1-4.1 9.7"
        "M6.3 12.4h3.4M8 11.1v2.6"
    ),
    "lock": (
        f"{_round_rect(4.4, 10.4, 15.2, 9.6, 2.2)}M8 10.4V7.8a4 4 0 0 1 8 0v2.6"
    ),
    "account": f"{_circle(12, 8.2, 3.7)}M4.9 20.3a7.1 7.1 0 0 1 14.2 0",
    # -- 3D -----------------------------------------------------------------
    "cube": (
        "M12 3.4 20.4 8v8L12 20.6 3.6 16V8Z"
        "M3.6 8 12 12.6 20.4 8M12 12.6v8"
    ),
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


__all__ = ["GRID", "PATHS", "icon", "known", "pixmap", "renderer", "svg_source"]
