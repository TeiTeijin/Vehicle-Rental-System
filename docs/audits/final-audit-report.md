# Final Audit Report

- **Branch:** `final-audit`
- **Baseline commit:** `b77c101685a766426571fccd41de27f28e597aeb`
- **Audit plan:** `docs/plans/2026-10-25-final-audit.md`
- **Register:** `docs/audits/final-audit-register.md`
- **Report status:** Complete — awaiting user approval

## Scope

End-to-end audit of the merged customer + staff Vehicle Rental System on the `final-audit` branch: customer account and booking flows (C1–C8), staff operational flows (S1–S12), and cross-cutting handoffs (X1–X9). The audit covers behavior, money/deposit accounting, migrations, security/PII boundaries, media resilience, and performance guardrails.

Out of scope: merging to `main`, live MySQL writes, external CarImages availability (classified as environmental when unavailable).

## Branch and commit

| Item | Value |
|---|---|
| Branch | `final-audit` |
| Baseline commit | `b77c101685a766426571fccd41de27f28e597aeb` |
| Baseline date | 2026-10-25 |
| Final code commit | `613a6d3` |
| Sign-off report | This commit (see `git log -1`) |

## Environment

| Item | Value |
|---|---|
| Audit device | Windows 11 |
| Audit device Python | 3.13.5 (`.venv`) |
| Verification host | Linux (this worktree) |
| Verification Python | 3.14.8 (`.venv`) |
| PySide6 | 6.11.2 |
| SQLAlchemy | 2.0.54 |
| pytest | 9.1.1 |
| Test database | in-memory SQLite (`tests/conftest.py`) |
| GUI mode | `QT_QPA_PLATFORM=offscreen` |

## Baseline failures

- Collected: **1225 tests** across 46 files.
- Full-suite baseline: **1223 passed, 2 failed, 0 skipped** (exit 1).
- Failures, both expected migration-order issues in `tests/test_migrate_staff_schema.py`:
  - `test_migrated_schema_satisfies_every_model_column` — `NoSuchTableError: BRANCH` (fixture runs only `migrate_staff_schema`).
  - `test_the_staff_models_work_against_the_migrated_database` — `BOOKING` has no `pickup_dt` (customer booking migration not run).
- Baseline log: `.superpowers/sdd/2026-10-25-final-audit/baseline.log` (local scratch).

## Findings summary

| Severity | Open | Fixed | Deferred | Waived |
|---|---|---|---|---|
| Critical | 0 | 1 | 0 | 0 |
| High | 0 | 10 | 0 | 0 |
| Medium | 0 | 10 | 0 | 0 |
| Low | 0 | 10 | 1 | 0 |
| Environmental | 0 | 0 | 0 | 0 |

Task 3 fixed two High migration defects: MySQL `ENUM` DDL was emitted on SQLite (F-001), and the documented order did not create the PSGC reference tables (F-002). A Low legacy-SQLite nullability limitation (F-003) is deferred with a documented disposition. Task 24 fixed the two High pricing defects (F-005/F-006) and the Medium customer-history label defect (F-007), and fixed the Low Fleet-stats ambiguity (F-013). Task 12 found and fixed a High pickup-gate defect: check-in did not re-check customer eligibility or refuse retired vehicles (F-010). Task 14 found and fixed a High extension defect: online return times were not moved and same-day time conflicts were missed (F-012). Task 16 found and fixed a Medium open-ended-workshop crash (F-015) and a Low odometer-integrity gap (F-014). Task 17 found and fixed a High customer-data defect: the staff edit form invented a 2000-01-01 birthdate for customers with none, bypassing the missing-DOB eligibility block (F-016). Task 18 found and fixed a Medium deposit-ledger defect (refunds could flip deposit/applied rows) and a Low ledger-label defect (F-017, F-018). Task 19 found and fixed a High outstanding-report defect (held deposits counted as paid; overpayments netted across bookings), plus Low date-basis and dashboard-label defects (F-019, F-020, F-021).

Detailed findings and evidence live in `docs/audits/final-audit-register.md`.

## Fixes and regression tests

