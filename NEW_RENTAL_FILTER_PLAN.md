# New Rental: filter dropdown, muting, continuous scrolling

Source of truth for this page. Read this after a context compaction instead of
re-deriving the design.

---

## 1. The shape

A **pinned top bar** plus **one** popover. Not a permanent left rail -- an earlier
build shipped a rail and it was rejected.

**Pinned top bar**, outside the scroll area so it never scrolls away:

```
[ search: make / model / plate ]  [ Filters v ]  [ 21 of 21 ]  [ Clear all ]
```

The count is `matched of total`, so the bar answers "how much did that last click
narrow this?" without the clerk subtracting in their head. Unfiltered the two are
equal and it collapses to the fleet size. This is the **only** vehicle count in
the UI -- it lives in the bar, never beside a brand.

**Popover**, anchored under the `Filters` button. Two columns:

```
+--------------------+----------------------------------+
| Brand              | [ search brands ]          <- Brand |
| Vehicle type       |  Toyota      v                    |
| Engine size        |  Honda                            |
| Price range        |  Ford                             |
| Passengers         |  ...                              |
| Transmission       +----------------------------------+
| Fuel type          |  matched count .  Clear all         |
+--------------------+----------------------------------+
```

- Left column = the filter **categories**. Click one, the right column swaps to
  that category's options.
- Right column = the options for the selected category.
- The **Brand** pane additionally has a small search bar, because the brand list
  is the only one long enough to need one.
- Filters still **combine**: SUV + automatic + under 4000 is a valid selection.
- Live updates. There is **no Apply button**.

### Styling: white only

Copied from the dashboard card, `staff.qss:534`:

```css
QFrame[class="card"] { border-radius: 28px; border: 1px solid #D8CCBB; background: #FFFFFF; }
```

White background, no tint, on the popover **and** on the left column. Separation
comes from the border and the `ActiveRow` ink bar, not from a fill colour.

---

## 2. No "N vehicles" text

Per-brand vehicle counts are **removed everywhere** and left as a comment. Brand
headings read `Toyota`, not `Toyota 24`.

Removed from `app/staff/brand_rows.py` (`BrandSection.count` and its
`head.addWidget(self.count)`). Do not reintroduce it.

---

## 3. Muted axes

An axis **greys out** when no value in it can produce a result under the *other*
axes' current selections.

Example: pick `Toyota` + `suv` -> zero motorcycles in the result -> **Engine
size** greys out and stops accepting clicks.

Rules:

- **Availability ignores that axis' own selection.** Otherwise picking
  `Motorcycle` instantly mutes CC, because nothing yet matches *both*, and the
  400cc bucket could never be reached.
- **Values inside a live axis mute individually.** Only the brands that still
  exist under the current type stay clickable.
- **Selections inside a muted axis are preserved, never cleared.** This is the
  existing behaviour from `filter_rail.py::_sync_cc_enabled`, generalised.
- **The axis the user just touched is never muted.** With a fully contradictory
  set (0 results) every axis would otherwise grey out at once and the panel would
  be a dead end. The last-touched axis stays live.
- `Clear all` re-enables everything.

Muting is derived fresh each refresh from the current filter, never stored, so
there is no state to oscillate.

---

## 4. Service layer

`app/services/vehicle_service.py`:

```python
def axis_availability(session, filters, *, exclude: str | None = None) -> dict[str, set]:
    """Distinct values reachable per axis, ignoring that axis' own selection."""
```

One `GROUP BY` per axis with the other axes applied. Called from the **same
debounced live refresh** as the count -- never on every keystroke.

- Axes: `makes`, `vehicle_classes`, `cc_buckets`, `transmissions`, `fuel_types`,
  `seats`.
- **Every** axis is asked with its own selection dropped from the query, spared
  axis included. That drop is what stops `Motorcycle` immediately greying the
  bucket it is about to be used to pick.
- Price is numeric, so it has no discrete values and is **narrowed, never
  muted**: the ceiling pulls down to the dearest reachable rate. The floor is
  left exactly as the clerk left it, because a `QSpinBox` cannot express
  "unset" -- clamping its floor up to the cheapest reachable rate would invent a
  minimum they never chose and read back as a real filter. The two are
  cross-corrected instead, so a floor above the new ceiling is pulled down to it
  rather than left describing a range nothing can match.
- `exclude` is the last-touched axis, exempted from **muting only**. It does not
  change any query, and it is also explicitly re-enabled rather than merely left
  alone, so an axis in use never still reads as muted from an earlier selection.
- An axis **missing** from the answer is left alone. A set means reachable
  values, an empty set means provably nothing left, and an absent key means the
  caller had no answer -- which is not the same as an answer of "nothing".
- A **failed** availability refresh calls `clear_availability()`, which forces
  every axis live. A grey that outlives its reason is a dead end the clerk
  cannot see their way out of.

