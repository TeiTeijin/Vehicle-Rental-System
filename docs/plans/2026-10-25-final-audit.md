# Final Audit Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audit the merged customer + staff system end to end, fix blocking defects, and produce a sign-off report without merging to `main` until the audit is approved.

**Architecture:** Two PySide6 desktop applications share SQLAlchemy services and one MySQL/SQLite schema. The customer app lives in `app/ui`; the staff app lives in `app/staff`; domain and data rules live in `app/services`, `app/domain`, and `app/models`. Audit by complete flow first, then by cross-cutting invariants.

**Tech Stack:** Python 3.14, PySide6, SQLAlchemy 2, MySQL/SQLite, pytest, pytest-xdist, PDF/CSV export, CarImages media API.

**Spec:** This plan is the audit specification. It also depends on:
- `docs/plans/2026-10-22-vehicle-rental-audit-remediation.md` — completed staff audit record.
- `plan.md` — completed performance work and live measurements.
- The merged branch `final-audit` at commit `515c014`.

**Draft location:** `/home/tei/.opencode/plan/2026-10-25-final-audit.md`  
**Canonical repo copy after Task 1:** `docs/plans/2026-10-25-final-audit.md`

**Current state:**
- `staff-audit-remediation` is at `7ad5259` and is the known-good staff baseline.
- `final-audit` is at `515c014` and contains the customer branch merge.
- `origin/Customer_side` was merged at `c38eff4`.
- The merge resolved conflicts in `booking_service.py`, `customer_service.py`, and `image_cache.py`.
- Merge-adjacent booking, customer, payment, security, and receipt tests pass.
- The full suite has **2 known failures** in `tests/test_migrate_staff_schema.py`; both are migration-order/coverage issues, not conflict markers.
- The customer branch has not been audited.

---

## Global Constraints

- Work only on the `final-audit` branch. Do not modify `main` or `staff-audit-remediation`.
- Do not use live MySQL for tests. Use a temporary SQLite database or a sanitized snapshot.
- If a live read-only measurement is necessary, record it separately and never let a test write to it.
- Run GUI tests with `QT_QPA_PLATFORM=offscreen`.
- All money remains `Decimal`; never write floats into money columns.
- Every database write must go through a service, not directly from a UI page.
- Preserve the layers: customer UI → services; staff UI → services; services → models/database.
- Preserve the separate customer (`app/ui`) and staff (`app/staff`) surfaces.
- Commit and push after every completed task. Use `git add` explicitly; do not commit unrelated files.
- Do not merge `main` until Task 24 passes and the user explicitly approves.
- One implementer at a time. Do not dispatch parallel subagents.
- Treat customer-branch tests as evidence, not proof. Verify the behavior they claim to cover.
- CarImages/API/media failures are environmental until proven otherwise; label them separately from product defects.
- If a finding requires a product, schema, or security policy decision, record it and stop before making a destructive change.
- Never weaken a test to make a finding disappear. Add a regression test for every fixed defect.

## Finding Severity

- **Critical:** data loss, auth bypass, wrong money/deposit accounting, or the main flow cannot complete.
- **High:** major flow broken, PII/cross-customer leak, migration failure, or staff cannot process a booking.
- **Medium:** workaround exists, incorrect secondary behavior, performance/UX defect.
- **Low:** polish, copy, minor layout, non-blocking inconsistency.
- **Environmental:** external API/network unavailable; record separately and retest when available.

## Review Focus

These are the five failure modes most likely to bite a real user. Each has an owning task and must be explicitly tested there.

1. **Customer/staff authentication boundary:** a staff account must not sign in through the customer flow, a customer must not reach staff pages, and inactive/flagged accounts must be blocked. Owner: Task 21, exercised by Tasks 5 and 9.
2. **Deposit liability:** held deposits are not revenue; `deposit_applied` is not cash; checkout settles the exact shortfall; refunds do not double-count. Owner: Task 13, exercised by Tasks 18 and 19.
3. **Availability and concurrency:** online and counter bookings use the same vehicle/window rules; overlapping dates, same-day rules, and simultaneous booking attempts are handled. Owner: Tasks 7, 10, and 12.
4. **Migration order:** staff schema + customer booking schema + guarantor/profile schema produce the same schema on fresh and legacy databases. Owner: Task 3.
5. **Branch/PII/media boundaries:** customer data, licence documents, branch selection, image cache keys, and payment references do not leak across customers or escape the cache directory. Owner: Tasks 21 and 22.

---

## System Inventory

### Size and surfaces

- 2 applications: customer and staff.
- 3 roles: customer, staff, admin.
- 15 database tables.
- 27 service modules.
- 28 operational/migration scripts.
- 46 test files.
- 10 staff pages and 8 report panels.
- 4 customer tabs plus vehicle details, booking schedule, and checkout screens.
- 26 shared branch settings, plus per-PC QSettings preferences.

### Actors

| Actor | Surfaces | Main capabilities |
|---|---|---|
| Customer | `app/ui` | Sign up, sign in, reset password, browse, book, view bookings/payments/notifications, edit profile |
| Staff | `app/staff` | Counter rentals, bookings, customers, fleet, payments, inspections, maintenance, reports |
| Admin | `app/staff` | Staff accounts, shared settings, backups, full staff access |
| Inactive customer | Customer app | Must be blocked from sign-in and booking |
| Flagged customer | Both | Staff see warning and override note; eligibility decides booking |
| First-run admin | Staff app | Create the first admin when no users exist |

### Customer screens

| Area | Modules |
|---|---|
| Router/session | `app/ui/main_window.py`, `session.py` |
| Authentication | `customer_login.py`, `auth_gate_dialog.py`, `signup.py`, `forgot_password.py` |
| Discovery | `home.py`, `hero.py`, `vehicles.py`, `vehicle_details.py`, `vehicle_photo.py` |
| Booking | `booking_schedule.py`, `booking_checkout.py` |
| Self-service | `bookings.py`, `payments.py`, `notifications.py`, `profile.py`, `account_popup.py` |

Customer tabs registered by the router: `vehicles`, `bookings`, `payments`, `profile`. Transient screens: `details`, `schedule`, `checkout`.

### Staff pages

| Page | Main responsibility |
|---|---|
| Dashboard | Revenue, channels, heatmap, recent transactions, monthly sales |
| New Rental | Walk-in customer lookup/create, vehicle/date selection, quote, payment, deposit, confirm |
| Bookings | Today/upcoming/history, search, check-in, checkout, cancel, extend, receipt |
| Fleet | Add/edit/retire vehicles, rates, bulk rate/status, photos, stats |
| Customers | Search/create/edit, licence docs, flags, notes, balances, open bookings |
| Payments | Ledger, search, record payment, refund, takings, outstanding |
| Inspections | Pre/post-rental inspections, mileage, fuel, damage |
| Maintenance | Schedule, start, complete, cancel, costs, due-soon, status sync |
| Reports | Revenue, bookings, utilization, performance, maintenance cost, receivables, customers, staff |
| Settings | Shared policy, staff accounts, first-run admin, backup, cache, API, about |

### Flow IDs

| ID | Flow | Primary owner task |
|---|---|---|
| C1 | Customer signup → account created | Task 5 |
| C2 | Customer login → session | Task 5 |
| C3 | Forgot/reset password | Task 5 |
| C4 | Browse → search/filter → vehicle details/media | Task 6 |
| C5 | Schedule → checkout → online booking created | Task 7 |
| C6 | Customer views bookings | Task 8 |
| C7 | Customer views payments/notifications | Task 8 |
| C8 | Customer profile/session/logout/auth gate | Task 8 |
| S1 | Staff login / first-run admin | Task 9 |
| S2 | Walk-in rental from lookup to confirmation | Task 10 |
| S3 | Online booking appears in staff Bookings | Task 11 |
| S4 | Pickup/check-in | Task 12 |
| S5 | Return/checkout, deposit, settlement, receipt | Task 13 |
| S6 | Cancellation and extension | Task 14 |
| S7 | Fleet lifecycle and rates | Task 15 |
| S8 | Maintenance lifecycle | Task 16 |
| S9 | Customer management and documents | Task 17 |
| S10 | Payments, refunds, deposits, takings, outstanding | Task 18 |
| S11 | Reports, dashboard, exports | Task 19 |
| S12 | Settings, staff accounts, backup, cache | Task 20 |
| X1 | Customer online booking → staff processing | Task 11 |
| X2 | Customer payment/deposit → staff ledger | Tasks 13, 18 |
| X3 | Staff payment/checkout → customer history | Task 13 |
| X4 | All flows → reports/dashboard | Task 19 |
| X5 | Branch/location → availability and pickup/return | Tasks 5–7, 11–13, 17 |
| X6 | Media/photos → customer and staff surfaces | Task 22 |
| X7 | Booking state → customer notifications | Tasks 8, 11, 13 |
| X8 | Roles/authorization/PII/branch scope | Task 21 |
| X9 | Migration/seed/data integrity | Tasks 3 and 21 |