- **F-001 / F-002 (Task 3):** `migrate_customer_booking` now emits SQLite-compatible DDL for the `payment_method` enum and creates the PSGC reference tables idempotently. Regression test: `tests/test_migrate_staff_schema.py::TestDocumentedMigrationOrder::test_documented_migration_order_matches_models`. All 119 migration tests pass; fresh and legacy orders are repeatable.
- **F-005 / F-006 (Task 24, High):** online quotes now apply the live long-term discount and motorcycle multiplier before adding fees/VAT, matching counter pricing. Regression tests: `test_online_booking_applies_long_term_discount`, `test_online_booking_applies_motorcycle_multiplier`.
- **F-007 (Task 24, Medium):** customer history labels `deposit_applied` as an applied deposit and states that no cash changed hands. Regression test: `test_customer_payment_history_labels_applied_deposit`.
- **F-013 (Task 24, Low):** the Fleet card now labels times rented, revenue, and maintenance as all-time, while utilization/rented days remain windowed. Regression test: `tests/test_staff_pages.py::TestFleetPage::test_details_panel_builds_sections`.
- **F-003 (Task 3, Low):** deferred/documented: legacy SQLite cannot relax `USERS.license_number` in place; fresh SQLite and the MySQL path are correct, and the migration prints instructions. Owner: database maintainer.
- **F-004 (Task 4, Medium):** receipts now print the online booking's customer-facing reference code. Regression test: `tests/test_final_audit_flow.py::test_online_booking_reaches_staff_and_completes`. Receipt/customer-payment suites pass (30 tests).
- **F-008 / F-009 (Task 11, Medium):** staff can search online bookings by the customer-facing reference, and the details panel shows the reference, pickup/return times, and branch names. Regression tests: `test_search_matches_the_customer_facing_reference`, `test_details_panel_shows_reference_branches_and_times`.
- **F-010 (Task 12, High):** check-in now re-checks active status, licence number and expiry (through the return date), flag overrides, and refuses retired or workshop vehicles. Regression tests: the six `TestCheckIn::test_refuses_*` / `test_a_flagged_customer_needs_an_override_note` cases in `tests/test_booking_service.py`.
- **F-011 (Task 13, Low):** deposit refunds keep the original tender instead of always recording cash. Regression test: `test_deposit_refund_keeps_the_original_tender`.
- **F-012 (Task 14, High):** extensions now move an online booking's return time and check the added window as datetimes, so a same-day booking cannot be double-booked. Regression tests: `test_extend_moves_the_online_return_time`, `test_extend_detects_a_same_day_time_clash`.
- **F-014 (Task 16, Low):** maintenance now refuses a current/future odometer below the vehicle's mileage and a completion reading below the job's start reading; historical backfill is still allowed. Regression tests: the four `TestOdometer` cases in `tests/test_maintenance_service.py`.
- **F-015 (Task 16, Medium):** open-ended workshop jobs now produce a clean conflict message ("from <date>") instead of a `TypeError` when booking, quoting, or extending. Regression tests: `test_booking_reports_the_open_ended_window`, `test_extension_reports_the_open_ended_window`.
- **F-016 (Task 17, High):** the staff customer form now represents a missing birthdate as "Not on file" and submits `None`, so editing another field cannot invent 2000-01-01 or bypass the missing-DOB eligibility block. Regression tests: `test_edit_form_keeps_a_missing_birthdate_empty`, `test_new_customer_form_does_not_invent_a_birthdate`.
- **F-017 (Task 18, Medium):** `refund_payment` now refuses `deposit` and `deposit_applied` rows, so a refund cannot silently mark a held deposit returned or remove a penalty credit. Regression tests: `test_refunding_a_deposit_row_is_refused`, `test_refunding_an_applied_deposit_is_refused`.
- **F-018 (Task 18, Low):** the staff ledger labels `deposit_applied` as "Deposit applied", shows "Ledger adjustment" instead of "Not cleared", and offers it in the Kind filter. Regression test: `test_a_deposit_applied_row_reads_as_a_ledger_adjustment`.
- **F-019 (Task 19, High):** `outstanding_total` now sums each booking's rental + penalties − (rental + deposit_applied) and clamps each at zero, so held deposits no longer reduce debt and one booking's overpayment cannot hide another's. Regression tests: `test_a_held_deposit_is_not_a_payment_against_the_bill`, `test_an_overpayment_cannot_hide_another_bookings_debt`.
- **F-020 (Task 19, Low):** report collections and deposits now filter by `paid_at`, matching the daily rows and every other revenue figure. Regression tests: `test_a_backdated_payment_counts_on_the_day_it_was_paid`, `test_a_backdated_deposit_counts_on_the_day_it_was_paid`.
- **F-021 (Task 19, Low):** the dashboard's Recent Transactions list now shows only rental and deposit cash rows, not `deposit_applied` ledger adjustments. Regression test: `test_a_deposit_application_is_not_a_cash_transaction`.
- **F-022 / F-023 (Task 21, High/Critical):** Windows backup snapshots now apply an owner-only ACL, and counter-created customers get unique random temporary passwords stored only as hashes. Regression tests: `test_a_snapshot_is_owner_only`, `test_the_settings_backup_helper_sanitizes_only_the_copy`, `test_counter_created_customers_get_unique_random_temporary_passwords`, `test_adding_a_customer_shows_the_temporary_password`.
- **F-024–F-029 (Task 22, High/Medium/Low):** media credentials are sent only to allowlisted API hosts; API/GLB cache keys are digest-disambiguated; a failed refresh preserves last-known media; cache paths are contained; CarImages has a live `ping`; transient cache read/write errors no longer delete valid entries. Regression tests are listed in the register (19 offline-resilience tests).
- **F-030–F-032 (Task 23, Medium/Low):** Bookings/Inspections/Maintenance filter and search in SQL before the row cap, capped lists show their limit, and the customers report uses one grouped lifetime-count query. Regression tests: the `*_outside_the_cap`, `*_latest_limit_is_shown`, and `test_query_count_is_bounded` cases in `tests/test_staff_pages.py` and `tests/test_report_service.py`.

