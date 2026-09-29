"""Colours and stylesheet for the staff application.

The staff app is a *separate* window from the customer app, but it should look
like it belongs to the same product, so it reuses the palette already in
``app/ui/styles.qss`` and adds the three semantic colours the operations
screens need. Those live here as constants rather than only in the QSS so that
code which draws -- status pills, the hand-drawn dashboard charts, a coloured
row -- can use the same values without re-parsing a stylesheet.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor

# -- palette ---------------------------------------------------------------

BG = "#F1E8D8"
NAVBAR = "#FFFFFF"
TEXT = "#1A1A1A"
MUTED = "#8A8175"
ACCENT = "#D8CCBB"
BORDER = "#D8CCBB"
SURFACE = "#EEEAE1"
SURFACE_HOVER = "#E6E1D6"
INK = "#171717"
PAPER = "#FFFFFF"

#: Semantic status colours. Green/amber/red rather than the existing neutrals,
#: because "is this car fine?" and "is this money late?" are the two questions
#: the staff app exists to answer, and neither has an answer in beige.
OK = "#4F7A5B"
WARN = "#B07D2B"
DANGER = "#A8452F"

#: Per-booking and per-payment status -> colour. Anything unmapped falls back
#: to MUTED, which reads as "no opinion" rather than as a fifth severity.
STATUS_COLOURS = {
    "available": OK,
    "rented": WARN,
    "maintenance": DANGER,
    "retired": MUTED,
    "pending": WARN,
    "confirmed": OK,
    "ongoing": WARN,
    "completed": MUTED,
    "cancelled": DANGER,
    "paid": OK,
    "refunded": DANGER,
    "failed": DANGER,
    "overdue": DANGER,
    "partial": WARN,
    "outstanding": WARN,
}

FONT = "Inter 18pt"

#: Object names, shared between the QSS and the code that sets them. A typo in
#: a stylesheet selector is silent -- the widget just renders unstyled -- so
#: these constants are what the Python actually uses.
OBJ_NAVBAR = "navbar"
OBJ_NAV_ITEM = "navItem"
OBJ_DEMO_BANNER = "demoBanner"
OBJ_STATUS = "statusPill"
OBJ_EMPTY = "emptyState"
OBJ_ERROR = "errorState"


def colour_for(status: str | None) -> QColor:
    """The colour for a status string, never ``None``."""
    return QColor(STATUS_COLOURS.get((status or "").strip().lower(), MUTED))


def stylesheet_path() -> Path:
    return Path(__file__).resolve().parent / "staff.qss"


def load_stylesheet() -> str:
    return stylesheet_path().read_text(encoding="utf-8")
