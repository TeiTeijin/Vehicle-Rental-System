# Staff Performance Remediation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Update this file and push after every task.

**Goal:** Reduce the staff app's live page loads from tens of seconds or minutes to roughly one to two seconds by removing N+1 queries and full-ledger loads, then keep those regressions from returning.

**Architecture:** Preserve the existing `models` → `services` → staff UI layering. Batch or eager-load related rows, push filtering and pagination into SQL, replace whole-ledger totals with aggregate queries, move blocking work off the GUI thread, and add composite indexes only after the query shapes are fixed. Every task is independently tested, committed, and pushed so work can resume from another device.

**Tech Stack:** Python 3.14, PySide6 6.11, SQLAlchemy 2.0, SQLite for tests, MySQL/PyMySQL for the live database, pytest 9.

**Spec:** The live performance measurements and recommendations recorded in this file, produced from the 2026-10-25 live database review.

## Progress Snapshot (2026-10-25)

- Branch: `staff-audit-remediation`; remote: `origin/staff-audit-remediation`.
- Canonical plan path: `plan.md`.
- Baseline: 20/20 staff audit tasks complete; full Linux suite 1101 passed, 1 skipped.
- Performance work: **1/11 tasks complete**.
- Live counter demo: paused at booking `#26527`; do not resume until the performance work is signed off.
- Branch must not be merged into `main` until the user approves.
- After each task: update its checkbox and this snapshot, run the targeted tests, commit, and `git push origin staff-audit-remediation`.

## Measured Baseline

These are live measurements from the workstation, not estimates. The live database is remote, so round-trip count is the dominant cost. The first query after a new connection also includes a ~2s TLS/handshake cost.

| Area | Current live measurement |
|---|---:|
| Bookings, 50 rows | 195 SQL statements, 29.3s |
| Bookings, 500 rows with eager loading (prototype) | 3 SQL statements, 1.4s |
| Fleet, 100 vehicles | 201 SQL statements, 24.5s |
| Fleet, all 1,012 vehicles | ~2,000 SQL statements, minutes |
| Payments ledger, all 11,275 rows | 1 query, 7.6s |
| Payments “takings total” | full ledger load, 5.0s |
| Outstanding, 4,499 bookings | 37 SQL statements, 12.3s |
| Customers, 304 rows | 7 SQL statements, 3.5s |
| Dashboard | 5 SQL statements, 1.2s, synchronous on the GUI thread |
| Inspections / Maintenance, 100 rows | 1 SQL statement each, ~0.5s |

Useful prototype measurements:

- Bookings with `joinedload`/`selectinload`: **3 SQL statements, 1.4s for 500 rows**.
- Payments with a 100-row limit: **1 SQL statement, 0.68s**.
- Outstanding with a 100-row limit and eager loading: **3 SQL statements, 0.76s**.
- Takings with `SUM()`: **1 SQL statement, 0.51s**.
- Fleet and utilization reports remain the worst offenders because they call per-vehicle services.

## Global Constraints

- All money is `Decimal`; never use `float` for monetary values.
- Every database write goes through a service; UI code must not mutate ORM rows directly.
- Tests must never touch the live MySQL database or the CarImages API.
- GUI tests run with `QT_QPA_PLATFORM=offscreen`.
- Preserve half-open booking semantics: `end_date` is the return day.
- Preserve the deposit-as-liability rules: held deposits are not revenue; `deposit_applied` is a ledger adjustment.
- Do not silently hide rows. Any cap must be visible in the UI as “latest N”, “largest N”, or an explicit page/count.
- New MySQL DDL must have a migration script under `scripts/` and a migration test under `tests/`.
- Run targeted tests after every task; run the full Linux suite before the final release check.
- Push after every task. Do not batch unrelated tasks into one commit.
- CarImages is externally down; media/API failures are environmental, not performance regressions.

## Review Focus

These are the highest-risk behavior changes implied by the performance work. Each gets a test in the task that owns it.

1. **Search must find records outside the first page.** Moving a cap before a Python filter would make search silently incomplete.
2. **Balance math must not change.** Aggregate SQL must match `payment_service.booking_balance` for rental, penalties, paid rental, and `deposit_applied`.
3. **Fleet due and maintenance flags must stay identical.** Batching must not change which booking is “next due” or which maintenance record blocks the vehicle.
4. **Payments totals must still exclude deposits and pending payments.** The aggregate query must use the same kind/status rules.
5. **A cap must be visible.** A user must not believe 100 rows are the whole ledger.
6. **The dashboard and report screens must not block the GUI thread while loading.**

