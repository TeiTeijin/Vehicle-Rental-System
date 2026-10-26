# Staff UI Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry the warm counter-rental visual language into Bookings (page plus six modals), Fleet, Customers, and Maintenance without changing their behaviour.

**Architecture:** Add one shared `ui_kit` and an opt-in `variant="warm"` table mode. Bookings and its modals adopt the kit first; Fleet, Customers, and Maintenance adopt the same page recipe in parallel. Out-of-scope pages keep the existing `pagePanel` defaults.

**Tech Stack:** Python 3, PySide6 widgets, QSS, pytest, SQLAlchemy.

**Spec:** `docs/superpowers/specs/2026-10-26-staff-ui-consistency-design.md`

## Global Constraints

- Scope: Bookings, its six modals, Fleet, Customers, Maintenance. Do not restyle Dashboard, Reports, Settings, login, or the sidebar.
- Card radius 18px, modal width 540px, page/card palette `#F1E8D8` page, `#FFFFFF` card, `#171717` ink, `#6B5E4F` muted, `#D8CCBB` border, `#EFE6D5` cream, `#9B2C1F` destructive.
- Keep compatibility attributes listed in the spec; hidden bridge controls are allowed.
- Bookings keeps Ref at column 0, Paid at 7, Owed at 8. Do not renumber those.
- The tools container stays the single `_tools_panel` host; its white filter card and cream selection bar are children, so containment assertions still pass.
- Booking details keeps reference code, pickup/return times, branches, Money, Inspections, and Penalties below the new summary; the modal body scrolls.
- Filters: Bookings status chips, Fleet status chips, Customers licence chips, Maintenance status chips. Maintenance keeps its type dropdown; hidden bridge combos stay in sync.
- Cancel stores `"<chip> — <detail>"` (Other requires detail); Paid so far = `amount_paid + deposit_held`, refund = `deposit_held`.
- Damage chips toggle words in `damage_edit`; only `damage_notes` is written.
- Settlement PaymentPanel uses the 540px shell and tiles but `show_kind=False`.
- Existing tests may be updated only where they pin old visual containers; behaviour assertions stay intact.
- Do not run tests during implementation. Write tests and implementation, then run the focused suites and the full suite once at the end, as the user requested.
- Do not commit from a subagent. The orchestrator commits after verification.
- No new dependencies.

## Review Focus

- A page with no selection must still render a calm empty selection bar and keep every action disabled.
- A page with an error/empty load must not leave the warm selection bar or stale summary visible.
- Long customer/vehicle names must not clip the table or push actions off the card.
- A modal opened for a completed/cancelled booking must show honest money tiles and not offer impossible actions.
- Settings, Reports, and Dashboard must remain pixel-identical after the QSS additions.

---

### Task 1: Shared warm kit

**Files:**
- Create: `app/staff/ui_kit.py`
- Modify: `app/staff/tables.py`
- Modify: `app/staff/overlay.py`
- Modify: `app/staff/staff.qss`
- Test: `tests/test_ui_kit.py`

