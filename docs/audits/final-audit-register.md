# Final Audit Register

- **Branch:** `final-audit`
- **Baseline commit:** `b77c101685a766426571fccd41de27f28e597aeb`
- **Plan:** `docs/plans/2026-10-25-final-audit.md`
- **Started:** 2026-10-25
- **Environment:** Windows 11, Python 3.13.5 (`.venv`), PySide6 6.11.2, SQLAlchemy 2.0.54, pytest 9.1.1
- **Test database:** throwaway in-memory SQLite via `tests/conftest.py`; no live MySQL is used.
- **GUI tests:** `QT_QPA_PLATFORM=offscreen`

## Status legend

`Not started` · `In progress` · `Pass` · `Finding` · `Blocked` · `Waived`

Severity: `Critical` · `High` · `Medium` · `Low` · `Environmental`

## Flow register

| ID | Area | Flow | Entry point | Services | Tables | Roles | Invariants | Existing tests | Evidence | Status | Severity | Finding |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C1 | Customer | Signup → account created | `app/ui/signup.py` | `auth_service.register_customer`, `location_service` | `USERS`, PSGC tables | Public | Unique email/licence; valid mobile/address/guarantor; password hashed; `role=customer`, `source=online`, `is_active=True`; staff licence collision rejected | `test_customer_auth_ui.py`, `test_signup_location.py`, `test_customer_flow_audit.py` | Task 5: valid signup stores hashed password, role=customer, source=online, is_active=True, PSGC codes, formatted address, guarantor; duplicate email/licence, staff licence, bad mobile/street/guarantor/city/province all rejected | Pass | — | — |
| C2 | Customer | Customer login → session | `app/ui/customer_login.py` | `auth_service.sign_in_customer` | `USERS` | Customer | Generic failure message; inactive blocked; staff account cannot use customer login; session holds customer identity | `test_customer_auth_ui.py`, `test_customer_flow_audit.py` | Task 5: valid login, generic wrong-password message, inactive blocked, staff account refused; 60 account tests passed | Pass | — | — |
| C3 | Customer | Forgot/reset password | `app/ui/forgot_password.py`, `auth_gate_dialog.py` | `auth_service.password_reset_account`, `reset_customer_password` | `USERS` | Public | No account enumeration; same password policy as signup; reset is scoped to the requested account | `test_customer_password_reset.py`, `test_customer_auth_ui.py` | Task 5: reset does not enumerate accounts, enforces the signup password policy, and changes the stored hash | Pass | — | — |
| C4 | Customer | Browse → search/filter → details/media | `app/ui/home.py`, `hero.py`, `vehicles.py`, `vehicle_details.py`, `vehicle_photo.py` | `vehicle_service`, `media_service`, `vehicle_photos`, `carimages_client`, `image_cache`, `api_cache` | `VEHICLE`, `VEHICLE_CATEGORY`, `VEHICLE_MEDIA`, `BRANCH` | Customer, public | Only rentable/available vehicles are bookable; prices/labels match services; retired/maintenance vehicles excluded; media failure degrades gracefully | `test_customer_flow_audit.py`, `test_media_warning.py`, `test_security_hardening.py` | Task 6: 51 discovery/media/security tests passed; audit_task6.py verified available-only showroom, search, filters, rate bounds, details, branch hours; retired/maintenance/rented excluded | Pass | — | — |
| C5 | Customer | Schedule → checkout → online booking created | `app/ui/booking_schedule.py`, `booking_checkout.py` | `booking_service.create_online_booking`, `assert_window_free`, `pricing.quote`, `branch_service`, `card_validation`, `payment_service` | `BOOKING`, `PAYMENT`, `VEHICLE`, `BRANCH` | Customer | Dates/times required; branch hours/slots enforced; overlaps rejected; same-day policy honored; totals exact; `pending`, `channel=online`, unique reference; expired licence/inactive/staff/workshop vehicle rejected; no full card/CVV stored | `test_customer_flow_audit.py`, `test_customer_checkout_payment.py`, `test_booking_service.py`, `test_pricing_policy.py` | Task 7: 100 booking/pricing tests passed; audit_task7.py verified pending cash, unique reference, branches, breakdown, overlap/hours/same-day, card last4 only, GCash reference, pending/failed payments, licence/active/role/workshop gates. F-005/F-006: online quote omits long-term and motorcycle multipliers | Finding | High | F-005, F-006 |
| C6 | Customer | Customer views bookings | `app/ui/bookings.py` | `booking_service.bookings_for` | `BOOKING`, `VEHICLE`, `PAYMENT` | Customer | Only own bookings; correct status buckets; totals match balance service | `test_customer_flow_audit.py`, `test_customer_payments.py` | Task 8: 34 self-service tests passed; audit_task8.py verified two-customer isolation and all/upcoming/completed/cancelled buckets | Pass | — | — |
| C7 | Customer | Customer views payments/notifications | `app/ui/payments.py`, `notifications.py` | `payment_service.payments_for_user`, `notification_service.notifications_for` | `PAYMENT`, `BOOKING` | Customer | Only own rows; rental/deposit/`deposit_applied` labeled correctly; notifications only own | `test_customer_payments.py`, `test_customer_notifications.py` | Task 8: payment history and notifications scoped to the customer; rental/deposit/deposit_applied balance math correct; F-007 deposit_applied label is generic/cash | Finding | Medium | F-007 |
| C8 | Customer | Profile/session/logout/auth gate | `app/ui/profile.py`, `account_popup.py`, `session.py`, `main_window.py` | `profile_service.profile_for/update_profile` | `USERS`, PSGC tables | Customer | Own profile only; validation/persistence; logout clears identity; protected actions show auth gate | `test_customer_profile.py`, `test_customer_account_popup.py`, `test_customer_flow_audit.py` | Task 8: profile counts/updates scoped and validated; logout clears identity and member pages; protected tabs use the auth gate (code + tests) | Pass | — | — |
| S1 | Staff | Staff login / first-run admin | `app/staff/__main__.py`, `login.py`, `shell.py`, `settings_panels.py` | `auth_service.sign_in`, `staff_service.ensure_first_admin`, password/set-active services | `USERS` | Staff, admin, first-run | Only one first admin; customer cannot use staff login; inactive blocked; temp password forces change; admin-only pages gated; sign-out/idle logout clears session | `test_staff_login.py`, `test_staff_shell.py`, `test_staff_settings.py` | Task 9: 112 login/shell/settings tests passed; audit_task9.py verified single first admin, first-run closure, email/password policy, temp password + forced change, temp expiry, inactive staff, last-admin protection, customer row refused by the staff login | Pass | — | — |
| S2 | Staff | Walk-in rental from lookup to confirmation | `app/staff/pages/new_rental.py`, `rental_wizard.py`, `booking_panels.py`, `customer_panels.py` | `counter_service.quote`, `booking_service.complete_counter_rental`, `payment_service.record_payment`, `receipt_service` | `USERS`, `BOOKING`, `PAYMENT`, `VEHICLE` | Staff | `channel=walk_in`; `created_by` set; scheduling fee ≤ rental total; GCash reference when configured; deposit is not revenue; receipt failure is a warning | `test_counter_rental.py`, `test_counter_service.py`, `test_booking_service.py`, `test_receipt_service.py` | Task 10: 167 counter/booking/receipt tests + 24 wizard page tests passed; audit_task10.py verified lookup/new/flag override, quote, fee floor/ceiling, GCash ref, partial and full pay, channel/created_by, rental + deposit rows, confirmation, receipt, receipt-failure warning | Pass | — | — |
| S3 | Staff | Online booking appears in staff Bookings | `app/staff/pages/bookings.py`, `booking_panels.py` | `booking_service.bookings_for`, `confirm_booking`, `cancel_booking`, `customer_service`, `notification_service` | `BOOKING`, `USERS`, `VEHICLE`, `PAYMENT` | Staff | Online booking visible with customer/vehicle/dates/branches/channel/reference/pending status/price; confirm/cancel visible to customer; cancellation releases vehicle | `test_customer_flow_audit.py`, `test_staff_pages.py`, `test_booking_service.py` | Task 11: audit_task11.py verified online booking in staff list (customer, vehicle, dates, online channel, pending, price) and search by reference; details now show reference/times/branches; confirm/cancel update the customer; cancel stores reason, releases the window, settles the deposit | Pass | — | F-008, F-009 fixed |
| S4 | Staff | Pickup/check-in | `app/staff/pages/bookings.py` | `booking_service.check_in`, `fleet_service.mark_rented`, `payment_service`, inspection service | `BOOKING`, `VEHICLE`, `PAYMENT`, `INSPECTION_REPORT` | Staff | Only pending/confirmed check in; licence/eligibility checked; mileage/fuel recorded; pre-rental inspection created; vehicle `rented`; booking `ongoing`; customer sees status | `test_booking_service.py`, `test_rental_life_cycle.py`, `test_staff_pages.py` | Task 12: 110 booking-service + 3 staff check-in tests passed; audit_task12.py verified settled pickup, ongoing/rented, mileage, pre-rental inspection, customer-visible status, and refusals for outstanding balance, expired/missing licence, inactive/flagged customer, retired/maintenance vehicle, backwards odometer. F-010 fixed | Pass | — | F-010 fixed |
| S5 | Staff | Return/checkout, deposit, settlement, receipt | `app/staff/pages/bookings.py`, `payments.py` | `booking_service.checkout_quote`, `check_out`, `apply_penalty`, `payment_service.settle_deposit`, `booking_balance`, `receipt_service` | `BOOKING`, `PAYMENT`, `PENALTY`, `VEHICLE`, `INSPECTION_REPORT` | Staff | Exact settlement; deposit applied to penalties first; remainder returned; held deposit not revenue; `deposit_applied` not cash; booking `completed`; vehicle `available`; post-rental inspection; receipt warning does not roll back | `test_booking_service.py`, `test_receipt_service.py`, `test_migrate_deposit_applied.py`, `test_customer_payments.py` | Task 13: 159 checkout/receipt/deposit/customer tests passed; audit_task13.py verified clean refund, late+damage penalties, deposit applied first, exact shortfall settlement, completed/available, post-rental inspection, receipt, and customer ledger. F-011 fixed | Pass | — | F-011 fixed |
| S6 | Staff | Cancellation and extension | `app/staff/pages/bookings.py`, `app/ui/bookings.py` | `booking_service.cancel_booking`, `extend_booking`, `extension_quote`, `payment_service.refund_payment` | `BOOKING`, `PAYMENT`, `VEHICLE` | Staff, customer | Only pending/confirmed cancel; reason stored; vehicle released; refunds follow policy; cancelled excluded from revenue/usage; extension uses same pricing; no window conflict | `test_booking_service.py`, `test_staff_pages.py`, `test_customer_flow_audit.py` | Pending Task 14 | Not started | — | — |
| S7 | Staff | Fleet lifecycle and rates | `app/staff/pages/fleet.py`, `fleet_panels.py`, `vehicle_cards.py`, `vehicle_details.py` | `fleet_service`, `vehicle_service`, `vehicle_photos`, `image_cache`, `media_service` | `VEHICLE`, `VEHICLE_CATEGORY`, `VEHICLE_RATE_LOG`, `VEHICLE_MEDIA`, `BOOKING` | Staff | Duplicate plate rejected; legal status transitions; retire preserves history; bulk changes preview/apply; rate history written; stats match reports; cache keys safe | `test_fleet_management.py`, `test_staff_pages.py`, `test_security_hardening.py` | Pending Task 15 | Not started | — | — |
| S8 | Staff | Maintenance lifecycle | `app/staff/pages/maintenance.py`, `maintenance_panels.py` | `maintenance_service`, `fleet_service` | `MAINTENANCE_RECORD`, `VEHICLE` | Staff | Legal scheduled/in_progress/completed/cancelled transitions; vehicle syncs to maintenance/available; due date/odometer stamped; history retained; blocking maintenance prevents booking | `test_maintenance_service.py`, `test_migrate_maintenance_lifecycle.py`, `test_staff_pages.py` | Pending Task 16 | Not started | — | — |
| S9 | Staff | Customer management and documents | `app/staff/pages/customers.py`, `customer_panels.py` | `customer_service`, `profile_service`, `location_service`, `auth_service` | `USERS`, `CUSTOMER_NOTE`, PSGC tables | Staff | Search by name/phone/email/licence; required fields; duplicate warnings; flags/notes audited; licence docs path-safe; balances/open bookings match services; online data visible | `test_customer_management.py`, `test_signup_location.py`, `test_customer_profile.py`, `test_staff_pages.py` | Pending Task 17 | Not started | — | — |
| S10 | Staff | Payments, refunds, deposits, outstanding | `app/staff/pages/payments.py` | `payment_service`, `receipt_service`, `report_service` | `PAYMENT`, `BOOKING`, `PENALTY` | Staff | Search before cap; kinds correct; refunds do not duplicate revenue; takings count rental-kind paid money; outstanding matches checkout; customer history matches ledger; Decimal end-to-end | `test_customer_payments.py`, `test_counter_rental.py`, `test_booking_service.py`, `test_receipt_service.py`, `test_staff_pages.py` | Pending Task 18 | Not started | — | — |
| S11 | Staff | Reports, dashboard, exports | `app/staff/pages/reports.py`, `report_panels.py`, `dashboard.py` | `report_service`, `dashboard_service`, `export_service` | `BOOKING`, `PAYMENT`, `PENALTY`, `VEHICLE`, `MAINTENANCE_RECORD`, `USERS` | Staff, admin | Every panel non-empty; rows match services; date boundaries correct; cancelled excluded; deposits do not inflate revenue; online bookings included; exports match panels; dashboard ≤3 queries off GUI thread | `test_report_service.py`, `test_staff_dashboard.py`, `test_staff_pages.py` | Pending Task 19 | Not started | — | — |
| S12 | Staff | Settings, staff accounts, backup, cache | `app/staff/pages/settings.py`, `settings_panels.py` | `settings_service`, `staff_service`, `backup_service`, `scripts/backup_live_db.py`, `scripts/secure_local_files.py` | `APP_SETTINGS`, `USERS` | Admin, staff | Admin edits shared policy; staff read-only; typed validation; defaults apply; pricing affects quotes; receipt settings affect output; account rules; backup live-SQLite only; sanitization removes secrets/PII | `test_staff_settings.py`, `test_backup_live_db.py`, `test_migrate_app_settings.py` | Pending Task 20 | Not started | — | — |
| X1 | Shared | Customer online booking → staff processing | `app/ui/booking_checkout.py` → `app/staff/pages/bookings.py` | `booking_service`, `customer_service`, `notification_service` | `BOOKING`, `USERS`, `VEHICLE`, `PAYMENT` | Customer, staff | Same booking identity/reference/price on both sides; staff actions update customer-visible status | `test_customer_flow_audit.py`, `test_staff_pages.py`, `test_booking_service.py` | Task 11: same booking/reference/price on both sides; staff confirm and cancel update the customer-visible status and notification | Pass | — | F-008, F-009 fixed |
| X2 | Shared | Customer payment/deposit → staff ledger | `app/ui/booking_checkout.py` → `app/staff/pages/payments.py` | `payment_service`, `booking_service`, `receipt_service` | `PAYMENT`, `BOOKING` | Customer, staff | Deposit rows are liabilities; applied deposit is not cash; customer and staff see the same rows/totals | `test_customer_payments.py`, `test_booking_service.py`, `test_counter_rental.py` | Task 13: held deposit excluded from takings; deposit_applied is not cash; exact settlement recorded; refund keeps the original tender | Pass | — | F-011 fixed |
| X3 | Shared | Staff payment/checkout → customer history | `app/staff/pages/bookings.py` → `app/ui/bookings.py`, `payments.py` | `booking_service`, `payment_service`, `receipt_service` | `BOOKING`, `PAYMENT`, `PENALTY` | Staff, customer | Customer sees completed status and exact payment/penalty rows; no staff-only data leaks | `test_customer_payments.py`, `test_booking_service.py` | Task 13: customer sees completed status plus rental/deposit/refund/deposit_applied rows; no staff-only data | Pass | — | — |
| X4 | Shared | All flows → reports/dashboard | `app/staff/pages/reports.py`, `dashboard.py` | `report_service`, `dashboard_service`, `export_service` | `BOOKING`, `PAYMENT`, `PENALTY`, `VEHICLE`, `MAINTENANCE_RECORD` | Staff, admin | Revenue excludes held deposits; includes online bookings; penalties and refunds correct; exports match panels; query budgets hold | `test_report_service.py`, `test_staff_dashboard.py`, `test_query_metrics.py` | Task 13: revenue report splits rental cash from penalty cash; deposit_applied adds no cash; period deposits match paid deposit rows; penalties raised reported separately | In progress | — | — |
| X5 | Shared | Branch/location → availability and pickup/return | Customer booking screens; staff New Rental/Bookings/Customers | `branch_service`, `booking_service`, `location_service`, `pricing` | `BRANCH`, `BOOKING`, `USERS`, `VEHICLE` | Customer, staff | Branch hours/slots enforced; pickup/return branches stored; availability scoped correctly; PSGC data resolves | `test_booking_service.py`, `test_signup_location.py`, `test_counter_rental.py` | Task 13 return: return date/mileage recorded against the booking; branch data preserved; remaining branch-scope checks in Task 17 | In progress | — | — |
| X6 | Shared | Media/photos → customer and staff surfaces | `app/ui/vehicle_details.py`, `vehicle_photo.py`, `app/staff/vehicle_cards.py` | `media_service`, `vehicle_photos`, `carimages_client`, `image_cache`, `api_cache` | `VEHICLE_MEDIA` | Customer, staff | No crash/hang on API failure; cache fallback; variant keys distinct; no traversal; no customer/staff data in metadata | `test_media_warning.py`, `test_security_hardening.py`, `scripts/test_image_cache.py` | Task 6 media: offline CarImages degradation tests passed; cache identity path-safe, traversal rejected, variants distinct, store/load/stats/clear verified; full resilience in Task 22 | In progress | — | — |
| X7 | Shared | Booking state → customer notifications | `app/services/notification_service.py`, `app/ui/notifications.py` | `notification_service`, `booking_service` | `BOOKING`, `PAYMENT` | Customer, staff | Status changes produce correct customer-visible notifications; no cross-customer leak | `test_customer_notifications.py`, `test_booking_service.py` | Task 8: notifications derived only from the customer's own bookings; no cross-customer leak | Pass | — | — |
| X8 | Security | Roles/authorization/PII/branch scope | All auth/customer/profile/payment/booking/media/backup paths | `auth_service`, `customer_service`, `profile_service`, `payment_service`, `backup_service` | `USERS`, `BOOKING`, `PAYMENT`, `VEHICLE_MEDIA` | Customer, staff, admin, inactive, flagged | Customer/staff boundary enforced; IDOR blocked; inactive/flagged per policy; passwords hashed; temp passwords not plaintext; licence docs/cache paths safe; backups sanitized; errors do not leak internals; Decimal money | `test_security_hardening.py`, `test_customer_flow_audit.py`, `test_staff_login.py`, `test_staff_pages.py`, `test_backup_live_db.py` | Task 5 auth boundary + Task 8 customer isolation + Task 9 staff role gate verified; remaining authorization/PII/path checks in Task 21 | In progress | — | — |
| X9 | Data | Migration/seed/data integrity | `scripts/migrate_*`, `scripts/init_db.py`, `scripts/seed_*` | migration scripts, `app.database`, models | All tables | System | Documented order produces all model tables/columns on fresh and legacy DBs; idempotent; seeds consistent; no data loss | `tests/test_migrate_*.py`, `test_seed_demo_data.py` | Task 3: full-schema test added; 119 migration tests passed; fresh order 0/0/0 twice; legacy order 0/0/1 (SQLite `license_number` limitation, F-003) | Pass | — | F-001/F-002 fixed; F-003 Low open |