---

## File Map

### Modified files

- `app/staff/pages/bookings.py` — Bookings list query, eager loading, filters, cap.
- `app/staff/pages/fleet.py` — Fleet list batching and cap.
- `app/staff/pages/payments.py` — Payments ledger cap, SQL filters, outstanding query.
- `app/staff/pages/customers.py` — Customer cap, SQL search, cached settings.
- `app/staff/pages/dashboard.py` — Async dashboard refresh and cache.
- `app/services/payment_service.py` — Aggregate takings, optional payment limits/search.
- `app/services/report_service.py` — Batched utilization/receivables.
- `app/staff/context.py` — Connection pool warm-up if needed.
- `app/staff/tables.py` — Only if a shared pagination/count label is added.
- `tests/test_staff_pages.py` — Page query-count and behavior tests.
- `tests/test_report_service.py` — Report query-count and math tests.
- `tests/test_staff_dashboard.py` — Dashboard async/caching tests.

### New files

- `scripts/migrate_performance_indexes.py` — MySQL index migration.
- `tests/test_migrate_performance_indexes.py` — Migration SQL/execution tests.
- `app/staff/query_metrics.py` — Optional slow-query/query-count helper for development and tests.

---

## Task 1: Eager-load the Bookings list

**Files:**
- Modify: `app/staff/pages/bookings.py`
- Test: `tests/test_staff_pages.py`

**Interfaces:**
- Consumes: `_booking_rows(context, filtr)`, `BookingFilter`, `payment_service.booking_balance`.
- Produces: unchanged row tuple shape; the same filters and balances, but with customer, vehicle, payments, and penalties loaded in three statements.

- [x] **Step 1: Write the failing query-count test**

Add `test_booking_page_load_is_not_n_plus_one` to `tests/test_staff_pages.py` near the existing customer N+1 test. Insert at least 40 extra `Booking` rows for the existing branch customer and vehicle, then use a SQLAlchemy `before_cursor_execute` listener around `_booking_rows(context, BookingFilter())`.

Assert:

```python
assert len(rows) >= 40
assert len(queries) <= 4, f"{len(queries)} queries for the bookings list"
```

- [x] **Step 2: Run the test and verify it fails**

Run:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py::test_booking_page_load_is_not_n_plus_one -v
```

Expected: FAIL with roughly 150+ queries because every booking lazy-loads four relationships.

- [x] **Step 3: Add eager loading**

In `_booking_rows`, add:

```python
from sqlalchemy.orm import joinedload, selectinload
```

and query:

```python
select(Booking)
.options(
    joinedload(Booking.user),
    joinedload(Booking.vehicle),
    selectinload(Booking.payments),
    selectinload(Booking.penalties),
)
.order_by(Booking.created_at.desc(), Booking.booking_id.desc())
.limit(cap)
```

Do not change the row tuple or balance calculations.

- [x] **Step 4: Run the test and the existing Bookings tests**

Run:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py -k "booking" -v
```

Expected: PASS; the new test uses at most four statements.

- [x] **Step 5: Update the plan, commit, and push**

Mark this task complete and update the progress snapshot. Then:

```bash
git add plan.md app/staff/pages/bookings.py tests/test_staff_pages.py
git commit -m "perf: eager-load the bookings list"
git push origin staff-audit-remediation
```

---

## Task 2: Batch the Fleet list queries

**Files:**
- Modify: `app/staff/pages/fleet.py`
- Test: `tests/test_staff_pages.py`

**Interfaces:**
- Consumes: `_fleet_rows(context, filtr)`, `FleetFilter`, `booking_service.BLOCKING_STATUSES`, `fleet_service.maintenance_conflict`.
- Produces: the same fleet row tuple and flag text, with at most one vehicle query, one maintenance-record query, and one active-booking query.

- [ ] **Step 1: Write the failing query-count test**

Add `test_fleet_page_load_is_not_n_plus_one` to `tests/test_staff_pages.py`. Insert at least 40 extra vehicles, call `_fleet_rows(context, FleetFilter())` with a `before_cursor_execute` listener, and assert:

```python
assert len(rows) >= 40
assert len(queries) <= 5, f"{len(queries)} queries for the fleet list"
```