**Interfaces:**
- Produces: `PageHeading(title, subtitle="", parent=None)` with `.title_label`, `.subtitle_label`, `.set_subtitle(text)`.
- Produces: `WarmPanel(parent=None, *, padding=16)` with `.body` (`QVBoxLayout`) and `variant="warm"`, `objectName="pagePanel"`.
- Produces: `StatusChipRow(options, parent=None)` where `options` is `Sequence[tuple[str, str]]`; `.changed = Signal(str)`, `.select(key)`, `.selected`, `.buttons`.
- Produces: `SelectionBar(parent=None)` with `.summary_label`, `.actions` (`QHBoxLayout`), `.set_summary(text)`, `.set_active(bool)`.
- Produces: `MetricTile(label, value, parent=None)` with `.label`, `.value_label`, `.set_value(text)`.
- Produces: `ChipGroup(options, parent=None)` with the same API as `StatusChipRow`.
- Produces: `BookingModal(eyebrow, title, subtitle="", parent=None)` with `.body`, `.footer_layout`, `.error_label`, `.set_back(text="Back")`, `.set_primary(text, danger=False)`, `.card`, `.title_label`.
- Produces: `LoadStateTable(..., variant="default", row_height=None)`; `Column(..., kind="text", weight=None, colour=None, wrap=False)`; kinds `text`, `status`, `payment`, `muted`, `strong`, `money`.
- Produces: `FrostedOverlay(window, *, card_width=OVERLAY_CARD_W)` and `.set_card_width(width)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_ui_kit.py` covers: chip selection emits once and updates `selected`; `SelectionBar.set_active(False)` disables actions and shows the prompt; `MetricTile.set_value` updates; `LoadStateTable(variant="warm")` sets the warm property, row height, and renders a status chip; `FrostedOverlay(..., card_width=540)` sets the scroll width; `BookingModal` is 540 wide and exposes eyebrow/title/footer.

- [ ] **Step 2: Implement the kit**

Create `ui_kit.py` with the interfaces above. Use `QPushButton` for chips (`objectName="choiceChip"`, checkable, auto-exclusive via `QButtonGroup`). Use `Card(radius=18)` for `BookingModal`'s card, but keep the modal body padding at 24. `SelectionBar` uses `objectName="selectionBar"` and a muted prompt.

- [ ] **Step 3: Extend `LoadStateTable`**

Add the optional `variant` and `row_height` parameters. For `variant="warm"`, set table/header object names or properties, turn off alternating colours, enable word wrap, set 56px rows and 44px header, and style text columns from `Column.kind`/`weight`/`colour`. Keep the default path unchanged.

- [ ] **Step 4: Extend `FrostedOverlay`**

Add a keyword-only `card_width` parameter defaulting to `OVERLAY_CARD_W`; store it and use it in `setFixedWidth`. Add `set_card_width` that also re-applies `_fit()` if visible.

- [ ] **Step 5: Add scoped QSS**

Append selectors for `QFrame#pagePanel[variant="warm"]`, `QPushButton#choiceChip`, `QFrame#selectionBar`, `QFrame#warmMetricTile`, `QTableView[variant="warm"]`, `QHeaderView#warmHeader::section`, and the `BookingModal` object names. Do not change existing selectors.

- [ ] **Step 6: Compile only**

Run `python -m py_compile app/staff/ui_kit.py app/staff/tables.py app/staff/overlay.py`. Do not run pytest yet.

---

### Task 2: Bookings page

**Files:**
- Modify: `app/staff/pages/bookings.py`
- Test: `tests/test_ui_bookings.py`

**Interfaces:**
- Consumes: `PageHeading`, `WarmPanel`, `StatusChipRow`, `SelectionBar`, `LoadStateTable(variant="warm")` from Task 1.
- Preserves: `_tools_panel`, `_data_panel`, `search`, `status_combo`, `channel_combo`, `from_edit`, `to_edit`, `overdue_check`, `clear_button`, `count_caption`, `_action_buttons`, all named buttons, `table`, `selected_booking_id()`, `focus_booking()`.

- [ ] **Step 1: Write the failing tests**

`tests/test_ui_bookings.py` covers: page has a `PageHeading`; tools/data panels are warm; status chips sync to `status_combo`; selection bar summary names the booking; Ref/Paid/Owed indexes still 0/7/8; warm table renders chips; Load more raises the cap; existing filters still narrow rows.

- [ ] **Step 2: Reorder `_booking_rows` to the reference columns**

Ref, Customer, Vehicle (two lines), Dates (two lines), Status, Payment, Total due, Paid, Owed, Channel, Booked. Keep the search-before-cap query logic untouched. Put the display money strings where they are today for Paid/Owed.

- [ ] **Step 3: Rebuild the page shell**

