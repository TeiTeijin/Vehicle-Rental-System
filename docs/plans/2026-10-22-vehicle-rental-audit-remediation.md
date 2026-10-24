# Vehicle Rental Staff Audit and Flow Remediation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the findings from the 2026-10-22 staff-app audit and the follow-up staff-flow review in independently testable phases, without changing the unfinished customer-facing UI.

**Architecture:** Keep the existing layers (`models` → `services` → `staff UI`). Add one settings-aware counter quote service, make booking availability depend on dates rather than the vehicle's present status, treat deposits as liabilities rather than rental revenue, seal the counter hand-offs (pickup balance, checkout shortfall, receipt failure), make the rental wizard exit safely, remove silent report truncation, and isolate GUI workers/network from the test harness.

**Tech Stack:** Python 3.14, PySide6 6.11, SQLAlchemy 2.0, SQLite (tests), MySQL/PyMySQL (live), pytest 9, reportlab, bcrypt.

**Spec:** The 2026-10-22 staff-app audit and the subsequent staff-flow review delivered in the conversation. Finding references below match those two reviews.

## Progress Snapshot (2026-10-24)

- Branch: `staff-audit-remediation` (remote: `origin`).
- Completed and merged: Tasks 1, 2, 3, 4, 5, 7, 11, 16, 17.
- In progress: None — next is Task 6.
- Not started: Tasks 6, 8, 9, 10, 12, 13, 14, 15, 18, 19, 20.
- Canonical plan path in the repo: `docs/plans/2026-10-22-vehicle-rental-audit-remediation.md`.
- Update this snapshot and the task status lines, commit, and push after each task review/merge.
- CarImages API is externally down; treat media/network failures as environmental, not product regressions.

---

## Decisions and Assumptions

These are the product/engineering decisions this plan makes. Review them before implementation.

**Product sign-off (2026-10-22):** the four policy decisions were explicitly approved before Tasks 4–7: deposits are liabilities with `deposit_applied`; pickup release is blocked until the balance is settled; checkout requires exact collection of any penalty shortfall; receipt failure commits first and warns, never rolls back.

1. **Deposit accounting:** a security deposit is a refundable liability, not rental payment. `amount_paid` will include rental-kind and `deposit_applied` rows, but not held deposits. This removes negative "owed" balances and prevents one booking's deposit from masking another booking's debt. A `deposit_applied` payment kind records the portion consumed by penalties; it is not revenue.
2. **Reports:** the 500-row caps are removed for branch-scale reports. If volume grows beyond a practical UI limit, pagination is a later feature, not a silent truncation.
3. **Settings:** settings that the Settings page exposes are either wired to behavior or removed. This plan wires them rather than deleting them.
4. **Customer documents:** documents remain local files, but the DB stores relative paths under `customer_docs/`; that directory is git-ignored.
5. **Scope:** customer-facing screens in `app/ui/` are out of scope. Shared service changes (media cache, payment semantics, settings) may affect them and must keep their unit tests passing.
6. **Counter flow settlement:** the staff UI must not release a vehicle with a positive pickup balance or finish a return with an uncollected penalty shortfall. Pickup routes to payment first; checkout collects any shortfall in a settlement step. The rental wizard cannot be dismissed by Escape or an outside click; cancellation requires confirmation. A receipt/PDF failure never rolls back a booking or payment.
7. **Counter flow state:** a pending customer chosen from the Customers page is a one-shot intent, not page-global state. Booking action buttons reflect both role and selected booking status. Completing or viewing a booking lands on the relevant booking rather than a stale list.

## Global Constraints

- All money is `Decimal`; never use `float` for a monetary value.
- Every database write goes through a service; UI code must not mutate ORM rows directly.
- Tests must never touch the live MySQL database or the CarImages API.
- **CarImages outage (2026-10-22):** the external CarImages API is down following PHP vulnerabilities. Treat CarImages/network failures as environmental, not product regressions; tests must still stay offline and must not mask unrelated failures.
- Preserve the existing half-open booking semantics: `end_date` is the return day; same-day rental is one rental day.
- New MySQL DDL must have a matching migration script under `scripts/` and a migration test under `tests/`.
- Run GUI tests with `QT_QPA_PLATFORM=offscreen`.
- No staff UI path may release a vehicle with a positive pickup balance or complete a checkout with an unsettled penalty shortfall. Show an actionable payment path instead.
- A successful booking/payment transaction must commit independently of receipt rendering. A receipt failure is recoverable UI state, never a rollback trigger.
- Escape and outside-click must not dismiss an active rental wizard. Cancellation must be explicit and confirmed.
- Each task ends with a targeted test command, a passing result, and a commit. Do not batch unrelated tasks into one commit.

## Review Focus

These are the highest-risk inputs and failure modes implied by the two audits. Each gets a test in the task that owns the code.

1. **A fully paid rental with a held deposit:** balance must be `0.00` and `deposit_held` positive; it must not look like a negative debt.
2. **A damage charge larger than the deposit:** the deposit is fully applied, no cash is returned, the shortfall is quoted, and checkout collects it without rolling back into an unrecoverable loop.
3. **A currently rented/reserved vehicle with a future request after its return date:** the booking must succeed unless another booking overlaps.
4. **Two counters booking the same car at the same time:** one transaction must win cleanly; the other must get a conflict, not a second overlap.
5. **A Settings change to deposit, fee, discount, same-day policy, or default method:** displayed quote and recorded payments must change together.
6. **A fee-only pickup with a balance due:** check-in must show the balance and block release until it is collected.
7. **A receipt write failure after a successful payment:** the booking and payments stay committed, and the UI shows a recoverable “receipt unavailable” state.
8. **An active rental wizard:** Escape or an outside click must not discard the draft; Cancel must require confirmation.
9. **A booking row in an invalid status:** only actions valid for that status are enabled, and no row means no row actions.
10. **A pending customer selected from Customers:** navigating away must clear the intent before the next rental starts.

---

## File Map

### New files

- `app/services/counter_service.py` — settings-aware counter quote; single source for display + payment.
- `app/domain/pricing.py` — immutable pricing policy (`long_term_days`, discount, motorcycle multiplier).
- `app/staff/workers.py` — shared, app-lifetime worker pools and bounded shutdown.
- `scripts/migrate_deposit_applied.py` — MySQL enum migration for `PAYMENT.kind`.
- `scripts/migrate_vehicle_media_urls.py` — widen signed-URL columns.
- `scripts/secure_local_files.py` — chmod helper for local secrets.
- `tests/test_counter_service.py`, `tests/test_pricing_policy.py`, `tests/test_worker_lifecycle.py` — focused regression tests.

### Modified files

- `app/services/booking_service.py` — quote, availability, deposit settlement, checkout quote/settlement, explicit fee/deposit inputs.
- `app/services/fleet_service.py` — status policy and half-open utilization math.
- `app/services/payment_service.py` — deposit liability and `deposit_applied`.
- `app/services/customer_service.py` — aggregate outstanding and configurable licence window.
- `app/services/report_service.py` — remove caps; correct utilization callers.
- `app/services/settings_service.py` — typed policy readers and validation.
- `app/services/image_cache.py` — safe cache key.
- `app/models/payment.py` — new payment kind.
- `app/models/vehicle_media.py` — widen URL columns.
- `app/staff/rental_wizard.py` — quote display/defaults, safe receipt flow, guarded cancellation, post-booking handoff.
- `app/staff/booking_panels.py` — check-in balance checkpoint and checkout settlement hand-off.
- `app/staff/pages/bookings.py` — state-aware actions, checkout settlement UI, booking focus.
- `app/staff/pages/customers.py` — aggregate balances; configurable window; one-shot rental intent.
- `app/staff/customer_panels.py` — debounced duplicate lookup.
- `app/staff/pages/new_rental.py` — async filter loading, pending-customer lifecycle, post-booking refresh.
- `app/staff/vehicle_details.py` — shared availability check; dismissible control.
- `app/staff/overlay.py` — dismissible control for the active wizard.
- `app/staff/shell.py` — install idle event filter; use shared workers.
- `tests/conftest.py` — offline media, temp image cache, worker cleanup.
- Relevant test files — behavioral coverage, including `tests/test_staff_pages.py`.

---

## Phase 1 — Make the test harness trustworthy

The full GUI suite currently stalls, so every later change needs a reliable verifier first.