## Traceability

### Screen-to-flow matrix

| Surface | Module | Flow IDs | Status |
|---|---|---|---|
| Customer router/session | `app/ui/main_window.py`, `session.py` | C1–C8, X1, X2, X7 | Active |
| Customer login | `app/ui/customer_login.py` | C2 | Active |
| Auth gate | `app/ui/auth_gate_dialog.py` | C3, C8 | Active |
| Signup | `app/ui/signup.py` | C1 | Active |
| Forgot/reset password | `app/ui/forgot_password.py` | C3 | Active |
| Home | `app/ui/home.py` | C4 | Active |
| Hero | `app/ui/hero.py` | C4 | Legacy (only referenced by unused `app/ui/dashboard.py`) |
| Vehicles | `app/ui/vehicles.py` | C4 | Active |
| Vehicle details | `app/ui/vehicle_details.py` | C4, C5 | Active |
| Vehicle photo | `app/ui/vehicle_photo.py` | C4, X6 | Active |
| Booking schedule | `app/ui/booking_schedule.py` | C5 | Active |
| Booking checkout | `app/ui/booking_checkout.py` | C5, X1, X2 | Active |
| Customer bookings | `app/ui/bookings.py` | C6, S6 | Active |
| Customer payments | `app/ui/payments.py` | C7, X3 | Active |
| Customer notifications | `app/ui/notifications.py` | C7, X7 | Active |
| Customer profile | `app/ui/profile.py` | C8 | Active |
| Account popup | `app/ui/account_popup.py` | C8 | Active |
| Customer dashboard | `app/ui/dashboard.py` | — | Legacy/unused (no active importer) |
| Staff shell/login | `app/staff/shell.py`, `login.py` | S1, S12, X8 | Active |
| Staff dashboard | `app/staff/pages/dashboard.py` | S11, X4 | Active |
| New Rental | `app/staff/pages/new_rental.py`, `rental_wizard.py`, `customer_panels.py` | S2, X2, X5 | Active |
| Bookings | `app/staff/pages/bookings.py`, `booking_panels.py` | S3, S4, S5, S6, X1, X2, X3, X5, X7 | Active |
| Fleet | `app/staff/pages/fleet.py`, `fleet_panels.py`, `vehicle_cards.py`, `vehicle_details.py` | S7, X6 | Active |
| Customers | `app/staff/pages/customers.py`, `customer_panels.py` | S9, X3, X5, X8 | Active |
| Payments | `app/staff/pages/payments.py` | S10, X2, X3, X4 | Active |
| Inspections | `app/staff/pages/inspections.py` | S4, S5 | Active (covered by S4/S5, no separate flow ID) |
| Maintenance | `app/staff/pages/maintenance.py`, `maintenance_panels.py` | S8, X4 | Active |
| Reports | `app/staff/pages/reports.py`, `report_panels.py` | S11, X4 | Active |
| Settings | `app/staff/pages/settings.py`, `settings_panels.py` | S1, S12, X8 | Active |
| Staff sidebar | `app/staff/sidebar.py` | S1, S12, X8 | Active |
| Dashboard screenshots | `app/staff/dashboard_shots.py`, `new_rental_shots.py` | — | Developer tooling, not user-facing |
| Icon/ink checks | `app/staff/icon_check.py`, `icon_preview.py`, `inkcheck.py` | — | Developer tooling, not user-facing |