## Final verification (Task 25)

- **Full suite:** `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -rs` → exit 0; 1289 collected, 1288 passed, 1 skipped (`tests/test_security_hardening.py:299`, Windows ACL check). Log: `/tmp/opencode/task25-full.log`.
- **Migration suite:** `pytest tests/test_migrate_*.py -q` → exit 0, 119 tests. Log: `/tmp/opencode/task25-migrations.log`.
- **Fresh SQLite:** `scripts.init_db` then the documented order (`migrate_staff_schema` → `migrate_customer_booking` → `migrate_guarantor_profile`) twice → all exits 0. Result: 15 tables, `BRANCH` and both PSGC tables present, `USERS` has 28 columns; second pass is idempotent.
- **Legacy SQLite copy:** staff and customer migrations exit 0 on both passes; `BRANCH`, PSGC tables, and online-booking columns are present; a sample `VEHICLE` row survives. The guarantor migration exits 1 on both passes with the documented F-003 message (`license_number` still NOT NULL); it leaves the data intact and is repeatable. This is the accepted Low legacy-only limitation.
- **Golden path:** `tests/test_final_audit_flow.py` → 1 passed. It covers online booking → staff confirm → check-in → rental and deposit payments → checkout/settlement → receipt → customer payment history → dashboard/report. Log: `/tmp/opencode/task25-golden.log`.
- **Live UI smoke:** A visible click-through was run on the real Wayland desktop (1920x1080) with OS-level mouse/keyboard input (`ydotool`) and screenshots (`grim`) against a throwaway SQLite database. See **Live UI smoke** below.
- **Constraints:** no live MySQL writes (throwaway SQLite only); GUI tests ran with `QT_QPA_PLATFORM=offscreen`; no conflict markers; `git diff --check` clean; working tree clean after the sign-off commit.

## Live UI smoke (2026-10-26)

A visible end-to-end pass was run against `/tmp/opencode/ui-smoke/ui-smoke.db` on the real Wayland desktop, using `ydotool` for actual mouse/keyboard input and `grim` for screenshots. No live MySQL or repository data was used.

1. **Customer booking:** signed in as the demo shopper, searched for the Isuzu F-Series, chose 26–28 Oct 2026, paid cash, and received reference **RW-HMZQJ5** with total **₱15,758.40**. The success overlay and Bookings tab showed the pending booking.
2. **Staff processing:** signed in as admin, searched by the customer reference, recorded the rental (**₱15,758.40**) and deposit (**₱5,000.00**), confirmed, checked in at 39,472 km, checked out at 39,600 km, and generated receipt 363.
3. **Customer result:** the Completed tab showed the returned Isuzu F-Series; Payments showed the rental as paid and the deposit as held/refunded.
4. **Staff reporting:** the Dashboard's Recent Transactions showed the rental, deposit, and refund; Reports loaded the revenue view; Fleet details showed **“All-time: Rented 1× · revenue ₱15,758.40 · maintenance ₱4,500.00.”**
5. **Pricing checks:** a 7-day Isuzu F-Series at ₱6,200/day showed rental **₱39,060.00** (10% long-term discount; undiscounted ₱43,400) and total **₱45,617.60**; a 2-day Honda Click at ₱2,500/day showed rental **₱3,500.00** (70% motorcycle multiplier; undiscounted ₱5,000) and total **₱5,790.40**.
6. **Deposit-applied label:** a scratch-data fixture created through the payment service showed the customer row as **“Deposit applied · Applied to penalties”**, with **“Applied”** as the time label and the note **“Security deposit applied to penalties; no cash changed hands.”**

Screenshots (local scratch, not committed): `/tmp/opencode/ui-smoke/15-booking-success.png`, `21-staff-bookings.png`, `32-staff-confirmed2.png`, `37-staff-completed.png`, `48-customer-completed-tab.png`, `49-customer-payments.png`, `54-staff-dashboard-final.png`, `55-staff-reports-final.png`, `69-moto-schedule.png`, `74-fseries-7day.png`, `75-customer-payments-deposit-applied.png`, `79-fleet-details.png`, and `receipt-363.png`.

## Deferred and waived items

- **F-003 (Low, deferred/documented):** legacy SQLite only. `migrate_guarantor_profile` cannot relax `USERS.license_number` in place and exits with instructions. Fresh SQLite matches the model, and the MySQL migration path relaxes the column. No production MySQL impact; owner is the database maintainer. Revisit only if a legacy SQLite database must support licence-less customers.

## Known environmental limitations

- CarImages/media API availability is external; the live smoke showed placeholder media where the API was unavailable, and failures remain classified as Environmental.
- The live UI smoke used a local Wayland desktop and a throwaway SQLite database; screenshots are local scratch and are not committed.

## Sign-off

**Audit result:** PASS — no open Critical or High findings. One Low finding (F-003) is deferred/documented and affects only legacy SQLite; fresh SQLite and the MySQL migration path are correct.

**Merge decision:** Ready for explicit user approval. Do not merge `final-audit` into `main` until the user approves this report.

**Approval:** Pending.
