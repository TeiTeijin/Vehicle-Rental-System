"""Colours and stylesheet for the staff application.

The staff app is a *separate* window from the customer app, but it should look
like it belongs to the same product, so it reuses the palette already in
``app/ui/styles.qss`` and adds the three semantic colours the operations
screens need. Those live here as constants rather than only in the QSS so that
code which draws -- status pills, the hand-drawn dashboard charts, a coloured
row -- can use the same values without re-parsing a stylesheet.
"""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PySide6.QtGui import QColor

# -- palette ---------------------------------------------------------------

BG = "#F1E8D8"
TEXT = "#1A1A1A"
MUTED = "#8A8175"
ACCENT = "#D8CCBB"
BORDER = "#D8CCBB"
SURFACE = "#EEEAE1"
SURFACE_HOVER = "#E6E1D6"
INK = "#171717"
PAPER = "#FFFFFF"

# -- the dashboard's second surface ------------------------------------------

INK_RAISED = "#232220"
INK_LINE = "#3A3733"
#: TAN = walk-in, BROWN = online.
TAN = "#B8A68A"
BROWN = "#8A6F4E"
#: Page background; the light cards' warm neighbour.
CREAM = BG

#: Semantic status colours.
OK = "#4F7A5B"
WARN = "#B07D2B"
DANGER = "#A8452F"

#: Status green that stays readable on INK.
OK_LIGHT = "#7FD6A4"

#: Order-activity heatmap shades, darkest first.
HEATMAP_LEVELS = (
    "#2A2823",
    "#4A433A",
    "#7A6C58",
    "#B8A68A",
    "#F1E8D8",
)

#: Dark-card hover tint; INK_RAISED lifted 6% towards white.
INK_HOVER = "#302F2D"

#: Channel -> display label.
CHANNEL_LABELS = {
    "walk_in": "Walk-in",
    "online": "Online",
}

CHANNEL_COLOURS = {
    "walk_in": TAN,
    "online": BROWN,
}

#: Dot/hairline colour for a channel pill.
CHANNEL_DOT_COLOURS = {
    "walk_in": "#B08A54",
    "online": "#6E5233",
}

#: Dot/hairline colour per payment method.
METHOD_DOT_COLOURS = {
    "cash": "#6FAE7C",
    "gcash": "#5B8FBF",
    "card": "#C97B4A",
}

#: Payment method as stored -> as displayed.
METHOD_LABELS = {
    "cash": "Cash",
    "gcash": "GCash",
    "card": "Credit/Debit Card",
}

#: Status -> colour; unmapped falls back to MUTED.
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

#: Object names shared with the QSS.
OBJ_DEMO_BANNER = "demoBanner"
OBJ_STATUS = "statusPill"
OBJ_EMPTY = "emptyState"
OBJ_ERROR = "errorState"

# -- the sidebar -------------------------------------------------------------

OBJ_SIDEBAR = "sidebar"
OBJ_SIDEBAR_WORDMARK = "sidebarWordmark"
OBJ_SIDEBAR_GROUP = "sidebarGroup"
OBJ_SIDEBAR_ITEM = "sidebarItem"
#: Set "true" on the item for the current page.
PROP_ACTIVE = "active"

# -- the page header --------------------------------------------------------

OBJ_PAGE_HEADER = "pageHeader"
OBJ_PAGE_OVERFLOW = "pageOverflow"
OBJ_AVATAR = "avatar"

# -- the page sheet ---------------------------------------------------------

OBJ_PAGE_PANEL = "pagePanel"

# -- sign in ----------------------------------------------------------------

#: Petrol. The brand colour, and the only non-semantic colour on the screen.
PETROL = "#1F4E5F"
PETROL_DEEP = "#16333F"
#: Field caption and empty-field placeholder.
CAPTION = "#9B978F"
#: Sign-in button; deliberately not the petrol primary.
BUTTON = "#E5D7BE"
BUTTON_HOVER = "#DCCBAE"
#: The rule under a field. Weightens to FIELD_FOCUS on focus.
RULE = "#CFC6B5"
#: Focus ring on the entry; matches the sign-in button sand.
FIELD_FOCUS = "#E5D7BE"
#: Focus colour where it must carry 12px text.
FIELD_FOCUS_TEXT = "#8A6D3F"

OBJ_LOGIN_ROOT = "loginRoot"
OBJ_LOGIN_FORM = "loginForm"
OBJ_LOGIN_WORDMARK = "loginWordmark"
OBJ_LOGIN_CAPTION = "loginCaption"
OBJ_LOGIN_FIELD = "loginField"
OBJ_LOGIN_REVEAL = "loginReveal"
OBJ_LABEL_REST = "loginLabel"
OBJ_LABEL_LIFTED = "loginLabelLifted"
OBJ_LOGIN_BUTTON = "loginButton"
OBJ_LOGIN_ERROR = "loginError"
OBJ_LOGIN_FOOT = "loginFoot"

# -- dashboard --------------------------------------------------------------

PROP_DARK = "dark"
PROP_HOVER = "hover"

OBJ_CARD = "card"
OBJ_CARD_TITLE = "cardTitle"
OBJ_CARD_SUBTITLE = "cardSubtitle"
OBJ_PILL = "pill"
OBJ_ICON_BUTTON = "iconButton"
OBJ_DASH_HEAD = "dashboardHead"
OBJ_DASH_SUBHEAD = "dashboardSubhead"
OBJ_BTN_PRIMARY = "btnPrimary"
OBJ_BTN_SECONDARY = "btnSecondary"
OBJ_SECTION_LABEL = "sectionLabel"
OBJ_ROW_NAME = "rowName"
OBJ_ROW_META = "rowMeta"
OBJ_ROW_AMOUNT = "rowAmount"
OBJ_LEGEND = "legendLabel"
OBJ_SEARCH_FIELD = "searchField"


def _money_from_env(name: str, fallback: str) -> Decimal:
    try:
        return Decimal(os.getenv(name, fallback))
    except (InvalidOperation, TypeError, ArithmeticError):
        return Decimal(fallback)


#: Annual revenue target the dashboard plots against; read from the environment.
SALES_TARGET = _money_from_env("RENTWHEELS_SALES_TARGET", "6000000")


def colour_for(status: str | None) -> QColor:
    """The colour for a status string, never ``None``."""
    return QColor(STATUS_COLOURS.get((status or "").strip().lower(), MUTED))


def stylesheet_path() -> Path:
    return Path(__file__).resolve().parent / "staff.qss"


def load_stylesheet() -> str:
    return stylesheet_path().read_text(encoding="utf-8")
