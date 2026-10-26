# Staff UI consistency: Bookings, Fleet, Customers, Maintenance

Date: 2026-10-26
Branch: `ui-improvement`

Reference images (the acceptance target):
`C:\Users\Lily Ann\Downloads\New Ui Booking\` — Bookings page, Booking
details, Cancel booking, Check in, Check out, Extend booking, Record
payment.

## Intent

The counter rental modal now has a settled visual language: warm paper
background, white 18px cards, ink primary actions, cream summary panels,
status chips, and a calm two-column modal. The daily list pages and the
booking action modals still use the earlier 28px panel / 8px control /
dropdown-filter recipe.

This work carries the new language into the counter's working surfaces
without changing how any of them behaves:

- the Bookings list and its six action modals;
- the Fleet, Customers, and Maintenance list pages.

Dashboard, Reports/Analytics, Settings, the login screen, and the sidebar
are explicitly out of scope.

## Goals

- One page recipe for Bookings, Fleet, Customers, and Maintenance:
  page title + inline count, white tools card (search, select filters,
  date range, clear), a chip row for status, a cream selection/action
  bar, and a white table card with a warm header and comfortable rows.
- Bookings matches the new reference closely: Ref, Customer, Vehicle,
  Dates, Status, Payment, Total due, Paid, Owed, Channel, Booked; status
  and payment chips; red owed figures; selected-row tint; footer count.
- The six booking modals match the reference's 540px card: eyebrow,
  title, booking subtitle, summary tiles, chip selectors, cream notices,
  and a sticky footer with Back plus one primary action. Cancel booking
  uses the existing destructive red.
- All existing workflows, permissions, keyboard/selection behaviour,
  async loading, deep links, and service validation stay intact.
- The new styling is opt-in, so out-of-scope pages keep their current
  look exactly.

## Non-goals

- No new business rules, pricing changes, or payment kinds. The reference
  offers Rental / Deposit / Penalty; this app supports Rental and Deposit
  only, so the penalty chip is omitted.
- No new windows, no replacement of `QTableWidget`, no rewrite of the
  worker/load model.
- No Dashboard, Reports, Settings, login, or sidebar restyle.
- No new dependencies or fonts.

## Constraints and compatibility

- Existing tests and callers reach into page objects by name. Keep
  `_tools_panel`, `_data_panel`, `search`, `clear_button`, `status_combo`,
  `channel_combo` / `licence_combo` / `type_combo`, `count_caption`,
  `_action_buttons`, the named action buttons, `table`, and
  `selected_booking_id()` working. Hidden compatibility controls are
  acceptable where the reference replaces a dropdown with chips.
- Bookings row data must keep Ref in column 0. The reference order keeps
  Paid at index 7 and Owed at index 8, so those existing assertions stay
  valid; only the total/status/payment/channel/booked indexes move.
- Booking mopanel attributes used by tests stay: `_body`, `_error`,
  `preview`, `kind_combo`, `method_combo`, `amount_edit`, `ref_edit`,
  `note_edit`, `mileage_edit`, `fuel_combo`, `damage_edit`, `charge_edit`,
  `reason_edit`, `go_button`, `collect_button`, `print_button`,
  `close_button`, `_on_go`, `_on_print`.
- `FrostedOverlay` gains an optional card width; its default stays 640
  for existing callers. Booking modals ask for 540.
- New QSS selectors are scoped by new object names or a `variant`
  property. `QFrame#pagePanel` without the property must render exactly
  as it does today.
- No test run is required during implementation. Finish the code, then
  run the focused suites and the full suite once, per the user's
  instruction.

## Approaches considered

1. **Shared UI kit + opt-in variants (chosen).** Add
   `app/staff/ui_kit.py` with the page header, warm panel, status chip
   row, selection bar, metric tile, chip group, and 540px modal shell.
   Teach `LoadStateTable` a `variant="warm"` plus optional row height and
   cell renderers. Each page adopts the kit; out-of-scope pages keep the
   old defaults. Trade-off: one new shared module and a small table
   extension, but no duplication and no global regressions.
2. **Duplicate the components per page.** Faster for one page, but the
   four surfaces would drift immediately and the modals would need their
   own copies. Rejected.
3. **Restyle globally in QSS.** Fastest to type, but it would change
   Settings, Reports, and Dashboard, violating the scope. Rejected.

## What gets built

### Shared kit

`app/staff/ui_kit.py`:

- `PageHeading` — title and inline subtitle/count; sits on the page
  background, not in a card.
- `WarmPanel` — `QFrame` with `objectName="pagePanel"` and
  `variant="warm"`; 18px radius, white fill.
- `StatusChipRow` / `StatusChip` — single-select checkable chips mapped
  to the page's existing status codes. A hidden or off-screen combo stays
  in sync for compatibility.
- `SelectionBar` — cream rounded bar with a summary label and action
  slot; shows an empty prompt when nothing is selected.
- `MetricTile` — cream label/value tile (used by the modals and the
  Bookings detail).
- `ChipGroup` — single-select chips with an optional hidden combo/value
  setter.
- `BookingModal` — 540px white sheet with eyebrow, title, subtitle,
  divider, body, error slot, and sticky footer. Supports a destructive
  primary style.
- small helpers for section headings and row rules.

`LoadStateTable` gains:

- `variant="warm"` — warm header background, 18px card corners, 56px
  rows, horizontal separators only, cream selected row;
- `row_height` and optional per-column renderers so Bookings can draw
  two-line vehicle/date cells and chips without changing its loader.

### Bookings page