### Task 1: Isolate network, image cache, and worker teardown

**Status:** ✅ complete — reviewed and approved; commits `c722e52`, `52bad20`.

**Files:**
- Create: `app/staff/workers.py`
- Create: `tests/test_worker_lifecycle.py`
- Modify: `tests/conftest.py`
- Modify: `app/staff/pages/*.py`, `app/staff/fleet_panels.py` where `QThreadPool(self)` is created
- Modify: `tests/test_staff_pages.py` only where the shell teardown helper lives

**Interfaces:**
- `app/staff/workers.py`: `loader_pool() -> QThreadPool`, `media_pool() -> QThreadPool`, `shutdown_pools(timeout_ms: int = 2000) -> None`.
- `tests/conftest.py`: autouse `offline_media` fixture patches `media_service.get_or_fetch_media` to return `[]`, patches `carimages_client.requests.get/post` to raise `AssertionError`, and points `image_cache.CACHE_DIR` at `tmp_path / "image-cache"`.

- [x] **Step 1: Write the failing worker-lifecycle test**

```python
def test_shared_pools_are_reused():
    from app.staff.workers import loader_pool, media_pool
    assert loader_pool() is loader_pool()
    assert media_pool() is media_pool()
    assert loader_pool() is not media_pool()

def test_shutdown_pools_is_bounded_when_a_worker_is_blocked():
    import time
    from app.staff.workers import shutdown_pools
    started = time.monotonic()
    shutdown_pools(timeout_ms=100)
    assert time.monotonic() - started < 2.0
```

- [x] **Step 2: Run it to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_worker_lifecycle.py -v`
Expected: FAIL with `ModuleNotFoundError: app.staff.workers`.

- [x] **Step 3: Add the shared pools and replace page-owned pools**

Implement two app-lifetime `QThreadPool` instances with small max thread counts. Replace `QThreadPool(self)` in staff pages with `loader_pool()` (or `media_pool()` for media resolvers). `shutdown_pools` calls `clear()` then `waitForDone(timeout_ms)`; it must not block forever.

- [x] **Step 4: Add the offline-media fixture**

In `tests/conftest.py`, set test API keys to empty before importing `app.config`, add the autouse fixture, and call `shutdown_pools(1000)` at the end of the session-scoped `qapp` fixture. The fixture must be overridable by tests that intentionally exercise media behavior.

- [x] **Step 5: Run the formerly hanging suite**

Run: `CI_API_KEY= CI_API_SECRET= QT_QPA_PLATFORM=offscreen timeout 900 .venv/bin/python -m pytest tests/test_staff_pages.py -q -o addopts=`
Expected: all 298 tests pass and the process exits before the timeout.

- [x] **Step 6: Run the full suite**

Run: `QT_QPA_PLATFORM=offscreen timeout 1200 .venv/bin/python -m pytest -q -o addopts=`
Expected: all tests pass within the timeout; no `QThreadPool::~QThreadPool` hang.

- [x] **Step 7: Commit**

```bash
git add app/staff/workers.py app/staff/pages app/staff/fleet_panels.py tests/conftest.py tests/test_worker_lifecycle.py tests/test_staff_pages.py
git commit -m "test: isolate media and bound GUI worker teardown"
```

---

## Phase 2 — Money, booking, and counter-flow correctness

### Task 2: One settings-aware counter quote for display and payment

**Status:** ✅ complete — reviewed and approved; commits `27210b6`, `2a57f7a`.

**Files:**
- Create: `app/services/counter_service.py`
- Create: `tests/test_counter_service.py`
- Modify: `app/services/booking_service.py`
- Modify: `app/staff/rental_wizard.py`
- Modify: `tests/test_counter_rental.py`
- Modify: `tests/test_staff_pages.py` (payment-panel label test)

**Interfaces:**
```python
@dataclass(frozen=True)
class CounterQuote:
    days: int
    total: Decimal
    deposit: Decimal
    scheduling_fee: Decimal
    default_method: str
    default_channel: str

def quote(session: Session, vehicle: Vehicle, start_date: date, end_date: date) -> CounterQuote: ...
```
`booking_service.complete_counter_rental` gains keyword-only `fee_floor: Decimal | None = None`, `deposit: Decimal | None = None`; these override settings reads and must be the exact values shown by the wizard. `WizardState` gains `scheduling_fee`, `default_method`, `default_channel`.

- [x] **Step 1: Write the failing quote test**

```python
def test_quote_uses_configured_fee_deposit_and_defaults(session, vehicle):
    from app.services import counter_service, settings_service
    settings_service.set_value(session, "scheduling_fee", "500.00")
    settings_service.set_value(session, "deposit_suv", "4000.00")
    settings_service.set_value(session, "default_payment_method", "gcash")
    settings_service.set_value(session, "default_channel", "online")
    vehicle.vehicle_class = "suv"
    q = counter_service.quote(session, vehicle, date(2026, 10, 5), date(2026, 10, 7))
    assert q.days == 2
    assert q.deposit == Decimal("4000.00")
    assert q.scheduling_fee == Decimal("500.00")
    assert q.default_method == "gcash"
    assert q.default_channel == "online"
```

- [x] **Step 2: Run it to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_counter_service.py -v`
Expected: FAIL because `counter_service` does not exist.

- [x] **Step 3: Implement `counter_service.quote`**

It calls `booking_service.quote_for`, `settings_service.deposit_for_class`, `settings_service.scheduling_fee`, and `settings_service.get_raw` for the defaults. It must not import the Qt wizard.

- [x] **Step 4: Wire the wizard**

`rental_wizard._on_scheduled` uses `counter_service.quote(...)` and stores all fields in `WizardState`. `PaymentPanel` uses `state.scheduling_fee` for the minimum/label and `state.default_method` for the initial method. `_on_pay` passes `fee_floor=state.scheduling_fee`, `deposit=state.deposit`, and `channel=state.default_channel` to `complete_counter_rental`. `complete_counter_rental` uses explicit values when supplied, else reads settings; validate non-negative and either allow `deposit == 0` by skipping the row or use Task 10's decision.

- [x] **Step 5: Add the display/record agreement test**

```python
def test_configured_deposit_is_both_displayed_and_recorded(...):
    settings_service.set_value(session, "deposit_suv", "4000.00")
    q = counter_service.quote(session, vehicle, start, end)
    booking, _ = booking_service.complete_counter_rental(
        session, user=customer, vehicle=vehicle,
        start_date=start, end_date=end,
        rental_amount=q.total, method="cash", staff_id=staff.user_id,
        fee_floor=q.scheduling_fee, deposit=q.deposit,
    )
    assert payment_service.deposit_held(session, booking) == Decimal("4000.00")
    deposit_row = next(p for p in booking.payments if p.kind == "deposit")
    assert deposit_row.amount == Decimal("4000.00")
```

- [x] **Step 6: Run the money tests**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_counter_service.py tests/test_counter_rental.py tests/test_staff_pages.py -q -o addopts=`
Expected: pass.

- [x] **Step 7: Commit**

```bash
git add app/services/counter_service.py app/services/booking_service.py app/staff/rental_wizard.py tests/test_counter_service.py tests/test_counter_rental.py tests/test_staff_pages.py
git commit -m "fix: use one settings-aware quote for counter rentals"
```

---

### Task 3: Allow future bookings on currently rented vehicles; serialize availability

**Status:** ✅ complete — reviewed and approved; commits `9aa79f2`, `4775af3`.

**Files:**
- Modify: `app/services/fleet_service.py`
- Modify: `app/services/booking_service.py`
- Modify: `app/staff/vehicle_details.py`
- Modify: `tests/test_booking_service.py`
- Modify: `tests/test_fleet_management.py`

**Interfaces:**
- `fleet_service.ALWAYS_BLOCKING_STATUSES = ("maintenance", "retired")`.
- `booking_service.assert_available(...)` uses date overlap for `rented`/`reserved`; always blocks maintenance/retired.
- `booking_service.create_booking(...)` locks the vehicle row with `SELECT ... FOR UPDATE` on MySQL before the availability read. SQLite ignores the clause; tests assert the compiled statement and the status/overlap behavior separately.

- [x] **Step 1: Write the failing future-booking test**

```python
def test_a_currently_rented_vehicle_can_be_booked_after_its_return(session, customer, vehicle, staff):
    vehicle.status = "rented"
    existing = booking_service.create_booking(session, customer, vehicle, D1, D3)
    existing.status = "ongoing"
    session.flush()
    future = booking_service.create_booking(session, customer, vehicle, D5, D7)
    assert future.booking_id is not None
