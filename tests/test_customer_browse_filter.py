"""Customer browse/filter: snapshot helpers, filter bar, and home wiring.

The customer app keeps the whole available showroom in memory after one
snapshot read. These tests pin the pure filtering over that snapshot and the
home page's Browse & Filter bar -- no database, no network, offscreen Qt only.
"""

from __future__ import annotations

import os
from contextlib import nullcontext
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from app.services import vehicle_service  # noqa: E402
from app.services.vehicle_photos import VehicleRef  # noqa: E402
from app.ui import home as home_ui  # noqa: E402
from app.ui.home import BrowseFilterBar, BrowseFilters, HomePage  # noqa: E402
from app.ui.vehicle_photo import VehiclePhoto  # noqa: E402


# --------------------------------------------------------------------------
# Snapshot helpers
# --------------------------------------------------------------------------


def _shop(
    vehicle_id: int,
    make: str = "Toyota",
    model: str = "Vios",
    *,
    year: int = 2022,
    rate: str = "2500.00",
    body: str = "Sedan",
    vehicle_class: str = "small",
    category: str | None = "Car",
) -> vehicle_service.ShopVehicle:
    return vehicle_service.ShopVehicle(
        vehicle_id=vehicle_id,
        make=make,
        model=model,
        year=year,
        name=f"{make} {model}".strip(),
        meta=body,
        rate=Decimal(rate),
        status="available",
        body=body,
        vehicle_class=vehicle_class,
        seats=5,
        transmission="Automatic",
        fuel_type="Petrol",
        category=category,
        plate_number=f"TST-{vehicle_id:03d}",
        photo_ref=VehicleRef(make=make, model=model, year=year),
    )


@pytest.fixture
def snapshot() -> list[vehicle_service.ShopVehicle]:
    return [
        _shop(1, "Toyota", "Vios", rate="2000.00"),
        _shop(2, "Toyota", "Innova", rate="3000.00", body="MPV"),
        _shop(3, "Honda", "Civic", rate="3500.00"),
        _shop(
            4,
            "Honda",
            "Click 125i",
            rate="1000.00",
            body="Scooter",
            vehicle_class="motorcycle",
            category="Motorcycle",
        ),
        _shop(5, "", "Nameless", rate="500.00"),
        _shop(6, "   ", "Blank", rate="700.00"),
    ]


def _ids(items) -> list[int]:
    return [item.vehicle_id for item in items]


def test_shop_makes_is_sorted_unique_and_skips_blank(snapshot):
    assert vehicle_service.shop_makes(snapshot) == ["Honda", "Toyota"]
    assert vehicle_service.shop_makes([]) == []


def test_shop_rate_bounds_returns_the_extremes(snapshot):
    assert vehicle_service.shop_rate_bounds(snapshot) == (
        Decimal("500.00"),
        Decimal("3500.00"),
    )
    assert vehicle_service.shop_rate_bounds([]) == (Decimal("0"), Decimal("0"))


def test_filter_shop_snapshot_by_make(snapshot):
    assert _ids(vehicle_service.filter_shop_snapshot(snapshot, make="Toyota")) == [
        1,
        2,
    ]
    assert vehicle_service.filter_shop_snapshot(snapshot, make="Nope") == []


def test_filter_shop_snapshot_price_bounds_are_inclusive(snapshot):
    assert _ids(
        vehicle_service.filter_shop_snapshot(
            snapshot, min_rate=2000, max_rate=3000
        )
    ) == [1, 2]
    # Both ends include a row sitting exactly on the limit.
    assert _ids(vehicle_service.filter_shop_snapshot(snapshot, min_rate=3000)) == [
        2,
        3,
    ]
    assert _ids(vehicle_service.filter_shop_snapshot(snapshot, max_rate=1000)) == [
        4,
        5,
        6,
    ]


def test_filter_shop_snapshot_zero_and_none_mean_no_limit(snapshot):
    assert _ids(vehicle_service.filter_shop_snapshot(snapshot)) == [1, 2, 3, 4, 5, 6]
    assert _ids(
        vehicle_service.filter_shop_snapshot(snapshot, min_rate=0, max_rate=0)
    ) == [1, 2, 3, 4, 5, 6]
    assert _ids(
        vehicle_service.filter_shop_snapshot(snapshot, min_rate=None, max_rate=None)
    ) == [1, 2, 3, 4, 5, 6]


def test_filter_shop_snapshot_combines_make_and_price(snapshot):
    assert _ids(
        vehicle_service.filter_shop_snapshot(
            snapshot, make="Toyota", min_rate=2500, max_rate=3000
        )
    ) == [2]


# --------------------------------------------------------------------------
# The filter bar
# --------------------------------------------------------------------------


def test_browse_filter_bar_reports_values_and_resets(qapp):
    bar = BrowseFilterBar()
    bar.set_makes(["Honda", "Toyota"])
    try:
        assert [
            bar.brand_combo.itemText(index)
            for index in range(bar.brand_combo.count())
        ] == ["All brands", "Honda", "Toyota"]
        assert bar.caption.text() == "BROWSE & FILTER"
        assert bar.min_rate.text() == "Any"

        bar.brand_combo.setCurrentIndex(bar.brand_combo.findText("Toyota"))
        bar.min_rate.setValue(1200)
        bar.max_rate.setValue(3400)
        assert bar.min_rate.text() == "\u20b1 1,200 / day"

        emitted: list[BrowseFilters] = []
        bar.browse_requested.connect(emitted.append)
        bar.browse_button.click()

        expected = BrowseFilters(make="Toyota", min_rate=1200, max_rate=3400)
        assert bar.filters() == expected
        assert emitted == [expected]

        # "Any" is the zero value and means no bound to the service.
        bar.min_rate.setValue(0)
        bar.max_rate.setValue(0)
        assert bar.filters() == BrowseFilters(make="Toyota")

        bar.reset_button.click()
        assert bar.filters() == BrowseFilters()
        assert bar.brand_combo.currentText() == "All brands"
        assert bar.min_rate.value() == 0
        assert bar.max_rate.value() == 0
    finally:
        bar.deleteLater()


