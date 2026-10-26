# Customer Browse + Filter Implementation

**Goal:** Add a clear browse/filter experience to the customer app's Vehicles home page: brand, price range, browse all, and reset.

**Architecture:** Pure in-memory filtering over the existing `vehicle_service.shop_snapshot()` list. No new DB queries, no schema changes. `HomePage` gets a filter bar; the existing `CatalogPage` shows the filtered result.

**Files:**
- Modify: `app/services/vehicle_service.py` — pure helpers `shop_makes`, `shop_rate_bounds`, `filter_shop_snapshot`.
- Modify: `app/ui/home.py` — `BrowseFilterBar` and `HomePage` wiring.
- Modify: `app/ui/vehicles.py` only if the catalog needs a filter summary/back text.
- Test: `tests/test_customer_browse_filter.py` (new).

**Constraints:**
- Customer app only; do not touch staff files.
- Use `Decimal` for rates; UI may use integer pesos.
- TDD, offscreen tests, throwaway SQLite only.
- No live DB.
- Keep existing type chips, hero, search, and shelves working.
- Deadline: 4:30 AM. Prioritize a working, tested filter over unrelated polish.

**Acceptance:**
- Home page shows a visible “Browse & filter” area with:
  - brand selector including “All brands”
  - minimum and maximum daily price inputs (0 = no limit)
  - “Browse vehicles” primary action
  - “Reset” action
- “Browse all vehicles” opens the catalog with the full snapshot.
- Applying filters opens the catalog with only matching make/price rows and a correct count/empty state.
- Invalid range (min > max) shows a clear inline error and does not open the catalog.
- Existing type chips and top-bar search still work.
- Tests cover service filtering and the filter bar's emitted values.