### State machines

| Entity | States / transitions |
|---|---|
| Booking | `pending → confirmed → ongoing → completed`; `pending/confirmed → cancelled`; extension; actual return date; no-show handling must be verified |
| Payment | `pending`, `paid`, `failed`, `refunded` |
| Payment kind | `rental`, `deposit`, `deposit_applied` |
| Deposit | Held → applied to penalties → returned/refunded; exact rules must be verified |
| Vehicle | `available`, `reserved`, `rented`, `maintenance`, `retired` |
| Maintenance | `scheduled`, `in_progress`, `completed`, `cancelled` |
| User | Role, active/inactive, flagged, temporary password, source `walk_in`/`online` |
| Branch | Pickup/return branch, opening hours, slot windows |

### Data model

| Table | Purpose |
|---|---|
| `USERS` | Customers, staff, admins; auth, licence, guarantor, PSGC, flags, status |
| `BRANCH` | Pickup/return locations and opening hours |
| `VEHICLE_CATEGORY` | Vehicle class and rate multiplier |
| `VEHICLE` | Fleet identity, specs, rates, status, paperwork |
| `VEHICLE_MEDIA` | Cached image/3D media metadata |
| `VEHICLE_RATE_LOG` | Rate-change history |
| `BOOKING` | Rental dates, online pickup/return fields, price breakdown, status |
| `PAYMENT` | Rental, deposit, and deposit-applied ledger rows |
| `PENALTY` | Late, damage, cleaning, other charges |
| `INSPECTION_REPORT` | Pre/post-rental inspection |
| `MAINTENANCE_RECORD` | Scheduled/in-progress/completed/cancelled maintenance |
| `CUSTOMER_NOTE` | Staff notes |
| `APP_SETTINGS` | Shared branch policy and pricing |
| `PSGC_PROVINCES` | Location reference data |
| `PSGC_CITIES_MUNICIPALITIES` | Location reference data |

### Services

| Group | Modules |
|---|---|
| Identity | `auth_service`, `profile_service`, `staff_service`, `customer_service`, `location_service` |
| Inventory | `vehicle_service`, `fleet_service`, `media_service`, `vehicle_photos`, `image_cache`, `api_cache`, `carimages_client` |
| Booking | `booking_service`, `branch_service`, `pricing`, `counter_service` |
| Money | `payment_service`, `card_validation`, `receipt_service` |
| Reporting | `report_service`, `dashboard_service`, `export_service` |
| Operations | `maintenance_service`, `notification_service`, `settings_service`, `backup_service` |
| Shared | `errors`, `app.database`, `app.config`, `app.domain.*`, `app.staff.context`, `app.staff.query_metrics` |

### Integrations and configuration

- Database: MySQL/SQLite, TLS CA handling, `DATABASE_URL`, session/transaction behavior.
- Media: CarImages API, signed URLs, image cache, API cache, 3D model GLB.
- Payments: mock card/GCash charging, reference validation, pay-at-pickup.
- Documents: receipt PDF, customer statement PDF, CSV/PDF exports.
- Notifications: in-app derived notifications; no external email/SMS.
- Settings: 26 shared keys across pricing, deposits, operations, and receipts.
- Per-PC preferences: theme, sidebar, export folder, refresh timer, idle logout.
- Security: password hashing, temp passwords, session, PII, licence docs, path safety.
- Backup: live DB snapshot, sanitization, local file security.

### Scripts

- Initialization/seeding: `init_db`, `seed_data`, `seed_demo_data`, `seed_customer_catalog`.
- Schema migrations: `migrate_staff_schema`, `migrate_customer_booking`, `migrate_customer_management`, `migrate_guarantor_profile`, `migrate_app_settings`, `migrate_deposit_applied`, `migrate_payment_kind`, `migrate_maintenance_lifecycle`, `migrate_performance_indexes`, `migrate_vehicle_class`, `migrate_vehicle_management`, `migrate_vehicle_media_urls`, `migrate_vehicle_specs`.
- Operations/security: `backup_live_db`, `secure_local_files`, `clear_media_cache`.
- Diagnostics/demo: `test_api_connection`, `test_auth`, `test_booking_flow`, `test_domain`, `test_hero`, `test_image_cache`, `test_media_service`, `show_gallery`.

### Test surface

46 test files, including:

- Customer: `test_customer_auth_ui`, `test_signup_location`, `test_customer_profile`, `test_customer_payments`, `test_customer_checkout_payment`, `test_customer_flow_audit`, `test_customer_notifications`, `test_customer_password_reset`, `test_customer_account_popup`.
- Staff: `test_staff_pages`, `test_staff_dashboard`, `test_staff_settings`, `test_staff_shell`, `test_staff_login`, `test_staff_ui`.
- Domain/services: `test_booking_service`, `test_counter_rental`, `test_counter_service`, `test_rental_life_cycle`, `test_payment_kind`, `test_receipt_service`, `test_pricing_policy`, `test_money`, `test_fleet_management`, `test_maintenance_service`, `test_report_service`.
- Migrations: 10 `test_migrate_*` files.
- Cross-cutting: `test_security_hardening`, `test_query_metrics`, `test_worker_lifecycle`, `test_backup_live_db`, `test_seed_demo_data`.

### Known merge gaps

1. `tests/test_migrate_staff_schema.py` fails because the fixture runs only `migrate_staff_schema`; `BRANCH` and the customer booking columns come from `migrate_customer_booking`.
2. The customer branch is unaudited.
3. Three merge resolutions need behavioral review: `booking_service.py`, `customer_service.py`, `image_cache.py`.
4. Auto-merged `payment_service.py`, `receipt_service.py`, `vehicle_service.py`, and `settings.py` need semantic review.
5. Branch/PSGC data and online booking columns need migration-order verification.
6. The old image-cache key scheme and the new safe/variant key scheme need compatibility verification.
7. Reports/dashboards now aggregate customer-created bookings and deposits; numbers must agree across both apps.

---

## Audit Register Contract

Create `docs/audits/final-audit-register.md` with one row per flow and cross-cutting item.

| Field | Meaning |
|---|---|
| ID | `C1`…`C8`, `S1`…`S12`, `X1`…`X9`, or `F-###` for a finding |
| Area | Customer, staff, shared, data, security, reporting |
| Flow | What the user/system is trying to do |
| Entry point | Screen, command, or service |
| Services | Modules involved |
| Tables | Rows read/written |
| Roles | Who can trigger it |
| Invariants | What must always be true |
| Existing tests | What already claims coverage |
| Evidence | UI state, DB rows, query counts, screenshots, log path |
| Status | `Not started`, `In progress`, `Pass`, `Finding`, `Blocked`, `Waived` |
| Severity | Critical / High / Medium / Low / Environmental |
| Finding | Reproduction and expected behavior |

**Definition of done for an audit task:**
- Every owned flow ID is `Pass` or has a `Finding` row.
- Evidence is recorded in the register.
- Any blocking fix has a regression test.
- The task's tests pass.
- The register and code are committed and pushed.

**If a task finds a non-blocking defect:** record it in the register and continue. Fix it in Task 24 unless it blocks the next flow.
**If a task finds a blocking defect:** fix it in the same task using TDD, add a regression test, and record the fix.

---

## Task 1: Establish the Audit Baseline and Register

**Files:**
- Create: `docs/audits/final-audit-register.md`
- Create: `docs/audits/final-audit-report.md`
- Copy: this plan to `docs/plans/2026-10-25-final-audit.md`
- Test: none; this is the audit workspace.