```

- [x] **Step 2: Run it to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest "tests/test_booking_service.py::test_a_currently_rented_vehicle_can_be_booked_after_its_return" -v`
Expected: FAIL with `ConflictError: ... currently rented and cannot be booked`.

- [x] **Step 3: Change the status policy**

Add `ALWAYS_BLOCKING_STATUSES` to `fleet_service`; keep `is_rentable()` for "can be handed over right now" callers. In `assert_available`, replace the `is_rentable` rejection with an `ALWAYS_BLOCKING_STATUSES` rejection. Leave workshop and overlap checks in place.

- [x] **Step 4: Add the row lock**

In `create_booking`, re-select the vehicle with `.with_for_update()` before `assert_available`. On MySQL this serializes two counters; on SQLite the statement is ignored but the test suite still exercises the path. Add a test that the compiled MySQL statement contains `FOR UPDATE`.

- [x] **Step 5: Make the details checker use the same rules**

In `VehicleDetailsPanel._on_check`, open a read session and call `booking_service.assert_available`. Catch `ConflictError` and show its message; on success show `Available ...`. Remove the separate inclusive-date/status guess. Set `checked_window` only after a successful check; on a conflict set it to `None` so the wizard cannot silently prefill the same clashing dates.

- [x] **Step 6: Make New Rental show only plausibly rentable vehicles**

Add a failing test that a retired vehicle and a maintenance vehicle are absent from New Rental while a currently rented or reserved vehicle remains visible (it may be bookable for later dates). In `NewRentalPage._load_page`, pass `statuses=("available", "rented", "reserved")` instead of `statuses=None`. Keep Fleet as the place that shows every status.

- [x] **Step 7: Run the availability tests**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_booking_service.py tests/test_fleet_management.py tests/test_staff_pages.py -q -o addopts=`
Expected: pass. If the SQLite threaded test is flaky, keep the compiled-SQL test and document the MySQL staging check.

- [x] **Step 8: Commit**

```bash
git add app/services/fleet_service.py app/services/booking_service.py app/staff/vehicle_details.py app/staff/pages/new_rental.py tests/test_booking_service.py tests/test_fleet_management.py tests/test_staff_pages.py
git commit -m "fix: book future dates on currently rented vehicles"
```

---

### Task 4: Treat deposits as liabilities; add `deposit_applied`

**Status:** ✅ complete — reviewed and approved; commit `fed4784` (main branch).

This task changes documented behavior. It is deliberately separate from Task 2 so the quote fix can ship independently. It needs explicit product sign-off on the decision at the top.

**Files:**
- Modify: `app/models/payment.py`
- Modify: `app/services/payment_service.py`
- Modify: `app/services/receipt_service.py`
- Create: `scripts/migrate_deposit_applied.py`
- Add: `tests/test_migrate_deposit_applied.py`
- Modify: `tests/test_counter_rental.py`, `tests/test_booking_service.py`, `tests/test_receipt_service.py`

**Interfaces:**
- `payment_service.KINDS = ("rental", "deposit", "deposit_applied")`.
- `BookingBalance` gains `deposit_applied: Decimal`.
- `settle_deposit(session, booking) -> Decimal` returns the deposit still held; after successful settlement it is `0`.
- `amount_paid` and `booking_balance.balance` exclude held deposit rows.

- [x] **Step 1: Write the failing balance test**

```python
def test_a_held_deposit_is_not_rental_payment(...):
    booking, _ = booking_service.complete_counter_rental(
        ..., rental_amount=total, method="cash", staff_id=staff.user_id,
        fee_floor=SCHEDULING_FEE, deposit=Decimal("3000.00"),
    )
    balance = payment_service.booking_balance(session, booking)
    assert balance.balance == Decimal("0.00")
    assert balance.amount_paid == total
    assert balance.deposit_held == Decimal("3000.00")
    assert balance.deposit_applied == Decimal("0.00")
```

- [x] **Step 2: Write the failing damage-settlement test**

```python
def test_damage_consumes_deposit_and_reports_zero_held(...):
    # paid booking, deposit 3000, damage charge 500
    balance = payment_service.booking_balance(session, booking)
    assert balance.deposit_applied == Decimal("500.00")
    assert balance.deposit_held == Decimal("0.00")
    assert balance.balance == Decimal("0.00")
```

- [x] **Step 3: Run both to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_counter_rental.py -q -o addopts=`
Expected: FAIL; current `amount_paid` includes the deposit and `deposit_held` remains 500.

- [x] **Step 4: Implement the liability ledger**

- `amount_paid` sums `rental` and `deposit_applied` paid rows.
- `deposit_held` = paid deposit rows - refunded deposit rows - applied deposit rows.
- `settle_deposit` computes `rental_paid`, `penalty_total`, and `penalties_outstanding = max(0, penalty_total - max(0, rental_paid - rental))`; applies `min(held, penalties_outstanding)` as a `deposit_applied` row; refunds the remainder as a `refunded` deposit row. The applied portion is not cash and is not revenue.
- `booking_balance` exposes `deposit_applied`.
- `receipt_rows` adds a `Deposit applied` line when non-zero and shows the correct held/returned figures.

- [x] **Step 5: Add the migration and migration test**

Write `scripts/migrate_deposit_applied.py` using the existing `migrate_payment_kind.py` pattern. It confirms the old enum, alters it to include `deposit_applied`, and is idempotent. Add `tests/test_migrate_deposit_applied.py` using the existing migration-test style. Run the migration against a copy of `backup.db` first and take a fresh backup before applying it to the live MySQL database; do not rewrite historical payment rows.

- [x] **Step 6: Update affected tests**

Update tests that intentionally asserted the old "absorbed credit" behavior. Do not weaken these invariants: after a clean checkout `deposit_held == 0`; after damage `deposit_applied > 0` and `balance == 0`; after a shortfall `balance > 0`.

- [x] **Step 7: Run the money suite**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_counter_rental.py tests/test_booking_service.py tests/test_receipt_service.py tests/test_migrate_deposit_applied.py -q -o addopts=`
Expected: pass.

- [x] **Step 8: Commit**

```bash
git add app/models/payment.py app/services/payment_service.py app/services/receipt_service.py scripts/migrate_deposit_applied.py tests/test_counter_rental.py tests/test_booking_service.py tests/test_receipt_service.py tests/test_migrate_deposit_applied.py
git commit -m "fix: treat deposits as liabilities and apply them to penalties"
```

---

### Task 5: Block pickup release until the pickup balance is settled

This task closes the counter-flow gap where the wizard allows “pay minimum now, rest at pick-up” but check-in releases the vehicle without showing or collecting that balance.

**Status:** ✅ complete — commit `8c6c59a`; service guard, panel checkpoint, and payment hand-off tested.

**Files:**
- Modify: `app/services/booking_service.py`
- Modify: `app/staff/booking_panels.py`
- Modify: `app/staff/pages/bookings.py`
- Modify: `tests/test_booking_service.py`
- Modify: `tests/test_staff_pages.py`

**Interfaces:**
- `booking_service.check_in(..., require_settlement: bool = False)` reads `payment_service.booking_balance(session, booking)` when `require_settlement` is true and raises `ConflictError` before `mark_rented` if `balance.balance > ZERO`.
- `CheckInPanel(parent=None, *, current_mileage: int = 0, outstanding: Decimal = ZERO)` gains `collect_requested = Signal()`. If `outstanding > ZERO`, the primary button is disabled and a “Record payment first” secondary button is visible; the latter emits `collect_requested`.
- `BookingsPage._do_check_in` always passes `require_settlement=True`. `open_check_in` passes `outstanding=self._outstanding(booking_id)` and connects `collect_requested` to hiding the check-in overlay and calling `open_payment_for(booking_id)`.

- [x] **Step 1: Write the failing service test**

```python
def test_check_in_requires_a_settled_pickup(session, customer, vehicle, staff):
    booking, _ = booking_service.complete_counter_rental(
        session,
        user=customer,
        vehicle=vehicle,
        start_date=D1,
        end_date=D3,
        rental_amount=booking_service.SCHEDULING_FEE,
        method="cash",
        staff_id=staff.user_id,
    )
    balance = payment_service.booking_balance(session, booking)
    assert balance.balance > ZERO

    with pytest.raises(ConflictError, match="outstanding"):
        booking_service.check_in(
            session, booking, staff, 42000, "full", require_settlement=True
        )

    payment_service.record_payment(
        session, booking, balance.balance, "cash", recorded_by=staff.user_id
    )
    booking_service.check_in(
        session, booking, staff, 42000, "full", require_settlement=True
    )
    assert booking.status == "ongoing"
