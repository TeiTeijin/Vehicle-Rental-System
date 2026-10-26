# Staff UI Issues Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed staff-app UI defects, including the dashboard sizing/dead controls, Check Details image/3D failures, schedule backgrounds, payment-entry clarity, and sidebar/settings defects.

**Architecture:** Five mostly independent workstreams, each in its own worktree, then cherry-picked into `fix/staff-ui-issues`. The main staff process must never create a `QWebEngineView`; 3D previews run in a separate process so a WebEngine failure cannot take down the staff app.

**Tech Stack:** Python 3.13+, PySide6 6.11, SQLAlchemy, pytest, Qt offscreen tests.

**Spec:** This plan implements the confirmed findings from the 2026-10-27 staff UI investigation.

## Global Constraints

- Work only in the assigned worktree; commit there; do not push.
- Write the failing regression test first, watch it fail, then implement.
- Never connect to or write to live MySQL. Tests use in-memory or throwaway SQLite.
- Run GUI tests with `QT_QPA_PLATFORM=offscreen`.
- Preserve the `models -> services -> UI` direction. Use `Decimal` for money.
- Do not edit files outside the task's assigned list. In particular, only Task 2 may edit `app/staff/staff.qss`.
- Do not leave dead controls: every visible button must do something useful or be hidden/disabled with clear explanatory copy.
- No embedded `QWebEngineView` in the main staff process. 3D opens a separate process window.
- Every task ends with a commit and a short report: files changed, tests run, result, and any uncertainty.

## Review Focus

- A user clicks the 3D tab and the staff app must remain alive even if the preview process fails.
- A user types in Recent Transactions search or presses Filter and the visible list must actually change.
- A user opens Check Details from a card with an already-cached image and must not see a pointless reload.
- A user clicks Rent after “Not available” must not get a wizard with different dates.
- A user collapses the sidebar and must get a usable icon rail, not a clipped logo/text.

---

### Task 1: Dashboard reliability and controls

**Files:**
- Modify: `app/staff/pages/dashboard.py`
- Modify: `app/staff/pages/base.py`
- Modify: `app/staff/cards.py`
- Modify: `app/staff/dashboard_charts.py`
- Modify: `app/services/dashboard_service.py`
- Test: `tests/test_staff_dashboard_fixes.py` (new)

**Interfaces:**
- `StaffPage.overflow_actions()` returns `list[tuple[str, Callable]]`; default empty. `StaffPage` hides `overflow_button` when empty and shows a `QMenu` when actions exist.
- `DashboardPage` exposes `transaction_search`, `transaction_filter`, and methods `set_transaction_query(text)` and `set_transaction_filter(key)` for tests.
- `DashboardPage` displays at most five matching recent transactions, searching/filtering the loaded recent set (fetch up to 50).

- [ ] **Step 1: Write failing tests** for the revenue-picker sizing policy after async fill, search toggling/visible-result filtering, filter menu applying method/status, the header overflow menu, error/loading state, and customer-name tooltip.
- [ ] **Step 2: Run the new tests and verify they fail for the expected reasons.**
- [ ] **Step 3: Implement the fixes** in the assigned files. Use `QComboBox.SizeAdjustPolicy.AdjustToContents` and `adjustSize()` after fill. Wire the search and filter controls to real state. Fetch up to 50 recent rows and render at most five after filtering. Add a loading/error banner with retry. Add customer-name tooltips. Remove the misleading pointing-hand cursor on non-clickable chart areas.
- [ ] **Step 4: Run `tests/test_staff_dashboard_fixes.py`, `tests/test_staff_dashboard.py`, and any affected page tests; verify pass.**
- [ ] **Step 5: Commit on the task branch.**

---

### Task 2: “When is it out?” white backgrounds

**Files:**
- Modify: `app/staff/rental_wizard.py`
- Modify: `app/staff/staff.qss`
- Test: `tests/test_schedule_visuals.py` (new)

**Interfaces:**
- `_date_cell` and `_legend_item` hosts use object name `wizardStep` (transparent inherited rule).
- `QFrame#wizardLegend` keeps its rounded frame but has `background: transparent`.

- [ ] **Step 1: Write failing tests** asserting the date/legend item hosts are transparent, the legend frame has no cream fill, and the swatches/day tiles remain styled.
- [ ] **Step 2: Run the new tests and verify they fail.**
- [ ] **Step 3: Implement the background cleanup.** Keep the legend color swatches, `plateChip`, `dayTile`, and `statusChip` backgrounds.
- [ ] **Step 4: Run the new tests plus the schedule-panel tests.**
- [ ] **Step 5: Commit on the task branch.**

---

### Task 3: Record Payment amount clarity

**Files:**
- Modify: `app/staff/booking_panels.py`
- Modify: `app/staff/pages/bookings.py`
- Test: `tests/test_record_payment_panel.py` (new)

**Interfaces:**
- `PaymentPanel.amount_edit` remains the editable amount.
- `PaymentPanel.amount_due_label` displays `pesos(self._outstanding)`.
- The panel makes clear that the prefilled amount is the rental balance and the security deposit is separate.
- A zero outstanding amount is clearly shown as `0.00`; the submit button is disabled when there is nothing to collect.