def test_browse_filter_bar_rejects_an_inverted_range(qapp):
    bar = BrowseFilterBar()
    bar.set_makes(["Toyota"])
    try:
        bar.brand_combo.setCurrentIndex(bar.brand_combo.findText("Toyota"))
        bar.min_rate.setValue(5000)
        bar.max_rate.setValue(1000)

        emitted: list[BrowseFilters] = []
        bar.browse_requested.connect(emitted.append)
        bar.browse_button.click()

        assert emitted == []
        assert not bar.error.isHidden()
        assert "minimum" in bar.error.text().lower()

        # Browse all ignores the range and keeps working.
        all_requested: list[bool] = []
        bar.browse_all_requested.connect(lambda: all_requested.append(True))
        bar.browse_all_button.click()
        assert all_requested == [True]
    finally:
        bar.deleteLater()


# --------------------------------------------------------------------------
# HomePage wiring
# --------------------------------------------------------------------------


@pytest.fixture
def home(qapp, monkeypatch, snapshot) -> HomePage:
    """A HomePage over the fixture snapshot, with no database or photo work."""
    monkeypatch.setattr(home_ui, "SessionLocal", lambda: nullcontext(object()))
    monkeypatch.setattr(
        vehicle_service, "shop_snapshot", lambda session: list(snapshot)
    )
    monkeypatch.setattr(home_ui, "warm_thumbnail", lambda ref: None)
    monkeypatch.setattr(VehiclePhoto, "set_vehicle", lambda self, ref: None)

    page = HomePage()
    yield page
    page.deleteLater()
    qapp.processEvents()


def _capture_open_listing(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def _open(self, title, items, *, empty_text, summary=""):
        calls.append(
            {
                "title": title,
                "items": list(items),
                "empty_text": empty_text,
                "summary": summary,
            }
        )

    monkeypatch.setattr(home_ui.CatalogPage, "open_listing", _open)
    return calls


def test_home_shows_the_browse_bar_between_hero_and_chips(home):
    body = home.dashboard.widget().layout()
    widgets = [body.itemAt(index).widget() for index in range(body.count())]
    assert widgets.index(home.hero) < widgets.index(home.browse_filter)
    assert widgets.index(home.browse_filter) < widgets.index(home.chips)
    assert not home.browse_filter.isHidden()
    assert [
        home.browse_filter.brand_combo.itemText(index)
        for index in range(home.browse_filter.brand_combo.count())
    ] == ["All brands", "Honda", "Toyota"]


def test_home_browse_all_opens_the_full_snapshot(home, monkeypatch, snapshot):
    calls = _capture_open_listing(monkeypatch)

    home.browse_filter.browse_all_button.click()

    assert len(calls) == 1
    assert calls[0]["title"] == "All vehicles"
    assert calls[0]["items"] == list(snapshot)
    assert calls[0]["summary"] == ""
    assert home._stack.currentWidget() is home.catalog


def test_home_browse_applies_brand_and_inclusive_price(home, monkeypatch):
    calls = _capture_open_listing(monkeypatch)
    bar = home.browse_filter

    bar.brand_combo.setCurrentIndex(bar.brand_combo.findText("Toyota"))
    bar.min_rate.setValue(2500)
    bar.max_rate.setValue(3000)
    bar.browse_button.click()

    assert len(calls) == 1
    assert calls[0]["title"] == "Browse vehicles"
    assert _ids(calls[0]["items"]) == [2]
    assert calls[0]["empty_text"]
    assert "Toyota" in calls[0]["summary"]
    assert "2,500" in calls[0]["summary"]
    assert "3,000" in calls[0]["summary"]
    assert home._stack.currentWidget() is home.catalog


def test_home_browse_without_matches_uses_the_filtered_empty_text(
    home, monkeypatch
):
    calls = _capture_open_listing(monkeypatch)
    bar = home.browse_filter

    bar.brand_combo.setCurrentIndex(bar.brand_combo.findText("Honda"))
    bar.min_rate.setValue(100_000)
    bar.browse_button.click()

    assert len(calls) == 1
    assert calls[0]["title"] == "Browse vehicles"
    assert calls[0]["items"] == []
    assert "No vehicles match" in calls[0]["empty_text"]


def test_home_inverted_range_does_not_open_the_catalog(home, monkeypatch):
    calls = _capture_open_listing(monkeypatch)
    bar = home.browse_filter

    bar.min_rate.setValue(4000)
    bar.max_rate.setValue(1000)
    bar.browse_button.click()

    assert calls == []
    assert not bar.error.isHidden()
    assert home._stack.currentWidget() is home.dashboard


def test_home_search_and_type_chips_still_use_the_whole_snapshot(
    home, monkeypatch
):
    calls = _capture_open_listing(monkeypatch)

    home.set_search("Civic")
    assert len(calls) == 1
    assert calls[0]["title"] == "Search results"
    assert _ids(calls[0]["items"]) == [3]

    calls.clear()
    all_chip = next(
        button for button in home.chips._buttons if button.text() == "All vehicles"
    )
    all_chip.click()
    assert len(calls) == 1
    assert calls[0]["title"] == "All vehicles"
    assert _ids(calls[0]["items"]) == [1, 2, 3, 4, 5, 6]
