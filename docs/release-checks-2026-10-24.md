# Audit remediation release checks — 2026-10-24

Branch: `staff-audit-remediation`
Head at check time: `6ce4040` (`fix: stop deleted vehicle rails from building cards`)
Python: 3.13.5 in `.venv`; PySide6; offscreen Qt platform.

This repository has no `README.md`; this document records the Task 20 release
checks instead.

## Automated checks

| Check | Command | Result |
| --- | --- | --- |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv\Scripts\python.exe -m pytest -q -o addopts=` | **1096 passed** in 11:18 (678.11s) |
| Non-GUI parallel | `... -n auto --ignore=tests/test_staff_pages.py --ignore=tests/test_staff_dashboard.py` | **687 passed** in 32.03s |
| Ruff | `py -m ruff check app tests` | All checks passed |

Targeted checks run during the remediation:

- Task 12 pricing/receipt work: 127 passed; targeted receipt test passed.
- Task 14 customer aggregates: `test_customer_management.py` +
  `test_staff_pages.py` → 344 passed.
- Task 15 New Rental worker: 71 passed (the stronger
  `-k "NewRental or new_rental or failed_fleet_load"` selection).
- Security hardening, including Windows ACLs: 23 passed.
- Bookings actions, including the new deposit path: covered by the full suite.

## Demo smoke test

`launch_demo.py` uses the real login form and real `StaffShell`. Running it
offscreen:

- signed in as `admin@rentwheels.local` (bcrypt password check);
- opened `dashboard`, `new_rental`, `bookings`, `fleet`, `customers`,
  `payments`, `inspections`, `maintenance`, `reports`, and `settings`
  (`show_page` returned `True` for each);
- exited cleanly when stopped after the smoke window; stderr was empty after
  the deleted-rail timer guard (`6ce4040`).

Interactive click-through is not possible in this headless environment. The
listed scenarios are covered by the automated GUI/service suites:

- rental wizard, Escape/click-outside draft behavior, explicit cancel,
  invalid-status button disabling, and “View in Bookings” focus handoff:
  `tests/test_staff_pages.py`;
- future rental on a rented car, quote/payment agreement, fee-only pickup,
  check-in blocking, damage settlement above deposit, and receipt failure:
  booking/rental-life-cycle/payment tests plus the Task 5–12 targeted runs;
- large report and CSV/PDF export: report/export tests in the suite.

## Local permissions

On Windows, POSIX mode bits are not meaningful: `os.stat` reports `666` for
`.env`, `ca.pem`, and `demo.db`. `scripts/secure_local_files.py` now removes
inherited ACLs and grants only the current user:

```text
.env    LILIAN-S\Lily Ann:(F)
ca.pem  LILIAN-S\Lily Ann:(F)
```

`backup.db` is absent and the script reports it as `missing` without failing.
On POSIX the same script still applies mode `0600`. The security tests assert
ACLs on Windows and mode bits on POSIX, so the suite is green on both.

## Known limitations

- The demo’s interactive flows were exercised through tests, not a human
  mouse/keyboard session; see the mapping above.
- POSIX permission tests are skipped/adapted on NTFS by design; the ACL is
  the enforceable control there.
- Product-policy items deliberately deferred in the plan are unchanged:
  refund UI, login throttling, and customer temporary-password replacement.
- CarImages is external and may be unavailable; media/network failures remain
  environmental.