```

- [x] **Step 2: Write the failing panel test**

```python
def test_check_in_panel_blocks_release_with_balance(qapp):
    panel = CheckInPanel(outstanding=Decimal("1500.00"))
    assert panel.go_button.isEnabled() is False
    assert panel.collect_button.isHidden() is False
```

- [x] **Step 3: Run both to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_booking_service.py tests/test_staff_pages.py -q -o addopts=`
Expected: FAIL because the service ignores `require_settlement` and the panel has no balance controls.

- [x] **Step 4: Implement the service guard**

In `check_in`, after the status/fuel checks and before `mark_rented`, read the booking balance when `require_settlement` is true. Raise `ConflictError` with the exact amount and a message that the balance must be collected before release. Keep the default `False` so existing service callers and tests remain compatible; the staff UI is the caller that opts in.

- [x] **Step 5: Add the panel checkpoint**

In `CheckInPanel`, show `Outstanding ...` when positive, assign the primary button to `self.go_button`, disable it while a balance is due, and add `self.collect_button` with the text “Record payment first”. Emit `collect_requested` when it is clicked. Do not change the fields or validation for a zero-balance pickup.

- [x] **Step 6: Wire the Bookings page**

In `open_check_in`, pass the current outstanding and connect `collect_requested` to close the check-in overlay and open `open_payment_for(booking_id)`. After the payment panel succeeds, the clerk returns to Bookings and starts check-in again; the refreshed table and zero balance allow release.

- [x] **Step 7: Run the pickup tests**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_booking_service.py tests/test_staff_pages.py -q -o addopts=`
Expected: pass.

- [x] **Step 8: Commit**

```bash
git add app/services/booking_service.py app/staff/booking_panels.py app/staff/pages/bookings.py tests/test_booking_service.py tests/test_staff_pages.py
git commit -m "fix: require pickup balance before vehicle release"
```

---

### Task 6: Collect a checkout penalty shortfall instead of dead-ending

This task depends on Task 4 (deposit liability). It preserves the rule that a rental should not close with an uncollected penalty, while giving the clerk a concrete way to collect the shortfall.

**Files:**
- Modify: `app/services/booking_service.py`
- Modify: `app/staff/booking_panels.py`
- Modify: `app/staff/pages/bookings.py`
- Modify: `tests/test_booking_service.py`
- Modify: `tests/test_staff_pages.py`

**Interfaces:**
```python
@dataclass(frozen=True)
class CheckoutQuote:
    late_fee: Decimal
    damage_charge: Decimal
    deposit_applied: Decimal
    deposit_refund: Decimal
    balance_due: Decimal

def checkout_quote(
    session: Session,
    booking: Booking,
    *,
    actual_return_date: date,
    damage_charge: Decimal | None = None,
) -> CheckoutQuote: ...

def check_out(
    ...,
    require_settlement: bool = True,
    settlement_amount: Decimal | None = None,
    settlement_method: str | None = None,
    settlement_reference: str | None = None,
) -> Inspection_Report: ...
```

- `checkout_quote` is read-only. It applies the configured late fee and the entered damage charge to the existing balance, applies the held deposit up to the penalty amount, and returns the amount the customer still owes and the amount to hand back.
- `check_out` applies the penalties, settles the deposit, and then:
  - raises `ConflictError` when a balance remains and no settlement was supplied;
  - records a `rental`-kind payment when `settlement_amount` is supplied, then re-reads the balance and raises if money is still owed.
- `PaymentPanel` gains an optional `heading: str = "Record payment"` so the checkout hand-off can say “Settle and check out” without changing its shared write path.

- [ ] **Step 1: Write the failing quote test**

```python
def test_checkout_quote_reports_shortfall_above_deposit(session, customer, vehicle, staff):
    quote = counter_service.quote(session, vehicle, D1, D3)
    booking, _ = booking_service.complete_counter_rental(
        session,
        user=customer,
        vehicle=vehicle,
        start_date=D1,
        end_date=D3,
        rental_amount=quote.total,
        method="cash",
        staff_id=staff.user_id,
        fee_floor=quote.scheduling_fee,
        deposit=Decimal("3000.00"),
    )
    booking_service.check_in(session, booking, staff, 42000, "full")
    quote = booking_service.checkout_quote(
        session, booking, actual_return_date=D3, damage_charge=Decimal("4000.00")
    )
    assert quote.deposit_applied == Decimal("3000.00")
    assert quote.balance_due == Decimal("1000.00")
```

- [ ] **Step 2: Write the failing service test**

```python
def test_checkout_with_shortfall_requires_and_records_settlement(...):
    with pytest.raises(ConflictError, match="outstanding"):
        booking_service.check_out(
            session, booking, staff, D3, 42100, "full", damage_charge=Decimal("4000.00")
        )

    booking_service.check_out(
        session,
        booking,
        staff,
        D3,
        42100,
        "full",
        damage_charge=Decimal("4000.00"),
        settlement_amount=Decimal("1000.00"),
        settlement_method="cash",
    )
    assert booking.status == "completed"
    assert payment_service.booking_balance(session, booking).balance == Decimal("0.00")
    assert payment_service.deposit_held(session, booking) == Decimal("0.00")
```

- [ ] **Step 3: Run both to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_booking_service.py -q -o addopts=`
Expected: FAIL because `checkout_quote` does not exist and `check_out` rolls back after a shortfall.

- [ ] **Step 4: Implement quote and settlement**

Add `CheckoutQuote` and `checkout_quote` to `booking_service.py`. Reuse the same configured late-fee reader and `money(...)` validation used by `check_out`; do not write rows. Add the settlement parameters to `check_out` and record the settlement only after penalties and deposit application, using `payment_service.record_payment(..., kind="rental", note="Checkout settlement")`. Require the amount to cover the quoted balance exactly; reject underpayment and overpayment with a clear `ValidationError`.

- [ ] **Step 5: Wire the checkout panel and page**

In `BookingsPage`, replace the direct check-out submit with `_submit_check_out(booking_id, values)`:

1. Read the booking and call `checkout_quote` before writing.
2. If `balance_due <= 0`, call the normal `_submit_for(booking_id, "check_out", values)`.
3. If `balance_due > 0`, open `PaymentPanel(balance_due, self, heading="Settle and check out")`. On its `submitted` signal, re-use the saved check-out values plus `settlement_amount`, `settlement_method`, and `settlement_reference` and call `_submit_for(booking_id, "check_out", combined)`.
4. Keep the payment panel open on failure so the clerk can correct the method/amount.

- [ ] **Step 6: Add the GUI regression**

Add a test that selecting an ongoing booking, entering a damage charge above the deposit, and submitting opens the settlement panel; a second submit records the shortfall and completes the checkout. Assert the booking is `completed`, the vehicle is `available`, and the balance is zero.

- [ ] **Step 7: Run the checkout tests**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_booking_service.py tests/test_staff_pages.py -q -o addopts=`
Expected: pass. Also re-run `tests/test_counter_rental.py` after Task 4 to confirm deposit accounting.

- [ ] **Step 8: Commit**

```bash
git add app/services/booking_service.py app/staff/booking_panels.py app/staff/pages/bookings.py tests/test_booking_service.py tests/test_staff_pages.py
git commit -m "fix: collect checkout penalty shortfalls"
```

---

### Task 7: Commit the rental before generating the receipt

**Status:** ✅ complete — reviewed and approved; commit `7152a4b`, merged as `e18b3b3`.

**Files:**
- Modify: `app/staff/rental_wizard.py`
- Modify: `tests/test_staff_pages.py`

**Interfaces:**
- `RentalWizard._generate_receipt_safely(booking_id: int) -> tuple[str | None, str | None]` returns `(receipt_path, error_message)` and never raises.
- `DonePanel(..., receipt_path: str | None, receipt_error: str | None = None)` disables the Print button when the path is `None` and shows a warning that the booking is saved.

- [x] **Step 1: Write the failing receipt test**

Monkeypatch `app.staff.rental_wizard.generate_receipt_pdf` to raise `OSError("read-only")`, drive a counter rental through `_on_pay`, and assert:

- the booking still exists with its payments;
- the Done panel is shown;
- `receipt_path is None` and a non-empty receipt error is visible;
- the Print button is disabled.

- [x] **Step 2: Run it to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py -k receipt -q -o addopts=`
Expected: FAIL because receipt generation is inside the transaction and the exception rolls back the booking.

