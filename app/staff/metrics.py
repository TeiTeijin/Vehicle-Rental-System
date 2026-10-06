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

#: Height of a row in the New Rental brand list. Matches NAV_ROW_H so the two
#: active-row lists -- the sidebar's pages and the popover's brand list -- have
#: the same rhythm and the highlight reads as one idea.
SECTION_ROW_H = 44

# -- the page ---------------------------------------------------------------
PAGE_MARGIN_X = 26
PAGE_MARGIN_Y = 22
PAGE_PANEL_RADIUS = 36
PAGE_PANEL_PAD = 16
HEADER_H = 44
AVATAR = 36

#: Gap between a page's fixed left column and its scrolling content. Wide enough
#: that the column's border does not touch the fleet cards.
PAGE_BODY_ROW_GAP = 18

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

# -- vehicle cards ----------------------------------------------------------
#: The "Check details" button radius; the image and card corners derive from it.
VEHICLE_BTN_RADIUS = 8
VEHICLE_IMAGE_RADIUS = VEHICLE_BTN_RADIUS
#: Twice the button radius: the card is deliberately rounder than its contents.
VEHICLE_CARD_RADIUS = VEHICLE_BTN_RADIUS * 2

VEHICLE_CARD_PAD = 14
VEHICLE_INNER_GAP = 10
VEHICLE_IMAGE_H = 150
VEHICLE_BTN_H = 32
VEHICLE_NAME_SIZE = 18
#: One text line, plus the price/button row's floor.
VEHICLE_LINE_H = 24
VEHICLE_META_H = 18
#: Hover lift on a card, in pixels; the card reserves this at its top.
HOVER_LIFT = 3
#: Hover: the photo zooms in, and the action strip fades up over it.
HOVER_ZOOM = 1.06
HOVER_ZOOM_MS = 180
HOVER_REVEAL_MS = 140
#: Height of the gradient the revealed action strip sits against.
VEHICLE_OVERLAY_H = 56
#: How far below its resting place the action strip starts, at reveal zero.
VEHICLE_OVERLAY_OFFSET = 8
#: Fixed so a skeleton reserves exactly the space its card will occupy. The
#: last row is the price line; the details button lives on the photo overlay.
VEHICLE_CARD_H = (
    VEHICLE_CARD_PAD * 2
    + VEHICLE_IMAGE_H
    + VEHICLE_INNER_GAP
    + VEHICLE_LINE_H
    + 6
    + VEHICLE_META_H
    + VEHICLE_INNER_GAP
    + VEHICLE_LINE_H
    + HOVER_LIFT
)

VEHICLE_CARD_MIN_W = 240
VEHICLE_CARD_MAX_W = 360
VEHICLE_CARD_GUTTER = 16

# -- the New Rental filter popover --------------------------------------------
#: Whole popover, categories plus options. Wide enough for two columns of 44px
#: rows without the passenger row having to wrap.
FILTER_POPOVER_W = 520
#: Fixed height: the tallest pane is the brand list, and a popover that grows with
#: the fleet would jump under the cursor as brands load.
FILTER_POPOVER_H = 420
#: Inset inside the popover's card, as (left, top, right, bottom). Matches
#: CARD_PADDING so the options sit on the same inset as the card's own content.
FILTER_POPOVER_PAD = (16, 16, 16, 16)
#: The left category column. Narrower than the nav sidebar (210) because these
#: labels are one or two words.
POPOVER_CATEGORY_W = 180
#: Cap on either column's scroll area, so a long brand list cannot push the
#: popover past FILTER_POPOVER_H.
POPOVER_PANE_MAX_H = 340
#: Height of a filter row: a checkbox, or a spin box.
FILTER_ROW_H = 30
#: Gap between a group's heading and its options, and between options.
FILTER_GAP = 6
#: Gap between groups.
FILTER_GROUP_GAP = 18
#: Height of the pinned search/filter bar above the fleet list. Not counted in
#: the popover's height: the bar is on the page, the popover floats over it.
FILTER_BAR_H = 58
#: Gap between brand sections in the New Rental fleet list.
BRAND_ROW_GAP = 22
#: Quiet period after a scroll gesture before the active section is read again.
#: Long enough to ignore the intermediate values of a flick, short enough that
#: the highlight still feels attached to the list.
SECTION_TRACK_MS = 120
#: How far from the bottom of the fleet list more brands are fetched, in pixels.
#: Roughly two card rows, so the next section is on screen before it is needed.
LOAD_MORE_PX = 420

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

MAX_RATE = 1_000_000