`VehicleFilter` gains a `makes: frozenset[str]` field. Brand is a real filter
axis now, not only a jump list -- required by the muting example in section 3.

### The Brand pane is the whole fleet, not what has been scrolled into view

This was the one bug the design invited, because Brand started life as a jump
list. Driving the pane from the built sections meant that ticking `Motorcycle`
dropped every car brand from the picker -- removing the very control that could
undo the tick. A filter axis must never narrow its own list.

So `NewRentalPage.refresh()` sets the pane from `vehicle_brands(session)` once,
and `_load_page()` no longer touches it. Ticking a brand reloads the grid, which
builds that brand's section, and a brand whose section is not built yet simply
has nothing to scroll to -- the reload brings it in.

### The Brand pane's rows are ticks, and the name is a second hit target

Each brand is a checkbox plus an `ActiveRow` carrying the same name, and the row
**toggles the tick** rather than navigating. The row stays clickable while the
checkbox is muted, which is the whole point: a brand greyed by the clerk's own
selection has to be untakeable. A disabled checkbox would leave them stuck on it.

Selection and highlighting are separate signals (`brand_toggled` vs `set_active`)
because they are separate facts. `section_activated` was removed rather than kept
dead: with Brand a filter, nothing navigates to a section any more.

---

## 5. The crash that must not come back

`app/staff/pages/new_rental.py:266` (removed build) overrode
`QMenu.exec(self, pos=None)` but called `super().exec()` with **no arguments**.
PySide6's `QMenu.exec` requires a position. Zero args is an access violation, not
a `TypeError`, so the process died instead of raising something catchable.

Observed: `exit=-1073741819` (`0xC0000005`).

| Trial | Result |
|---|---|
| `p.exec(QPoint(10, 10))` | **crash, 0xC0000005** |
| plain `QMenu` + `exec(pos)` | fine |
| `QVBoxLayout` installed on a `QMenu` + `exec(pos)` | fine |
| `QMenu` subclass whose override calls `super().exec()` (no args) | **crash** |

So it is specifically the zero-arg super call. The popover is a plain `QFrame`
with `Qt.Popup`, positioned with `mapToGlobal()`, shown via `show()`. **No `QMenu`,
no `exec()` override, ever.**

A regression test asserts that opening the popover and ticking a box leaves the
process alive and the popover still open -- with `QTest.mouseClick`, not
`setChecked`. That distinction is the whole test: a programmatic tick delivers no
mouse event at all, so the original test was green while the panel still shut on
every real click. A second test asserts the page's
`WA_ShowWithoutActivating` is still set, so dropping the mitigation cannot quietly
turn the click test into a tautology.

---

## 6. Schema (unchanged, already done)

`app/models/vehicle.py`:

```python
vehicle_class = Column(Enum('small','medium','suv','van','pickup','truck','motorcycle'), nullable=True)
engine_cc     = Column(Integer, nullable=True)
```

Both nullable: an existing row must not break the model import, and a car
genuinely has no displacement.

`scripts/migrate_vehicle_class.py`:

1. Add both columns if absent. DDL is per dialect: `ENUM(...)` is MySQL syntax
   and SQLite rejects it outright, so SQLite gets `VARCHAR` + `CHECK`.
2. Backfill `vehicle_class` from `body_style`.
3. Backfill `engine_cc` for motorcycles by parsing digits out of the model name
   (`Click 125i`->125, `NMAX 155`->155, `Ninja 400`->400, `P200`->200). A number
   outside 50..2000 is not displacement, so `Model 3` stays NULL.
4. Idempotent: safe to run twice, and a hand-corrected row is not overwritten.
5. `--database` is **required**. The project's default `DATABASE_URL` is the live
   Aiven instance, so a migration that ran because nobody passed a flag would be
   a production schema change nobody asked for.

```
python -m scripts.migrate_vehicle_class --database demo.db --dry-run
python -m scripts.migrate_vehicle_class --database demo.db
```

`medium` and `truck` are deliberately absent from the body-style mapping -- no
pre-migration row is either. They come from `scripts/seed_demo_data.py`, which
writes every class explicitly.

`scripts/migrate_staff_schema.py` also creates the two columns. It cannot leave
them out: its own test asserts the migrated schema satisfies every model column,
and every `Vehicle` INSERT names every column.

---

## 7. Highlight mechanism

Reused from the staff dashboard sidebar, as asked.

`app/staff/sidebar.py` `NavItem` is a checkable `QAbstractButton` with
`autoExclusive`, and its `paintEvent` draws the active state: a `theme.INK`
rounded bar, `NAV_ACTIVE_BAR` (3px) wide, inset 4px top and bottom, radius 1.5.
Font weight goes Medium -> DemiBold when active.