- [x] **Step 3: Move the receipt out of the transaction**

Commit the booking, payments, and balance in the existing `with self._context.session()` block. After it exits, call `_generate_receipt_safely(booking_id)` using a fresh `reading()` session. Do not generate the receipt inside the write transaction.

- [x] **Step 4: Make the Done panel recoverable**

Change `DonePanel` to accept an optional receipt path. When absent, show “Receipt could not be generated” with the error and disable Print. The booking summary and “View in Bookings”/“Done” actions remain available.

- [x] **Step 5: Run the wizard tests**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py tests/test_receipt_service.py -q -o addopts=`
Expected: pass.

- [x] **Step 6: Commit**

```bash
git add app/staff/rental_wizard.py tests/test_staff_pages.py
git commit -m "fix: keep bookings committed when receipt generation fails"
```

---

### Task 8: Guard wizard exits and make the pending customer one-shot

**Files:**
- Modify: `app/staff/overlay.py`
- Modify: `app/staff/vehicle_details.py`
- Modify: `app/staff/rental_wizard.py`
- Modify: `app/staff/pages/new_rental.py`
- Modify: `app/staff/pages/customers.py`
- Modify: `tests/test_staff_pages.py`

**Interfaces:**
- `FrostedOverlay.set_dismissible(flag: bool) -> None` and `VehicleDetailsOverlay.set_dismissible(flag: bool) -> None`; default `True`. When false, `keyPressEvent` ignores Escape and `mousePressEvent` ignores outside clicks.
- `RentalWizard` gains `cancelled = Signal()` and `finished = Signal()`. `_StepBase` gains `cancel_requested = Signal()` and a “Cancel rental” button; `RentalWizard._swap` connects every step’s cancel signal to `_on_cancel`.
- `RentalWizard._on_cancel` asks `app.staff.widgets.confirm` for confirmation. On yes, re-enable dismissal, hide the overlay, and emit `cancelled`; on no, stay on the current step.
- `NewRentalPage` sets `overlay.set_dismissible(False)` when the wizard starts and restores `True` before hiding it. It clears `_pending_customer_id` at the start of every `refresh()` and exposes `pending_customer_id` for tests.
- `LicencePanel._on_create_values` raises `ValidationError` with the eligibility reasons instead of a bare `RuntimeError`, so the form shows the actual age/licence problem.

- [ ] **Step 1: Write the failing dismiss test**

Show a `FrostedOverlay`, call `set_dismissible(False)`, send Escape with `QTest.keyClick`, and assert the overlay is still visible. Also click outside the card column and assert it remains visible.

- [ ] **Step 2: Write the failing wizard cancel and pending-customer tests**

- With the wizard overlay guarded, Escape does not hide it.
- Clicking “Cancel rental” with `confirm` patched to `True` hides the overlay and emits `cancelled`.
- Set `page.set_pending_customer(42)`, call `page.refresh()`, and assert `page.pending_customer_id is None`; the next wizard receives `customer_id=None`.
- A blocked new-customer eligibility result displays the actual reason text, not the generic failure message.

- [ ] **Step 3: Run them to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py -k "wizard or pending or dismiss" -q -o addopts=`
Expected: FAIL because the overlay has no dismissible guard, no wizard cancel button, and pending customer survives refresh.

- [ ] **Step 4: Implement the overlay guard**

Add `_dismissible`/`set_dismissible` to both overlay classes. In the Escape and outside-click handlers, return early when dismissal is disabled. Keep default behavior unchanged for customer, fleet, and ordinary details overlays.

- [ ] **Step 5: Implement the wizard cancel path**

Add the cancel signal/button to `_StepBase._buttons` and connect it in `RentalWizard._swap`. In `_on_cancel`, use `confirm(self._overlay, "Discard this rental?", detail="The customer, dates, and payment choice will be lost.", destructive=True)`; only hide and emit when confirmed. Restore dismissibility in `_on_cancel`, `_on_finished`, and `_on_view_bookings`.

- [ ] **Step 6: Clear pending intent and improve the eligibility message**

Clear `_pending_customer_id` in `NewRentalPage.refresh()` before reloading. Replace the `RuntimeError` in `LicencePanel._on_create_values` with `ValidationError("; ".join(verdict["reasons"]))`; keep the transaction rollback so no blocked customer is written.

- [ ] **Step 7: Run the flow tests**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py -q -o addopts=`
Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add app/staff/overlay.py app/staff/vehicle_details.py app/staff/rental_wizard.py app/staff/pages/new_rental.py app/staff/pages/customers.py tests/test_staff_pages.py
git commit -m "fix: guard wizard exits and clear one-shot customer intent"
```

---

### Task 9: Make booking actions state-aware and finish the handoff

**Files:**
- Modify: `app/staff/pages/bookings.py`
- Modify: `app/staff/pages/new_rental.py`
- Modify: `app/staff/rental_wizard.py`
- Modify: `tests/test_staff_pages.py`

**Interfaces:**
- `BookingsPage.selected_booking_status() -> str | None` reads the status column of the selected row.
- `BookingsPage.action_allowed(action: str, status: str | None) -> bool` returns false when no row is selected. `details` is allowed for any staff role when a row is selected; every other action must pass the existing role check and the status map:
  - `confirm`: `pending`
  - `check_in`: `pending`, `confirmed`
  - `check_out`: `ongoing`
  - `record_payment`: everything except `cancelled`
  - `extend`: `pending`, `confirmed`, `ongoing`
  - `cancel`: `pending`, `confirmed`
  - `details`: any selected row
- `_action_buttons` includes the Details button; `_refresh_actions` disables every action when no row is selected and otherwise applies `action_allowed`.
- `RentalWizard` stores `last_booking_id`. Its `navigate` callable accepts `(key: str, booking_id: int | None = None)`. `NewRentalPage._navigate_to_bookings` sets `BookingsPage._pending_focus` before navigation completes.
- `RentalWizard.finished` refreshes/resets New Rental after Done; `cancelled` does the same.

- [ ] **Step 1: Write the failing action-state tests**

Select a completed booking and assert Check in/Check out are disabled. Select an ongoing booking and assert Check out is enabled while Check in is disabled. Clear the selection and assert all row actions are disabled. Add an admin test that Cancel is enabled for pending and disabled for completed.

- [ ] **Step 2: Write the failing handoff tests**

- After “View in Bookings”, the Bookings page receives the new booking id in `_pending_focus`.
- After “Done”, the New Rental page refreshes and clears the selected vehicle/window.
- The focus helper selects the row once the async table load completes.

- [ ] **Step 3: Run them to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py -k "action or focus or handoff" -q -o addopts=`
Expected: FAIL because action buttons ignore status and the wizard only calls `show_page("bookings")` without focus.

- [ ] **Step 4: Implement the status map and button refresh**

Add `selected_booking_status` and `action_allowed`; include `details` in `_action_buttons`; keep the service-level validations as the final safety net. Do not change who may perform an action: admins still get the admin-only actions, staff still get `COUNTER_ACTIONS`.

- [ ] **Step 5: Implement the post-booking handoff**

Store the completed booking id in `RentalWizard`; pass a two-argument navigation wrapper from `NewRentalPage`; set `_pending_focus` and call `focus_booking` on the Bookings page. Connect `finished` and `cancelled` to a page reset handler that clears the selected vehicle/window and calls `refresh()`. Keep the overlay hidden before navigation.

- [ ] **Step 6: Run the handoff tests**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py -q -o addopts=`
Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add app/staff/pages/bookings.py app/staff/pages/new_rental.py app/staff/rental_wizard.py tests/test_staff_pages.py
git commit -m "fix: gate booking actions by status and focus completed bookings"
```

---

### Task 10: Zero-deposit and fee configuration validation

**Files:**
- Modify: `app/services/settings_service.py`
- Modify: `app/services/booking_service.py`
- Modify: `app/services/counter_service.py`
- Modify: `tests/test_counter_rental.py`, `tests/test_staff_settings.py`

**Interfaces:**
- A configured deposit of `0` skips the deposit payment row and is valid.
- A configured scheduling fee greater than the rental total is rejected with a clear `ValidationError` before payment.
- `complete_counter_rental` keeps its explicit `deposit`/`fee_floor` parameters.

- [ ] **Step 1: Write the failing zero-deposit test**

```python
def test_a_zero_deposit_rental_does_not_fail(...):
    settings_service.set_value(session, "deposit_suv", "0.00")
    q = counter_service.quote(session, vehicle, start, end)
    booking, _ = booking_service.complete_counter_rental(
        ..., rental_amount=q.total, method="cash", staff_id=staff.user_id,
        fee_floor=q.scheduling_fee, deposit=q.deposit,
    )
    assert payment_service.deposit_held(session, booking) == Decimal("0.00")
    assert [p.kind for p in booking.payments] == ["rental"]
