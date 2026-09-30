"""The dashboard's measurements, in one place.

Qt stylesheets cannot be read back at runtime, so a radius written in QSS and a
radius written in Python are two values that can disagree with no error. The
dashboard's grid is the strictest layout in the app -- three columns of cards
that must align on their edges and their internal spacing -- so the numbers
live here and the stylesheet's selectors are asserted against them in
``tests/test_staff_dashboard.py``.

Everything is a plain int. Device-pixel scaling happens in Qt; these are logical
pixels and the whole app is authored at 1x.
"""

from __future__ import annotations

# -- cards ------------------------------------------------------------------
#: 28px. Large enough that the corner reads as a deliberate choice rather than
#: a default, and the same on light and dark cards so the grid is even.
CARD_RADIUS = 28

#: 26px. Comfortable against a 28px radius -- slightly less than the corner, so
#: text never looks like it is escaping the curve.
CARD_PADDING = 26

#: 16px between cards. Wider and the grid stops reading as one block; narrower
#: and the light cards on cream begin to lose their edges.
CARD_GUTTER = 16

# -- buttons ----------------------------------------------------------------
ICON_BUTTON = 40
ICON_BUTTON_RADIUS = 12

#: A filled action button, e.g. "New Rental".
BTN_HEIGHT = 44
BTN_RADIUS = 12
BTN_PAD_X = 18

# -- pills ------------------------------------------------------------------
PILL_HEIGHT = 28
PILL_RADIUS = 999  # a stadium, not a circle

# -- typography -------------------------------------------------------------
#: The revenue figure. 52px is the largest text in the app, which is correct:
#: it is the one number the screen exists to show.
HERO_SIZE = 52
#: The channel totals beside it. Smaller than the hero so the eye lands on the
#: revenue first and the split second.
SECONDARY_HERO_SIZE = 22
TITLE_SIZE = 16
SUBTITLE_SIZE = 13
CAPTION_SIZE = 13
BODY_SIZE = 15
#: The tiny month/row labels inside charts.
AXIS_SIZE = 11

# -- the heatmap ------------------------------------------------------------
#: 13px squares. Below about 10px the five shades stop being distinguishable at
#: all and the grid reads as one grey block.
CELL = 13
CELL_GAP = 5

# -- motion -----------------------------------------------------------------
#: 600ms for a curve to draw itself. Long enough to read as deliberate, short
#: enough that a member of staff waiting on a number is not kept waiting.
DRAW_MS = 600
#: Per-cell delay in the heatmap's stagger. 60 cells x 6ms is about 360ms of
#: total stagger, which reads as a wave rather than a flicker.
CELL_STAGGER_MS = 6
HOVER_MS = 120
FADE_MS = 400

#: The window the dashboard is designed at. The shell opens here, and it is also
#: the size the screenshot tests render at, so a layout that only works at some
#: other size is caught rather than shipped.
REFERENCE_W = 1440
REFERENCE_H = 900

#: Below this width the three-column grid becomes two, and below
#: `NARROW_W` one. Set from the brief's 1440 reference: the sidebar's own
#: minimum plus two cards' worth of content.
TWO_COL_W = 1180
NARROW_W = 900