### Service-to-flow matrix

| Service module | Flow IDs | Public data-mutating functions |
|---|---|---|
| `auth_service` | C1, C2, C3, S1, X8 | `register_customer`, `reset_customer_password` |
| `profile_service` | C8, S9, X8 | `update_profile` |
| `staff_service` | S1, S12, X8 | `create_staff`, `ensure_first_admin`, `change_password`, `force_change_password`, `reset_password`, `set_active` |
| `customer_service` | S2, S9, X3, X5, X8 | `create_customer`, `update_contact`, `update_customer`, `deactivate_customer`, `reactivate_customer`, `set_flag`, `clear_flag`, `add_note`, `store_licence_doc` |
| `location_service` | C1, C8, S9, X5 | read-only (`resolve`, `province_options`, `municipalities`) |
| `vehicle_service` | C4, C5, S7, X6 | read-only showcase/search helpers |
| `fleet_service` | S4, S7, S8, X4, X6 | `set_status`, `mark_rented`, `mark_available`, `mark_maintenance`, `schedule_maintenance`, `complete_maintenance`, `add_vehicle`, `update_vehicle`, `record_mileage`, `retire_vehicle`, `unretire_vehicle`, `set_daily_rate`, `bulk_set_rate`, `bulk_set_status` |
| `media_service` | C4, S7, X6 | `get_or_fetch_media`, `probe_media` |
| `vehicle_photos` | C4, S7, X6 | `fetch_photo` (cache write) |
| `image_cache` | C4, S7, X6, X8 | `store`, `clear` |
| `api_cache` | C4, X6 | `store`, `clear` |
| `carimages_client` | C4, X6 | read-only HTTP client |
| `booking_service` | C5–C7, S2–S6, X1–X5, X7 | `create_booking`, `create_online_booking`, `complete_counter_rental`, `confirm_booking`, `cancel_booking`, `check_in`, `check_out`, `extend_booking`, `apply_penalty` |
| `branch_service` | C5, S2, S3, X5 | read-only (`validate_window`, `within_hours`, `time_slots`) |
| `pricing` | C5, S2, S6, X4 | read-only calculator |
| `counter_service` | S2, X2 | read-only quote (`quote`) |
| `payment_service` | C5, S2, S5, S6, S10, X2, X3, X4 | `record_payment`, `settle_deposit`, `refund_payment` |
| `card_validation` | C5, X2 | `collect` (mock charge; no persistence) |
| `receipt_service` | S2, S5, S10, X3 | `generate_receipt_pdf`, `generate_statement_pdf` |
| `report_service` | S11, X4 | read-only reporting |
| `dashboard_service` | S11, X4 | read-only reporting |
| `export_service` | S11, X4 | `write_csv`, `write_pdf` |
| `maintenance_service` | S8, X4 | `start_maintenance`, `complete_maintenance`, `cancel_maintenance`, `sync_vehicle_status` |
| `notification_service` | C7, S3, X7 | read-only derived notifications |
| `settings_service` | C5, S2, S5, S12, X4 | `set_value`, `set_many` |
| `backup_service` | S12, X8 | `create_sanitized_sqlite_snapshot` |
| `errors` | All | shared exception types |
| `app.database` | All | engine/session factory |
| `app.config` | All | configuration |
| `app.domain.*` | C5, S2, S6 | pricing/availability domain rules |
| `app.staff.context` | S1–S12, X8 | access control/session context |
| `app.staff.query_metrics` | S11, X4 | query counting |