```

- [ ] **Step 2: Write the fee validation test**

Set `scheduling_fee` above the rental total and assert the service raises a configuration error instead of accepting a payment the customer was told was valid.

- [ ] **Step 3: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_counter_rental.py -q -o addopts=`
Expected: FAIL with `Payment amount must be greater than zero`.

- [ ] **Step 4: Implement skip/validation**

Skip the deposit `record_payment` call when `deposit == 0`. Reject `scheduling_fee > total` with a clear `ValidationError`. Keep the Settings-page validation honest.

- [ ] **Step 5: Run and commit**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_counter_rental.py tests/test_staff_settings.py -q -o addopts=`

```bash
git add app/services/settings_service.py app/services/booking_service.py app/services/counter_service.py tests/test_counter_rental.py tests/test_staff_settings.py
git commit -m "fix: support zero-deposit rentals and validate fee configuration"
```

---

## Phase 3 — Reports and settings behavior

### Task 11: Remove silent 500-row truncation and fix utilization

**Status:** ✅ complete — reviewed and approved; commit `b1723e3`, merged as `fd7e5d2`.

**Files:**
- Modify: `app/services/report_service.py`
- Modify: `app/services/fleet_service.py`
- Modify: `tests/test_report_service.py`, `tests/test_fleet_management.py`
- Modify: `tests/test_staff_pages.py` only if a summary assertion assumes the old count

**Interfaces:**
- `bookings_report` returns all rows in the period; `BookingsReport.total` equals the number returned.
- `receivables_report` filters settled balances before any row cap; remove `.limit(500)`.
- `fleet_service.vehicle_stats` uses half-open rental occupancy; `RentalCalculator.rental_days` remains authoritative.

- [x] **Step 1: Write the failing 501-booking test**

```python
def test_bookings_report_does_not_silently_stop_at_500(session, ...):
    for _ in range(501):
        make_booking(...)
    report = report_service.bookings_report(session, D10, D20)
    assert report.total == 501
    assert len(report.rows) == 501
```

- [x] **Step 2: Write the failing utilization test**

A `D10 → D20` booking must report `rented_days == 10`, not 11, because `RentalCalculator.rental_days(D10, D20) == 10`. A two-day booking must report 2.

- [x] **Step 3: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_report_service.py tests/test_fleet_management.py -q -o addopts=`
Expected: FAIL with 500 and 11.

- [x] **Step 4: Remove caps and fix the window math**

Remove `.limit(500)` from both reports. In `fleet_service`, count rental occupancy as `max(1, end - start)` within the clipped half-open window; keep maintenance/retired day math separate. Update the D10→D20 and two-day assertions.

- [x] **Step 5: Add a large-export regression**

Add a report export test with 501 rows asserting the CSV/PDF path receives all 501 rows.

- [x] **Step 6: Run and commit**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_report_service.py tests/test_fleet_management.py tests/test_staff_pages.py -q -o addopts=`

```bash
git add app/services/report_service.py app/services/fleet_service.py tests/test_report_service.py tests/test_fleet_management.py tests/test_staff_pages.py
git commit -m "fix: report all rows and count rental days consistently"
```

---

### Task 12: Wire pricing, same-day, and operational settings

**Files:**
- Create: `app/domain/pricing.py`
- Create: `tests/test_pricing_policy.py`
- Modify: `app/domain/vehicle_types.py`, `app/domain/rental_calculator.py`
- Modify: `app/services/booking_service.py`, `app/services/settings_service.py`, `app/services/customer_service.py`, `app/services/maintenance_service.py`
- Modify: `app/staff/pages/customers.py`, `app/staff/rental_wizard.py`, `app/staff/booking_panels.py`
- Modify: `tests/test_domain.py`, `tests/test_counter_rental.py`, `tests/test_customer_management.py`, `tests/test_maintenance_service.py`, `tests/test_staff_settings.py`

**Interfaces:**
```python
@dataclass(frozen=True)
class PricingPolicy:
    long_term_days: int = 7
    long_term_discount: Decimal = Decimal("0.90")
    moto_multiplier: Decimal = Decimal("0.70")

def policy_from_settings(session) -> PricingPolicy: ...
```
`RentalCalculator.total_cost(vehicle, start_date, end_date, *, policy=None) -> Decimal`; `Vehicle.calculate_rate(days, *, policy=None)` with defaults preserving old behavior. `customer_service.eligibility_for(..., expiring_days: int = 30)`; `customers._customer_rows` reads the setting. `maintenance_service.due_soon(..., within_days: int | None = None, within_km: int | None = None)` reads settings when omitted. `booking_service.create_booking` rejects same-day when `allow_same_day` is false. `PaymentPanel` selects `default_payment_method`; `_on_scheduled` uses `default_channel`; `receipt_auto_open` controls opening the generated receipt.

- [ ] **Step 1: Write failing policy tests**

```python
def test_long_term_setting_changes_quote(...):
    settings_service.set_value(session, "long_term_days", "3")
    settings_service.set_value(session, "long_term_discount", "0.50")
    days, total = booking_service.quote_for(session, vehicle, start, end)
    assert total == Decimal("3750.00")  # 3 days x 2500 x 0.50

def test_moto_multiplier_setting_changes_quote(...):
    settings_service.set_value(session, "moto_multiplier", "0.80")
    ...
```

- [ ] **Step 2: Write failing operational tests**

- `allow_same_day=0` rejects same-day `create_booking`.
- `licence_expiring_days=90` changes the "expires soon" warning.
- `maint_due_days=30` changes `due_soon`.
- `default_payment_method=gcash` preselects GCash.
- `receipt_auto_open=0` does not call `QDesktopServices.openUrl`.

- [ ] **Step 3: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_pricing_policy.py tests/test_counter_rental.py tests/test_customer_management.py tests/test_maintenance_service.py -q -o addopts=`

- [ ] **Step 4: Implement the pricing policy**

Move the hardcoded `7`, `0.90`, and `0.70` into `PricingPolicy`; keep the old defaults so domain tests still express the default business rules. Pass the policy from `booking_service.quote_for`, `create_booking`, `extension_quote`, and `extend_booking`.

- [ ] **Step 5: Wire the operational settings**

Add readers for `allow_same_day`, `licence_expiring_days`, `maint_due_days`, `maint_due_km`, `receipt_auto_open`, `default_payment_method`, and `default_channel`. Remove/retain constants only as defaults.

- [ ] **Step 6: Run and commit**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_domain.py tests/test_pricing_policy.py tests/test_counter_rental.py tests/test_customer_management.py tests/test_maintenance_service.py tests/test_staff_settings.py -q -o addopts=`

```bash
git add app/domain app/services/booking_service.py app/services/settings_service.py app/services/customer_service.py app/services/maintenance_service.py app/staff/pages/customers.py app/staff/rental_wizard.py app/staff/booking_panels.py tests/test_domain.py tests/test_pricing_policy.py tests/test_counter_rental.py tests/test_customer_management.py tests/test_maintenance_service.py tests/test_staff_settings.py
git commit -m "fix: wire pricing and operational settings to behavior"
```

---

### Task 13: Make auto-logout activity-aware

**Files:**
- Modify: `app/staff/shell.py`
- Modify: `tests/test_staff_shell.py`

**Interfaces:**
- `StaffShell.__init__` installs the shell as an event filter on `QApplication.instance()`.
- `StaffShell.eventFilter` resets `_idle_timer` for mouse press, key press, and wheel events while signed in.
- `StaffShell.closeEvent` or destructor removes the filter to avoid dangling callbacks.

- [ ] **Step 1: Write the failing idle test**

```python
def test_input_resets_the_idle_timer(shell):
    shell.context.sign_in(...)
    shell.apply_prefs()  # auto_logout_mins > 0
    before = shell._idle_timer.remainingTime()
    QTest.keyClick(shell.login_view.password, Qt.Key_A)
    assert shell._idle_timer.remainingTime() >= before
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_shell.py -q -o addopts=`
Expected: FAIL; timer is unchanged because the filter is not installed.

- [ ] **Step 3: Install the filter and add cleanup**

Install it after the timer is created; remove it on close. Keep `show_page` resetting the timer. Do not reset while signed out.

- [ ] **Step 4: Run and commit**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_shell.py -q -o addopts=`