- [ ] **Step 1: Write failing tests** for prefill with `1234.56`, zero-outstanding state, and the visible amount-due caption.
- [ ] **Step 2: Run the new tests and verify they fail.**
- [ ] **Step 3: Implement the label/prefill/zero-state behavior.** Keep `open_payment_for` re-reading the live balance.
- [ ] **Step 4: Run the new tests plus the booking/payment panel tests.**
- [ ] **Step 5: Commit on the task branch.**

---

### Task 4: Settings, sidebar, and filter explanations

**Files:**
- Modify: `app/staff/pages/settings.py`
- Modify: `app/staff/shell.py`
- Modify: `app/staff/sidebar.py`
- Modify: `app/staff/prefs.py`
- Modify: `app/staff/filter_popover.py`
- Test: `tests/test_staff_settings_sidebar_fixes.py` (new)

**Interfaces:**
- `Sidebar.set_collapsed(bool)` renders a 64px icon-only rail: hide the wordmark and labels, center icons, keep tooltips; expanding restores the full rail.
- `Sidebar.collapsed` exposes the state.
- Settings controls without a consumer are either wired or clearly marked preview-only/disabled; do not persist a pref that claims to change the app when nothing reads it.
- Disabled filter groups/options get an explanatory tooltip: no matches with the other filters.

- [ ] **Step 1: Write failing tests** for collapsed width/label visibility/tooltips, restored width, settings control honesty, and disabled-filter tooltips.
- [ ] **Step 2: Run the new tests and verify they fail.**
- [ ] **Step 3: Implement the sidebar rail and setting honesty. Do not edit `app/staff/staff.qss`; use inline styles/local styling.**
- [ ] **Step 4: Run the new tests plus the settings and filter tests.**
- [ ] **Step 5: Commit on the task branch.**

---

### Task 5: Check Details gallery, 3D, and availability

**Files:**
- Modify: `app/staff/vehicle_details.py`
- Modify: `app/staff/vehicle_cards.py`
- Modify: `app/staff/pages/new_rental.py`
- Modify: `app/services/vehicle_photos.py`
- Modify: `app/staff/fleet_panels.py`
- Modify: `app/staff/metrics.py`
- Create: `app/widgets/spinner.py`
- Create: `app/staff/model_preview.py`
- Create: `app/staff/model_preview_window.py`
- Test: `tests/test_staff_vehicle_details_fixes.py` (new)
- Test: `tests/test_model_preview.py` (new)

**Interfaces:**
- `Spinner(QWidget)` has `start()`, `stop()`, `set_running(bool)`, and paints a rotating arc.
- `app.staff.model_preview.launch_model_preview(glb_path: Path, label: str) -> bool` starts a separate process using `sys.executable -m app.staff.model_preview_window <glb-path> <label>`; returns False if it cannot start.
- `model_preview_window.main(argv)` shows a QWebEngineView with the bundled local `model-viewer.min.js` and the supplied GLB.
- `Gallery` never imports/instantiates `QWebEngineView`; its 3D tab shows a ready card and an “Open 3D preview” button.
- `Gallery` uses a `views` argument / `views_for()` so motorcycles show only `front` and no 3D tab.
- `Gallery` shows the spinner while a photo or 3D model is loading and stops it on success/failure.
- `Gallery._cached_bytes(view)` checks the exact view first; `front` may fall back to `default`/`front34`; side views never use the front fallback.
- `Gallery` does not switch back to 3D after the user has selected another tab.
- Check availability runs off the GUI thread and disables the button while running; a failed check disables Rent until a new successful check.
- `VehicleDetailsOverlay` uses a responsive width capped by the available window width.
- `VehicleCard` body click activates the same details action as the “Check details” button.

- [ ] **Step 1: Write failing tests** for spinner state, cache selection, motorcycle views, no main-process WebEngine, external-preview launch, tab-switch safety, failed-availability Rent gating, responsive width, and card-body click.
- [ ] **Step 2: Run the new tests and verify they fail.**
- [ ] **Step 3: Implement the safe preview architecture and gallery fixes.** Remove or neutralize the embedded `QWebEngineView` paths in `Gallery` and Fleet details. Keep the local GLB download worker. Use `app.ui.vehicle_photo.model_viewer_document` for the child window.
- [ ] **Step 4: Run the new tests plus `tests/test_staff_pages.py`, `tests/test_fleet_management.py`, and `tests/test_media_resilience.py`.**
- [ ] **Step 5: Commit on the task branch.**

---

### Task 6: Integration and verification

**Files:**
- All of the above; only conflict-resolution edits.
- Test suite.

- [ ] **Step 1: Cherry-pick the five task commits into `fix/staff-ui-issues` in dependency order.**
- [ ] **Step 2: Resolve any conflicts without dropping task behavior.**
- [ ] **Step 3: Run `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -rs` to completion.**
- [ ] **Step 4: Run a focused offscreen UI smoke test for the dashboard, Check Details, schedule, payment panel, and collapsed sidebar.**
- [ ] **Step 5: Commit integration fixes and report the final commit hash.**