### Table-to-flow matrix

| Table | Read by | Written by | Invariants |
|---|---|---|---|
| `USERS` | C1–C3, C8, S1–S3, S9, S12, X8 | C1, C8, S1, S2, S9, S12 | Unique email/licence; hashed passwords; role/source/active/flag state; last active admin protected |
| `BRANCH` | C4, C5, S2, S3, X5 | migrations/seeds only | Opening hours/slots used by both apps |
| `VEHICLE_CATEGORY` | C4, C5, S7 | migrations/seeds | Rate multiplier and deposit class consistent |
| `VEHICLE` | C4–C6, S2–S8, X4–X6 | S2, S4, S5, S7, S8 | Status transitions legal; retired/maintenance not bookable; plate unique |
| `VEHICLE_MEDIA` | C4, S7, X6 | media cache | No customer/staff PII; variant keys distinct |
| `VEHICLE_RATE_LOG` | S7 | S7 | Every rate change recorded |
| `BOOKING` | C5–C7, S2–S6, S10, S11, X1–X5, X7 | C5, S2–S6 | Status machine; exact totals; branch/pickup/return fields; reference unique |
| `PAYMENT` | C5, C7, S2, S4–S6, S10, S11, X2–X4 | C5, S2, S4–S6, S10 | Kinds/statuses; deposit liability; refunds do not duplicate revenue; Decimal |
| `PENALTY` | S5, S10, S11, X4 | S5 | Penalty types and amounts; deposit applied before refund |
| `INSPECTION_REPORT` | S4, S5 | S4, S5 | Pre/post inspection per booking; mileage/fuel recorded |
| `MAINTENANCE_RECORD` | S8, S11, X4 | S8 | Legal status transitions; blocking rows sync vehicle status |
| `CUSTOMER_NOTE` | S9 | S9 | Author/timestamp; notes append-only |
| `APP_SETTINGS` | C5, S2, S5, S12, X4 | S12 | Typed values; defaults apply; pricing affects quotes |
| `PSGC_PROVINCES` | C1, C8, S9, X5 | seeds | Reference data |
| `PSGC_CITIES_MUNICIPALITIES` | C1, C8, S9, X5 | seeds | City belongs to province |