**Interfaces:**
- Consumes: `final-audit` branch at `515c014`.
- Produces: a committed audit register with all IDs, baseline logs, and a canonical plan path in the repo.

- [x] **Step 1: Refresh and verify the branch**

```bash
git fetch origin --prune
git switch final-audit
git pull --ff-only
git status --short --branch
git log --oneline --decorate -5
```

Record the exact commit in the register.

- [x] **Step 2: Copy the plan into the repo**

Copy `/home/tei/.opencode/plan/2026-10-25-final-audit.md` to `docs/plans/2026-10-25-final-audit.md`. This is the canonical copy for other devices.

- [x] **Step 3: Create the register skeleton**

Add the 29 flow IDs (`C1`–`C8`, `S1`–`S12`, `X1`–`X9`) with the fields from the Audit Register Contract. Set every status to `Not started`.

- [x] **Step 4: Record the test baseline**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest --collect-only -q
QT_QPA_PLATFORM=offscreen python -m pytest -q > /tmp/opencode/final-audit-baseline.log 2>&1
echo $?
```

Record the collected count, failures, skips, and log path. Expected current failures: the two `test_migrate_staff_schema.py` tests.

- [x] **Step 5: Create the report skeleton**

Add sections for scope, branch/commit, environment, baseline failures, findings summary, fixes, and sign-off status.

- [x] **Step 6: Commit and push**

```bash
git add docs/plans/2026-10-25-final-audit.md docs/audits/final-audit-register.md docs/audits/final-audit-report.md
git commit -m "docs: start final audit register"
git push origin final-audit
```

**Exit criteria:** the plan and register are in the repo; baseline is recorded; no production code changed.

---

## Task 2: Complete the Whole-System Traceability Map

**Files:**
- Modify: `docs/audits/final-audit-register.md`
- Reference: `app/ui/main_window.py`, `app/staff/shell.py`, all `app/services/*.py`, all `app/models/*.py`.

**Interfaces:**
- Consumes: the flow IDs and inventory in this plan.
- Produces: every screen, flow, service, table, and test mapped to a register row.

- [x] **Step 1: Add the screen-to-flow matrix**

For each customer screen and staff page, list which flow IDs it owns. Include transient screens (`details`, `schedule`, `checkout`) and report panels.

- [x] **Step 2: Add the service-to-flow matrix**

For each service module, list the flows it serves and the public functions that mutate data.

- [x] **Step 3: Add the table-to-flow matrix**

For each table, list which flows read and write it, and which invariants apply.

- [x] **Step 4: Add the state-machine table**

Record every legal and illegal transition for booking, payment, deposit, vehicle, maintenance, and user status.

- [x] **Step 5: Add open questions**

Use `Open question` rows with an owner task, not vague `TBD`s. Examples:
- Is there a staff branch assignment, or is branch scope only pickup/return?
- Is no-show represented as `cancelled`, or is it missing?
- Are old image-cache keys migrated or invalidated?
- Does `migrate_guarantor_profile` belong in the canonical migration order?

- [x] **Step 6: Review for missing surfaces**

Check `app/ui/dashboard.py`, `app/ui/hero.py`, `app/staff/booking_panels.py`, `customer_panels.py`, `rental_wizard.py`, and report panels. Mark each as active, unused/legacy, or needing audit.

- [x] **Step 7: Commit and push**

```bash
git add docs/audits/final-audit-register.md
git commit -m "docs: map final audit flows and surfaces"
git push origin final-audit
```

**Exit criteria:** every route and service has a flow owner; every unknown has an owner task.

---

## Task 3: Migration and Schema Gate

**Files:**
- Modify: `tests/test_migrate_staff_schema.py` (fixture and/or new full-schema test)
- Read: `scripts/migrate_staff_schema.py`, `scripts/migrate_customer_booking.py`, `scripts/migrate_guarantor_profile.py`, `app/models/*.py`
- Modify if the audit decision requires it: migration scripts and their docs
- Test: `tests/test_migrate_staff_schema.py`, `tests/test_migrate_customer_management.py`, `tests/test_signup_location.py`

**Interfaces:**
- Consumes: the legacy `old_db` fixture and `Base.metadata`.
- Produces: one documented migration order that produces all model tables/columns on a legacy database.

- [x] **Step 1: Write the failing full-schema test**

Add `test_documented_migration_order_matches_models` to `tests/test_migrate_staff_schema.py`. It must:
1. Build the legacy database.
2. Run `migrate_staff_schema`, then `migrate_customer_booking`, then `migrate_guarantor_profile` against the same engine.
3. Assert every table and column in `Base.metadata` exists.
4. Assert a `Booking` row can be inserted with `pickup_dt`, `return_dt`, and branch foreign keys.

Before the fix, expected FAIL: `BRANCH` missing or `BOOKING.pickup_dt` missing.

- [x] **Step 2: Run it and capture the failure**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_migrate_staff_schema.py -k "documented_migration_order" -v
```

Record the exact missing table/column.

- [x] **Step 3: Decide the canonical order**

Preferred order:
1. `python -m scripts.migrate_staff_schema`
2. `python -m scripts.migrate_customer_booking`
3. `python -m scripts.migrate_guarantor_profile`
4. seeds

If a migration script already performs another's work, document that instead of duplicating it. Record the decision in the register.

- [x] **Step 4: Fix the test fixture or production migration**

If the separate customer migration is intentional, update the `old_db` fixture to run the documented order and keep the staff migration focused. If a production script is incomplete, add the missing table/column with an idempotent guard. Do not make the test pass by weakening the metadata comparison.

- [x] **Step 5: Run all migration tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_migrate_*.py -q
```

Expected: PASS.

- [x] **Step 6: Verify fresh and legacy databases**

```bash
rm -f /tmp/opencode/final-audit-fresh.db
DATABASE_URL=sqlite:////tmp/opencode/final-audit-fresh.db python -m scripts.init_db
DATABASE_URL=sqlite:////tmp/opencode/final-audit-fresh.db python -m scripts.migrate_staff_schema
DATABASE_URL=sqlite:////tmp/opencode/final-audit-fresh.db python -m scripts.migrate_customer_booking
DATABASE_URL=sqlite:////tmp/opencode/final-audit-fresh.db python -m scripts.migrate_guarantor_profile
```

Run the same order against a copied legacy SQLite database. Both must be idempotent on a second run.

- [x] **Step 7: Commit and push**

```bash
git add tests/test_migrate_staff_schema.py scripts/ docs/audits/final-audit-register.md
git commit -m "fix: make final audit schema migration complete"
git push origin final-audit
```

**Exit criteria:** migration tests pass; fresh and legacy databases match models; order documented.

---

## Task 4: Golden-Path End-to-End Harness

**Files:**
- Create: `tests/test_final_audit_flow.py`
- Modify: `docs/audits/final-audit-register.md`
- Reference: `tests/test_customer_flow_audit.py`, `tests/test_counter_rental.py`, `tests/test_booking_service.py`, `tests/test_receipt_service.py`

**Interfaces:**
- Consumes: temporary SQLite `StaffContext`, `booking_service`, `payment_service`, `receipt_service`, `report_service`.
- Produces: one repeatable test proving a customer-created booking can be processed by staff to completion.

- [x] **Step 1: Write the harness test**

`test_online_booking_reaches_staff_and_completes` must:
1. Seed a category, branch, vehicle, customer, and staff member in a temporary SQLite database.
2. Create an online booking with `booking_service.create_online_booking`.
3. Assert `status == "pending"`, `channel == "online"`, pickup/return datetimes, and a reference code.
4. Confirm it as staff, check it in, record rental and deposit payments.
5. Check it out with no shortfall; assert `status == "completed"`.
6. Assert customer `payments_for_user` sees the rental/deposit rows.
7. Assert `receipt_service.receipt_rows` includes the booking reference.
8. Assert dashboard/report figures include the rental revenue and exclude held deposit from revenue.

- [x] **Step 2: Run it**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_final_audit_flow.py -k "online_booking_reaches_staff_and_completes" -v
```

If it fails, record the failure as a finding. Fix only if it blocks later tasks; otherwise continue and add to Task 24.

- [x] **Step 3: Add the result to the register**

Mark `C5`, `S3`, `S4`, `S5`, `X1`, `X2`, `X3`, and `X4` as `Pass` or `Finding` based on the evidence.

- [x] **Step 4: Commit and push**

```bash
git add tests/test_final_audit_flow.py docs/audits/final-audit-register.md
git commit -m "test: add final audit golden path"
git push origin final-audit
```

**Exit criteria:** one reproducible test covers the customer-to-staff-to-customer loop, or the failure is recorded with a blocking decision.

---

## Task 5: Customer Account Flow (C1–C3)

**Files:**
- Read: `app/ui/signup.py`, `app/ui/customer_login.py`, `app/ui/forgot_password.py`, `app/ui/auth_gate_dialog.py`
- Read: `app/services/auth_service.py`, `app/services/profile_service.py`, `app/services/location_service.py`
- Test: `tests/test_customer_auth_ui.py`, `tests/test_signup_location.py`, `tests/test_customer_password_reset.py`, `tests/test_customer_flow_audit.py`

**Interfaces:**
- Consumes: `auth_service.register_customer`, `sign_in_customer`, `password_reset_account`, `reset_customer_password`, `location_service.resolve`.
- Produces: verified account creation, login, reset, and role-boundary behavior.

- [ ] **Step 1: Run the account tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_customer_auth_ui.py \
  tests/test_signup_location.py \
  tests/test_customer_password_reset.py \
  tests/test_customer_flow_audit.py -q
```

- [ ] **Step 2: Walk the signup flow on a temp database**

Use a temporary SQLite database and the customer app. Exercise:
- valid signup
- duplicate email
- duplicate licence
- staff licence collision
- invalid mobile
- invalid street address
- incomplete guarantor
- city outside province
- random province

Verify the `USERS` row: password hash (not plaintext), `role="customer"`, `source="online"`, PSGC codes, formatted address, licence, guarantor, `is_active=True`.

- [ ] **Step 3: Walk login and reset**

Verify:
- valid customer login
- wrong password returns a generic message
- inactive customer cannot sign in
- staff account cannot use customer login
- password reset does not enumerate accounts
- reset password follows the same policy as signup

- [ ] **Step 4: Record evidence and findings**

Add `C1`, `C2`, `C3`, and the auth part of `X8` to the register. Record exact test commands and DB values.

- [ ] **Step 5: Fix blocking findings with TDD**

If a blocking defect exists, add a regression test first, then fix the service/UI. Do not change the policy silently.

- [ ] **Step 6: Commit and push**

```bash
git add app/ui app/services tests docs/audits/final-audit-register.md
git commit -m "audit: verify customer account flow"
git push origin final-audit
```

**Exit criteria:** `C1`–`C3` are `Pass` or have findings; auth boundary evidence is recorded.

---

## Task 6: Discovery, Vehicle Details, and Media (C4)

**Files:**
- Read: `app/ui/home.py`, `app/ui/hero.py`, `app/ui/vehicles.py`, `app/ui/vehicle_details.py`, `app/ui/vehicle_photo.py`
- Read: `app/services/vehicle_service.py`, `media_service.py`, `vehicle_photos.py`, `carimages_client.py`, `image_cache.py`, `api_cache.py`
- Test: `tests/test_customer_flow_audit.py`, `tests/test_media_warning.py`, `tests/test_security_hardening.py`

**Interfaces:**
- Consumes: showcase/search/filter services, branch data, media cache, CarImages client.
- Produces: verified browsing, filtering, vehicle details, and media fallback behavior.

- [ ] **Step 1: Run the discovery and media tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_customer_flow_audit.py \
  tests/test_media_warning.py \
  tests/test_security_hardening.py -q
```

- [ ] **Step 2: Walk discovery on a temp database**

Exercise search, category/body/fuel/transmission filters, rate bounds, vehicle details, and branch selection. Verify:
- only rentable/available vehicles appear where expected
- prices and labels match the service values
- empty/error states are usable
- a retired/maintenance vehicle is not bookable

- [ ] **Step 3: Verify media behavior**

With CarImages unavailable:
- the UI must not crash or hang
- cached media is used when present
- a clear placeholder/fallback appears
- failures are recorded as environmental, not product defects

With cache present:
- image variants (`front`, `side`, etc.) map to distinct keys
- `_identity` has no path separators
- `_paths` rejects traversal
- cache clear/stats work

- [ ] **Step 4: Record evidence and findings**

Mark `C4`, `X5` (branch), and `X6` (media) in the register. Include whether the failure was environmental.

- [ ] **Step 5: Fix blocking findings with TDD**

- [ ] **Step 6: Commit and push**

```bash
git add app/ui app/services tests docs/audits/final-audit-register.md
git commit -m "audit: verify customer discovery and media"
git push origin final-audit
```

**Exit criteria:** `C4` is `Pass` or has findings; media failures are classified.

---

## Task 7: Online Booking and Checkout (C5)

**Files:**
- Read: `app/ui/booking_schedule.py`, `app/ui/booking_checkout.py`, `app/services/booking_service.py`, `branch_service.py`, `pricing.py`, `card_validation.py`, `payment_service.py`
- Test: `tests/test_customer_flow_audit.py`, `tests/test_customer_checkout_payment.py`, `tests/test_booking_service.py`, `tests/test_pricing_policy.py`

**Interfaces:**
- Consumes: `booking_service.create_online_booking`, `assert_window_free`, `pricing.quote`, `branch_service.validate_window`, `card_validation.collect`.
- Produces: verified booking creation, pricing, payment-method, and branch rules.

- [ ] **Step 1: Run the booking tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_customer_flow_audit.py \
  tests/test_customer_checkout_payment.py \
  tests/test_booking_service.py \
  tests/test_pricing_policy.py -q
```

- [ ] **Step 2: Walk the booking flow**

Verify:
- pickup/return date and time are required
- branch opening hours and slot windows are enforced
- overlapping bookings are rejected
- same-day policy is honored
- price breakdown equals `subtotal + insurance_fee + service_fee + vat`
- `total_cost` equals the grand total
- long-term and motorcycle multipliers apply correctly
- online booking starts `pending`
- `channel="online"`, `reference_code` is unique, pickup/return branches are stored
- expired licence, inactive customer, staff account, and workshop vehicle are rejected

- [ ] **Step 3: Walk payment methods**

For card, GCash, and pay-at-pickup:
- card number is never stored in full; only `card_last4`
- CVV is never stored
- GCash reference rules are enforced when required
- failed/pending payments do not mark the booking paid
- customer cannot submit an impossible amount

- [ ] **Step 4: Record evidence and findings**

Mark `C5`, `X2`, `X5`, and the booking part of `X1` in the register.

- [ ] **Step 5: Fix blocking findings with TDD**

- [ ] **Step 6: Commit and push**

```bash
git add app/ui app/services tests docs/audits/final-audit-register.md
git commit -m "audit: verify online booking and checkout"
git push origin final-audit
```

**Exit criteria:** `C5` is `Pass` or has findings; pricing and payment invariants are evidenced.

---

## Task 8: Customer Self-Service and Session (C6–C8)

**Files:**
- Read: `app/ui/bookings.py`, `payments.py`, `notifications.py`, `profile.py`, `account_popup.py`, `session.py`, `main_window.py`
- Read: `booking_service.bookings_for`, `payment_service.payments_for_user`, `notification_service.notifications_for`, `profile_service.profile_for/update_profile`
- Test: `tests/test_customer_payments.py`, `test_customer_profile.py`, `test_customer_notifications.py`, `test_customer_account_popup.py`, `test_customer_flow_audit.py`

**Interfaces:**
- Consumes: signed-in customer identity and customer-scoped service queries.
- Produces: verified customer-only visibility, profile updates, notifications, and logout.

- [ ] **Step 1: Run the self-service tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_customer_payments.py \
  tests/test_customer_profile.py \
  tests/test_customer_notifications.py \
  tests/test_customer_account_popup.py \
  tests/test_customer_flow_audit.py -q
```

- [ ] **Step 2: Verify customer isolation**

Create two customers with bookings and payments. Sign in as each and verify:
- bookings list only own rows
- payments list only own rows
- notifications only own rows
- profile only own row
- direct service calls with another user's ID are not exposed through the UI

- [ ] **Step 3: Verify status and money display**

For each booking state (`pending`, `confirmed`, `ongoing`, `completed`, `cancelled`), verify the customer sees the correct bucket/status. Verify rental, deposit, and `deposit_applied` rows are labeled correctly and totals match `payment_service.booking_balance`.

- [ ] **Step 4: Verify profile and session**

Edit name, phone, address, licence, guarantor, and location. Verify validation, DB persistence, and refresh across the navbar/profile. Verify logout clears identity and protected actions show the auth gate.

- [ ] **Step 5: Record evidence and findings**

Mark `C6`, `C7`, `C8`, `X7`, and the customer-data part of `X8`.

- [ ] **Step 6: Fix blocking findings with TDD**

- [ ] **Step 7: Commit and push**

```bash
git add app/ui app/services tests docs/audits/final-audit-register.md
git commit -m "audit: verify customer self-service"
git push origin final-audit
```

**Exit criteria:** customer isolation and self-service behavior are evidenced.

---

## Task 9: Staff Authentication and First-Run Admin (S1)

**Files:**
- Read: `app/staff/__main__.py`, `app/staff/login.py`, `app/staff/shell.py`, `app/staff/settings_panels.py`
- Read: `app/services/auth_service.py`, `staff_service.py`
- Test: `tests/test_staff_login.py`, `tests/test_staff_shell.py`, `tests/test_staff_settings.py`

**Interfaces:**
- Consumes: `auth_service.sign_in`, `staff_service.ensure_first_admin`, `change_password`, `force_change_password`, `reset_password`, `set_active`.
- Produces: verified staff/admin login, first-run setup, temporary-password handling, and role gates.

- [ ] **Step 1: Run the staff auth tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_staff_login.py \
  tests/test_staff_shell.py \
  tests/test_staff_settings.py -q
```

- [ ] **Step 2: Walk first-run setup**

On an empty database:
- the first-run admin form appears
- creating the first admin succeeds
- a second first-run admin is blocked
- password policy and email validation are enforced

- [ ] **Step 3: Walk staff login and role gates**

Verify:
- valid staff/admin login
- customer account cannot use staff login
- inactive staff cannot sign in
- temporary password forces a change
- admin-only pages/settings are hidden or blocked for staff
- sign-out clears the session
- idle logout works

- [ ] **Step 4: Record evidence and findings**

Mark `S1` and the staff part of `X8`.

- [ ] **Step 5: Fix blocking findings with TDD**

- [ ] **Step 6: Commit and push**

```bash
git add app/staff app/services tests docs/audits/final-audit-register.md
git commit -m "audit: verify staff authentication and first run"
git push origin final-audit
```

**Exit criteria:** staff/admin boundaries and first-run behavior are evidenced.

---

## Task 10: Walk-In Rental (S2)

**Files:**
- Read: `app/staff/pages/new_rental.py`, `app/staff/rental_wizard.py`, `app/staff/booking_panels.py`, `app/staff/customer_panels.py`
- Read: `counter_service.quote`, `booking_service.complete_counter_rental`, `payment_service.record_payment`, `receipt_service`
- Test: `tests/test_counter_rental.py`, `tests/test_counter_service.py`, `tests/test_booking_service.py`, `tests/test_receipt_service.py`

**Interfaces:**
- Consumes: staff customer lookup/create, vehicle/date selection, quote, payment, deposit, confirm.
- Produces: verified walk-in rental from start to confirmed booking and receipt.

- [ ] **Step 1: Run the counter tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_counter_rental.py \
  tests/test_counter_service.py \
  tests/test_booking_service.py \
  tests/test_receipt_service.py -q
```

- [ ] **Step 2: Walk a walk-in rental**

Exercise:
- existing customer lookup by name/phone/licence
- new customer creation
- flagged customer override note
- vehicle/date selection
- quote and scheduling fee
- partial rental payment
- deposit collection
- confirmation
- receipt generation

Verify `BOOKING.channel="walk_in"`, `created_by`, payment rows, deposit row, vehicle status, and receipt contents.

- [ ] **Step 3: Verify counter invariants**

- scheduling fee cannot exceed rental total
- GCash reference required when configured
- deposit is recorded as `kind="deposit"`, not revenue
- booking confirms only under the intended payment state
- receipt failure is a warning, not a booking rollback

- [ ] **Step 4: Record evidence and findings**

Mark `S2`, `X2`, and the counter part of `X5`.

- [ ] **Step 5: Fix blocking findings with TDD**

- [ ] **Step 6: Commit and push**

```bash
git add app/staff app/services tests docs/audits/final-audit-register.md
git commit -m "audit: verify walk-in rental flow"
git push origin final-audit
```

**Exit criteria:** a walk-in rental can be created, paid, confirmed, and receipted with correct DB state.

---

## Task 11: Online Booking Handoff and Staff Bookings (S3, X1)

**Files:**
- Read: `app/staff/pages/bookings.py`, `app/staff/booking_panels.py`
- Read: `booking_service.bookings_for`, `confirm_booking`, `cancel_booking`, `customer_service`, `notification_service`
- Test: `tests/test_customer_flow_audit.py`, `tests/test_staff_pages.py`, `tests/test_booking_service.py`

**Interfaces:**
- Consumes: an online booking created in Task 7 and the staff Bookings page.
- Produces: verified customer-to-staff visibility and staff confirmation/cancellation.

- [ ] **Step 1: Run the handoff tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_customer_flow_audit.py \
  tests/test_staff_pages.py \
  tests/test_booking_service.py -q
```

- [ ] **Step 2: Create an online booking and open staff Bookings**

Verify the booking appears with:
- customer identity
- vehicle
- pickup/return dates and times
- pickup/return branches
- online channel
- reference code
- pending status
- correct price breakdown

- [ ] **Step 3: Confirm and cancel from staff**

Verify staff can confirm a pending online booking, cancel with a reason, and that the customer app sees the new status. Verify cancellation releases the vehicle and handles any payment/refund state.

- [ ] **Step 4: Record evidence and findings**

Mark `S3` and `X1`. Record any missing customer data or staff-side assumptions.

- [ ] **Step 5: Fix blocking findings with TDD**

- [ ] **Step 6: Commit and push**

```bash
git add app/staff app/services tests docs/audits/final-audit-register.md
git commit -m "audit: verify online booking staff handoff"
git push origin final-audit
```

**Exit criteria:** an online booking is fully visible and actionable in staff Bookings, and the customer sees the result.

---

## Task 12: Pickup and Check-In (S4)

**Files:**
- Read: `app/staff/pages/bookings.py`, `booking_service.check_in`, `fleet_service.mark_rented`, `payment_service`, `inspection_report`
- Test: `tests/test_booking_service.py`, `tests/test_rental_life_cycle.py`, `tests/test_staff_pages.py`

**Interfaces:**
- Consumes: a pending/confirmed booking and an eligible customer/vehicle.
- Produces: verified check-in, inspection, vehicle status, and payment/deposit state.

- [ ] **Step 1: Run the check-in tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_booking_service.py \
  tests/test_rental_life_cycle.py \
  tests/test_staff_pages.py -q
```

- [ ] **Step 2: Walk check-in**

Verify:
- only pending/confirmed bookings can check in
- licence and eligibility are checked
- mileage and fuel are recorded
- pre-rental inspection is created
- vehicle becomes `rented`
- rental/deposit payments are recorded correctly
- booking becomes `ongoing`
- customer sees the status change

- [ ] **Step 3: Verify invalid check-ins**

- wrong status
- expired/missing licence
- inactive/flagged customer without override
- vehicle in maintenance/retired
- mileage below recorded value

- [ ] **Step 4: Record evidence and findings**

Mark `S4`, `X2`, and the pickup part of `X5`.

- [ ] **Step 5: Fix blocking findings with TDD**

- [ ] **Step 6: Commit and push**

```bash
git add app/staff app/services tests docs/audits/final-audit-register.md
git commit -m "audit: verify pickup and check-in"
git push origin final-audit
```

**Exit criteria:** check-in creates the correct inspection/payment/vehicle/booking state and is visible to the customer.

---

## Task 13: Return, Checkout, Deposit, and Receipt (S5, X2–X4)

**Files:**
- Read: `booking_service.checkout_quote`, `check_out`, `apply_penalty`, `payment_service.settle_deposit`, `booking_balance`, `receipt_service`
- Read: `app/staff/pages/bookings.py`, `app/staff/pages/payments.py`
- Test: `tests/test_booking_service.py`, `tests/test_receipt_service.py`, `tests/test_migrate_deposit_applied.py`, `tests/test_customer_payments.py`

**Interfaces:**
- Consumes: an ongoing booking with rental/deposit payments and possible penalties.
- Produces: verified checkout quote, deposit application/return, exact settlement, receipt, and customer/report visibility.

- [ ] **Step 1: Run the checkout tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_booking_service.py \
  tests/test_receipt_service.py \
  tests/test_migrate_deposit_applied.py \
  tests/test_customer_payments.py -q
```

- [ ] **Step 2: Walk a clean return**

Verify:
- `checkout_quote` computes late fee and damage charge
- `check_out` requires an exact settlement when a balance remains
- deposit is applied to penalties first
- remaining deposit is returned/refunded
- held deposit is not counted as revenue
- `deposit_applied` is not counted as cash
- booking becomes `completed`
- vehicle becomes `available`
- post-rental inspection is created
- receipt generates and opens
- customer sees the completed booking and payment rows
- reports reflect rental revenue, penalties, and deposit liability correctly

- [ ] **Step 3: Walk shortfall and failure paths**

- balance due but no settlement → blocked
- settlement not exactly equal → blocked
- receipt generation failure → visible warning, booking remains committed
- damage/late penalty → correct rows and totals
- deposit insufficient for penalties → outstanding balance remains

- [ ] **Step 4: Record evidence and findings**

Mark `S5`, `X2`, `X3`, `X4`, and the return part of `X5`.

- [ ] **Step 5: Fix blocking findings with TDD**

- [ ] **Step 6: Commit and push**

```bash
git add app/staff app/services tests docs/audits/final-audit-register.md
git commit -m "audit: verify checkout and deposit accounting"
git push origin final-audit
```

**Exit criteria:** checkout, deposit, receipt, customer history, and reports agree; no held deposit is treated as revenue.

---

## Task 14: Cancellation and Extension (S6)

**Files:**
- Read: `booking_service.cancel_booking`, `extend_booking`, `extension_quote`, `payment_service.refund_payment`
- Read: `app/staff/pages/bookings.py`, `app/ui/bookings.py`
- Test: `tests/test_booking_service.py`, `tests/test_staff_pages.py`, `tests/test_customer_flow_audit.py`

**Interfaces:**
- Consumes: pending/confirmed/ongoing bookings and payment state.
- Produces: verified cancellation, refund, extension, availability, and customer/staff visibility.

- [ ] **Step 1: Run the cancellation/extension tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_booking_service.py \
  tests/test_staff_pages.py \
  tests/test_customer_flow_audit.py -q
```

- [ ] **Step 2: Walk cancellation**

Verify:
- only pending/confirmed bookings cancel
- reason is required/stored
- vehicle is released
- paid rental/deposit is refunded according to policy
- customer and staff see `cancelled`
- reports exclude cancelled bookings from revenue/usage

- [ ] **Step 3: Walk extension**

Verify:
- pending/confirmed/ongoing bookings can extend
- extension quote uses the same pricing rules
- new return window does not conflict with another booking
- payment/deposit state remains coherent
- customer and staff see the new dates/total

- [ ] **Step 4: Record evidence and findings**

Mark `S6` and the extension/cancellation parts of `X2` and `X5`.

- [ ] **Step 5: Fix blocking findings with TDD**

- [ ] **Step 6: Commit and push**

```bash
git add app/staff app/ui app/services tests docs/audits/final-audit-register.md
git commit -m "audit: verify cancellation and extension"
git push origin final-audit
```

**Exit criteria:** cancellation/refund and extension flows are consistent across customer, staff, payments, and reports.

---

## Task 15: Fleet Lifecycle and Rates (S7)

**Files:**
- Read: `app/staff/pages/fleet.py`, `app/staff/fleet_panels.py`, `app/staff/vehicle_cards.py`, `app/staff/vehicle_details.py`
- Read: `fleet_service`, `vehicle_service`, `vehicle_photos`, `image_cache`, `media_service`
- Test: `tests/test_fleet_management.py`, `tests/test_staff_pages.py`, `tests/test_security_hardening.py`

**Interfaces:**
- Consumes: `fleet_service.add_vehicle`, `update_vehicle`, `retire_vehicle`, `unretire_vehicle`, `set_daily_rate`, `bulk_set_rate`, `bulk_set_status`, `vehicle_stats`.
- Produces: verified fleet CRUD, status, rate history, photos, and stats.

- [ ] **Step 1: Run the fleet tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_fleet_management.py \
  tests/test_staff_pages.py \
  tests/test_security_hardening.py -q
```

- [ ] **Step 2: Walk fleet CRUD**

Verify:
- add vehicle with category, plate, rate, specs, paperwork
- duplicate plate rejected
- edit identity/specs/rates
- retire/unretire with booking history preserved
- status transitions are legal
- bulk rate/status changes preview and apply correctly
- rate history rows are written

- [ ] **Step 3: Verify photos and stats**

Verify:
- image cache keys are safe and variant-aware
- old/new cache behavior is coherent
- missing CarImages media degrades gracefully
- vehicle stats (rented days, maintenance days, revenue) match report/service values

- [ ] **Step 4: Record evidence and findings**

Mark `S7`, `X6`, and the fleet part of `X4`.

- [ ] **Step 5: Fix blocking findings with TDD**

- [ ] **Step 6: Commit and push**

```bash
git add app/staff app/services tests docs/audits/final-audit-register.md
git commit -m "audit: verify fleet lifecycle"
git push origin final-audit
```

**Exit criteria:** fleet CRUD, rates, retirement, photos, and stats are consistent and history-safe.

---

## Task 16: Maintenance Lifecycle (S8)

**Files:**
- Read: `app/staff/pages/maintenance.py`, `app/staff/maintenance_panels.py`
- Read: `maintenance_service`, `fleet_service`
- Test: `tests/test_maintenance_service.py`, `tests/test_migrate_maintenance_lifecycle.py`, `tests/test_staff_pages.py`

**Interfaces:**
- Consumes: `maintenance_service.start_maintenance`, `complete_maintenance`, `cancel_maintenance`, `sync_vehicle_status`, `due_soon`, `spend_summary`.
- Produces: verified maintenance lifecycle, vehicle blocking, costs, and reports.

- [ ] **Step 1: Run the maintenance tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_maintenance_service.py \
  tests/test_migrate_maintenance_lifecycle.py \
  tests/test_staff_pages.py -q
```

- [ ] **Step 2: Walk maintenance**

Verify:
- schedule maintenance with type, dates, estimate, shop, odometer
- overlapping/blocking maintenance is rejected or handled
- start/complete/cancel transitions
- vehicle status syncs to `maintenance`/`available`
- completing stamps next due date/odometer
- cancellation keeps history and reason
- costs and due-soon queries match reports

- [ ] **Step 3: Verify booking conflicts**

A vehicle in blocking maintenance must not be bookable for those dates. A completed/cancelled record must release it.

- [ ] **Step 4: Record evidence and findings**

Mark `S8` and the maintenance part of `X4`.

- [ ] **Step 5: Fix blocking findings with TDD**

- [ ] **Step 6: Commit and push**

```bash
git add app/staff app/services tests docs/audits/final-audit-register.md
git commit -m "audit: verify maintenance lifecycle"
git push origin final-audit
```

**Exit criteria:** maintenance state, vehicle blocking, costs, and reports are consistent.

---

## Task 17: Customer Management and Documents (S9, X3)

**Files:**
- Read: `app/staff/pages/customers.py`, `app/staff/customer_panels.py`
- Read: `customer_service`, `profile_service`, `location_service`, `auth_service`
- Test: `tests/test_customer_management.py`, `tests/test_signup_location.py`, `tests/test_customer_profile.py`, `tests/test_staff_pages.py`

**Interfaces:**
- Consumes: customer search/create/edit, flags, notes, licence docs, balances, open bookings, eligibility.
- Produces: verified staff customer view and customer-data handoff.

- [ ] **Step 1: Run the customer-management tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_customer_management.py \
  tests/test_signup_location.py \
  tests/test_customer_profile.py \
  tests/test_staff_pages.py -q
```

- [ ] **Step 2: Walk customer management**

Verify:
- search by name/phone/email/licence
- create customer with required fields
- duplicate email/licence/phone warnings
- edit every staff-maintained field
- deactivate/reactivate with history preserved
- flag/clear flag with reason and audit fields
- notes add/list
- licence front/back storage and path safety
- balance/open-booking aggregates match payments/bookings
- online customer data appears correctly to staff

- [ ] **Step 3: Verify customer/staff data agreement**

For one online customer, compare:
- profile fields
- licence/guarantor/PSGC data
- booking count
- open bookings
- outstanding balance

between customer profile, staff Customers, and services.

- [ ] **Step 4: Record evidence and findings**

Mark `S9`, `X3`, and the customer-data part of `X5`/`X8`.

- [ ] **Step 5: Fix blocking findings with TDD**

- [ ] **Step 6: Commit and push**

```bash
git add app/staff app/services tests docs/audits/final-audit-register.md
git commit -m "audit: verify customer management"
git push origin final-audit
```

**Exit criteria:** staff and customer views agree on customer data, balances, and documents.

---

## Task 18: Payments, Refunds, Deposits, and Outstanding (S10)

**Files:**
- Read: `app/staff/pages/payments.py`
- Read: `payment_service`, `receipt_service`, `report_service`
- Test: `tests/test_customer_payments.py`, `tests/test_counter_rental.py`, `tests/test_booking_service.py`, `tests/test_receipt_service.py`, `tests/test_staff_pages.py`

**Interfaces:**
- Consumes: `record_payment`, `refund_payment`, `settle_deposit`, `booking_balance`, `takings`, `outstanding_total`, `payments_for_user`.
- Produces: verified payment ledger, refunds, deposits, takings, and outstanding balances.

- [ ] **Step 1: Run the payment tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_customer_payments.py \
  tests/test_counter_rental.py \
  tests/test_booking_service.py \
  tests/test_receipt_service.py \
  tests/test_staff_pages.py -q
```

- [ ] **Step 2: Walk the staff ledger**

Verify:
- search runs in SQL before the row cap
- payment rows show rental/deposit/deposit_applied correctly
- recording rental, deposit, pending, failed, and refunded rows
- refunds do not duplicate revenue
- takings count only rental-kind paid money
- outstanding uses the same balance rules as booking checkout
- customer payment history matches staff ledger for the same booking

- [ ] **Step 3: Verify money invariants**

- `Decimal` end-to-end
- no float writes
- held deposit is a liability
- applied deposit is a ledger adjustment, not cash
- refund cannot exceed paid amount
- checkout shortfall cannot be hidden by a deposit row

- [ ] **Step 4: Record evidence and findings**

Mark `S10`, `X2`, and the payments part of `X3`/`X4`.

- [ ] **Step 5: Fix blocking findings with TDD**

- [ ] **Step 6: Commit and push**

```bash
git add app/staff app/services tests docs/audits/final-audit-register.md
git commit -m "audit: verify payments and deposits"
git push origin final-audit
```

**Exit criteria:** staff ledger, customer history, checkout, takings, and outstanding agree.

---

## Task 19: Reports, Dashboard, and Exports (S11, X4)

**Files:**
- Read: `app/staff/pages/reports.py`, `app/staff/pages/report_panels.py`, `app/staff/pages/dashboard.py`
- Read: `report_service`, `dashboard_service`, `export_service`
- Test: `tests/test_report_service.py`, `tests/test_staff_dashboard.py`, `tests/test_staff_pages.py`

**Interfaces:**
- Consumes: completed/cancelled bookings, payments, deposits, penalties, fleet, maintenance, customers, staff.
- Produces: verified report numbers, dashboard figures, query behavior, and exports.

- [ ] **Step 1: Run the reporting tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_report_service.py \
  tests/test_staff_dashboard.py \
  tests/test_staff_pages.py -q
```

- [ ] **Step 2: Verify every report panel**

For `revenue`, `bookings`, `utilization`, `performance`, `maintenance`, `receivables`, `customers`, and `staff`:
- summary is non-empty
- table rows match service values
- date-range boundaries are correct
- cancelled bookings are excluded where required
- deposit rows do not inflate revenue
- customer-created online bookings are included
- export CSV/PDF row counts and totals match the panel

- [ ] **Step 3: Verify dashboard and cache**

- dashboard collect uses at most 3 queries
- dashboard remains off the GUI thread
- utilization first load is bounded and repeat load uses the 60-second cache
- exports always read fresh data, not the utilization cache
- query counts do not regress

- [ ] **Step 4: Record evidence and findings**

Mark `S11`, `X4`, and the reporting part of `X2`.

- [ ] **Step 5: Fix blocking findings with TDD**

- [ ] **Step 6: Commit and push**

```bash
git add app/staff app/services tests docs/audits/final-audit-register.md
git commit -m "audit: verify reports and dashboard"
git push origin final-audit
```

**Exit criteria:** all reports/exports agree with source services and money rules.

---

## Task 20: Settings, Staff Accounts, Backup, and Cache (S12)

**Files:**
- Read: `app/staff/pages/settings.py`, `app/staff/settings_panels.py`
- Read: `settings_service`, `staff_service`, `backup_service`, `scripts/backup_live_db.py`, `scripts/secure_local_files.py`
- Test: `tests/test_staff_settings.py`, `tests/test_backup_live_db.py`, `tests/test_migrate_app_settings.py`

**Interfaces:**
- Consumes: shared settings, staff account management, backup snapshot/sanitization, cache controls.
- Produces: verified admin policy, staff account, backup, and cache behavior.

- [ ] **Step 1: Run the settings/backup tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_staff_settings.py \
  tests/test_backup_live_db.py \
  tests/test_migrate_app_settings.py -q
```

- [ ] **Step 2: Walk settings**

Verify:
- admin can edit shared policy; staff sees read-only
- money/int/decimal/bool/choice validation
- defaults apply when no row exists
- pricing changes affect subsequent quotes
- receipt settings affect receipt output
- first-run/admin/staff account rules
- temporary password/reset/activate/deactivate
- cache clear/stats and API status
- backup only on live SQLite; live MySQL is blocked with instructions
- backup sanitization removes passwords/tokens/PII as designed

- [ ] **Step 3: Record evidence and findings**

Mark `S12` and the settings part of `X8`.

- [ ] **Step 4: Fix blocking findings with TDD**

- [ ] **Step 5: Commit and push**

```bash
git add app/staff app/services scripts tests docs/audits/final-audit-register.md
git commit -m "audit: verify settings and backup"
git push origin final-audit
```

**Exit criteria:** admin/staff policy boundaries, settings validation, backup, and cache are verified.

---

## Task 21: Security, Authorization, and Data Integrity (X8)

**Files:**
- Read: all auth/customer/profile/payment/booking/media/backup code paths
- Test: `tests/test_security_hardening.py`, `tests/test_customer_flow_audit.py`, `tests/test_staff_login.py`, `tests/test_staff_pages.py`, `tests/test_backup_live_db.py`

**Interfaces:**
- Consumes: the role matrix, customer/staff data access, branch scope, PII, password flows, image cache.
- Produces: verified authorization and data-integrity boundaries.

- [ ] **Step 1: Build the permission matrix**

For every page/service action, record: customer, staff, admin, inactive, flagged. Include branch scope and ownership.

- [ ] **Step 2: Test IDOR and cross-customer isolation**

Create two customers and two staff accounts. Verify:
- customer cannot read another customer's bookings/payments/notifications/profile
- staff cannot access admin-only settings unless admin
- customer cannot reach staff pages or staff services
- inactive/flagged accounts are handled per policy
- direct object IDs are checked against ownership/role

- [ ] **Step 3: Test PII, secrets, and path safety**

Verify:
- passwords are hashed, never logged/displayed
- temporary passwords are not stored in plaintext
- licence docs stay inside their directory
- image cache keys reject traversal
- backup snapshots are sanitized
- error messages do not leak credentials or internals

- [ ] **Step 4: Test state/data integrity**

Verify illegal booking/payment/vehicle/maintenance transitions are rejected. Verify no UI bypasses a service or writes directly to the DB. Verify all money is `Decimal`.

- [ ] **Step 5: Record evidence and findings**

Mark `X8` and update `X3`/`X5`/`X6` where relevant. Assign severity.

- [ ] **Step 6: Fix blocking findings with TDD**

- [ ] **Step 7: Commit and push**

```bash
git add app tests docs/audits/final-audit-register.md
git commit -m "audit: verify security and data boundaries"
git push origin final-audit
```

**Exit criteria:** role, ownership, branch, PII, and state-integrity boundaries are evidenced.

---

## Task 22: Media, Cache, and External API Resilience (X6)

**Files:**
- Read: `app/services/carimages_client.py`, `media_service.py`, `vehicle_photos.py`, `image_cache.py`, `api_cache.py`
- Read: `app/staff/pages/settings.py` cache/API sections
- Test: `tests/test_media_warning.py`, `tests/test_security_hardening.py`, `scripts/test_image_cache.py`, `scripts/test_media_service.py`

**Interfaces:**
- Consumes: CarImages availability, cached media, image variants, 3D models, cache clear/stats.
- Produces: verified graceful degradation and cache safety.

- [ ] **Step 1: Run the media/security tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_media_warning.py \
  tests/test_security_hardening.py -q
```

- [ ] **Step 2: Walk media failure modes**

With CarImages unavailable:
- customer vehicle details and staff fleet cards must not hang/crash
- cached images/3D models are used when fresh
- stale/missing media shows a placeholder
- errors are surfaced without blocking booking/payment
- API status/cache clear controls work

- [ ] **Step 3: Walk cache key compatibility**

Verify:
- old cache keys and new safe/variant keys behave coherently
- variants do not collide
- `_paths` rejects traversal
- clear/stats count both image and API caches correctly
- no customer/staff data is written into media metadata

- [ ] **Step 4: Record evidence and findings**

Mark `X6`; classify external failures as Environmental.

- [ ] **Step 5: Fix blocking findings with TDD**

- [ ] **Step 6: Commit and push**

```bash
git add app/services app/staff tests docs/audits/final-audit-register.md
git commit -m "audit: verify media and cache resilience"
git push origin final-audit
```

**Exit criteria:** media failures degrade gracefully and cache paths are safe.

---

## Task 23: Performance, Query Counts, and Pagination (X4)

**Files:**
- Read: `app/staff/query_metrics.py`, staff pages, `dashboard_service`, `report_service`, `customer_service`, `fleet_service`, `payment_service`
- Test: `tests/test_query_metrics.py`, `tests/test_staff_pages.py`, `tests/test_staff_dashboard.py`, `tests/test_report_service.py`

**Interfaces:**
- Consumes: the optimized staff pages and report services.
- Produces: verified query budgets, caps, pagination, background loading, and cache behavior.

- [ ] **Step 1: Run the performance tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_query_metrics.py \
  tests/test_staff_pages.py \
  tests/test_staff_dashboard.py \
  tests/test_report_service.py -q
```

- [ ] **Step 2: Verify query budgets**

Use `count_queries` around:
- Bookings list
- Fleet list
- Payments ledger/search
- Outstanding aggregate
- Customers page
- Dashboard collect
- Inspections
- Maintenance
- Reports

Expected: existing budgets hold; dashboard ≤3 queries; customer page ≤2 queries; payment search runs before the cap.

- [ ] **Step 3: Verify pagination/caps**

- visible row limits are shown
- caps do not hide search matches
- report exports still include all required rows
- utilization cache does not affect exports
- background refresh does not race with navigation

- [ ] **Step 4: Record evidence and findings**

Mark the performance part of `X4`; record any regressions as Medium unless they block a flow.

- [ ] **Step 5: Fix blocking findings with TDD**

- [ ] **Step 6: Commit and push**

```bash
git add app/staff app/services tests docs/audits/final-audit-register.md
git commit -m "audit: verify performance guardrails"
git push origin final-audit
```

**Exit criteria:** query budgets, caps, pagination, background loading, and caches are verified.

---

## Task 24: Triage and Remediate Findings

**Files:**
- Modify: whichever files the findings require
- Test: one regression test per fixed defect
- Modify: `docs/audits/final-audit-register.md`, `docs/audits/final-audit-report.md`

**Interfaces:**
- Consumes: all `Finding` rows from Tasks 3–23.
- Produces: fixed Critical/High findings, regression tests, and a prioritized backlog for Medium/Low.

- [ ] **Step 1: Triage the register**

Sort findings by severity. For each Critical/High, create a remediation row `R-###` with:
- root cause
- affected flows
- reproduction
- expected behavior
- files to change
- regression test name

- [ ] **Step 2: Fix one finding at a time with TDD**

For each Critical/High:
1. Write the failing regression test.
2. Run it and verify it fails for the recorded reason.
3. Implement the minimal fix.
4. Run the targeted tests.
5. Run the full suite.
6. Commit and push.

- [ ] **Step 3: Do not batch unrelated fixes**

Keep each commit scoped to one finding or one tightly related group. Update the register after each fix.

- [ ] **Step 4: Decide Medium/Low disposition**

For each Medium/Low, record one of:
- fixed now
- deferred with owner and reason
- waived with explicit user approval

- [ ] **Step 5: Re-run the affected flow tasks**

After a fix, re-run the owning flow task's tests and evidence steps. Mark the finding `Fixed` only when the regression test and flow evidence pass.

- [ ] **Step 6: Commit and push**

```bash
git add app tests docs/audits/final-audit-register.md docs/audits/final-audit-report.md
git commit -m "fix: close final audit findings"
git push origin final-audit
```

**Exit criteria:** no open Critical/High findings; every fixed finding has a regression test; Medium/Low have a recorded disposition.

---

## Task 25: Final Regression and Sign-Off

**Files:**
- Modify: `docs/audits/final-audit-report.md`
- Modify: `docs/audits/final-audit-register.md`
- No production changes unless a final regression failure requires a scoped fix.

**Interfaces:**
- Consumes: all completed audit tasks and remediations.
- Produces: a green final branch and an audit sign-off report.

- [ ] **Step 1: Run the full suite**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q > /tmp/opencode/final-audit-final.log 2>&1
echo $?
```

Expected: exit 0, no failures. Record skip count and log path.

- [ ] **Step 2: Run migration verification**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_migrate_*.py -q
```

Run the documented migration order on a fresh SQLite database and a copied legacy database. Verify both are idempotent.

- [ ] **Step 3: Run the golden path and flow smoke tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_final_audit_flow.py -q
```

Re-run the focused tests for every flow task with open evidence gaps.

- [ ] **Step 4: Manual UI smoke test**

On a temporary SQLite database:
- customer signup/login → browse → online booking
- staff login → see booking → confirm → check in
- staff checkout → deposit/settlement → receipt
- customer sees completed booking/payment
- staff dashboard/reports reflect the booking

Record screenshots or log evidence in the register.

- [ ] **Step 5: Verify constraints**

- no live MySQL writes
- GUI tests ran offscreen
- no conflict markers
- `git diff --check` clean
- working tree clean
- no uncommitted audit artifacts

- [ ] **Step 6: Write the final report**

Include:
- branch and commit
- scope and flows covered
- environment
- test results
- findings by severity
- fixes and regression tests
- deferred/waived items
- known environmental limitations
- explicit sign-off decision

- [ ] **Step 7: Commit and push**

```bash
git add docs/audits/final-audit-report.md docs/audits/final-audit-register.md
git commit -m "docs: complete final audit sign-off"
git push origin final-audit
```

- [ ] **Step 8: Stop for approval**

Do **not** merge `final-audit` into `main`. Present the report and request explicit approval. Only after approval should a separate merge task be created.

**Exit criteria:** full suite green, all Critical/High findings closed, report complete, branch pushed, and approval requested.

---

## Resume Checklist for Another Device

1. `git fetch origin --prune`
2. `git switch final-audit`
3. `git pull --ff-only`
4. Open `docs/plans/2026-10-25-final-audit.md`
5. Open `docs/audits/final-audit-register.md`
6. Find the first task with unchecked steps or a `Finding` status.
7. Read that task's files/interfaces and run its test command.
8. Continue from the first unchecked step; do not restart completed tasks.
9. Commit and push after each task.
10. Do not merge `main` until Task 25 is complete and approved.

## Execution Recommendation

Use **native execution** (one implementer in this session) rather than parallel subagents: the flows share state, and the audit register must be updated sequentially. If subagent-driven execution is used, keep one task and one reviewer at a time, and never let two tasks modify the same service file concurrently.