- [ ] **Step 2: Run the test and verify it fails**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py::test_fleet_page_load_is_not_n_plus_one -v
```

Expected: FAIL with roughly two queries per vehicle.

- [ ] **Step 3: Batch the data**

In `_fleet_rows`:

1. Add `selectinload(Vehicle.maintenance_records)` to the vehicle query so `fleet_service.maintenance_conflict` reads loaded records.
2. Replace the per-vehicle `booking_service.active_bookings` calls with one grouped query:

```python
from sqlalchemy import func, select
from app.models import Booking

due_by_vehicle = dict(
    session.execute(
        select(Booking.vehicle_id, func.min(Booking.end_date))
        .where(
            Booking.vehicle_id.in_([v.vehicle_id for v in vehicles]),
            Booking.status.in_(booking_service.BLOCKING_STATUSES),
        )
        .group_by(Booking.vehicle_id)
    ).all()
)
```

3. Use `due = due_by_vehicle.get(vehicle.vehicle_id)` in the loop.
4. Keep the existing `maintenance_conflict` call and issue text unchanged.

- [ ] **Step 4: Run the Fleet and Bookings tests**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py -k "fleet or booking" -v
```

Expected: PASS; Fleet still shows the same due dates, overdue flags, and maintenance text.

- [ ] **Step 5: Update the plan, commit, and push**

```bash
git add plan.md app/staff/pages/fleet.py tests/test_staff_pages.py
git commit -m "perf: batch the fleet list queries"
git push origin staff-audit-remediation
```

---

## Task 3: Aggregate payments and cap the ledger

**Files:**
- Modify: `app/services/payment_service.py`
- Modify: `app/staff/pages/payments.py`
- Test: `tests/test_staff_pages.py`

**Interfaces:**
- Consumes: `payment_service.takings`, `payment_service.list_payments`, `_payment_rows`, `_takings_total`.
- Produces: `takings()` as a single `SUM()` query; `list_payments(..., limit=None)`; the ledger shows the latest `prefs.rows_limit(100)` rows with an explicit “latest” label.

- [ ] **Step 1: Write failing tests**

Add two tests:

1. `test_takings_total_uses_one_aggregate_query` — insert a rental payment and a deposit payment, wrap `payment_service.takings(session)` in a query-count listener, and assert one SQL statement and the same Decimal total as before.
2. `test_payment_ledger_is_capped` — insert enough payments to exceed the configured limit, call `_payment_rows(context)`, and assert the returned count is at most `prefs.rows_limit(100)` and that the UI text says the list is capped/latest.

- [ ] **Step 2: Run the tests and verify they fail**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py -k "takings_total_uses_one_aggregate or payment_ledger_is_capped" -v
```

Expected: FAIL because `takings()` loads full `Payment` rows and `_payment_rows` returns the whole ledger.

- [ ] **Step 3: Implement the aggregate and limit**

In `payment_service.py`, import `func` and replace `takings()` with a `select(func.coalesce(func.sum(Payment.amount), 0))` query filtered by:

```python
Payment.status == "paid"
Payment.kind == "rental"
```

and the existing `created_at` date bounds.

Add an optional `limit: int | None = None` keyword to `list_payments` and apply `.limit(limit)` when set. In `_payment_rows`, pass `limit=prefs.rows_limit(100)`.

Add a visible label next to the payments table, for example:

```text
Latest 100 payments (change the row limit in Settings to see more).
```

Use the actual configured limit in the text.

- [ ] **Step 4: Run the Payments tests**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py -k "payments or takings" -v
```

Expected: PASS; deposit and pending payments are still excluded from takings.

- [ ] **Step 5: Update the plan, commit, and push**

```bash
git add plan.md app/services/payment_service.py app/staff/pages/payments.py tests/test_staff_pages.py
git commit -m "perf: aggregate payments and cap the ledger"
git push origin staff-audit-remediation
```

---

## Task 4: Aggregate and cap outstanding balances

**Files:**
- Modify: `app/staff/pages/payments.py`
- Test: `tests/test_staff_pages.py`

**Interfaces:**
- Consumes: `_outstanding_rows(context, search)`, `payment_service.booking_balance` semantics.
- Produces: one aggregate query for outstanding bookings, ordered by largest balance, capped at `prefs.rows_limit(100)`, with the same displayed balances.

- [ ] **Step 1: Write the failing tests**

Add:

1. `test_outstanding_page_load_is_not_n_plus_one` — create enough bookings to exceed the cap; wrap `_outstanding_rows(context)` in a query-count listener; assert at most four SQL statements and a row count at most the configured cap.
2. Extend the existing balance tests to assert the outstanding amounts still match `booking_balance` for rental, penalty, paid, and `deposit_applied` cases.