```bash
git add app/staff/shell.py tests/test_staff_shell.py
git commit -m "fix: reset idle logout on real user input"
```

---

## Phase 4 — Performance and concurrency

### Task 14: Aggregate customer balances and debounce duplicate checks

**Files:**
- Modify: `app/services/customer_service.py`
- Modify: `app/staff/pages/customers.py`
- Modify: `app/staff/customer_panels.py`
- Modify: `tests/test_customer_management.py`, `tests/test_staff_pages.py`

**Interfaces:**
- `customer_service.outstanding_by_customer(session) -> dict[int, Decimal]`
- `customer_service.open_bookings_by_customer(session) -> dict[int, bool]`
- `CustomersPage._customer_rows` uses both maps; no per-customer `outstanding_for` loop.
- `CustomerFormPanel` uses a 250 ms single-shot timer for `_check_duplicates`; the direct `textChanged` connection is removed.

- [ ] **Step 1: Write the failing query-count test**

```python
def test_customer_page_load_is_not_n_plus_one(branch):
    from sqlalchemy import event
    queries = []
    event.listen(engine, "before_cursor_execute", lambda *a: queries.append(a))
    ...
    assert len(queries) < 20  # for 50 customers, not 50 * bookings
```

- [ ] **Step 2: Write the debounce test**

Type three phone characters; assert `_check_duplicates` runs once after the timer, not three times.

- [ ] **Step 3: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_customer_management.py tests/test_staff_pages.py -q -o addopts=`

- [ ] **Step 4: Implement the aggregate maps**

Use three grouped queries: booking totals, penalty totals, and paid-payment totals, keyed by `Booking.user_id`. The paid-payment total includes `rental` and `deposit_applied` rows, but excludes held `deposit` rows. Combine them in Python; the result must equal `outstanding_for` for every customer in the fixture. Keep `outstanding_for` for single-customer use.

- [ ] **Step 5: Replace the page loop and add debounce**

In `_customer_rows`, look up `owed` and `has_open` from the maps. In `CustomerFormPanel`, create a single-shot timer and start/restart it on phone/licence changes.

- [ ] **Step 6: Run and commit**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_customer_management.py tests/test_staff_pages.py -q -o addopts=`

```bash
git add app/services/customer_service.py app/staff/pages/customers.py app/staff/customer_panels.py tests/test_customer_management.py tests/test_staff_pages.py
git commit -m "perf: aggregate customer balances and debounce duplicate checks"
```

---

### Task 15: Move New Rental filter work off the GUI thread

**Files:**
- Modify: `app/staff/pages/new_rental.py`
- Modify: `tests/test_staff_pages.py`
- Reuse: `app/staff/workers.py`

**Interfaces:**
- `_apply_filters` shows the existing loading state and starts a `loader_pool()` worker.
- The worker returns a plain tuple: `(rows, count, reachable, rate_high)` using plain `RentalVehicle`/tuple data, never ORM rows.
- A generation counter drops stale filter results.

- [ ] **Step 1: Write the failing responsiveness test**

Use a fake context whose `showcase_vehicle_rows` sleeps for 100 ms; call `_apply_filters`; assert the method returns in under 50 ms and the table shows the loading state.

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py -k new_rental -q -o addopts=`

- [ ] **Step 3: Implement the worker**

Move `_load_page`, `_update_count`, and `_refresh_availability` reads into one worker. Emit a signal to the GUI thread; update sections and filter chips there. Use a generation counter to ignore stale results.

- [ ] **Step 4: Run and commit**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py -k new_rental -q -o addopts=`

```bash
git add app/staff/pages/new_rental.py tests/test_staff_pages.py
git commit -m "perf: load new-rental filters without blocking the UI"
```

---

## Phase 5 — Security and data hygiene

### Task 16: Harden local images, URLs, and documents

**Status:** ✅ complete — reviewed and approved; commits `a1a92f1`, `0b6dd84`; merged as `3477029`.

**Files:**
- Modify: `app/services/image_cache.py`
- Modify: `app/models/vehicle_media.py`
- Create: `scripts/migrate_vehicle_media_urls.py`
- Create: `tests/test_migrate_vehicle_media_urls.py`
- Modify: `app/services/customer_service.py`
- Modify: `app/staff/pages/customers.py`, `app/staff/customer_panels.py`, `app/staff/rental_wizard.py` where document paths are opened
- Modify: `.gitignore`
- Create: `scripts/secure_local_files.py`
- Add: `tests/test_security_hardening.py`

**Interfaces:**
- `image_cache._identity(make, model, year)` returns a safe key: lowercase slug plus a short SHA-256 digest, with no `/`, `\`, or `..` components.
- `image_cache._paths` verifies `target.resolve().is_relative_to(CACHE_DIR.resolve())` before returning.
- `Vehicle_Media.image_url` and `model_3d_url` become `Text` (or `String(512)`); migration matches.
- `customer_service.store_licence_doc` returns `"customer_docs/<filename>"`; `customer_service.resolve_doc_path(value) -> Path` resolves against `BASE_DIR`.
- `.gitignore` includes `customer_docs/`.
- `scripts/secure_local_files.py` chmods existing local secrets to `0600` and reports missing files without failing.

- [x] **Step 1: Write the failing traversal test**

```python
def test_cache_key_cannot_escape_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(image_cache, "CACHE_DIR", tmp_path / "cache")
    image_cache.store("../../evil", "x", 2020, b"data", "https://example.test/x")
    assert not (tmp_path.parent / "evil-x-2020.img").exists()
    assert list((tmp_path / "cache").glob("*.img"))
```

- [x] **Step 2: Write the failing URL-length migration test**

Assert the model accepts a 400-character signed URL and the migration test confirms the column width.

- [x] **Step 3: Write the failing document-path test**

Store a doc, assert the returned value starts with `customer_docs/`, and assert `resolve_doc_path` resolves inside `BASE_DIR`. Assert `.gitignore` contains `customer_docs/`.

- [x] **Step 4: Implement the hardening**

Add the safe cache key, path check, model/migration column widening, relative document paths, ignore entry, and permission script. Keep opening a document working on the same machine. Enforce matching maximum lengths in the services/forms for licence numbers and free-text fields so MySQL does not reject or truncate valid-looking staff input.

- [x] **Step 5: Run and commit**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_security_hardening.py tests/test_migrate_vehicle_media_urls.py tests/test_customer_management.py -q -o addopts=`

```bash
git add app/services/image_cache.py app/models/vehicle_media.py scripts/migrate_vehicle_media_urls.py scripts/secure_local_files.py app/services/customer_service.py app/staff .gitignore tests/test_security_hardening.py tests/test_migrate_vehicle_media_urls.py tests/test_customer_management.py
git commit -m "security: harden cache keys, media URLs, and customer docs"
```

---

### Task 17: Remove local secret exposure from database snapshots

**Status:** ✅ complete — commit `fdfa4dc`; media URLs blanked before insert, snapshot chmod 0600, tested.

**Files:**
- Modify: `scripts/backup_live_db.py` or add `scripts/sanitize_backup.py`
- Modify: `tests/test_backup_live_db.py`
- Modify: deployment notes / `README.md`

**Interfaces:**
- A backup copy must not contain live signed media URLs or API keys. Blank `VEHICLE_MEDIA.image_url`/`model_3d_url` in the snapshot, or document that the backup is encrypted and restricted.
- `.env`, `ca.pem`, `backup.db`, and `demo.db` are mode `0600` on the developer machine.
- The CarImages key is rotated if the backup or machine was shared.