### State-machine table

| Entity | Legal transition | Trigger | Illegal transitions |
|---|---|---|---|
| Booking | `pending → confirmed` | `confirm_booking` | Any transition from `completed`/`cancelled` |
| Booking | `pending → cancelled`, `confirmed → cancelled` | `cancel_booking` (reason stored, deposit settled) | `ongoing/completed/cancelled → cancelled` |
| Booking | `pending → ongoing`, `confirmed → ongoing` | `check_in` | `ongoing/completed/cancelled → ongoing` |
| Booking | `ongoing → completed` | `check_out` | `pending/confirmed/completed/cancelled → completed` |
| Booking | `pending/confirmed/ongoing` + new end date | `extend_booking` (repriced) | Extension of `completed`/`cancelled`; conflicting new window |
| Payment | row created `pending`/`paid`/`failed` | `record_payment` | Updating a `refunded` row back to paid |
| Payment | `paid → refunded` | `refund_payment` (reason required) | `pending/failed → refunded`; refund exceeding paid |
| Deposit | held (`deposit`, `paid`) → applied (`deposit_applied`) → returned (`deposit`, `refunded`) | `settle_deposit` | Treating held/applied deposit as revenue; double refund |
| Vehicle | `available → maintenance/retired` | `set_status`, `retire_vehicle` | Retiring a rented vehicle without check-in |
| Vehicle | `rented → available/maintenance` | `mark_available`/`mark_maintenance` | `rented → retired` directly |
| Vehicle | `maintenance → available/rented/retired` | `sync_vehicle_status`, `set_status` | `retired → rented` |
| Vehicle | `retired → available` | `unretire_vehicle` | Any transition from `retired` other than unretire |
| Maintenance | `scheduled → in_progress/completed/cancelled` | start/complete/cancel | `completed/cancelled → anything` |
| Maintenance | `in_progress → completed/cancelled` | complete/cancel | `scheduled → scheduled` duplicate active row |
| User | active ↔ inactive | `set_active` | Deactivating the last active admin |
| User | `must_change_password: true → false` | `change_password`/`force_change_password` | Staff reset for a customer account |
| User | flag set/cleared with reason/audit fields | `set_flag`/`clear_flag` | Silent flag change without reason |

### Missing-surface review

| Surface | Finding |
|---|---|
| `app/ui/dashboard.py` | Legacy/unused page; only `app/ui/hero.py` imports it, and nothing imports `hero.py`. Not audited; no route reaches it. |
| `app/ui/hero.py` | Legacy/unused; only referenced by `app/ui/dashboard.py`. Not audited. |
| `app/staff/booking_panels.py` | Active; imported by `app/staff/pages/bookings.py`. Covered by S3–S6. |
| `app/staff/customer_panels.py` | Active; imported by `pages/customers.py` and `rental_wizard.py`. Covered by S2/S9. |
| `app/staff/rental_wizard.py` | Active; imported by New Rental and the customer/booking/fleet/maintenance panels. Covered by S2. |
| `app/staff/pages/report_panels.py` | Active; imported by `pages/reports.py`. Covered by S11. |
| `app/staff/pages/inspections.py` | Active page without its own flow ID; exercised through S4/S5. |
| `app/staff/dashboard_shots.py`, `new_rental_shots.py`, `icon_check.py`, `icon_preview.py`, `inkcheck.py` | Developer/screenshot tooling; not user-facing and not audited. |
| `app/ui/async_load.py`, `theme.py`, `icons.py`; `app/staff/widgets.py`, `tables.py`, `cards.py`, `charts.py`, `filter_popover.py`, `overlay.py`, `brand_rows.py`, `branding.py`, `section_row.py` | Shared support widgets/styles; exercised indirectly by the flow tests. |