Page heading with inline `count_caption`. Warm tools host containing the white search/channel/date/overdue/clear card, the hidden `status_combo` bridge, the status chip row, and the cream selection bar; keep the existing `_tools_panel` as the single host so containment assertions pass. Warm data panel with the table and a footer (`Showing N of latest M`, Load more only while the cap is reached and below 500). Keep the skeleton but restyle its panels as warm.

- [ ] **Step 4: Rebuild the action bar as a selection bar**

Move the existing action buttons into `SelectionBar.actions`; keep `_action_buttons` and `_refresh_actions`. Summary text: `#ref · customer · vehicle`; empty prompt otherwise. Cancel button text `Cancel booking`, destructive style.

- [ ] **Step 5: Implement Load more**

Add a page-local cap starting at `prefs.rows_limit(100)`; clicking Load more raises it by 50 up to 500 and reloads. `_booking_rows` accepts an optional cap override; default behaviour and search-before-cap stay unchanged.

- [ ] **Step 6: Compile only**

Run `python -m py_compile app/staff/pages/bookings.py`. Do not run pytest yet.

---

### Task 3: Booking modals

**Files:**
- Modify: `app/staff/booking_panels.py`
- Modify: `app/staff/pages/bookings.py` (modal construction arguments only)
- Test: `tests/test_ui_bookings.py`

**Interfaces:**
- Consumes: `BookingModal`, `MetricTile`, `ChipGroup` from Task 1.
- Preserves: every field and signal listed in the spec's compatibility section; `FrostedOverlay` opens at 540px for these panels.

- [ ] **Step 1: Write the failing tests**

Cover: each panel is a `BookingModal` with the reference eyebrow/title; details tiles show Total/Paid/Owed; cancel reason chips set `reason_edit`; check-in fuel/damage chips set `fuel_combo`/`damage_edit`; check-out computes distance and floor hint; extend quick chips move the date and preview text; payment kind/method chips sync the hidden combos and the footer total; GCash gating still works.

- [ ] **Step 2: Add `BookingModal`-based panels**

Replace the `_StepBase` inheritance with `BookingModal`, keeping `_body` as an alias for `body` so old tests keep working. Add the reference sections/tiles/chips. Booking details keeps reference code, pickup/return times, branches, Money, Inspections, and Penalties below the summary tiles in the scrollable body. Cancel money uses `amount_paid + deposit_held` for Paid so far and `deposit_held` for Refund due; the reason becomes `"<chip> — <detail>"` with Other requiring detail. Do not remove or rename compatibility fields.

- [ ] **Step 3: Wire page data into the modals**

`open_cancel` passes the selected booking's paid/refund summary; `open_details` passes nothing new because the panel loads its own row; `open_record_payment` passes total/paid; check-out passes pickup mileage.

- [ ] **Step 4: Open at 540px**

In `_overlay_panel`, construct `FrostedOverlay(window, card_width=540)`; verify the existing geometry/dismiss behaviour.

- [ ] **Step 5: Compile only**

Run `python -m py_compile app/staff/booking_panels.py app/staff/pages/bookings.py`. Do not run pytest yet.

---

### Task 4: Fleet page

**Files:**
- Modify: `app/staff/pages/fleet.py`
- Test: `tests/test_ui_lists.py`

**Interfaces:**
- Consumes: Task 1 kit.
- Preserves: `search`, `status_combo`, `clear_button`, `_tools_panel`, `_data_panel`, `add_button`, `edit_button`, `details_button`, `bulk_button`, `retire_button`, `table`, skeleton/loading/empty states.

- [ ] **Step 1: Write the failing tests**

Page heading present; panels warm; status chips sync to `status_combo`; selection bar summary shows plate/make/model; all action buttons keep their enabled rules; warm table rows render.

- [ ] **Step 2: Rebuild the page shell**

Same recipe as Bookings: heading, warm tools card, status chip row, warm table card, warm skeleton. Keep the existing filter logic.