- [ ] **Step 2: Run the tests and verify they fail**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py -k "outstanding_page_load or outstanding" -v
```

Expected: FAIL because the current code loads every qualifying booking and eagerly loads payments/penalties.

- [ ] **Step 3: Replace the full scan with an aggregate query**

In `_outstanding_rows`, query `Booking` with `joinedload(Booking.user)` and `joinedload(Booking.vehicle)`, and correlated scalar subqueries for:

- penalty total: `SUM(Penalty.amount)` by booking;
- paid total: `SUM(Payment.amount)` where `status == "paid"` and `kind IN ("rental", "deposit_applied")`;
- balance: `Booking.total_cost + penalty_total - paid_total`.

Filter to `Booking.status IN ("confirmed", "ongoing", "completed")` and `balance > 0`, order by balance descending, and limit to `prefs.rows_limit(100)`. Build the existing row tuple from the aggregate values; do not call `booking_balance` in the loop.

Add a visible “Largest 100 balances” label using the configured limit.

- [ ] **Step 4: Run the Payments and report tests**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py -k "payments or outstanding" tests/test_report_service.py -v
```

Expected: PASS; outstanding remains largest-first and settled bookings remain hidden.

- [ ] **Step 5: Update the plan, commit, and push**

```bash
git add plan.md app/staff/pages/payments.py tests/test_staff_pages.py
git commit -m "perf: aggregate outstanding balances"
git push origin staff-audit-remediation
```

---

## Task 5: Make caps explicit across Bookings, Fleet, and Customers

**Files:**
- Modify: `app/staff/pages/bookings.py`
- Modify: `app/staff/pages/fleet.py`
- Modify: `app/staff/pages/customers.py`
- Test: `tests/test_staff_pages.py`

**Interfaces:**
- Consumes: `prefs.rows_limit`, existing table headings.
- Produces: a visible “Latest N” / “Showing N of M” caption for every capped list; no silent truncation.

- [ ] **Step 1: Write failing caption tests**

Add tests that set `prefs.rows_limit` low, create more rows than the limit, refresh the page, and assert the caption contains the configured number and the word “Latest” or “Showing”.

- [ ] **Step 2: Run the tests and verify they fail**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py -k "latest or capped or showing" -v
```

Expected: FAIL because the pages currently cap silently or not at all.

- [ ] **Step 3: Add the captions and caps**

- Bookings: cap the SQL query at `prefs.rows_limit(100)` and add “Latest N bookings”.
- Fleet: cap at `prefs.rows_limit(100)` and add “Showing latest N vehicles”.
- Customers: cap at `prefs.rows_limit(100)` and add “Latest N customers”.
- Keep the existing filters; do not move filtering after the cap.

- [ ] **Step 4: Run the page tests**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py -k "booking or fleet or customer" -v
```

Expected: PASS; all existing row expectations remain true for the small test branch.

- [ ] **Step 5: Update the plan, commit, and push**

```bash
git add plan.md app/staff/pages/bookings.py app/staff/pages/fleet.py app/staff/pages/customers.py tests/test_staff_pages.py
git commit -m "perf: make list caps explicit"
git push origin staff-audit-remediation
```

---

## Task 6: Paginate and SQL-filter Customers

**Files:**
- Modify: `app/staff/pages/customers.py`
- Test: `tests/test_staff_pages.py`

**Interfaces:**
- Consumes: `_customer_rows`, `CustomerFilter`, `customer_service.outstanding_by_customer`.
- Produces: customer search and filtering in SQL, a capped page, and no per-keystroke full-list rebuild.

- [ ] **Step 1: Write the failing tests**

Add a test with more than the configured customer limit and a search term that matches only an older row outside the first page. Assert the matching customer is still returned.

- [ ] **Step 2: Run the test and verify it fails**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py -k "customer_search_outside" -v
```

Expected: FAIL because the page filters the already-capped Python list.

- [ ] **Step 3: Move filters to SQL and cap the result**

Apply the search, licence bucket, active, balance, and flag filters to the SQL query before `.limit(cap)`. Keep the existing aggregate balance queries scoped to the returned customer IDs where possible. Cache the `licence_expiring_days` settings lookup for the page load.

- [ ] **Step 4: Run the customer tests**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py -k "customer" -v
```

Expected: PASS; the existing customer N+1 test remains under 20 queries.

- [ ] **Step 5: Update the plan, commit, and push**

```bash
git add plan.md app/staff/pages/customers.py tests/test_staff_pages.py
git commit -m "perf: filter and cap customers in SQL"
git push origin staff-audit-remediation
```