## Findings

| ID | Task | Severity | Flow(s) | Reproduction | Expected | Status | Fix commit | Regression test |
|---|---|---|---|---|---|---|---|---|
| F-001 | 3 | High | X9 | Run `migrate_customer_booking` against SQLite: `ALTER TABLE BOOKING ADD COLUMN payment_method ENUM('cash','card','gcash') NULL` fails with `near "'cash'": syntax error`. | Dialect-appropriate DDL for SQLite (`VARCHAR(16)`). | Fixed | Task 3 | `test_documented_migration_order_matches_models` |
| F-002 | 3 | High | X9, C1 | The documented migration order leaves `PSGC_PROVINCES` and `PSGC_CITIES_MUNICIPALITIES` absent on a legacy database, so sign-up location lookups have no tables. | Create both reference tables idempotently in the customer migration. | Fixed | Task 3 | `test_documented_migration_order_matches_models` |
| F-003 | 3 | Low | X9 | On legacy SQLite, `migrate_guarantor_profile` cannot relax `USERS.license_number` from NOT NULL and exits 1 with instructions. | Document the limitation; MySQL migration path relaxes the column; fresh SQLite matches the model. | Open (Task 24) | — | — |
| F-004 | 4 | Medium | S5, X3 | A completed online booking's receipt shows the internal Booking ID but not the customer-facing `reference_code` quoted at checkout. | Print `Reference: RW-...` when the booking has a reference code. | Fixed | Task 4 | `test_online_booking_reaches_staff_and_completes` |
| F-005 | 7 | High | C5, X1, X4 | A 7-day online car booking uses `pricing.quote`, which ignores `long_term_discount`; online subtotal 17,500 vs counter 15,750 at 2,500/day. | Apply `policy_from_settings` and the domain calculator to the online subtotal, then add fees/VAT. | Open (Task 24) | — | `test_online_booking_applies_long_term_discount` |
| F-006 | 7 | High | C5, X1, X4 | A 2-day online motorcycle booking ignores `moto_multiplier`; online subtotal 5,000 vs counter 3,500 at 2,500/day. | Apply the motorcycle multiplier in the online quote. | Open (Task 24) | — | `test_online_booking_applies_motorcycle_multiplier` |
| F-007 | 8 | Medium | C7 | A `deposit_applied` ledger row appears in the customer payment history as the fallback label "Payment" with method "Cash at the counter", implying cash changed hands. | Label it "Deposit applied" and describe it as an applied deposit, not cash. | Open (Task 24) | — | `test_customer_payment_history_labels_applied_deposit` |
| F-008 | 11 | Medium | S3, X1 | The staff Bookings search box advertises "Search ref, customer, plate…" but `_matches` never checked `booking.reference_code`, so the customer-facing `RW-...` reference quoted at the counter returned no rows. | Include the reference code in the search haystacks. | Fixed | Task 11 | `test_search_matches_the_customer_facing_reference` |
| F-009 | 11 | Medium | S3, X1 | The staff booking details panel showed only the date range, not the online reference code, pickup/return times, or pickup/return branches, so staff could not reconcile the customer's confirmation. | Show reference, pickup/return datetimes, and branch names when present. | Fixed | Task 11 | `test_details_panel_shows_reference_branches_and_times` |
| F-010 | 12 | High | S4, X5 | `booking_service.check_in` never re-checked the customer: a booking made with a valid licence could be handed over after the licence expired or the account was deactivated/flagged, and a retired vehicle could become `rented` because `fleet_service.mark_rented` does not validate transitions. | Re-check active status, licence number/expiry (today and through the return date), flag override, and refuse maintenance/retired vehicles at check-in. | Fixed | Task 12 | `test_refuses_an_expired_licence_at_pickup`, `test_refuses_an_inactive_customer_at_pickup`, `test_refuses_a_customer_without_a_licence_number`, `test_refuses_a_licence_that_expires_before_the_return`, `test_a_flagged_customer_needs_an_override_note`, `test_refuses_a_retired_vehicle_at_pickup` |
| F-011 | 13 | Low | S5, X2, X3 | `settle_deposit` always wrote the refunded-deposit row with `method="cash"`, even when the deposit was taken by card or GCash, so customer/staff history showed a card refund as cash. | Use the original deposit row's method for the refund. | Fixed | Task 13 | `test_deposit_refund_keeps_the_original_tender` |

## Baseline

- Collected: **1225 tests** (46 test files).
- Baseline full-suite run: **1223 passed, 2 failed, 0 skipped** (exit 1).
- Failures, both expected migration-order issues:
  - `tests/test_migrate_staff_schema.py::TestResultMatchesModels::test_migrated_schema_satisfies_every_model_column` — `NoSuchTableError: BRANCH`.
  - `tests/test_migrate_staff_schema.py::TestResultMatchesModels::test_the_staff_models_work_against_the_migrated_database` — `BOOKING` has no `pickup_dt`.
- Baseline log: `.superpowers/sdd/2026-10-25-final-audit/baseline.log` (local scratch; not committed).
- Command: `$env:QT_QPA_PLATFORM='offscreen'; .venv\Scripts\python.exe -m pytest -q`.

## Task 13 notes: return, checkout, deposit, receipt

