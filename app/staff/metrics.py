"""The dashboard's measurements, in one place.

Qt stylesheets cannot be read back at runtime, so a radius written in QSS and a
radius written in Python are two values that can disagree with no error. The
dashboard's grid is the strictest layout in the app -- two rows of
differently-proportioned cards that must align on their edges and their internal
spacing -- so the numbers live here and the stylesheet's selectors are asserted
against them in ``tests/test_staff_dashboard.py``.

**Breakpoints are content widths, not window widths.** The card grid is told
which layout to use by how wide *it* is, which is the window minus the sidebar
minus the page margins. Storing window widths here and comparing them against a
widget's own width is the bug the old 1180/900 pair encoded: at the shipped
1440 window the main area is 1146px, so a "1180 means three columns" threshold
picked two columns on a 1440px screen. The numbers below are the content widths
the layout actually has to fit.

Everything is a plain int. Device-pixel scaling happens in Qt; these are logical
pixels and the whole app is authored at 1x.
"""

from __future__ import annotations

# -- the window --------------------------------------------------------------
REFERENCE_W = 1440
REFERENCE_H = 900

WINDOW_MIN_W = 900
WINDOW_MIN_H = 640

# -- the sidebar ------------------------------------------------------------
SIDEBAR_W = 210
SIDEBAR_MARGIN = 16
SIDEBAR_RADIUS = 28
SIDEBAR_PAD = 18

NAV_ROW_H = 44
NAV_GAP = 8
NAV_ICON = 16
NAV_ACTIVE_BAR = 3

# -- the page ---------------------------------------------------------------
PAGE_MARGIN_X = 26
PAGE_MARGIN_Y = 22
PAGE_PANEL_RADIUS = 36
PAGE_PANEL_PAD = 16
HEADER_H = 44
AVATAR = 36

# -- cards ------------------------------------------------------------------
#: 28px, the same on light and dark cards.
CARD_RADIUS = 28

#: 26px, just inside the 28px corner.
CARD_PADDING = 26

#: 16px between cards.
CARD_GUTTER = 16

# -- the card grid ----------------------------------------------------------
ROW1_H = 230
ROW2_H = 300

REVENUE_SHARE = 35
CHANNELS_SHARE = 65
ACTIVITY_SHARE = 30
TRANSACTIONS_SHARE = 38
TARGET_SHARE = 32

WIDE_CONTENT_W = 1140
MEDIUM_CONTENT_W = 760

# -- buttons ----------------------------------------------------------------
ICON_BUTTON = 40
ICON_BUTTON_RADIUS = 12

BTN_HEIGHT = 48
BTN_RADIUS = 12
BTN_PAD_X = 18

# -- pills ------------------------------------------------------------------
PILL_HEIGHT = 28
PILL_RADIUS = 999  # a stadium, not a circle

# -- typography -------------------------------------------------------------
#: Revenue figure size.
HERO_SIZE = 38
#: Floor `HeroNumber` shrinks to before eliding.
HERO_MIN_SIZE = 22
#: The sales target headline. The brief's ~32px regular.
TARGET_SIZE = 32
#: Card titles: 18px on light and dark alike.
TITLE_SIZE = 18
SUBTITLE_SIZE = 13
CAPTION_SIZE = 13
BODY_SIZE = 15
#: The tiny month/row labels inside charts.
AXIS_SIZE = 11
#: Axis-label size.
AXIS_SIZE_LARGE = 12

# -- the heatmap ------------------------------------------------------------
#: Derived heatmap cell size, clamped to the range below.
CELL_MIN = 11
CELL_MAX = 18
CELL_GAP = 5
#: 8px, per the brief.
CELL_RADIUS = 8
#: Left gutter for the Mon/Wed/Fri labels.
HEATMAP_LABEL_GUTTER = 30
#: Room for the month row above the cells and the "Less/More" legend below.
HEATMAP_TOP = 18
HEATMAP_BOTTOM = 20

# -- motion -----------------------------------------------------------------
#: 600ms for a curve to draw itself.
DRAW_MS = 600
#: Per-cell delay in the heatmap's stagger.
CELL_STAGGER_MS = 6
HOVER_MS = 120
FADE_MS = 400
