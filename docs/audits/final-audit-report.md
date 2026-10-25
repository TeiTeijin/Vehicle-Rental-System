# Final Audit Report

- **Branch:** `final-audit`
- **Baseline commit:** `b77c101685a766426571fccd41de27f28e597aeb`
- **Audit plan:** `docs/plans/2026-10-25-final-audit.md`
- **Register:** `docs/audits/final-audit-register.md`
- **Report status:** In progress

## Scope

End-to-end audit of the merged customer + staff Vehicle Rental System on the `final-audit` branch: customer account and booking flows (C1–C8), staff operational flows (S1–S12), and cross-cutting handoffs (X1–X9). The audit covers behavior, money/deposit accounting, migrations, security/PII boundaries, media resilience, and performance guardrails.

Out of scope: merging to `main`, live MySQL writes, external CarImages availability (classified as environmental when unavailable).

## Branch and commit

| Item | Value |
|---|---|
| Branch | `final-audit` |
| Baseline commit | `b77c101685a766426571fccd41de27f28e597aeb` |
| Baseline date | 2026-10-25 |
| Final commit | Pending |

## Environment

| Item | Value |
|---|---|
| OS | Windows 11 |
| Python | 3.13.5 (`.venv`) |
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
| Critical | 0 | 0 | 0 | 0 |
| High | 2 | 4 | 0 | 0 |
| Medium | 1 | 4 | 0 | 0 |
| Low | 2 | 2 | 0 | 0 |
| Environmental | 0 | 0 | 0 | 0 |

Task 3 fixed two High migration defects: MySQL `ENUM` DDL was emitted on SQLite (F-001), and the documented order did not create the PSGC reference tables (F-002). A Low legacy-SQLite nullability limitation (F-003) is open for Task 24 disposition. Task 7 found two open High pricing defects: online long-term discount (F-005) and motorcycle multiplier (F-006) are not applied. Task 8 found an open Medium labeling defect: `deposit_applied` rows read as cash in customer history (F-007). Task 12 found and fixed a High pickup-gate defect: check-in did not re-check customer eligibility or refuse retired vehicles (F-010). Task 14 found and fixed a High extension defect: online return times were not moved and same-day time conflicts were missed (F-012). Task 16 found and fixed a Medium open-ended-workshop crash (F-015) and a Low odometer-integrity gap (F-014).

Detailed findings and evidence live in `docs/audits/final-audit-register.md`.

## Fixes and regression tests

- **F-001 / F-002 (Task 3):** `migrate_customer_booking` now emits SQLite-compatible DDL for the `payment_method` enum and creates the PSGC reference tables idempotently. Regression test: `tests/test_migrate_staff_schema.py::TestDocumentedMigrationOrder::test_documented_migration_order_matches_models`. All 119 migration tests pass; fresh and legacy orders are repeatable.
- **F-003 (Task 3, Low):** legacy SQLite cannot relax `USERS.license_number` in place; documented in the register for Task 24 disposition.
- **F-004 (Task 4, Medium):** receipts now print the online booking's customer-facing reference code. Regression test: `tests/test_final_audit_flow.py::test_online_booking_reaches_staff_and_completes`. Receipt/customer-payment suites pass (30 tests).
- **F-008 / F-009 (Task 11, Medium):** staff can search online bookings by the customer-facing reference, and the details panel shows the reference, pickup/return times, and branch names. Regression tests: `test_search_matches_the_customer_facing_reference`, `test_details_panel_shows_reference_branches_and_times`.
- **F-010 (Task 12, High):** check-in now re-checks active status, licence number and expiry (through the return date), flag overrides, and refuses retired or workshop vehicles. Regression tests: the six `TestCheckIn::test_refuses_*` / `test_a_flagged_customer_needs_an_override_note` cases in `tests/test_booking_service.py`.
- **F-011 (Task 13, Low):** deposit refunds keep the original tender instead of always recording cash. Regression test: `test_deposit_refund_keeps_the_original_tender`.
- **F-012 (Task 14, High):** extensions now move an online booking's return time and check the added window as datetimes, so a same-day booking cannot be double-booked. Regression tests: `test_extend_moves_the_online_return_time`, `test_extend_detects_a_same_day_time_clash`.
- **F-014 (Task 16, Low):** maintenance now refuses a current/future odometer below the vehicle's mileage and a completion reading below the job's start reading; historical backfill is still allowed. Regression tests: the four `TestOdometer` cases in `tests/test_maintenance_service.py`.
- **F-015 (Task 16, Medium):** open-ended workshop jobs now produce a clean conflict message ("from <date>") instead of a `TypeError` when booking, quoting, or extending. Regression tests: `test_booking_reports_the_open_ended_window`, `test_extension_reports_the_open_ended_window`.

## Deferred and waived items

Pending.

## Known environmental limitations

- CarImages/media API availability is external; failures are recorded as Environmental and retested when available.
- Interactive manual UI click-through is limited on this headless host; offscreen Qt tests and service-level flow evidence are used, and any unexercised UI step is recorded as a gap.

## Sign-off

**Status:** Pending — do not merge `final-audit` into `main` until this report is complete and the user explicitly approves.