- Test command: `$env:QT_QPA_PLATFORM='offscreen'; .venv\Scripts\python.exe -m pytest tests/test_booking_service.py tests/test_receipt_service.py tests/test_migrate_deposit_applied.py tests/test_customer_payments.py -q -o addopts=` → 159 passed.
- Service walk: `.superpowers/sdd/2026-10-25-final-audit/audit_task13.py` against a throwaway SQLite DB → all checks passed.
- Clean return: quote shows no late fee/damage, full deposit refund, nothing due; checkout completes the booking, frees the vehicle, records the post-rental inspection, refunds the deposit; takings and period collections equal the rental only; the deposit is reported as a deposit, not revenue.
- Late + damage within deposit: 2 days late at 1,000/day plus 500 damage = 2,500 penalties; 2,500 deposit applied, 500 refunded; booking settles at zero; `deposit_applied` is a paid ledger row but adds no cash.
- Deposit shortfall: 5,000 damage against a 2,000 deposit leaves 3,000 due; checkout is blocked without settlement; 2,999.99 is refused; exactly 3,000 completes the booking. Revenue report splits rental cash from penalty cash and does not count the 2,000 `deposit_applied`.
- Customer history: rental, deposit, refunded deposit, and `deposit_applied` rows are visible and scoped to the customer.
- Receipt: booking ID, deposit, and settlement text present; the online reference is printed when present.
- Finding: **F-011 Low** — deposit refunds were always recorded as cash; fixed to keep the original tender, with a regression test.

## Task 12 notes: pickup and check-in

- Test command: `$env:QT_QPA_PLATFORM='offscreen'; .venv\Scripts\python.exe -m pytest tests/test_booking_service.py tests/test_rental_life_cycle.py -q -o addopts=` → 110 + 1 passed; `tests/test_staff_pages.py -k "check_in or pickup"` → 3 passed.
- Service walk: `.superpowers/sdd/2026-10-25-final-audit/audit_task12.py` against a throwaway SQLite DB → all checks passed.
- Happy path: a confirmed counter rental with an outstanding balance is refused at pickup (`require_settlement=True`); after payment, check-in records the pre-rental inspection (mileage, fuel, damage, inspector), moves the vehicle to `rented` and the booking to `ongoing`, and the customer sees `ongoing`.
- Refusals: check-in twice, expired licence, missing licence number, licence expiring before the return date, inactive customer, flagged customer without an override (an override note allows it), retired vehicle, workshop vehicle, backwards odometer.
- Finding: **F-010 High** — check-in had no eligibility gate and `mark_rented` bypassed the legal-transition table, so an expired/deactivated/flagged customer or a retired vehicle could be handed over. Fixed in `booking_service.check_in` with six regression tests.

## Task 11 notes: online booking handoff

- Test command: `$env:QT_QPA_PLATFORM='offscreen'; .venv\Scripts\python.exe -m pytest tests/test_customer_flow_audit.py tests/test_staff_pages.py tests/test_booking_service.py -q -o addopts=` → see the Task 11 log; targeted regression tests pass (below).
- Targeted UI regression: `tests/test_staff_pages.py -k "reference or branches_and_times"` → 3 passed.
- Service walk: `.superpowers/sdd/2026-10-25-final-audit/audit_task11.py` against a throwaway SQLite DB → all checks passed.
- Staff list: online booking shows customer, vehicle, dates, `Online` channel, `pending` status, total/paid/owed, and `Unpaid`.
- Search: the customer-facing `RW-...` reference now finds the row (F-008 fixed); cancelled bookings remain searchable.
- Details: the panel now shows the reference, pickup/return datetimes, and pickup/return branch names (F-009 fixed).
- Confirm: staff confirmation is visible to the customer through `bookings_for` and produces a notification.
- Cancel: reason stored, customer sees `cancelled`, the vehicle window is free again, the held deposit is settled/returned, and rental-kind payments stay recorded (the scheduling fee is non-refundable).
- Findings: **F-008 Medium** (reference search missing) and **F-009 Medium** (details panel omitted reference/times/branches), both fixed with regression tests.

## Task 10 notes: walk-in rental

- Test command: `$env:QT_QPA_PLATFORM='offscreen'; .venv\Scripts\python.exe -m pytest tests/test_counter_rental.py tests/test_counter_service.py tests/test_booking_service.py tests/test_receipt_service.py -q -o addopts=` → 167 passed.
- Wizard UI tests: `tests/test_staff_pages.py -k "wizard or receipt_failure or flag_override or full_pay_books"` → 24 passed.
- Service walk: `.superpowers/sdd/2026-10-25-final-audit/audit_task10.py` against a throwaway SQLite DB → all checks passed.
- Lookup: name/phone/email fragments and normalized licence resolve; a duplicate licence is refused with a search-instead message.
- Walk-in: quote 4 days / total 10,000 / deposit 2,000 / fee 100; payment below the fee and above the total refused; GCash reference required when configured; flagged customer gets an override note plus author/timestamp; booking is `walk_in`, `created_by` set, `confirmed`; rental row 4,000 and deposit row 2,000; `amount_paid` counts rental only; takings exclude the deposit; receipt shows customer, booking, and deposit; full payment settles the balance while the deposit stays held.
- Receipt failure: the wizard commits the booking first, then renders the receipt; `test_receipt_failure_keeps_the_booking_and_offers_done` proves a failure is a warning, not a rollback.
- No findings.

## Task 9 notes: staff authentication and first-run admin

- Test command: `$env:QT_QPA_PLATFORM='offscreen'; .venv\Scripts\python.exe -m pytest tests/test_staff_login.py tests/test_staff_shell.py tests/test_staff_settings.py -q -o addopts=` → 112 passed.
- Service walk: `.superpowers/sdd/2026-10-25-final-audit/audit_task9.py` against a throwaway SQLite DB → all checks passed.
- Verified: empty database has no users; first admin is `admin`/active/hashed and does not force a change; a second first-run admin is refused; first-run email and password policy enforced; `create_staff` sets `must_change_password`; the temporary password signs in once, the forced change clears the flag and invalidates it; temporary passwords obey the policy; deactivation is stored; the last active admin cannot be deactivated; the shared credential check returns a customer row and `LoginView` is the role gate that refuses it (covered by `test_staff_shell.py::TestRoleGate`).
- Admin-only pages: `PageSpec(..., admin_only=True)` and `StaffContext.require_admin` are covered by `test_staff_shell.py`; sign-out and idle logout are covered there too.
- No findings.

## Task 8 notes: customer self-service and session