- [x] **Step 1: Write the failing backup-sanitization test**

Create a source DB with a `Vehicle_Media` URL containing `api_key=secret`; run the backup; assert the output contains no `api_key=`.

- [x] **Step 2: Implement sanitization**

Either blank URL columns in the copied database or exclude media rows, keeping schema and row counts for restore tests. Add the script to the backup workflow and document permissions.

- [x] **Step 3: Run and commit**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_backup_live_db.py -q -o addopts=`

```bash
git add scripts/backup_live_db.py scripts/sanitize_backup.py tests/test_backup_live_db.py README.md
git commit -m "security: sanitize media URLs in local database snapshots"
```

---

## Phase 6 — Engineering hygiene

### Task 18: Make the full suite a CI gate and remove tracked junk

**Files:**
- Add: `.github/workflows/tests.yml`
- Add: `pyproject.toml` with Ruff settings
- Modify: `requirements-dev.txt`
- Delete: `app/staff/filter_popover.py.bak`, `app/ui/tempCodeRunnerFile.py`, `demo.db.pre-maintenance-bak`
- Modify: `.vscode/settings.json`; `.gitignore` remains with `customer_docs/`

**Interfaces:**
- CI runs non-GUI tests in parallel and GUI tests serially with `QT_QPA_PLATFORM=offscreen` and a hard timeout.
- `ruff check app tests` and `pytest` are required jobs.

- [ ] **Step 1: Add the CI workflow**

Install `requirements.txt` and `requirements-dev.txt`; run `ruff check app tests`. Run non-GUI tests with `-n auto` while ignoring the GUI modules, then run the GUI modules serially with `QT_QPA_PLATFORM=offscreen` and a timeout:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -o addopts= -n auto \
  --ignore=tests/test_staff_pages.py --ignore=tests/test_staff_dashboard.py \
  --ignore=tests/test_staff_ui.py --ignore=tests/test_staff_shell.py \
  --ignore=tests/test_staff_login.py --ignore=tests/test_staff_settings.py

QT_QPA_PLATFORM=offscreen timeout 1200 .venv/bin/python -m pytest -q -o addopts= \
  tests/test_staff_pages.py tests/test_staff_dashboard.py tests/test_staff_ui.py \
  tests/test_staff_shell.py tests/test_staff_login.py tests/test_staff_settings.py
```

Do not provide `.env` or network credentials.

- [ ] **Step 2: Remove tracked junk and fix VS Code**

Use `git rm` for the three tracked junk files. Update `.vscode/settings.json` to `${workspaceFolder}/.venv/bin/python`.

- [ ] **Step 3: Run Ruff and the full suite**

Run: `.venv/bin/ruff check app tests`
Run: `QT_QPA_PLATFORM=offscreen timeout 1200 .venv/bin/python -m pytest -q -o addopts=`
Expected: pass. Fix new lint findings only; do not mass-reformat in this task.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/tests.yml pyproject.toml requirements-dev.txt .vscode/settings.json .gitignore
git rm app/staff/filter_popover.py.bak app/ui/tempCodeRunnerFile.py demo.db.pre-maintenance-bak
git commit -m "chore: add CI, linting, and remove tracked junk"
```

---

### Task 19: Show payment status on the dashboard

**Files:**
- Modify: `app/services/dashboard_service.py`
- Modify: `app/staff/pages/dashboard.py`
- Modify: `tests/test_staff_dashboard.py`

**Interfaces:**
- `TransactionRow` gains `status: str`.
- `recent_transactions` includes `payment.status`; pending and failed rows must not be presented as received money.
- The dashboard labels or filters non-paid rows; choose one behavior and test it.

- [ ] **Step 1: Write the failing test**

Create a paid, a pending, and a failed payment. Assert the dashboard row data preserves each status, and that the rendered amount for a pending/failed row is not styled as received cash.

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_dashboard.py -q -o addopts=`
Expected: FAIL because `TransactionRow` has no status.

- [ ] **Step 3: Implement the status field and presentation**

Add `status` to the dataclass and query mapping. In `dashboard.py`, render a small status caption (“Pending”, “Failed”, “Refunded”) or exclude non-paid rows; do not show a pending transfer as a normal received amount.

- [ ] **Step 4: Run and commit**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_dashboard.py -q -o addopts=`

```bash
git add app/services/dashboard_service.py app/staff/pages/dashboard.py tests/test_staff_dashboard.py
git commit -m "fix: distinguish pending and failed dashboard transactions"
```

---

### Task 20: Final regression and release checks

- [ ] **Step 1: Run the complete suite**

Run: `QT_QPA_PLATFORM=offscreen timeout 1200 .venv/bin/python -m pytest -q -o addopts=`
Expected: all tests pass, no hang.

- [ ] **Step 2: Run the non-GUI suite in parallel**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -o addopts= -n auto --ignore=tests/test_staff_pages.py --ignore=tests/test_staff_dashboard.py`
Expected: pass, faster than serial.

- [ ] **Step 3: Smoke-test the demo**

Run: `./launch_demo.py` (or the documented demo launcher). Verify:
- sign in;
- book a future rental on a currently rented car;
- change settings and confirm quote/payment agree;
- create a fee-only pickup, confirm Check in blocks release, record the balance, and then check in;
- return a rental with a damage charge larger than the deposit, collect the settlement shortfall, and confirm the booking completes;
- force a receipt-path failure (for example a read-only export folder) and confirm the booking remains saved with a visible warning;
- start a rental wizard, press Escape and click outside, and confirm the draft remains; cancel explicitly and confirm it closes;
- confirm action buttons disable for invalid booking statuses;
- use “View in Bookings” and confirm the new booking is focused;
- produce a large report;
- export CSV/PDF;
- sign out, close, and confirm no freeze.

- [ ] **Step 4: Verify local permissions**

Run: `stat -c '%a %n' .env ca.pem backup.db demo.db`
Expected: `600` for secret-bearing files (certificate may be `644` only if the team explicitly accepts it).

- [ ] **Step 5: Commit final documentation**

```bash
git add README.md docs
git commit -m "docs: record audit remediation and release checks"
```

---

## Deferred / policy decisions

These need a product decision or a larger schema change; record the decision before scheduling them.

- **Refund workflow:** `payment_service.refund_payment` exists but no staff UI calls it. Decide whether counter staff may refund rental payments after cancellation or vehicle unavailability. If yes, add an admin-only refund action with a reason, payment selector, and audit trail.
- **Login throttling:** add failed-attempt counters/lockout only if the app is exposed beyond trusted counter PCs.
- **Temporary customer password:** counter-created customers currently share a fixed temporary password. Replace it with a random single-use secret when the customer login is introduced.
- **Customer-document storage:** relative paths fix same-branch machines; shared/remote branches still need object storage or a file server.
- **Report pagination:** removing the 500-row cap is correct at branch scale. If reports regularly exceed several thousand rows, add explicit pagination rather than reinstating a silent cap.
- **Broad exception/logging cleanup:** the 125 `except Exception` sites need a staged audit; start with worker/media and money paths, add a logging configuration, and do not mass-rewrite UI noise-suppression in the remediation branch.
- **Payments → Bookings return context:** the Payments “Collect” action intentionally deep-links into the single Bookings write path. After payment the clerk stays on Bookings. A “Back to Payments” affordance is useful but not a correctness fix; decide after observing counter use.
- **Dashboard click-through:** the dashboard is intentionally read-only. Add links to source lists/reports only if staff ask for them; the sidebar already provides navigation.
- **First-run auto sign-in:** creating the first admin returns to the login screen rather than signing in automatically. This is safer and loses no data; no change planned.

---

## Self-Review Notes

- Every High finding from the original audit maps to Task 2–9, 16, or 17.
- Every Medium finding maps to Task 3–6, 11, 14–16, 19, or the deferred list.
- The intentional product-policy changes are Task 4 (deposit liability), Task 5 (pickup settlement), Task 6 (checkout settlement), and Task 7 (receipt failure is not a booking failure). Review them before implementation.
- The staff-flow review is covered by Tasks 3, 5–9, and the expanded Task 20 smoke test.
- The plan does not touch customer UI screens; shared services remain covered by unit tests.
- Commands use `.venv/bin/python`, `QT_QPA_PLATFORM=offscreen`, and `-o addopts=` when per-test output is needed.