- Header: `Bookings` with `Latest N bookings · showing M` inline.
- Tools card: search, channel select, From/To dates, `Overdue only`,
  `Clear`; status chips below (All, Pending, Confirmed, Checked in,
  Checked out, Cancelled). The old `status_combo` remains as the
  compatibility bridge and is driven by the chips.
- Selection bar: selected `#ref · customer · vehicle` on the left;
  Record payment (dark), Check in, Extend, Details (white), Cancel
  booking (red outline). Existing Confirm/Check out buttons remain in
  the map and are shown disabled when they do not apply, preserving the
  current action rules.
- Table: reference column order and chips; row tint on selection; red
  owed when positive; footer `1–N of N` plus a Load more that raises the
  local cap in steps of 50 (up to 500) without changing search-before-cap
  behaviour.
- Skeleton and empty/error states keep working, restyled to the warm
  panels.

### Booking modals

All six move from `_StepBase` to `BookingModal` (540px) while keeping
their signals and fields:

- **Booking details** — BOOKING eyebrow, `#id · customer`, dates/days/
  channel, status chip, Total/Paid/Owed tiles, Customer and Vehicle
  columns, cream deposit/payment notice, Print receipt + Close.
- **Cancel booking** — CANCEL BOOKING eyebrow, title, subtitle, reason
  chips (Customer request, No-show, Vehicle unavailable, Payment issue,
  Other) plus optional details, Paid so far / Refund due summary, danger
  notice, Keep booking + Cancel booking.
- **Check in** — CHECK IN eyebrow, `Release the vehicle`, odometer,
  fuel chips (Empty/¼/½/¾/Full), damage chips + detail, outstanding
  balance notice and Record payment first, Back + Check in.
- **Check out** — CHECK OUT eyebrow, `Take the vehicle back`, pickup/
  distance tiles, return odometer with floor hint, fuel chips, damage
  notes + charge, green deposit notice, Back + Check out.
- **Extend booking** — EXTEND BOOKING eyebrow, current/new return,
  +1 day/+2 days/+3 days/+1 week chips, availability notice, Was/Now/
  Extra to collect summary, Back + Extend to <date>.
- **Record payment** — RECORD PAYMENT eyebrow, `Take money for this
  booking`, Total/Paid/Owed tiles, Rental/Deposit chips, amount,
  Full balance/Half/Custom chips, Cash/GCash/Card chips, reference and
  note, Back + Record ₱X.

The page passes the selected booking's customer, vehicle, and balance into
the modal so the headers and tiles are real, not placeholders.

### Fleet, Customers, Maintenance

Each page gets the same header, white tools card, status chip row, cream
selection bar, and warm table. Their existing filters, columns, actions,
permissions, loading, and selection rules stay unchanged; only the
presentation and control shapes change. Maintenance keeps its two tables
(due and log) in the warm style.

## Testing and verification

Implementation finishes with:

- focused tests for the shared kit, page variants, chip sync, selection
  bar, 540px modals, each modal's preserved fields, and the booking
  column order;
- the existing staff-page suites (bookings, fleet, customers,
  maintenance, dashboard/settings/reports to prove they did not change);
- a full suite run;
- screenshots from the demo database at 1440×1000 for the Bookings page,
  its six modals, and the three other pages, compared with the reference
  images.

## Builder's calls

- The reference's Penalty chip is omitted because the service supports
  only rental and deposit payments.
- The reference's `Load more` is implemented as a local cap increase so
  the existing search-before-cap guarantee is preserved. The footer is
  `Showing N of latest M`, not a total-count query; the header count
  tracks the same cap. Load more appears only while the cap has not been
  reached and N equals the cap.
- The selection bar keeps the existing action set (including Confirm and
  Check out) rather than hiding actions, so role/status rules stay
  visible and testable. The tools container remains the single
  `_tools_panel` host; the white filter card and cream selection bar are
  its children, so existing containment assertions keep working.
- The eyebrow labels use the reference wording even where the page title
  already repeats it.
- **Booking details keeps every existing fact.** Reference code, pickup/
  return times, branches, Money, Inspections, and Penalties stay below
  the new summary tiles, inside the scrollable modal body. Nothing is
  dropped to fit 540px.
- **Existing tests are not frozen at the visual-structure level.** The
  named attributes, signals, cell indexes, and user-visible behaviour
  stay. Assertions that only pin the old containment (for example
  `open_button` being a direct tools child or details labels being
  direct `_body` children) may be updated to query the new structure;
  behaviour assertions must not be weakened.
- **Damage chips are note presets.** `No damage` clears the notes.
  `Scratch`, `Dent`, and `Cracked glass` toggle words in a comma-separated
  `damage_edit` value; the free-text detail remains the last word. Only
  the existing `damage_notes` field is written.
- **Cancel reasons stay one string.** The selected chip is prefixed to
  the detail: `"Customer request — <detail>"`. `Other` requires a detail;
  the other chips may be used alone. The fallback free-text `reason_edit`
  still satisfies the old contract.
- **Cancel money is cash in / cash back.** Paid so far is
  `amount_paid + deposit_held`; refund due is `deposit_held`, matching
  `cancel_booking` (deposit refunded, rental-kind money retained).
- **Filter chips by page:** Bookings status, Fleet status, Customers
  licence state (All / Valid / Expiring / Expired), Maintenance status.
  Maintenance keeps its type dropdown; Customers keeps a hidden
  `licence_combo` bridge; Bookings/Fleet keep hidden `status_combo`
  bridges.
- **Settlement uses the payment modal shell but not the kind chips.**
  `PaymentPanel(show_kind=False)` gets the 540px shell, Total/Paid/Owed
  tiles, method/amount/reference, and the checkout heading; the clerk
  cannot accidentally record a deposit during checkout.