- Test command: `$env:QT_QPA_PLATFORM='offscreen'; .venv\Scripts\python.exe -m pytest tests/test_customer_payments.py tests/test_customer_profile.py tests/test_customer_notifications.py tests/test_customer_account_popup.py tests/test_customer_flow_audit.py -q -o addopts=` → 34 passed.
- Service walk: `.superpowers/sdd/2026-10-25-final-audit/audit_task8.py` against a throwaway SQLite DB.
- Passed: two-customer isolation for bookings, payments, notifications, and profile counts; all status buckets (`upcoming` = pending/confirmed/ongoing, `completed`, `cancelled`); rental + deposit + `deposit_applied` balance math (`amount_paid` 1,100, `deposit_held` 400, `deposit_applied` 100).
- Profile/session: `test_customer_profile.py` covers edits/validation/persistence; `main_window._sign_out` clears `CustomerSession`, member pages, notifications, and returns to Vehicles; `MEMBER_TABS` routes guests through `AuthGateDialog` (code inspection + popup logout test).
- Finding (deferred to Task 24): **F-007 Medium** — `app/ui/payments.py` has no `deposit_applied` label, so the row renders as "Payment" and `_method_label` says "Cash at the counter".

## Task 7 notes: online booking and checkout

- Test command: `$env:QT_QPA_PLATFORM='offscreen'; .venv\Scripts\python.exe -m pytest tests/test_customer_flow_audit.py tests/test_customer_checkout_payment.py tests/test_booking_service.py tests/test_pricing_policy.py -q -o addopts=` → 131 passed.
- Service walk: `.superpowers/sdd/2026-10-25-final-audit/audit_task7.py` against a throwaway SQLite DB.
- Passed: cash/pay-at-pickup stays `pending` with no payment row; online channel; pickup/return datetimes and branches; unique `RW-` reference; `subtotal + insurance + service + vat == total_cost == total`; overlap rejected; outside branch hours rejected; same-day bills one day; card keeps only `last4` and no full number/CVV; GCash reference stored; pending/failed payments do not count; expired licence, inactive customer, staff account, and workshop vehicle rejected.
- Findings (deferred to Task 24, same root cause in `pricing.quote`):
  - **F-005 High:** 7-day online car subtotal 17,500 vs counter 15,750; `long_term_discount` is not applied online.
  - **F-006 High:** 2-day online motorcycle subtotal 5,000 vs counter 3,500; `moto_multiplier` is not applied online.
- No blocking finding: the online flow completes, but the money is wrong for long-term/motorcycle rentals.

## Task 6 notes: discovery, vehicle details, media

- Test command: `$env:QT_QPA_PLATFORM='offscreen'; .venv\Scripts\python.exe -m pytest tests/test_customer_flow_audit.py tests/test_media_warning.py tests/test_security_hardening.py -q` → 51 passed.
- Service walk: `.superpowers/sdd/2026-10-25-final-audit/audit_task6.py` against a throwaway SQLite DB → all checks passed.
- Discovery: only `available` vehicles appear; search excludes workshop/retired/rented vehicles; make/class/fuel/transmission/seats/rate filters and rate bounds agree; details labels derive from stored values; branch window accepts inside-hours and rejects outside-hours.
- Media: CarImages was not contacted (offline harness); `test_media_warning.py` covers graceful fallback. Cache `_identity` strips path separators and `..`; `_paths` rejects traversal; front/side variants store and load distinct bytes; stats and clear work.
- Environmental note: live CarImages availability was not tested; failures are classified environmental, not product defects.
- No findings.

## Task 5 notes: customer account flow

- Test command: `$env:QT_QPA_PLATFORM='offscreen'; .venv\Scripts\python.exe -m pytest tests/test_customer_auth_ui.py tests/test_signup_location.py tests/test_customer_password_reset.py tests/test_customer_flow_audit.py -q` → 60 passed.
- Service/DB walk: `.superpowers/sdd/2026-10-25-final-audit/audit_task5.py` against a throwaway SQLite DB → all checks passed.
- Verified DB row for a valid signup: password hash (not plaintext), `role=customer`, `source=online`, `is_active=True`, PSGC codes, formatted address, no online licence, guarantor fields, emergency contact.
- Rejected: duplicate email, duplicate licence, staff licence collision, invalid mobile, invalid street, incomplete guarantor, city outside province, random province.
- Login/reset: valid login; generic wrong-password message; inactive and staff accounts blocked from customer login; reset does not enumerate accounts; reset enforces the signup policy and changes the hash.
- No findings.

## Task 4 notes: golden path

- Test: `tests/test_final_audit_flow.py::test_online_booking_reaches_staff_and_completes`.
- Evidence: customer creates an online booking (`pending`, `channel=online`, pickup/return datetimes, reference code); staff confirms, checks in, records rental + deposit, checks out; booking `completed`, vehicle `available`; customer payment history sees both kinds; receipt includes the reference; revenue report and dashboard include rental and exclude the held deposit.
- Command: `$env:QT_QPA_PLATFORM='offscreen'; .venv\Scripts\python.exe -m pytest tests/test_final_audit_flow.py -q` → 1 passed.
- F-004 fixed: receipt now prints `Reference: RW-...`.

## Task 3 notes: canonical migration order

1. `python -m scripts.migrate_staff_schema`
2. `python -m scripts.migrate_customer_booking`
3. `python -m scripts.migrate_guarantor_profile`
4. seeds (`seed_data`, `seed_customer_catalog`, `seed_demo_data` as needed)

- `migrate_staff_schema` already adds the guarantor columns, so step 3 is a no-op for columns; its remaining job is relaxing `USERS.license_number` on MySQL.
- `migrate_customer_booking` now creates `BRANCH` and the two PSGC reference tables, then adds the online booking columns. The PSGC tables are created empty; reference rows remain operator data.
- `migrate_customer_booking` now translates MySQL `ENUM(...)` DDL to `VARCHAR(16)` on SQLite (F-001).
- Legacy SQLite cannot relax `USERS.license_number` in place; step 3 reports that and exits 1. Fresh SQLite and the MySQL production path are correct. Recorded as F-003 for Task 24 disposition.

## Open questions

| Question | Owner task | Resolution |
|---|---|---|
| Is there a staff branch assignment, or is branch scope only pickup/return? | Task 21 | Open |
| Is no-show represented as `cancelled`, or is it missing? | Task 14 | Open |
| Are old image-cache keys migrated or invalidated? | Task 22 | Open |
| Does `migrate_guarantor_profile` belong in the canonical migration order? | Task 3 | Resolved: yes, for MySQL `license_number` nullability; its columns are already added by `migrate_staff_schema`. Legacy SQLite reports an in-place limitation (F-003). |
| Is `reserved` ever stored, or is it a dead enum value? | Task 15 | Open |
| Does `fleet_service.mark_rented` bypass `LEGAL_TRANSITIONS` (`available → rented`)? | Task 15 | Open |
| Should the legacy `app/ui/dashboard.py` / `hero.py` be deleted? | Task 24 (backlog) | Open |