Extracted into `app/staff/section_row.py::SectionRow`, so the popover's two
columns and the fleet's section tracker share one active-row treatment. `NavItem`
keeps working unchanged -- the paint moves, not the behaviour.

Used for: the left category column, the Brand pane's rows, and the fleet's
current-section indicator. The highlight **follows the scroll**, so whichever
pane is open keeps pointing at where the user is in the fleet.

A muted axis greys **both** columns: the values beside it, and its row in the
left column. A category whose every option is greyed is itself unusable, and the
clerk should be able to see that before clicking across to it. `muted_axes()`
reads the left column's state, so the tests assert what the clerk sees.

Section offsets come from an arithmetic `_section_offsets()` sum, not
`geometry()`. `geometry()` is not trustworthy before the layout has run, and a
click can arrive in the same frame the section was built -- which is exactly when
the highlight used to land in the wrong place.

---

## 8. Continuous scrolling (unchanged, already working)

- `BRANDS_PER_PAGE = 4`, `LOAD_MORE_PX = 420`.
- `_fill_viewport()` is scheduled with `QTimer.singleShot(0, ...)` after
  `showEvent`, after the layout, and from the debounced scroll handler -- never
  called inline from a layout pass.
- `BrandSection.minimumSizeHint` returns `sizeHint()`, because a section's
  height is fixed (heading over a fixed-height rail) but its `QScrollArea` child
  is designed to shrink. Without this the fleet list never scrolls, so the
  brands past the first are unreachable.
- `SECTION_TRACK_MS = 120` quiet period before the active section is re-read.

---

## 9. Files

| File | Change |
|---|---|
| `app/models/vehicle.py` | + `vehicle_class`, + `engine_cc` (done) |
| `scripts/migrate_vehicle_class.py` | new -- add + backfill (done) |
| `scripts/seed_demo_data.py` | populate both columns (done) |
| `app/staff/section_row.py` | shared active-row paint (done) |
| `app/staff/sidebar.py` | use `SectionRow`'s paint (done) |
| `app/services/vehicle_service.py` | `VehicleFilter.makes`, `axis_availability()` |
| `app/staff/filter_popover.py` | **replaces `filter_rail.py`**, which is deleted |
| `app/staff/brand_rows.py` | per-brand counts removed |
| `app/staff/pages/base.py` | pinned top-bar slot; drop dead `SIDEBAR` machinery |
| `app/staff/pages/new_rental.py` | popover + pinned bar |
| `app/staff/metrics.py`, `theme.py`, `staff.qss` | popover metrics and styles |
| `tests/test_staff_pages.py` | popover, muting, pinned bar tests |
| `tests/test_migrate_vehicle_class.py` | migration tests (done) |

`StaffPage.SIDEBAR` was used by New Rental only, so it is removed with the rail.
It was a generic hook, not a shared contract.

---

## 10. Tests

- opening the popover does not kill the process (the `0xC0000005`)
- **a real `QTest.mouseClick` on a box ticks it and leaves the popover open**,
  twice over -- the old programmatic version passed while the panel still shut
- ticking a brand by its name row selects it, and selects it again to clear it
- a greyed brand can still be unticked by its name
- `Honda` + `motorcycle` greys out Engine size; unticking `Honda` restores it
- a class with no recorded displacement greys out Engine size on its own
- a muted axis keeps its ticks through a real selection
- a Brand pane row greys out when absent under the current type
- the last-touched axis stays live at 0 results
- a failed availability refresh puts every axis back live, including greys left
  by the last successful one
- an axis the caller had no answer for is left live, not muted
- search in the Brand pane narrows the rows; the top-bar search filters the grid
- CC bucket boundaries (125/126, 155/156, 400/401)
- combined price + seats + transmission + fuel
- the price ceiling narrows to the dearest reachable rate, and a floor the clerk
  never touched is not moved
- clear-all restores the unfiltered set and re-enables every axis
- section highlight tracks the scrolled-to section
- migration adds columns, backfills correctly, is idempotent

**Status: 621 passing.** The muting tests deliberately use a fleet that isolates
engine size. `_add_vehicle` leaves `seats`/`transmission`/`fuel_type` NULL, and a
NULL column has no values to offer, so those axes grey out for an unrelated reason
and a test asserting only that *something* is muted would pass while proving
nothing. The seeded branch car also records no `engine_cc`, which is why "picking
`suv` alone" legitimately mutes engine size.

---

## 11. Definition of done

`pytest` green. Staff app launches on `demo.db`. The pinned top bar stays put
while the fleet scrolls. `Filters` opens a white two-column popover; every
category is reachable from the left, the Brand pane has its own search bar,
options combine live, impossible axes grey out without losing their selection,
no "N vehicles" text anywhere, and the app cannot be killed by opening it.