- [ ] **Step 3: Rebuild the action row**

Move the existing buttons into `SelectionBar`; summary from the selected vehicle; danger styling for Retire.

- [ ] **Step 4: Compile only**

`python -m py_compile app/staff/pages/fleet.py`. No pytest yet.

---

### Task 5: Customers page

**Files:**
- Modify: `app/staff/pages/customers.py`
- Test: `tests/test_ui_lists.py`

**Interfaces:**
- Consumes: Task 1 kit.
- Preserves: `search`, `licence_combo`, `clear_button`, `_tools_panel`, `_data_panel`, `add_button`, `edit_button`, `details_button`, `settle_button`, `deactivate_button`, `export_button`, `table`, skeleton/loading/empty states.

- [ ] **Step 1: Write the failing tests**

Page heading present; panels warm; licence chips/select remain; selection summary shows name/licence; action enable rules unchanged; warm table.

- [ ] **Step 2: Rebuild the page shell**

Heading, warm tools card (search + licence-state chip row over a hidden `licence_combo` + clear), warm table card and skeleton.

- [ ] **Step 3: Rebuild the action row**

Move buttons into `SelectionBar`; summary from selected customer; danger styling for Deactivate.

- [ ] **Step 4: Compile only**

`python -m py_compile app/staff/pages/customers.py`. No pytest yet.

---

### Task 6: Maintenance page

**Files:**
- Modify: `app/staff/pages/maintenance.py`
- Test: `tests/test_ui_lists.py`

**Interfaces:**
- Consumes: Task 1 kit.
- Preserves: `search`, `type_combo`, `status_combo`, `clear_button`, `_tools_panel`, `_data_panel`, due/log tables, all action buttons, skeleton/loading/empty states.

- [ ] **Step 1: Write the failing tests**

Page heading present; panels warm; status/type filters still work; selection bar summary shows the selected job; both tables use the warm variant; action enable rules unchanged.

- [ ] **Step 2: Rebuild the page shell**

Heading, warm tools card, status chip row, warm due/log cards and skeleton.

- [ ] **Step 3: Rebuild the action row**

Move existing buttons into `SelectionBar`; summary from the selected job/vehicle.

- [ ] **Step 4: Compile only**

`python -m py_compile app/staff/pages/maintenance.py`. No pytest yet.

---

### Task 7: Verification

**Files:**
- Modify: tests only if a compatibility assertion needs the new visual values.
- Verify: `tests/test_ui_kit.py`, `tests/test_ui_bookings.py`, `tests/test_ui_lists.py`, `tests/test_staff_pages.py`, `tests/test_issue_fixes.py`, then the full suite.

- [ ] **Step 1: Run the focused new tests**

`$env:QT_QPA_PLATFORM='offscreen'; python -m pytest tests/test_ui_kit.py tests/test_ui_bookings.py tests/test_ui_lists.py -q -o addopts=`

- [ ] **Step 2: Run the existing staff suites**

`python -m pytest tests/test_staff_pages.py tests/test_issue_fixes.py tests/test_staff_dashboard.py tests/test_staff_settings.py -q -o addopts=`

- [ ] **Step 3: Run the full suite once**

`python -m pytest tests -q -o addopts=`

- [ ] **Step 4: Capture screenshots**

Use a copy of the demo database at 1440×1000: Bookings page, its six modals, Fleet, Customers, Maintenance. Compare with the reference images and fix obvious spacing/colour issues.

- [ ] **Step 5: Commit**

`git add app/staff/ui_kit.py app/staff/tables.py app/staff/overlay.py app/staff/staff.qss app/staff/pages/bookings.py app/staff/booking_panels.py app/staff/pages/fleet.py app/staff/pages/customers.py app/staff/pages/maintenance.py tests/test_ui_kit.py tests/test_ui_bookings.py tests/test_ui_lists.py docs/superpowers/...` then commit with a message describing the UI consistency pass.