---

## Task 7: Move dashboard refresh off the GUI thread

**Files:**
- Modify: `app/staff/pages/dashboard.py`
- Test: `tests/test_staff_dashboard.py`

**Interfaces:**
- Consumes: `dashboard_service.collect`, existing worker pool.
- Produces: an async dashboard refresh that shows the skeleton/current values until the figures land; cached for the configured refresh interval.

- [ ] **Step 1: Write the failing async test**

Add a test that monkeypatches `dashboard_service.collect` to block briefly, calls `page.refresh()`, and asserts the call returns before the figures land and the GUI remains responsive.

- [ ] **Step 2: Run the test and verify it fails**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_dashboard.py -k "async" -v
```

Expected: FAIL because `refresh()` calls `collect()` synchronously.

- [ ] **Step 3: Use the worker pool and a generation guard**

Move the `collect()` call into the shared loader pool, emit the figures back to the GUI thread, and ignore stale generations. Keep a short cache keyed by the configured `behavior/dashboard_refresh_ms`; invalidate on explicit refresh.

- [ ] **Step 4: Run the dashboard tests**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_dashboard.py -v
```

Expected: PASS; existing figures and chart behavior are unchanged.

- [ ] **Step 5: Update the plan, commit, and push**

```bash
git add plan.md app/staff/pages/dashboard.py tests/test_staff_dashboard.py
git commit -m "perf: load the dashboard off the GUI thread"
git push origin staff-audit-remediation
```

---

## Task 8: Batch report queries

**Files:**
- Modify: `app/services/report_service.py`
- Modify: `app/staff/pages/report_panels.py`
- Test: `tests/test_report_service.py`

**Interfaces:**
- Consumes: `utilization_report`, `receivables_report`, `bookings_report`.
- Produces: utilization and receivables reports with bounded query counts; large report tables capped or paginated with a visible count.

- [ ] **Step 1: Write failing query-count tests**

For a 20-vehicle fixture, assert `utilization_report` uses at most 10 SQL statements. For a receivables fixture, assert `receivables_report` uses at most 5 SQL statements and that the returned rows still match the existing balances.

- [ ] **Step 2: Run the tests and verify they fail**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_report_service.py -k "query_count or utilization or receivables" -v
```

Expected: FAIL; utilization currently calls `vehicle_stats` and `period_cost` per vehicle.

- [ ] **Step 3: Replace per-vehicle loops with grouped queries**

Compute rented days, maintenance days, revenue, and shop cost in grouped queries keyed by vehicle ID, then join the maps in memory. Keep the existing sorting and display labels. Cap the receivables/bookings report rows with an explicit “showing N of M” caption.

- [ ] **Step 4: Run the report tests**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_report_service.py tests/test_staff_pages.py -k "report" -v
```

Expected: PASS; report totals and ordering are unchanged.

- [ ] **Step 5: Update the plan, commit, and push**

```bash
git add plan.md app/services/report_service.py app/staff/pages/report_panels.py tests/test_report_service.py
git commit -m "perf: batch report queries"
git push origin staff-audit-remediation
```

---

## Task 9: Add composite indexes through a migration

**Files:**
- Create: `scripts/migrate_performance_indexes.py`
- Create: `tests/test_migrate_performance_indexes.py`
- Modify: `app/models/booking.py`, `app/models/payment.py`, `app/models/maintenance_record.py`, `app/models/inspection_report.py`, `app/models/user.py` if the project declares indexes there.

**Interfaces:**
- Consumes: the existing migration-script pattern.
- Produces: idempotent MySQL indexes for the hot filters and joins.

- [ ] **Step 1: Write failing migration tests**

Test the generated SQL and an execution against SQLite where supported. Assert the script is idempotent and refuses to run against the wrong database.

- [ ] **Step 2: Run the tests and verify they fail**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_migrate_performance_indexes.py -v
```

Expected: FAIL because the migration script does not exist.

- [ ] **Step 3: Add the indexes**

Add, with project-consistent names:

```text
PAYMENT(status, kind, paid_at)
PAYMENT(booking_id, status, kind)
BOOKING(vehicle_id, status, end_date)
BOOKING(status, created_at)
MAINTENANCE_RECORD(vehicle_id, status, start_date, end_date)
INSPECTION_REPORT(inspected_at)
USERS(role, created_at)
```

Do not add an index if an equivalent one already exists. Use `CREATE INDEX IF NOT EXISTS` only if the target MySQL version supports it; otherwise check `information_schema` first.

- [ ] **Step 4: Run the migration tests**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_migrate_performance_indexes.py -v
```

