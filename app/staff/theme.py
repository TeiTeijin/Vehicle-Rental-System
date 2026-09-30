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
NAVBAR = "#FFFFFF"
TEXT = "#1A1A1A"
MUTED = "#8A8175"
ACCENT = "#D8CCBB"
BORDER = "#D8CCBB"
SURFACE = "#EEEAE1"
SURFACE_HOVER = "#E6E1D6"
INK = "#171717"
PAPER = "#FFFFFF"

# -- the dashboard's second surface ------------------------------------------
# The dashboard is the only staff screen with a dark half. It is a different
# object from the one the rest of the app sits on, not a recolouring of it: the
# revenue figure and the activity grid are the two things a manager wants to
# find instantly, and putting them on the page background would leave them
# competing with eight light cards for the same attention.
#
# INK is the card. INK_RAISED is a 6% lift of it and exists for hover on those
# cards -- a hover tint derived from the card's own colour, so a dark card does
# not need a second hand-picked value to stay coherent.

INK_RAISED = "#232220"
INK_LINE = "#3A3733"
#: Surface mix for the two channel curves and the sales line. TAN carries a
#: rental that was walked in, BROWN one taken online -- both sit close enough to
#: the warm neutrals to belong on PAPER without reading as a status colour.
TAN = "#B8A68A"
BROWN = "#8A6F4E"
#: The page background doubles as the light cards' warm neighbour. Named
#: separately so a card can be "the cream" without importing the whole app's
#: background role into it.
CREAM = BG

#: Semantic status colours. Green/amber/red rather than the existing neutrals,
#: because "is this car fine?" and "is this money late?" are the two questions
#: the staff app exists to answer, and neither has an answer in beige.
OK = "#4F7A5B"
WARN = "#B07D2B"
DANGER = "#A8452F"

#: Hover on a dark card: INK_RAISED lifted 6% towards white. Computed rather
#: than eyeballed, and kept next to INK_RAISED so the two cannot drift apart.
INK_HOVER = "#302F2D"

#: Channel -> its curve colour, and the label the dashboard shows for it. The
#: stored values are the database enum; these are what a human reads.
CHANNEL_LABELS = {
    "walk_in": "Walk-in",
    "online": "Online",
}

CHANNEL_COLOURS = {
    "walk_in": TAN,
    "online": BROWN,
}

#: Payment method as stored -> as displayed. Not `.title()`: "gcash" titles to
#: "Gcash", and the brand is "GCash". "card" is stored generically because a
#: single column cannot say which scheme, so it is labelled for both.
METHOD_LABELS = {
    "cash": "Cash",
    "gcash": "GCash",
    "card": "Credit/Debit Card",
}

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

# -- sign in ----------------------------------------------------------------
# The sign-in sits on the app's own background, with no panel behind it. A
# card on a differently-coloured field read as a separate object dropped into
# the middle of a window that is otherwise one flat surface.
#
# The accent is petrol rather than the amber that automotive work suggests on
# purpose -- amber already means WARN in STATUS_COLOURS, and a brand colour
# that looks like an alert is a trap for anyone reading this app quickly.

#: Petrol. The brand colour, and the only non-semantic colour on the screen.
PETROL = "#1F4E5F"
PETROL_DEEP = "#16333F"
#: The field caption, and the placeholder that floats over an empty field.
#: Used by the sign-in, which is the only screen that needs it.
CAPTION = "#9B978F"
#: The sign-in button: a warm sand, deliberately not the petrol primary, so it
#: does not read as the most important control in the app.
BUTTON = "#E5D7BE"
BUTTON_HOVER = "#DCCBAE"
#: The rule under a field. Weightens to FIELD_FOCUS on focus.
RULE = "#CFC6B5"
#: Keyboard focus ring on the entry itself. The sign-in's button sand, so the
#: one accent on that page is a single colour.
FIELD_FOCUS = "#E5D7BE"
#: The focus colour where it has to carry 12px text. #E5D7BE on white is
#: 1.42:1 and would leave the lifted caption invisible, so the caption takes a
#: darker shade of the same hue -- 4.84:1, and still recognisably the sand.
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
# The dashboard's cards are set with a dynamic property rather than an object
# name, because "this card is dark" is a variant of a card and not a different
# kind of widget: `Card(dark=True)` sets the property and the stylesheet picks
# it up. Object names are for identity, properties for state.
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
    """A Decimal from the environment, or `fallback` if it is not usable.

    Falls back rather than raising. A mistyped environment variable should not
    stop the staff app from starting -- that is the one screen nobody can open
    the branch without -- and the alternative is a dashboard showing a wrong
    number because `Decimal("six million")` raised on import.
    """
    try:
        return Decimal(os.getenv(name, fallback))
    except (InvalidOperation, TypeError, ArithmeticError):
        return Decimal(fallback)


#: The annual revenue target the dashboard plots against.
#:
#: Read from the environment because a target is a business decision and belongs
#: to whoever runs the branch, not to a constant in a stylesheet module. It is
#: deliberately NOT the ₱20,000,000 in the design brief: at roughly ₱500,000 a
#: month, that figure would leave the progress bar permanently near zero and
#: stop carrying information. Six million is round, is roughly what the branch
#: is actually turning over, and leaves the year about half-sold by September,
#: so the bar means something.
SALES_TARGET = _money_from_env("RENTWHEELS_SALES_TARGET", "6000000")


def colour_for(status: str | None) -> QColor:
    """The colour for a status string, never ``None``."""
    return QColor(STATUS_COLOURS.get((status or "").strip().lower(), MUTED))


def stylesheet_path() -> Path:
    return Path(__file__).resolve().parent / "staff.qss"


def load_stylesheet() -> str:
    return stylesheet_path().read_text(encoding="utf-8")
