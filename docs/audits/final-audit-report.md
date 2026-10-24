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
| High | 0 | 2 | 0 | 0 |
| Medium | 0 | 1 | 0 | 0 |
| Low | 1 | 0 | 0 | 0 |
| Environmental | 0 | 0 | 0 | 0 |

Task 3 fixed two High migration defects: MySQL `ENUM` DDL was emitted on SQLite (F-001), and the documented order did not create the PSGC reference tables (F-002). A Low legacy-SQLite nullability limitation (F-003) is open for Task 24 disposition.

Detailed findings and evidence live in `docs/audits/final-audit-register.md`.

## Fixes and regression tests

- **F-001 / F-002 (Task 3):** `migrate_customer_booking` now emits SQLite-compatible DDL for the `payment_method` enum and creates the PSGC reference tables idempotently. Regression test: `tests/test_migrate_staff_schema.py::TestDocumentedMigrationOrder::test_documented_migration_order_matches_models`. All 119 migration tests pass; fresh and legacy orders are repeatable.
- **F-003 (Task 3, Low):** legacy SQLite cannot relax `USERS.license_number` in place; documented in the register for Task 24 disposition.
- **F-004 (Task 4, Medium):** receipts now print the online booking's customer-facing reference code. Regression test: `tests/test_final_audit_flow.py::test_online_booking_reaches_staff_and_completes`. Receipt/customer-payment suites pass (30 tests).

## Deferred and waived items

Pending.

## Known environmental limitations

- CarImages/media API availability is external; failures are recorded as Environmental and retested when available.
- Interactive manual UI click-through is limited on this headless host; offscreen Qt tests and service-level flow evidence are used, and any unexercised UI step is recorded as a gap.

## Sign-off

**Status:** Pending — do not merge `final-audit` into `main` until this report is complete and the user explicitly approves.