Expected: PASS; the script is safe to re-run.

- [ ] **Step 5: Update the plan, commit, and push**

```bash
git add plan.md scripts/migrate_performance_indexes.py tests/test_migrate_performance_indexes.py app/models
git commit -m "perf: add composite query indexes"
git push origin staff-audit-remediation
```

---

## Task 10: Add query-count and slow-query guardrails

**Files:**
- Create: `app/staff/query_metrics.py`
- Modify: `tests/test_staff_pages.py`, `tests/test_report_service.py`
- Optional: `app/staff/context.py`

**Interfaces:**
- Consumes: SQLAlchemy engine events.
- Produces: a reusable query counter for tests and a development-only slow-query logger.

- [ ] **Step 1: Write failing tests for the helper**

Test that the counter records statements, can be attached/removed from an engine, and that the slow-query logger only logs statements over the configured threshold.

- [ ] **Step 2: Run the tests and verify they fail**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py tests/test_report_service.py -k "query_metrics" -v
```

Expected: FAIL because the helper does not exist.

- [ ] **Step 3: Implement the helper and use it**

Add a context manager:

```python
@contextmanager
def count_queries(engine):
    ...
```

and a development logger gated by an environment variable such as `STAFF_SLOW_QUERY_MS`. Replace the ad-hoc listeners in the performance tests with the helper. Add query-count assertions to every page loader touched by this plan.

- [ ] **Step 4: Run the page/report tests**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_staff_pages.py tests/test_report_service.py -v
```

Expected: PASS; every loader stays under its documented query budget.

- [ ] **Step 5: Update the plan, commit, and push**

```bash
git add plan.md app/staff/query_metrics.py tests/test_staff_pages.py tests/test_report_service.py app/staff/context.py
git commit -m "test: guard staff page query counts"
git push origin staff-audit-remediation
```

---

## Task 11: Re-measure the live database and record results

**Files:**
- Modify: `plan.md`

**Interfaces:**
- Consumes: the completed loaders and the read-only live measurement script pattern.
- Produces: a signed-off performance record in this file; no live writes.

- [ ] **Step 1: Run the full Linux suite**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

Expected: all tests pass; note any environmental CarImages failures separately.

- [ ] **Step 2: Re-run the read-only live measurements**

Measure the same page loaders against the live database with query-count listeners:

- Bookings: 100 rows
- Fleet: 100 vehicles
- Payments: latest 100
- Takings total
- Outstanding: latest 100
- Customers: latest 100
- Dashboard
- Inspections / Maintenance

Record the statement count and elapsed time for each.

- [ ] **Step 3: Record the results in this plan**

Add a “Measured After” table and mark each target:

- Bookings: target ≤ 4 statements, ~1–2s.
- Fleet: target ≤ 5 statements, ~1–2s.
- Payments ledger: target ≤ 2 statements, <1s.
- Takings: target 1 statement, ~0.5s.
- Outstanding: target ≤ 4 statements, <1s.
- Customers: target ≤ 10 statements, <1.5s.
- Dashboard: target non-blocking; ~1s background.
- Reports: target bounded query counts.

- [ ] **Step 4: Update the plan, commit, and push**

```bash
git add plan.md
git commit -m "docs: record staff performance verification"
git push origin staff-audit-remediation
```

- [ ] **Step 5: Stop and request sign-off**

Do not merge into `main`. Report the before/after table and ask the user whether to proceed with the live counter demo or continue with the remaining P1/P2 work.

---

## Completion Checklist

- [x] Task 1: Bookings eager loading
- [ ] Task 2: Fleet batch loading
- [ ] Task 3: Payments aggregate + cap
- [ ] Task 4: Outstanding aggregate + cap
- [ ] Task 5: Explicit caps
- [ ] Task 6: Customers SQL filters + cap
- [ ] Task 7: Dashboard async
- [ ] Task 8: Reports batched
- [ ] Task 9: Composite indexes
- [ ] Task 10: Query guardrails
- [ ] Task 11: Live re-measurement

## Resume Instructions for Another Device

1. `git fetch origin`
2. `git switch staff-audit-remediation`
3. `git pull --ff-only`
4. Read `plan.md` and continue at the first unchecked task.
5. After the task: run its targeted test, update the checkbox and Progress Snapshot, commit, and push.
6. Never run tests against live MySQL; use SQLite fixtures.
