"""Tests for the staff pages.

Each page is built over a small, hand-made dataset rather than the demo seed,
so a test states exactly the situation it is about. Data is built relative to
`date.today()` because every page asks the clock what day it is -- New Rental,
Fleet and Customers all key off it, and a page that read a fixed date would test
something other than what ships.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.database import Base  # noqa: E402
from app.models import (  # noqa: E402
    Booking,
    Inspection_Report,
    Maintenance_Record,
    Payment,
    Users,
    Vehicle,
    Vehicle_Category,
    Vehicle_Media,
)
from app.staff.context import (  # noqa: E402
    DatabaseSelection,
    DatabaseTarget,
    StaffContext,
    StaffUser,
)
from app.staff import theme  # noqa: E402
from app.staff.metrics import (  # noqa: E402
    FILTER_BAR_H,
    FILTER_POPOVER_H,
    FILTER_POPOVER_W,
    POPOVER_CATEGORY_W,
    SECTION_ROW_H,
    VEHICLE_BTN_RADIUS,
    VEHICLE_CARD_H,
    VEHICLE_CARD_MIN_W,
    VEHICLE_CARD_RADIUS,
    VEHICLE_IMAGE_RADIUS,
)
from app.staff.pages.base import StaffPage  # noqa: E402
from app.staff.pages.bookings import COUNTER_ACTIONS, BookingsPage  # noqa: E402
from app.staff.pages.customers import EXPIRING_SOON_DAYS  # noqa: E402
from app.staff.pages.dashboard import DashboardPage, pesos  # noqa: E402
from app.staff.pages.fleet import FleetPage  # noqa: E402
from app.staff.pages.inspections import InspectionsPage  # noqa: E402
from app.staff.pages.new_rental import NewRentalPage  # noqa: E402
from app.staff.pages.payments import PaymentsPage  # noqa: E402
from app.staff.shell import StaffShell  # noqa: E402
from app.staff.tables import LoadState  # noqa: E402
from app.staff.filter_popover import MAX_RATE, FilterPopover  # noqa: E402
from app.staff.section_row import ActiveRow  # noqa: E402
from app.staff.vehicle_cards import (  # noqa: E402
    MediaResolver,
    RentalVehicle,
    VehicleCard,
    VehicleSlot,
)
from app.staff.brand_rows import BrandSection, VehicleRail  # noqa: E402
from app.utils.security import hash_password  # noqa: E402
from PySide6.QtCore import Qt, QPoint, QThreadPool  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402
from app.widgets.skeleton import Skeleton  # noqa: E402


def qapp_process(_widget=None, turns: int = 8) -> None:
    """Pump the event loop so queued layout and builds have settled.

    The page's first section can only be read after the layout has run, and the
    rails stagger their card builds one per turn, so a test that asserts on
    geometry has to let the loop move. A fixed number of turns rather than a
    sleep: the work being waited on is already queued, so it finishes as fast as
    the machine can and there is nothing to gain from a fixed delay.
    """
    app = QApplication.instance()
    for _ in range(turns):
        app.processEvents()

#: Rebuilt per test by `branch`; a run crossing midnight must not go stale.
TODAY = date.today()


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


# --------------------------------------------------------------------------
# A small branch
# --------------------------------------------------------------------------


class Branch:
    """Fixture data, written once so several tests can share one database."""

    def __init__(self, context: StaffContext) -> None:
        self.context = context
        with context.session() as session:
            category = Vehicle_Category(category_name="Car", base_rate_multiplier=1.0)
            session.add(category)
            session.flush()

            self.admin = self._user(
                session,
                full_name="Branch Admin",
                email="admin@example.com",
                role="admin",
                licence=date(2030, 1, 1),
            )
            self.counter = self._user(
                session,
                full_name="Counter Staff",
                email="counter@example.com",
                role="staff",
                licence=date(2030, 1, 1),
            )
            self.customer = self._user(
                session,
                full_name="Ana Reyes",
                email="ana@example.com",
                role="customer",
                licence=TODAY + timedelta(days=400),
            )
            self.expiring = self._user(
                session,
                full_name="Soon Lapsed",
                email="soon@example.com",
                role="customer",
                licence=TODAY + timedelta(days=EXPIRING_SOON_DAYS - 1),
            )
            self.lapsed = self._user(
                session,
                full_name="Already Lapsed",
                email="lapsed@example.com",
                role="customer",
                licence=TODAY - timedelta(days=10),
            )

            self.out_car = self._car(session, category, "OUT-001", status="rented")
            self.due_back_car = self._car(
                session, category, "BACK-001", status="rented"
            )
            self.late_car = self._car(session, category, "LATE-001", status="rented")
            self.free_car = self._car(session, category, "FREE-001")
            self.promised_car = self._car(session, category, "PROM-001")
            self.collecting_car = self._car(session, category, "GO-001")
            self.shop_car = self._car(
                session, category, "SHOP-001", status="maintenance"
            )

            self.out_booking = self._booking(
                session,
                self.customer,
                self.out_car,
                TODAY - timedelta(days=3),
                TODAY + timedelta(days=4),
                status="ongoing",
            )
            self.due_back = self._booking(
                session,
                self.customer,
                self.due_back_car,
                TODAY - timedelta(days=2),
                TODAY,
                status="ongoing",
            )
            self.overdue = self._booking(
                session,
                self.customer,
                self.late_car,
                TODAY - timedelta(days=5),
                TODAY - timedelta(days=2),
                status="ongoing",
            )
            self.upcoming = self._booking(
                session,
                self.customer,
                self.promised_car,
                TODAY + timedelta(days=2),
                TODAY + timedelta(days=4),
                status="confirmed",
            )
            self.collecting = self._booking(
                session,
                self.customer,
                self.collecting_car,
                TODAY,
                TODAY + timedelta(days=3),
                status="confirmed",
            )
            self.finished = self._booking(
                session,
                self.customer,
                self.free_car,
                TODAY - timedelta(days=20),
                TODAY - timedelta(days=15),
                status="completed",
                actual_return_date=TODAY - timedelta(days=15),
                total_cost=Decimal("15000.00"),
            )

            session.add(
                Maintenance_Record(
                    vehicle_id=self.shop_car.vehicle_id,
                    description="Brake pads",
                    start_date=TODAY - timedelta(days=1),
                    end_date=TODAY + timedelta(days=2),
                    cost=Decimal("4500.00"),
                    status="ongoing",
                )
            )

            session.add_all(
                [
                    Payment(
                        booking_id=self.finished.booking_id,
                        amount=Decimal("15000.00"),
                        method="cash",
                    status="paid",
                    paid_at=datetime.combine(
                        TODAY - timedelta(days=15), datetime.min.time()
                    ),
                    recorded_by=self.counter.user_id,
                    created_at=datetime.combine(
                        TODAY - timedelta(days=15), datetime.min.time()
                    ),
                ),
                Payment(
                    booking_id=self.finished.booking_id,
                    amount=Decimal("2500.00"),
                    method="gcash",
                    status="pending",
                    paid_at=None,
                    recorded_by=self.counter.user_id,
                    reference_no="GC-UNPAID",
                    created_at=datetime.combine(TODAY, datetime.min.time()),
                ),
                ]
            )
            session.add(
                Inspection_Report(
                    booking_id=self.out_booking.booking_id,
                    inspection_type="pre-rental",
                    inspected_by=self.counter.user_id,
                    mileage_reading=12000,
                    fuel_level="full",
                    damage_notes=None,
                    photo_url=None,
                    inspected_at=datetime.combine(TODAY - timedelta(days=3), datetime.min.time()),
                )
            )

    @staticmethod
    def _user(
        session, *, full_name, email, role, licence, index=0
    ) -> Users:
        row = Users(
            full_name=full_name,
            email=email,
            password_hash=hash_password("counter-password"),
            address="1 Test Street",
            license_number=f"LIC{abs(hash(email)) % 10**7:07d}",
            license_expiry=licence,
            role=role,
            created_at=datetime(2026, 1, 1, 9, 0),
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def _car(session, category, plate, status="available") -> Vehicle:
        row = Vehicle(
            category_id=category.category_id,
            make="Toyota",
            model="Vios",
            year=2022,
            plate_number=plate,
            daily_rate=Decimal("2500.00"),
            mileage=10_000,
            status=status,
            created_at=datetime(2026, 1, 1, 9, 0),
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def _booking(
        session,
        user,
        vehicle,
        start,
        end,
        *,
        status,
        actual_return_date=None,
        total_cost=Decimal("7500.00"),
    ) -> Booking:
        row = Booking(
            user_id=user.user_id,
            vehicle_id=vehicle.vehicle_id,
            start_date=start,
            end_date=end,
            actual_return_date=actual_return_date,
            total_cost=total_cost,
            status=status,
            created_by=None,
            created_at=datetime(2026, 9, 1, 10, 0),
        )
        session.add(row)
        session.flush()
        return row


@pytest.fixture
def branch(tmp_path):
    """A `StaffContext` over a small branch, signed in as the given role."""
    global TODAY
    # Re-read the clock per test, not per session. See the note on TODAY.
    TODAY = date.today()

    path = tmp_path / "branch.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    engine.dispose()

    context = StaffContext(
        DatabaseSelection(
            url=f"sqlite:///{path}", target=DatabaseTarget.LIVE, label="Test"
        )
    )
    built = Branch(context)
    context.sign_in(StaffUser(built.admin.user_id, "Branch Admin", "admin@example.com", "admin"))
    yield built, context
    context.dispose()


@pytest.fixture
def shell(qapp, branch):
    _built, context = branch
    window = StaffShell(context)
    yield window
    window.deleteLater()
    qapp.processEvents()


def as_staff(branch):
    built, context = branch
    context.sign_in(
        StaffUser(built.counter.user_id, "Counter Staff", "counter@example.com", "staff")
    )


def as_admin(branch):
    """The inverse of `as_staff`, for the tests that compare the two roles."""
    built, context = branch
    context.sign_in(
        StaffUser(built.admin.user_id, "Branch Admin", "admin@example.com", "admin")
    )


def _add_vehicle(
    context,
    *,
    make: str,
    model: str,
    plate: str,
    category_name: str = "Car",
    vehicle_class: str | None = None,
    engine_cc: int | None = None,
    daily_rate: str = "3000.00",
    seats: int | None = None,
    transmission: str | None = None,
    fuel_type: str | None = None,
    status: str = "available",
) -> int:
    """Add one available vehicle, creating its category if the branch lacks it."""
    with context.session() as session:
        category = (
            session.query(Vehicle_Category)
            .filter(Vehicle_Category.category_name == category_name)
            .first()
        )
        if category is None:
            category = Vehicle_Category(
                category_name=category_name, base_rate_multiplier=1.0
            )
            session.add(category)
            session.flush()
        row = Vehicle(
            category_id=category.category_id,
            make=make,
            model=model,
            year=2021,
            plate_number=plate,
            daily_rate=Decimal(daily_rate),
            mileage=1_000,
            status=status,
            vehicle_class=vehicle_class,
            engine_cc=engine_cc,
            seats=seats,
            transmission=transmission,
            fuel_type=fuel_type,
            created_at=datetime(2026, 1, 1, 9, 0),
        )
        session.add(row)
        session.flush()
        return row.vehicle_id


# --------------------------------------------------------------------------
# The role gate on a real page
# --------------------------------------------------------------------------


class TestPageRoleGate:
    def test_a_page_refuses_to_render_for_a_signed_out_shell(self, qapp, branch):
        _built, context = branch
        context.sign_out()
        window = StaffShell(context)
        page = FleetPage(window)
        with pytest.raises(Exception):
            page.refresh()
        window.deleteLater()

    def test_a_customer_identity_cannot_reach_a_page(self, qapp, branch):
        built, context = branch
        context.sign_in(
            StaffUser(built.customer.user_id, "Ana", "ana@example.com", "customer")
        )
        window = StaffShell(context)
        with pytest.raises(Exception):
            FleetPage(window).refresh()
        window.deleteLater()

    def test_staff_may_refresh_every_page(self, shell, branch):
        as_staff(branch)
        for spec in shell.pages:
            assert spec.admin_only is False, spec.key
            page = spec.factory(shell)
            assert isinstance(page, StaffPage)
            page.refresh()  # must not raise


# --------------------------------------------------------------------------
# New Rental
# --------------------------------------------------------------------------


class TestNewRentalFilterPopover:
    """The popover is a widget over no database, so it can be asserted directly."""

    def test_the_filters_live_in_a_popover_not_a_docked_column(self, shell):
        page = NewRentalPage(shell)
        assert page.filter_popover.isVisible() is False
        # Parenthesised deliberately: without them this parses as
        # `assert page.filter_rail if ... else (None is None)`, which passes on a
        # truthy attribute and so would not catch the rail coming back at all.
        assert (hasattr(page, "filter_rail") is False), (
            "the docked rail is gone; `page.filter_rail` must not come back"
        )

    def test_the_popover_is_never_a_qmenu(self, shell):
        page = NewRentalPage(shell)
        # A QMenu anywhere in the page's tree is the shape that crashed: an
        # overridden `exec()` called with no position is an access violation, not
        # a catchable error (`exit=-1073741819`).
        assert page.findChildren(QMenu) == []

    def test_opening_the_popover_does_not_kill_the_process(self, shell):
        page = NewRentalPage(shell)
        page.show()
        page.resize(1440, 900)
        qapp_process(page)
        page.open_popover()
        qapp_process(page)
        # Reaching here at all is the assertion: the old build died in this call.
        assert page.filter_popover.isVisible() is True
        page.close_popover()

    def test_the_popover_toggles(self, shell):
        page = NewRentalPage(shell)
        page.show()
        qapp_process(page)
        page.toggle_popover()
        assert page.filter_popover.isVisible() is True
        page.toggle_popover()
        assert page.filter_popover.isVisible() is False

    def test_a_real_mouse_click_on_a_box_leaves_the_popover_open(self, qapp):
        """The regression the brief is really about.

        Ticking a box inside a `Qt.Popup` closes it on the mouse *release* that
        delivered the click, unless something holds the grab off. Programmatic
        `setChecked` cannot reproduce that, because it delivers no mouse event at
        all -- so the old test passed green while the panel still shut on every
        adjustment a clerk made. `QTest.mouseClick` sends a real press and release.

        Built as a standalone popover configured exactly as `NewRentalPage`
        configures it, rather than through a page. The panel is a top-level window
        and this platform does not map the one a live page parents it to, so a
        click aimed at it goes nowhere and the test would fail for a reason that
        has nothing to do with the grab. The flags and the attribute are what make
        the difference, and both are set here.
        """
        pop = FilterPopover()
        pop.setWindowFlags(Qt.WindowType.Popup)
        # The page's mitigation. Without it a `Qt.Popup` shuts on the release that
        # delivered the click.
        pop.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        pop.set_brands(["Toyota", "Honda"])
        pop.show()
        qapp_process(pop)
        assert pop.isVisible() is True

        # A category row first, the way a clerk uses it -- it is what puts the pane
        # on screen, and Qt discards mouse events for hidden widgets.
        pop._categories["vehicle_classes"].click()
        qapp_process(pop)
        box = pop.type_group.boxes["motorcycle"]
        assert box.isVisible() is True

        # Near the left edge rather than at the centre, which is QTest's default.
        # A widget inside a scroll area can sit outside whatever the offscreen
        # platform has actually mapped, and a click at the centre then lands on
        # nothing at all -- failing for a reason that has nothing to do with the
        # grab. The left edge is over the indicator and is always mapped.
        spot = QPoint(10, box.height() // 2)
        QTest.mouseClick(
            box, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, spot
        )
        qapp_process(pop)

        assert box.isChecked() is True, "the click never reached the box"
        assert pop.isVisible() is True, (
            "the popover must survive the click that adjusted it"
        )

        # Twice over: a grab that is held at all has to survive repeated use, not
        # just the first adjustment.
        QTest.mouseClick(
            box, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, spot
        )
        qapp_process(pop)
        assert box.isChecked() is False
        assert pop.isVisible() is True

    def test_the_page_holds_the_popovers_grab_off(self, shell):
        page = NewRentalPage(shell)
        # Asserted on its own so the mitigation cannot be dropped from the page
        # without the click test above going quietly meaningless.
        assert page.filter_popover.testAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating
        ) is True
        assert page.filter_popover.windowFlags() & Qt.WindowType.Popup

    def test_a_clicking_a_brand_name_ticks_it_rather_than_navigating(self, qapp):
        """The name row is a second hit target on the tick, not a jump link.

        Brand is a filter axis now, so a row that scrolled instead of selecting
        would quietly do the wrong thing -- and clicking the *name* is how a clerk
        ticks brands that are showing as greyed.
        """
        pop = FilterPopover()
        pop.set_brands(["Toyota", "Honda"])
        pop.show()
        qapp_process(pop)

        pop.brand_pane.rows["Honda"].click()
        qapp_process(pop)
        assert pop.brand_pane.boxes["Honda"].isChecked() is True
        assert pop.current_filter().makes == frozenset({"Honda"})

        # Twice, to put it back -- so the row cannot be a one-way scroll.
        pop.brand_pane.rows["Honda"].click()
        qapp_process(pop)
        assert pop.brand_pane.boxes["Honda"].isChecked() is False
        assert pop.current_filter().makes == frozenset()

    def test_a_greyed_brand_can_still_be_unticked_by_its_name(self, qapp):
        """The whole point of keeping the name clickable.

        A brand greyed by the clerk's own selection is one they need to be able to
        take back. A disabled checkbox would leave them stuck on it.
        """
        pop = FilterPopover()
        pop.set_brands(["Toyota", "Honda"])
        pop.show()
        qapp_process(pop)

        pop.brand_pane.boxes["Honda"].setChecked(True)
        pop.set_availability({"makes": {"Toyota"}})
        assert pop.brand_pane.boxes["Honda"].isEnabled() is False

        pop.brand_pane.rows["Honda"].click()
        qapp_process(pop)
        assert pop.brand_pane.boxes["Honda"].isChecked() is False

    def test_the_popover_is_a_fixed_two_column_box(self, qapp):
        pop = FilterPopover()
        assert pop.width() == FILTER_POPOVER_W
        assert pop.height() == FILTER_POPOVER_H
        assert pop._category_scroll.width() == POPOVER_CATEGORY_W

    def test_the_popover_is_white_with_the_cards_border_and_nothing_else(self, qapp):
        pop = FilterPopover()
        # `class="card"` is what puts it under the dashboard card rule: white,
        # 28px radius, 1px border. Not a tinted panel.
        assert pop.property("class") == "card"

    def test_every_axis_the_brief_asked_for_has_a_category(self, qapp):
        pop = FilterPopover()
        titles = pop.category_titles()
        assert titles == [
            "Brand",
            "Vehicle type",
            "Engine size",
            "Price range",
            "Passengers",
            "Transmission",
            "Fuel type",
        ]
        # Dropped on purpose: there is no branch or pickup-location axis to filter.
        assert not any("location" in t or "branch" in t for t in titles)

    def test_clicking_a_category_swaps_the_right_column(self, qapp):
        pop = FilterPopover()
        assert pop.current_category() == "makes"
        pop._categories["cc_buckets"].click()
        assert pop.current_category() == "cc_buckets"
        assert pop.panes.currentWidget() is pop.cc_group

    def test_the_engine_size_buckets_are_the_ones_a_shop_quotes(self, qapp):
        pop = FilterPopover()
        assert list(pop.cc_group.boxes) == [
            "Up to 125cc",
            "126 - 155cc",
            "156 - 400cc",
            "Over 400cc",
        ]

    def test_an_untouched_popover_reports_no_filter(self, qapp):
        pop = FilterPopover()
        assert pop.current_filter().is_empty() is True
        assert pop.current_filter().max_rate is None

    def test_ticking_a_box_publishes_the_whole_selection(self, qapp):
        pop = FilterPopover()
        seen = []
        pop.filters_changed.connect(seen.append)

        pop.type_group.boxes["suv"].setChecked(True)
        assert len(seen) == 1
        assert seen[0].vehicle_classes == frozenset({"suv"})
        assert seen[0].is_empty() is False

    def test_ticking_a_box_leaves_the_popover_open(self, qapp, shell):
        page = NewRentalPage(shell)
        page.show()
        qapp_process(page)
        page.open_popover()
        page.filter_popover.type_group.boxes["suv"].setChecked(True)
        qapp_process(page)
        # The regression this whole shape exists to avoid: a `Qt.Popup` that
        # closes on the click that delivered the tick would make every single
        # adjustment dismiss the panel.
        assert page.filter_popover.isVisible() is True
        page.close_popover()

    def test_two_boxes_in_one_group_are_an_or(self, qapp):
        pop = FilterPopover()
        pop.fuel_group.boxes["Petrol"].setChecked(True)
        pop.fuel_group.boxes["Diesel"].setChecked(True)
        assert pop.current_filter().fuel_types == frozenset({"Petrol", "Diesel"})

    def test_two_groups_are_an_and(self, qapp):
        pop = FilterPopover()
        pop.type_group.boxes["suv"].setChecked(True)
        pop.seat_group.boxes["7"].setChecked(True)
        filters = pop.current_filter()
        assert filters.vehicle_classes == frozenset({"suv"})
        assert filters.seats == frozenset({7})

    def test_a_brand_is_an_axis_like_any_other(self, qapp):
        pop = FilterPopover()
        pop.set_brands(["Toyota", "Honda"])
        pop.brand_pane.boxes["Toyota"].setChecked(True)
        pop.seat_group.boxes["7"].setChecked(True)
        filters = pop.current_filter()
        assert filters.makes == frozenset({"Toyota"})
        assert filters.seats == frozenset({7})

    def test_the_price_spin_reads_as_a_real_bound(self, qapp):
        pop = FilterPopover()
        pop.price_group.max_rate.setValue(4000)
        assert pop.current_filter().max_rate == 4000

    def test_an_untouched_upper_spin_is_no_ceiling(self, qapp):
        # The top of the spin's range is a sentinel, not a million-a-day bound, so
        # the popover can express "unset" and Clear all can disable itself.
        pop = FilterPopover()
        assert pop.price_group.max_rate.maximum() == MAX_RATE
        assert pop.current_filter().max_rate is None

    def test_an_inverted_price_range_is_swapped_not_rejected(self, qapp):
        pop = FilterPopover()
        pop.price_group.min_rate.setValue(6000)
        pop.price_group.max_rate.setValue(2000)
        filters = pop.current_filter()
        assert (filters.min_rate, filters.max_rate) == (2000, 6000)

    def test_clear_resets_every_control_and_publishes_once(self, qapp):
        pop = FilterPopover()
        pop.type_group.boxes["suv"].setChecked(True)
        pop.seat_group.boxes["5"].setChecked(True)
        pop.price_group.min_rate.setValue(1000)
        pop.set_brands(["Toyota"])
        pop.brand_pane.boxes["Toyota"].setChecked(True)
        seen = []
        pop.filters_changed.connect(seen.append)

        pop.clear()
        assert len(seen) == 1, "clearing is one change, not one per control"
        assert seen[0].is_empty() is True
        assert pop.type_group.selected() == set()
        assert pop.brand_pane.selected() == set()
        assert pop.price_group.min_rate.value() == 0

    def test_the_brand_pane_has_its_own_search_bar(self, qapp):
        pop = FilterPopover()
        assert pop.brand_pane.search.placeholderText() == "Search makes"
        # Only the Brand pane has one; it is the only list long enough to need it.
        assert pop.brand_pane.search.parent() is pop.brand_pane
        assert not hasattr(pop.type_group, "search")

    def test_the_brand_pane_search_hides_non_matching_rows(self, qapp):
        pop = FilterPopover()
        pop.set_brands(["Toyota", "Honda", "Ford"])
        pop.brand_pane.search.setText("to")
        assert pop.visible_brands() == ["Toyota"]
        pop.brand_pane.search.setText("")
        assert pop.visible_brands() == ["Toyota", "Honda", "Ford"]

    def test_the_brand_pane_search_is_not_a_filter(self, qapp):
        pop = FilterPopover()
        pop.set_brands(["Toyota", "Honda"])
        pop.brand_pane.search.setText("Honda")
        # Navigating the list, not narrowing the fleet: the grid is untouched.
        assert pop.current_filter().is_empty() is True

    def test_the_brand_pane_can_be_shown_empty(self, qapp):
        pop = FilterPopover()
        pop.set_brands(())
        assert pop.brand_pane.order == []
        pop.set_brands(["Toyota", "Honda"])
        assert pop.brand_pane.order == ["Toyota", "Honda"]


class TestNewRentalMuting:
    """Greyed-out axes, fed from `axis_availability` by the page."""

    def test_an_unreachable_axis_is_greyed_but_keeps_its_ticks(self, qapp):
        pop = FilterPopover()
        pop.cc_group.boxes["Over 400cc"].setChecked(True)
        # A selection of SUVs leaves no motorcycle anywhere.
        pop.set_availability(
            {
                "makes": {"Toyota"},
                "vehicle_classes": {"suv"},
                "cc_buckets": set(),
                "transmissions": {"Automatic"},
                "fuel_types": {"Diesel"},
                "seats": {7},
            },
            exempt="vehicle_classes",
        )
        assert pop.cc_group.isEnabled() is False
        # Dimmed, not discarded: the clerk's choice survives being unreachable.
        assert pop.cc_group.boxes["Over 400cc"].isChecked() is True

    def test_a_reachable_axis_is_live(self, qapp):
        pop = FilterPopover()
        pop.set_availability(
            {
                "makes": {"Toyota"},
                "vehicle_classes": {"suv"},
                "cc_buckets": {"156 - 400cc"},
                "transmissions": {"Automatic"},
                "fuel_types": {"Diesel"},
                "seats": {7},
            }
        )
        assert pop.muted_axes() == []

    def test_an_unavailable_value_inside_a_live_axis_is_greyed(self, qapp):
        pop = FilterPopover()
        pop.set_brands(["Toyota", "Honda"])
        # Only Toyota has an SUV; Honda's row has nothing to contribute.
        pop.set_availability({"makes": {"Toyota"}})
        assert pop.brand_pane.boxes["Toyota"].isEnabled() is True
        assert pop.brand_pane.boxes["Honda"].isEnabled() is False

    def test_the_last_touched_axis_is_never_greyed(self, qapp):
        pop = FilterPopover()
        # Nothing is reachable, which would otherwise grey every column and leave
        # no way back out of a zero-result selection.
        pop.set_availability(
            {
                "makes": set(),
                "vehicle_classes": set(),
                "cc_buckets": set(),
                "transmissions": set(),
                "fuel_types": set(),
                "seats": set(),
            },
            exempt="vehicle_classes",
        )
        assert pop.type_group.isEnabled() is True
        assert "vehicle_classes" not in pop.muted_axes()

    def test_price_is_narrowed_rather_than_greyed(self, qapp):
        pop = FilterPopover()
        pop.price_group.min_rate.setValue(9000)
        pop.set_availability({}, rate_high=2500)
        # The dearest reachable rate is the honest ceiling, so a dead 9000 floor
        # cannot sit there describing a range nothing can match.
        assert pop.price_group.max_rate.maximum() == 2500
        assert pop.price_group.min_rate.value() == 2500
        assert pop.price_group.isEnabled() is True

    def test_price_the_clerk_never_touched_is_not_moved(self, qapp):
        pop = FilterPopover()
        pop.price_group.min_rate.setValue(1000)
        pop.set_availability({}, rate_high=2500)
        # Only the ceiling is narrowed; a floor the clerk chose stays theirs.
        assert pop.price_group.min_rate.value() == 1000

    def test_an_axis_the_caller_has_no_answer_for_is_left_live(self, qapp):
        pop = FilterPopover()
        pop.set_brands(["Toyota", "Honda"])
        # Only an answer about makes: the other axes are left exactly as they were,
        # not greyed on the strength of a question that was never asked.
        pop.set_availability({"makes": {"Toyota"}})
        assert pop.brand_pane.boxes["Honda"].isEnabled() is False
        assert pop.cc_group.isEnabled() is True
        assert pop.seat_group.isEnabled() is True

    #: The muting tests need a fleet where engine size is the *only* axis in play,
    #: so both vehicles carry seats, transmission and fuel. `_add_vehicle` leaves
    #: those NULL by default, and a NULL column has no values to offer, so those
    #: axes would grey out for an unrelated reason and a test could pass while
    #: proving nothing about engine size.
    #:
    #: The bike records a displacement and the SUV deliberately does not, because
    #: `engine_cc` is a motorcycle field -- a car's displacement is not what this
    #: schema records. That asymmetry is what makes "Honda + motorcycle" a real
    #: dead end: Honda's only vehicle is the CRV.
    @staticmethod
    def _car_and_bike(context) -> None:
        _add_vehicle(
            context,
            make="Honda",
            model="CRV",
            plate="HON-001",
            vehicle_class="suv",
            seats=7,
            transmission="Automatic",
            fuel_type="Diesel",
        )
        _add_vehicle(
            context,
            make="Yamaha",
            model="NMAX",
            plate="BIKE-001",
            vehicle_class="motorcycle",
            engine_cc=155,
            seats=2,
            transmission="Manual",
            fuel_type="Petrol",
        )

    def test_engine_size_is_live_while_a_motorcycle_is_reachable(self, qapp, shell, branch):
        _built, context = branch
        self._car_and_bike(context)

        page = NewRentalPage(shell)
        page.show()
        page.resize(1440, 900)
        page.refresh()
        pop = page.filter_popover

        pop.type_group.boxes["motorcycle"].setChecked(True)
        page._availability_timer.stop()
        page._refresh_availability()
        qapp_process(page)
        # The NMAX is a 155, so its bucket is on the table.
        assert "cc_buckets" not in pop.muted_axes()

    def test_a_brand_with_no_motorcycle_greys_out_engine_size(self, qapp, shell, branch):
        _built, context = branch
        self._car_and_bike(context)

        page = NewRentalPage(shell)
        page.show()
        page.resize(1440, 900)
        page.refresh()
        pop = page.filter_popover

        pop.type_group.boxes["motorcycle"].setChecked(True)
        while not page._exhausted:
            page._load_page()
        # This is the user's case: Honda has the CRV and no bike, so engine size
        # has nothing left to say.
        pop.brand_pane.boxes["Honda"].setChecked(True)
        page._availability_timer.stop()
        page._refresh_availability()
        qapp_process(page)
        assert "cc_buckets" in pop.muted_axes()

    def test_unticking_the_brand_restores_engine_size(self, qapp, shell, branch):
        _built, context = branch
        self._car_and_bike(context)

        page = NewRentalPage(shell)
        page.show()
        page.resize(1440, 900)
        page.refresh()
        pop = page.filter_popover

        pop.type_group.boxes["motorcycle"].setChecked(True)
        while not page._exhausted:
            page._load_page()
        pop.brand_pane.boxes["Honda"].setChecked(True)
        page._availability_timer.stop()
        page._refresh_availability()
        assert "cc_buckets" in pop.muted_axes()

        pop.brand_pane.boxes["Honda"].setChecked(False)
        page._availability_timer.stop()
        page._refresh_availability()
        assert "cc_buckets" not in pop.muted_axes()

    def test_a_muted_axis_keeps_its_ticks_through_a_real_selection(self, qapp, shell, branch):
        _built, context = branch
        self._car_and_bike(context)

        page = NewRentalPage(shell)
        page.show()
        page.resize(1440, 900)
        page.refresh()
        pop = page.filter_popover

        pop.type_group.boxes["motorcycle"].setChecked(True)
        pop.cc_group.boxes["126 - 155cc"].setChecked(True)
        while not page._exhausted:
            page._load_page()
        # Brand last, on purpose: the axis the clerk just touched is never greyed,
        # so the brand must be the final tick for engine size to be the casualty.
        pop.brand_pane.boxes["Honda"].setChecked(True)
        page._availability_timer.stop()
        page._refresh_availability()
        assert "cc_buckets" in pop.muted_axes()
        # Dimmed, not discarded: the clerk's engine size is still there to come
        # back to when the brand is unticked.
        assert pop.cc_group.boxes["126 - 155cc"].isChecked() is True

    def test_a_failing_availability_query_leaves_the_columns_live(self, shell, branch, monkeypatch):
        from app.staff.pages import new_rental

        _built, context = branch
        page = NewRentalPage(shell)
        page.refresh()

        def _boom(*_args, **_kwargs):
            raise RuntimeError("no availability")

        monkeypatch.setattr(new_rental, "axis_availability", _boom)
        page._availability_timer.stop()
        page._refresh_availability()
        # Greying itself out on a transient hiccup would be worse than no greying.
        assert page.filter_popover.muted_axes() == []

    def test_a_failed_refresh_clears_greys_from_the_last_one(self, shell, branch, monkeypatch):
        from app.services import vehicle_service
        from app.staff.pages import new_rental

        _built, context = branch
        self._car_and_bike(context)
        page = NewRentalPage(shell)
        page.show()
        page.resize(1440, 900)
        page.refresh()
        pop = page.filter_popover

        # A real narrowing first, so there is a genuine grey to be left behind.
        pop.type_group.boxes["motorcycle"].setChecked(True)
        while not page._exhausted:
            page._load_page()
        pop.brand_pane.boxes["Honda"].setChecked(True)
        page._availability_timer.stop()
        page._refresh_availability()
        assert pop.muted_axes() != []

        def _boom(*_args, **_kwargs):
            raise RuntimeError("no availability")

        monkeypatch.setattr(new_rental, "axis_availability", _boom)
        page._refresh_availability()
        # A grey that outlives its reason is a dead end the clerk cannot see their
        # way out of, so the failure has to undo it rather than simply do nothing.
        assert pop.muted_axes() == []


class TestNewRentalSectionHighlight:
    def test_the_brand_list_matches_the_sections_built(self, qapp, shell, branch):
        _built, context = branch
        _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        page = NewRentalPage(shell)
        page.refresh()
        assert page.filter_popover.brand_pane.order == page.brands_shown

    def test_the_brand_in_view_is_the_one_highlighted(self, qapp, shell, branch):
        _built, context = branch
        _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        page = NewRentalPage(shell)
        page.show()
        page.resize(1440, 900)
        page.refresh()
        while not page._exhausted:
            page._load_page()
        qapp_process(page)

        assert page.current_section == page.brands_shown[0]
        active = [
            b
            for b in page.filter_popover.brand_pane.order
            if page.filter_popover.brand_row(b).is_active()
        ]
        assert active == [page.brands_shown[0]]

    def test_the_highlight_is_the_sidebar_paint_not_a_new_one(self, qapp):
        pop = FilterPopover()
        pop.set_brands(["Toyota"])
        row = pop.brand_row("Toyota")
        assert isinstance(row, ActiveRow)
        pop.set_active_section("Toyota")
        assert row.is_active() is True
        pop.set_active_section(None)
        assert row.is_active() is False

    def test_the_open_category_uses_the_same_active_row(self, qapp):
        pop = FilterPopover()
        row = pop._categories["makes"]
        assert isinstance(row, ActiveRow)
        pop._categories["seats"].click()
        assert row.is_active() is False
        assert pop._categories["seats"].is_active() is True

    def test_a_brand_the_fleet_does_not_have_is_not_offered(self, qapp, shell, branch):
        page = NewRentalPage(shell)
        page.refresh()
        assert "Mazda" not in page.filter_popover.brand_pane.order


class TestNewRentalPage:
    def test_the_page_uses_the_dashboard_panel(self, shell):
        page = NewRentalPage(shell)
        assert page.PANEL is True
        assert page.panel is not None
        assert page.HEADER is False

    def test_the_header_row_is_gone(self, shell):
        page = NewRentalPage(shell)
        assert page._header.isVisible() is False

    def test_staff_may_open_the_page(self, shell):
        page = NewRentalPage(shell)
        page.refresh()

    def test_every_vehicle_gets_a_slot_whatever_its_status(self, shell, branch):
        _built, context = branch
        with context.reading() as session:
            total = session.query(Vehicle).count()
        page = NewRentalPage(shell)
        page.refresh()
        assert total > 0
        assert len(page.slots) == total

    def test_the_page_loads_one_page_of_brands_at_a_time(self, shell, branch, monkeypatch):
        from app.staff.pages import new_rental

        monkeypatch.setattr(new_rental, "BRANDS_PER_PAGE", 1)
        _built, context = branch
        _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        _add_vehicle(context, make="Yamaha", model="NMAX", plate="BIKE-001")

        page = NewRentalPage(shell)
        page.refresh()
        assert page.brands_shown == ["Honda"]

        page._load_page()
        assert page.brands_shown == ["Honda", "Toyota"]

        page._load_page()
        assert page.brands_shown == ["Honda", "Toyota", "Yamaha"]
        assert page._exhausted

        page._load_page()
        assert page.brands_shown == ["Honda", "Toyota", "Yamaha"]

    def test_the_fleet_is_grouped_into_a_row_per_brand(self, shell, branch):
        _built, context = branch
        _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        _add_vehicle(context, make="Yamaha", model="NMAX", plate="BIKE-001")

        page = NewRentalPage(shell)
        page.refresh()
        assert page.brands_shown == ["Honda", "Toyota", "Yamaha"]
        for section in page._sections:
            assert section.heading.text() == section.brand
            assert all(slot.vehicle.make == section.brand for slot in section.slots)

    def test_a_brand_heading_is_just_the_make(self, shell, branch):
        _built, context = branch
        _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        _add_vehicle(context, make="Honda", model="City", plate="HON-002")

        page = NewRentalPage(shell)
        page.refresh()
        # Removed on request: the heading used to read "Honda 2 vehicles". The one
        # count that remains is in the top bar, describing the whole fleet.
        assert [s.heading.text() for s in page._sections] == page.brands_shown
        assert all(not hasattr(s, "count") for s in page._sections)

    def test_the_search_and_filters_bar_is_pinned_above_the_scroll(self, shell):
        page = NewRentalPage(shell)
        page.show()
        page.resize(1440, 900)
        qapp_process(page)
        bar = page.pinned_bar
        assert bar.isVisible() is True
        assert bar.height() == FILTER_BAR_H
        # Outside the scroll area, so it cannot scroll away with the fleet.
        assert page.scroll_area.isAncestorOf(bar) is False
        assert bar.y() < page.scroll_area.y()

    

    #: Brands wide enough that a click can put a middle one at the top of the
    #: window. With three brands the list barely overflows, so the last brand can
    #: never reach the top and the scroll would clamp to nothing.
    SCROLL_BRANDS = ("Boris", "Citroen", "Daihatsu", "Eagle", "Fiat", "GMC", "Holden")

    def _tall_fleet(self, context) -> None:
        for index, make in enumerate(self.SCROLL_BRANDS):
            _add_vehicle(
                context, make=make, model=f"Model{index}", plate=f"S{index:03d}"
            )

    def _scrolling_page(self, shell, monkeypatch, exhaust: bool = True):
        """A page showing a fleet taller than the window.

        `exhaust=False` leaves brands unloaded, which is the state a scrolling test
        needs: only then does reaching the bottom have anything left to append.
        """
        from app.staff.pages import new_rental

        monkeypatch.setattr(new_rental, "BRANDS_PER_PAGE", 1)
        page = NewRentalPage(shell)
        page.show()
        page.resize(1440, 900)
        page.refresh()
        if exhaust:
            while not page._exhausted:
                page._load_page()
        qapp_process(page)
        return page

    def test_a_clicked_brand_scrolls_to_it(self, qapp, shell, branch, monkeypatch):
        _built, context = branch
        self._tall_fleet(context)
        page = self._scrolling_page(shell, monkeypatch)
        bar = page.scroll_area.verticalScrollBar()
        assert bar.maximum() > 0, "the fleet has to be taller than the window"

        target = page.brands_shown[4]
        page.scroll_to_section(target)
        qapp_process(page)
        assert bar.value() > 0
        assert page.current_section == target

    def test_the_highlight_follows_the_scroll_rather_than_where_it_landed(
        self, qapp, shell, branch, monkeypatch
    ):
        _built, context = branch
        self._tall_fleet(context)
        page = self._scrolling_page(shell, monkeypatch)
        assert page.current_section == page.brands_shown[0]

        for target in (page.brands_shown[2], page.brands_shown[5], None):
            if target is None:
                page.scroll_area.verticalScrollBar().setValue(0)
                page._settle_timer.stop()
                page._track_section()
                assert page.current_section == page.brands_shown[0]
            else:
                page.scroll_to_section(target)
                page._settle_timer.stop()
                page._track_section()
                assert page.current_section == target
            active = page.filter_popover.brand_row(page.current_section)
            assert active.is_active() is True

    def test_only_one_brand_is_highlighted_at_a_time(
        self, qapp, shell, branch, monkeypatch
    ):
        _built, context = branch
        self._tall_fleet(context)
        page = self._scrolling_page(shell, monkeypatch)
        page.scroll_to_section(page.brands_shown[4])
        page._settle_timer.stop()
        page._track_section()
        active = [
            b
            for b in page.filter_popover.brand_pane.order
            if page.filter_popover.brand_row(b).is_active()
        ]
        assert active == [page.brands_shown[4]]

    def test_the_list_really_is_scrollable_and_so_really_is_continuous(
        self, qapp, shell, branch, monkeypatch
    ):
        _built, context = branch
        self._tall_fleet(context)
        page = self._scrolling_page(shell, monkeypatch, exhaust=False)
        bar = page.scroll_area.verticalScrollBar()
        assert bar.maximum() > 0
        before = len(page.brands_shown)
        bar.setValue(bar.maximum())
        qapp_process(page)
        assert len(page.brands_shown) > before

    def test_the_count_comes_from_the_database(self, qapp, shell, branch):
        _built, context = branch
        with context.reading() as session:
            total = session.query(Vehicle).count()
        page = NewRentalPage(shell)
        page.refresh()
        assert f"{total:,}" in page.count_label.text()

    def test_a_filter_narrows_the_sections_that_are_built(self, qapp, shell, branch):
        from app.services.vehicle_service import VehicleFilter

        _built, context = branch
        _add_vehicle(context, make="Honda", model="Civic", plate="HON-001", vehicle_class="medium")
        _add_vehicle(context, make="Ford", model="Ranger", plate="FOR-001", vehicle_class="pickup")
        page = NewRentalPage(shell)
        page.show()
        page.resize(1440, 900)
        page.refresh()
        assert set(page.brands_shown) == {"Toyota", "Honda", "Ford"}

        page.filter_popover.type_group.boxes["pickup"].setChecked(True)
        qapp_process(page)
        assert page.brands_shown == ["Ford"]
        assert page.slots[0].vehicle.model == "Ranger"

    def test_clearing_the_rail_restores_the_fleet(self, qapp, shell, branch):
        _built, context = branch
        _add_vehicle(context, make="Ford", model="Ranger", plate="FOR-001", vehicle_class="pickup")
        page = NewRentalPage(shell)
        page.show()
        page.resize(1440, 900)
        page.refresh()
        before = list(page.brands_shown)

        page.filter_popover.type_group.boxes["pickup"].setChecked(True)
        qapp_process(page)
        assert page.brands_shown != before

        page.clear_filters()
        qapp_process(page)
        assert page.brands_shown == before

    def test_a_search_narrows_the_fleet_to_what_it_names(self, qapp, shell, branch):
        _built, context = branch
        _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        _add_vehicle(context, make="Ford", model="Ranger", plate="FOR-001")
        page = NewRentalPage(shell)
        page.show()
        page.resize(1440, 900)
        page.refresh()

        page.search.setText("Ranger")
        qapp_process(page)
        assert page.brands_shown == ["Ford"]

    def test_a_search_matching_no_vehicle_says_so_instead_of_breaking(self, qapp, shell, branch):
        _built, context = branch
        page = NewRentalPage(shell)
        page.show()
        page.resize(1440, 900)
        page.refresh()

        page.search.setText("no-such-vehicle")
        page._settle_timer.stop()
        while not page._exhausted:
            page._load_page()
        qapp_process(page)
        assert page._sections == []
        assert page._empty.isVisible() is True
        assert page.current_section is None

    def test_a_selection_that_matches_nothing_does_not_raise(self, qapp, shell, branch):
        _built, context = branch
        page = NewRentalPage(shell)
        page.show()
        page.resize(1440, 900)
        page.refresh()

        page.filter_popover.price_group.min_rate.setValue(900_000)
        qapp_process(page)
        assert page._sections == []

    def test_a_filter_that_matches_nothing_reports_zero(self, qapp, shell, branch):
        _built, context = branch
        page = NewRentalPage(shell)
        page.show()
        page.resize(1440, 900)
        page.refresh()
        page.filter_popover.price_group.min_rate.setValue(900_000)
        qapp_process(page)
        assert page.matched == 0
        assert page.count_label.text() == "0 of 7 vehicles"

    def test_the_page_survives_a_failing_count(self, qapp, shell, branch, monkeypatch):
        from app.staff.pages import new_rental

        def _boom(*_args, **_kwargs):
            raise RuntimeError("count unavailable")

        monkeypatch.setattr(new_rental, "count_filtered_vehicles", _boom)
        page = NewRentalPage(shell)
        page.refresh()
        assert "unavailable" in page.count_label.text()
        assert page.brands_shown, "the fleet still loads when the count does not"


class TestVehicleFilters:
    def test_brands_come_from_the_fleet(self, branch):
        from app.services.vehicle_service import vehicle_brands

        _built, context = branch
        with context.reading() as session:
            assert vehicle_brands(session) == ["Toyota"]

    def test_the_fleet_can_be_narrowed_to_a_set_of_brands(self, branch):
        from app.services.vehicle_service import showcase_vehicle_rows

        _built, context = branch
        with context.reading() as session:
            grouped = showcase_vehicle_rows(
                session, category=None, statuses=None, makes=["Toyota", "Honda"]
            )
            hondas = showcase_vehicle_rows(
                session, category=None, statuses=None, makes=["Honda"]
            )
        assert len(grouped) == 7
        assert hondas == []

    def test_an_empty_filter_restricts_nothing(self, branch):
        from app.services.vehicle_service import VehicleFilter

        assert VehicleFilter().is_empty() is True
        assert VehicleFilter(search="   ").is_empty() is True
        assert VehicleFilter(max_rate=1000).is_empty() is False

    def test_a_filter_describes_itself_for_the_readout(self, branch):
        from app.services.vehicle_service import VehicleFilter

        assert VehicleFilter().describe() == "No filters"
        assert VehicleFilter(vehicle_classes=frozenset({"suv"})).describe() == "1 filter"
        assert (
            VehicleFilter(seats=frozenset({5, 7}), search="vios").describe()
            == "3 filters"
        )

    def test_the_whole_fleet_is_returned_when_nothing_is_ticked(self, branch):
        from app.services.vehicle_service import (
            VehicleFilter,
            showcase_vehicle_rows,
        )

        _built, context = branch
        with context.reading() as session:
            rows = showcase_vehicle_rows(
                session, category=None, statuses=None, filters=VehicleFilter()
            )
        assert len(rows) == 7


class TestFilterQueries:
    """The rail's selection is applied in SQL, so these read a real database."""

    def _fleet(self, context) -> None:
        _add_vehicle(
            context, make="Toyota", model="Vios", plate="AAA-001",
            vehicle_class="small", daily_rate="1200", seats=5,
            transmission="Automatic", fuel_type="Petrol",
        )
        _add_vehicle(
            context, make="Toyota", model="Fortuner", plate="AAA-002",
            vehicle_class="suv", daily_rate="4800", seats=7,
            transmission="Automatic", fuel_type="Diesel", engine_cc=2700,
        )
        _add_vehicle(
            context, make="Honda", model="Click", plate="MMP-001",
            category_name="Motorcycle", vehicle_class="motorcycle",
            daily_rate="800", seats=2, transmission="Manual",
            fuel_type="Petrol", engine_cc=125,
        )
        _add_vehicle(
            context, make="Kawasaki", model="NMAX", plate="MMP-002",
            category_name="Motorcycle", vehicle_class="motorcycle",
            daily_rate="1500", seats=2, transmission="Automatic",
            fuel_type="Petrol", engine_cc=155,
        )

    def _models(self, context, filters) -> set[str]:
        from app.services.vehicle_service import showcase_vehicle_rows

        with context.reading() as session:
            return {v.model for v, _ in showcase_vehicle_rows(
                session, category=None, statuses=None, filters=filters
            )}

    def test_a_size_class_narrows_the_fleet(self, branch):
        from app.services.vehicle_service import VehicleFilter

        _built, context = branch
        self._fleet(context)
        assert self._models(
            context, VehicleFilter(vehicle_classes=frozenset({"motorcycle"}))
        ) == {"Click", "NMAX"}
        assert self._models(
            context, VehicleFilter(vehicle_classes=frozenset({"suv"}))
        ) == {"Fortuner"}

    def test_a_displacement_bucket_narrows_the_fleet(self, branch):
        from app.services.vehicle_service import VehicleFilter

        _built, context = branch
        self._fleet(context)
        assert self._models(
            context, VehicleFilter(cc_buckets=frozenset({"Up to 125cc"}))
        ) == {"Click"}
        assert self._models(
            context, VehicleFilter(cc_buckets=frozenset({"126 - 155cc"}))
        ) == {"NMAX"}
        # A car with no recorded cc is not in any bucket, which is correct: the
        # clerk asked about engine sizes and the database has nothing to say.
        assert self._models(
            context, VehicleFilter(cc_buckets=frozenset({"Over 400cc"}))
        ) == {"Fortuner"}

    def test_two_displacement_buckets_are_an_or(self, branch):
        from app.services.vehicle_service import VehicleFilter

        _built, context = branch
        self._fleet(context)
        assert self._models(
            context,
            VehicleFilter(cc_buckets=frozenset({"Up to 125cc", "126 - 155cc"})),
        ) == {"Click", "NMAX"}

    def test_a_price_range_narrows_the_fleet(self, branch):
        from app.services.vehicle_service import VehicleFilter

        _built, context = branch
        self._fleet(context)
        assert self._models(context, VehicleFilter(min_rate=1000, max_rate=2000)) == {
            "Vios",
            "NMAX",
        }
        assert self._models(context, VehicleFilter(min_rate=4000)) == {"Fortuner"}

    def test_a_passenger_count_narrows_the_fleet(self, branch):
        from app.services.vehicle_service import VehicleFilter

        _built, context = branch
        self._fleet(context)
        assert self._models(context, VehicleFilter(seats=frozenset({7}))) == {"Fortuner"}

    def test_transmission_and_fuel_narrow_the_fleet(self, branch):
        from app.services.vehicle_service import VehicleFilter

        _built, context = branch
        self._fleet(context)
        assert self._models(
            context, VehicleFilter(transmissions=frozenset({"Manual"}))
        ) == {"Click"}
        assert self._models(
            context, VehicleFilter(fuel_types=frozenset({"Diesel"}))
        ) == {"Fortuner"}

    def test_two_vehicles_in_one_class_are_not_narrowed_away(self, branch):
        from app.services.vehicle_service import VehicleFilter

        _built, context = branch
        self._fleet(context)
        assert self._models(
            context, VehicleFilter(vehicle_classes=frozenset({"motorcycle"}))
        ) == {"Click", "NMAX"}

    def test_the_axes_combine_as_an_and(self, branch):
        from app.services.vehicle_service import VehicleFilter

        _built, context = branch
        self._fleet(context)
        assert self._models(
            context,
            VehicleFilter(
                vehicle_classes=frozenset({"motorcycle"}),
                transmissions=frozenset({"Automatic"}),
            ),
        ) == {"NMAX"}

    def test_a_search_matches_make_model_or_plate(self, branch):
        from app.services.vehicle_service import VehicleFilter

        _built, context = branch
        self._fleet(context)
        assert self._models(context, VehicleFilter(search="fortuner")) == {"Fortuner"}
        assert self._models(context, VehicleFilter(search="kawasaki")) == {"NMAX"}
        assert self._models(context, VehicleFilter(search="mmp-002")) == {"NMAX"}
        assert self._models(context, VehicleFilter(search="nothing")) == set()

    def test_a_search_ignores_case_and_surrounding_space(self, branch):
        from app.services.vehicle_service import VehicleFilter

        _built, context = branch
        self._fleet(context)
        assert self._models(context, VehicleFilter(search="  FORTUNER  ")) == {
            "Fortuner"
        }

    def test_the_count_agrees_with_the_rows(self, branch):
        from app.services.vehicle_service import (
            VehicleFilter,
            count_filtered_vehicles,
        )

        _built, context = branch
        self._fleet(context)
        filters = VehicleFilter(vehicle_classes=frozenset({"motorcycle"}))
        with context.reading() as session:
            counted = count_filtered_vehicles(session, filters)
        assert counted == len(self._models(context, filters)) == 2

    def test_the_count_reads_the_whole_fleet_not_one_page(self, branch):
        from app.services.vehicle_service import (
            VehicleFilter,
            count_filtered_vehicles,
        )

        _built, context = branch
        self._fleet(context)
        filters = VehicleFilter(vehicle_classes=frozenset({"motorcycle"}))
        with context.reading() as session:
            # Only the first brand is ever loaded by the page, but the count answers
            # for both -- otherwise it would read 1 and lie about the other bike.
            counted = count_filtered_vehicles(
                session, filters, available_only=True
            )
        assert counted == 2

    def test_the_count_of_everything_is_the_fleet(self, branch):
        from app.services.vehicle_service import (
            VehicleFilter,
            count_filtered_vehicles,
        )

        _built, context = branch
        with context.reading() as session:
            seeded = count_filtered_vehicles(session, VehicleFilter())
        self._fleet(context)
        with context.reading() as session:
            # `branch` already seeds three vehicles, so the count has to be the
            # seeded ones plus the four added here -- not just the four.
            assert count_filtered_vehicles(session, VehicleFilter()) == seeded + 4


class TestBrandSection:
    def _vehicle(self, index: int) -> RentalVehicle:
        return RentalVehicle(
            vehicle_id=index,
            make="Toyota",
            model=f"V{index}",
            year=2020,
            daily_rate=Decimal("1000"),
            seats=4,
        )

    def test_a_section_shows_the_brand(self, qapp):
        section = BrandSection(
            "Toyota", [self._vehicle(index) for index in range(2)], QThreadPool()
        )
        assert section.heading.text() == "Toyota"
        assert [slot.vehicle.vehicle_id for slot in section.slots] == [0, 1]


class TestVehicleCard:
    def _vehicle(self, index: int = 7) -> RentalVehicle:
        return RentalVehicle(
            vehicle_id=index,
            make="Testmake",
            model=f"Testmodel{index}",
            year=1999,
            daily_rate=Decimal("2500"),
            seats=5,
            photo_url=None,
        )

    def test_the_card_carries_the_name_price_and_capacity(self, qapp):
        card = VehicleCard(self._vehicle())
        assert card.name.full_text() == "Testmake Testmodel7"
        assert card.price.text() == "\u20b12,500 / day"
        assert card.meta.text() == "5 seats"

    def test_check_details_reports_the_vehicle(self, qapp):
        card = VehicleCard(self._vehicle())
        seen = []
        card.details_requested.connect(seen.append)
        card.details.click()
        assert seen == [7]

    def test_the_details_button_lives_on_the_photo_overlay(self, qapp):
        card = VehicleCard(self._vehicle())
        assert card.details is card.photo.details
        card.photo.set_reveal(1.0)
        assert card.photo.reveal == 1.0
        assert card.photo.details.isEnabled()

    def test_the_photo_zoom_is_animatable(self, qapp):
        card = VehicleCard(self._vehicle())
        card.photo.set_zoom(1.06)
        assert card.photo.zoom == pytest.approx(1.06)

    def test_a_slot_takes_a_photo_url_that_arrives_late(self, qapp):
        slot = VehicleSlot(self._vehicle())
        pool = QThreadPool()
        slot.build(pool)
        first = slot.card._loader
        slot.set_photo_url("https://example.test/car.webp")
        assert slot._photo_url == "https://example.test/car.webp"
        assert slot.card._loader is not first

    def test_the_card_is_twice_as_round_as_its_button(self):
        assert VEHICLE_CARD_RADIUS == VEHICLE_BTN_RADIUS * 2
        assert VEHICLE_IMAGE_RADIUS == VEHICLE_BTN_RADIUS

    def test_the_stylesheet_agrees_with_the_button_radius(self):
        style = theme.load_stylesheet()
        assert "QPushButton#vehicleDetailsButton" in style
        block = style.split("QPushButton#vehicleDetailsButton", 1)[1].split("}", 1)[0]
        assert f"border-radius: {VEHICLE_BTN_RADIUS}px" in block

    def test_a_slot_swaps_the_skeleton_for_the_card_when_the_photo_arrives(self, qapp):
        slot = VehicleSlot(self._vehicle())
        assert slot._stack.currentWidget() is slot.skeleton
        pool = QThreadPool()
        slot.build(pool)
        pool.waitForDone(5000)
        qapp.processEvents()
        assert slot.card is not None
        assert slot._stack.currentWidget() is slot.card

    def test_a_slot_is_built_only_once(self, qapp):
        slot = VehicleSlot(self._vehicle())
        pool = QThreadPool()
        slot.build(pool)
        card = slot.card
        slot.build(pool)
        assert slot.card is card

    def test_a_rail_builds_one_card_per_vehicle(self, qapp):
        rail = VehicleRail(QThreadPool(), [self._vehicle(index) for index in range(3)])
        assert [slot.vehicle.vehicle_id for slot in rail.slots] == [0, 1, 2]
        qapp.processEvents()
        rail.build_now()
        assert all(slot.built and slot.card is not None for slot in rail.slots)

    def test_a_rail_side_scrolls_when_it_overflows(self, qapp):
        rail = VehicleRail(QThreadPool(), [self._vehicle(index) for index in range(9)])
        rail.resize(VEHICLE_CARD_MIN_W, VEHICLE_CARD_H)
        rail.show()
        qapp.processEvents()
        assert rail.horizontalScrollBar().maximum() > 0


def test_the_shimmer_skeleton_is_shared_with_the_hero():
    import app.ui.hero as hero

    assert hero.Skeleton is Skeleton


def test_the_media_resolver_fills_photo_urls_for_the_whole_fleet(qapp, branch, monkeypatch):
    from app.services import media_service
    from app.staff import vehicle_cards

    class _Row:
        view_angle = "front34"
        image_url = "https://example.test/signed.webp"

    _built, context = branch
    monkeypatch.setattr(
        vehicle_cards.media_service,
        "get_or_fetch_media",
        lambda session, vehicle, include_3d=False: [_Row()],
    )
    assert media_service.pick_photo_url([_Row()]) == "https://example.test/signed.webp"

    resolver = MediaResolver(context)
    seen: dict[int, str] = {}
    resolver.signals.resolved.connect(seen.update)
    resolver.run()

    with context.reading() as session:
        expected = {v.vehicle_id for v in session.query(Vehicle).all()}
    assert set(seen) == expected
    assert set(seen.values()) == {"https://example.test/signed.webp"}


def test_the_media_resolver_can_be_limited_to_one_page(qapp, branch, monkeypatch):
    from app.staff import vehicle_cards

    class _Row:
        view_angle = "front34"
        image_url = "https://example.test/signed.webp"

    _built, context = branch
    monkeypatch.setattr(
        vehicle_cards.media_service,
        "get_or_fetch_media",
        lambda session, vehicle, include_3d=False: [_Row()],
    )
    with context.reading() as session:
        first_page = [v.vehicle_id for v in session.query(Vehicle).all()][:2]

    resolver = MediaResolver(context, first_page)
    seen: dict[int, str] = {}
    resolver.signals.resolved.connect(seen.update)
    resolver.run()
    assert set(seen) == set(first_page)


# --------------------------------------------------------------------------
# Fleet
# --------------------------------------------------------------------------


class TestFleetPage:
    def test_every_car_is_listed(self, shell, branch):
        page = FleetPage(shell)
        page.refresh()
        assert page.table.row_count() == 7

    def test_status_is_shown_as_itself(self, shell, branch):
        page = FleetPage(shell)
        page.refresh()
        by_plate = {
            page.table.cell_text(r, 0): page.table.cell_text(r, 5)
            for r in range(page.table.row_count())
        }
        assert by_plate["OUT-001"] == "Rented"
        assert by_plate["FREE-001"] == "Available"
        assert by_plate["SHOP-001"] == "Maintenance"

    def test_a_car_in_the_workshop_says_what_is_blocking_it(self, shell, branch):
        page = FleetPage(shell)
        page.refresh()
        blocked = {
            page.table.cell_text(r, 0): page.table.cell_text(r, 8)
            for r in range(page.table.row_count())
        }
        assert "Maintenance" in blocked["SHOP-001"]

    def test_a_free_car_is_not_marked_blocked(self, shell, branch):
        page = FleetPage(shell)
        page.refresh()
        blocked = {
            page.table.cell_text(r, 0): page.table.cell_text(r, 8)
            for r in range(page.table.row_count())
        }
        assert blocked["FREE-001"] == "-"

    def test_due_back_is_the_next_end_date(self, shell, branch):
        page = FleetPage(shell)
        page.refresh()
        due = {
            page.table.cell_text(r, 0): page.table.cell_text(r, 6)
            for r in range(page.table.row_count())
        }
        assert due["BACK-001"] == TODAY.strftime("%d %b")

    def test_only_a_late_car_is_flagged_overdue(self, shell, branch):
        page = FleetPage(shell)
        page.refresh()
        late = {
            page.table.cell_text(r, 0): page.table.cell_text(r, 7)
            for r in range(page.table.row_count())
        }
        assert late["LATE-001"] == "Yes"
        assert late["BACK-001"] == "-"
        assert late["FREE-001"] == "-"


# --------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------


class TestCustomersPage:
    def _rows(self, shell, branch):
        from app.staff.pages.customers import CustomersPage

        page = CustomersPage(shell)
        page.refresh()
        return {
            page.table.cell_text(r, 0): (page.table.cell_text(r, 4), page.table.cell_text(r, 5))
            for r in range(page.table.row_count())
        }

    def test_staff_accounts_are_not_listed_as_customers(self, shell, branch):
        rows = self._rows(shell, branch)
        assert "Branch Admin" not in rows
        assert "Counter Staff" not in rows

    def test_a_valid_licence_is_not_flagged(self, shell, branch):
        licence, blocked = self._rows(shell, branch)["Ana Reyes"]
        assert licence.startswith("Valid to")
        assert blocked == "-"

    def test_a_lapsed_licence_blocks(self, shell, branch):
        licence, blocked = self._rows(shell, branch)["Already Lapsed"]
        assert licence.startswith("Expired")
        assert blocked == "Yes"

    def test_a_licence_inside_the_window_is_soon_not_blocked(self, shell, branch):
        """A licence is usable on the day before it expires. Treating the
        window as invalid would refuse a customer who can legally drive today."""
        licence, blocked = self._rows(shell, branch)["Soon Lapsed"]
        assert licence.startswith("Expires")
        assert blocked == "Soon"

    def test_the_rental_count_comes_from_the_bookings(self, shell, branch):
        from app.staff.pages.customers import CustomersPage

        page = CustomersPage(shell)
        page.refresh()
        counts = {
            page.table.cell_text(r, 0): page.table.cell_text(r, 6)
            for r in range(page.table.row_count())
        }
        assert counts["Ana Reyes"] == "6"

    def test_the_blocked_list_agrees_with_the_table(self, shell, branch):
        """`blocked_customers` answers "cannot rent today", so it must match the
        `Yes` rows and not the `Soon` ones -- a licence inside the window is
        still valid today, and treating it as blocked would refuse a customer
        who is legally fine to drive."""
        from app.staff.pages.customers import CustomersPage

        page = CustomersPage(shell)
        page.refresh()
        yes_rows = {
            page.table.cell_text(r, 0)
            for r in range(page.table.row_count())
            if page.table.cell_text(r, 5) == "Yes"
        }
        assert set(page.blocked_customers()) == yes_rows == {"Already Lapsed"}


# --------------------------------------------------------------------------
# Payments
# --------------------------------------------------------------------------


class TestPaymentsPage:
    def test_both_payments_are_listed(self, shell, branch):
        page = PaymentsPage(shell)
        page.refresh()
        assert page.takings.row_count() == 2

    def test_an_uncleared_payment_says_not_cleared(self, shell, branch):
        page = PaymentsPage(shell)
        page.refresh()
        rows = {
            page.takings.cell_text(r, 3): page.takings.cell_text(r, 6)
            for r in range(page.takings.row_count())
        }
        assert rows["15,000.00"] != "Not cleared"
        assert rows["2,500.00"] == "Not cleared"

    def test_a_settled_booking_is_not_chased(self, shell, branch):
        """The finished rental is fully paid, so it must not be in the list. A
        screen that shows it as owed sends someone after money already taken."""
        built, _context = branch
        page = PaymentsPage(shell)
        page.refresh()
        listed = {page.outstanding.cell_text(r, 0) for r in range(page.outstanding.row_count())}
        assert f"#{built.finished.booking_id}" not in listed
        assert f"#{built.overdue.booking_id}" in listed

    def test_outstanding_is_largest_first(self, shell, branch):
        built, context = branch
        with context.session() as session:
            session.add(
                Payment(
                    booking_id=built.overdue.booking_id,
                    amount=Decimal("1000.00"),
                    method="cash",
                    status="paid",
                    paid_at=datetime.combine(TODAY, datetime.min.time()),
                    recorded_by=built.counter.user_id,
                )
            )
        page = PaymentsPage(shell)
        page.refresh()
        amounts = [
            Decimal(page.outstanding.cell_text(r, 8).replace(",", ""))
            for r in range(page.outstanding.row_count())
        ]
        assert amounts == sorted(amounts, reverse=True)

    def test_the_penalties_column_is_separate_from_the_rental(self, shell, branch):
        from app.services.booking_service import apply_penalty

        built, context = branch
        with context.session() as session:
            booking = session.get(Booking, built.overdue.booking_id)
            apply_penalty(
                session, booking, "late_return", Decimal("1000.00"), "Two days late"
            )
        page = PaymentsPage(shell)
        page.refresh()
        row = next(
            r
            for r in range(page.outstanding.row_count())
            if page.outstanding.cell_text(r, 0) == f"#{built.overdue.booking_id}"
        )
        # Booking, Customer, Vehicle, Status, Rental, Penalties, Total due, Paid, Owed
        assert page.outstanding.cell_text(row, 4) == "7,500.00"
        assert page.outstanding.cell_text(row, 5) == "1,000.00"
        assert page.outstanding.cell_text(row, 6) == "8,500.00"
        assert page.outstanding.cell_text(row, 8) == "8,500.00"


# --------------------------------------------------------------------------
# Inspections
# --------------------------------------------------------------------------


class TestInspectionsPage:
    def test_reports_are_listed(self, shell, branch):
        page = InspectionsPage(shell)
        page.refresh()
        assert page.table.row_count() == 1
        # Booking, Vehicle, Customer, Type, When, Odometer, Fuel, Damage, Photo, Inspector
        assert page.table.cell_text(0, 0) == f"#{branch[0].out_booking.booking_id}"
        assert page.table.cell_text(0, 3) == "Pre Rental"

    def test_the_inspector_is_named(self, shell, branch):
        page = InspectionsPage(shell)
        page.refresh()
        assert page.table.cell_text(0, 9) == "Counter Staff"

    def test_a_missing_photo_is_shown_not_hidden(self, shell, branch):
        page = InspectionsPage(shell)
        page.refresh()
        assert page.table.cell_text(0, 8) == "No"

    def test_an_empty_branch_is_empty_not_broken(self, shell, branch):
        built, context = branch
        with context.session() as session:
            session.query(Inspection_Report).delete()
        page = InspectionsPage(shell)
        page.refresh()
        assert page.table.state is LoadState.EMPTY


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------


class TestDashboardPage:
    """The cards, charts and role split have their own file --
    `test_staff_dashboard.py`. What is checked here is how the page integrates
    with a real branch, and that the old metric strip the redesign removed is
    genuinely gone.
    """

    def test_the_revenue_card_shows_the_year_to_date_total(self, shell, branch):
        from app.services import dashboard_service

        page = DashboardPage(shell, animate=False)
        page.refresh()
        with page.context.reading() as session:
            months = dashboard_service.month_sales(session, date.today().year)
            collected_today = dashboard_service.revenue_on(session, date.today())
        ytd = sum((m.total for m in months), Decimal("0.00"))
        assert page.revenue_value.text() == pesos(ytd)
        assert pesos(collected_today) in page.revenue_collections.text()

    def test_the_old_metric_strip_and_its_tile_are_gone(self, shell, branch):
        import app.staff.pages.dashboard as module

        page = DashboardPage(shell, animate=False)
        page.refresh()
        assert not hasattr(page, "_strip_tiles")
        assert not hasattr(module, "MetricTile")

    def test_both_roles_see_the_same_figures(self, shell, branch):
        page = DashboardPage(shell, animate=False)
        page.refresh()
        admin_value = page.revenue_value.text()
        as_staff(branch)
        staff_page = DashboardPage(shell, animate=False)
        staff_page.refresh()
        assert staff_page.revenue_value.text() == admin_value


# --------------------------------------------------------------------------
# Bookings: the write paths
# --------------------------------------------------------------------------


class TestBookingsPageActions:
    def _select(self, page: BookingsPage, booking_ref: str) -> None:
        for row in range(page.table.row_count()):
            if page.table.cell_text(row, 0) == booking_ref:
                page.table.table.selectRow(row)
                return
        raise AssertionError(f"{booking_ref} not in the table")

    def test_the_list_shows_the_balance(self, shell, branch):
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        row = next(
            r
            for r in range(page.table.row_count())
            if page.table.cell_text(r, 0) == f"#{built.finished.booking_id}"
        )
        assert page.table.cell_text(row, 7) == "15,000.00"
        assert page.table.cell_text(row, 8) == "0.00"

    def test_an_unpaid_rental_shows_what_is_owed(self, shell, branch):
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        row = next(
            r
            for r in range(page.table.row_count())
            if page.table.cell_text(r, 0) == f"#{built.overdue.booking_id}"
        )
        assert page.table.cell_text(row, 8) == "7,500.00"

    def test_nothing_selected_is_refused_not_crashed(self, shell, monkeypatch):
        import app.staff.pages.bookings as module

        messages = []
        monkeypatch.setattr(module, "toast", lambda *a, **k: messages.append(a[1:]))
        page = BookingsPage(shell)
        page.refresh()
        page.table.table.clearSelection()
        page.table.table.setCurrentCell(-1, -1)
        assert page.run("confirm") is False
        assert "Select a booking" in messages[-1][0]

    def test_staff_may_not_cancel(self, shell, branch, monkeypatch):
        import app.staff.pages.bookings as module

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        as_staff(branch)
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        self._select(page, f"#{built.upcoming.booking_id}")
        assert page.can("cancel") is False
        assert page.run("cancel", reason="not needed") is False

    def test_staff_may_do_the_counter_work(self, shell, branch):
        as_staff(branch)
        page = BookingsPage(shell)
        for action in COUNTER_ACTIONS:
            assert page.can(action) is True

    def test_admin_may_cancel(self, shell, branch, monkeypatch):
        import app.staff.pages.bookings as module

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        monkeypatch.setattr(module, "confirm", lambda *a, **k: True)
        built, context = branch
        page = BookingsPage(shell)
        page.refresh()
        self._select(page, f"#{built.upcoming.booking_id}")
        assert page.can("cancel") is True
        assert page.cancel_booking("Customer changed their mind") is True
        with context.reading() as session:
            booking = session.get(Booking, built.upcoming.booking_id)
            assert booking.status == "cancelled"

    def test_a_cancelled_booking_cannot_be_cancelled_again(self, shell, branch, monkeypatch):
        """A service refusal has to surface, not quietly succeed."""
        import app.staff.pages.bookings as module

        refusals = []
        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        monkeypatch.setattr(module, "confirm", lambda *a, **k: True)
        monkeypatch.setattr(
            module, "blocking_error", lambda parent, exc, **k: refusals.append(exc)
        )
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()

        self._select(page, f"#{built.upcoming.booking_id}")
        assert page.cancel_booking("First reason") is True
        # The table reloads after the cancel, so the row must be found again.
        self._select(page, f"#{built.upcoming.booking_id}")
        assert page.cancel_booking("Second reason") is False
        assert refusals, "the second cancel was refused silently"

    def test_recording_a_payment_updates_the_balance(self, shell, branch, monkeypatch):
        import app.staff.pages.bookings as module

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        built, context = branch
        page = BookingsPage(shell)
        page.refresh()
        self._select(page, f"#{built.overdue.booking_id}")
        assert page.run(
            "record_payment",
            amount=Decimal("1000.00"),
            method="cash",
            reference_no="",
            note="",
        ) is True
        with context.reading() as session:
            booking = session.get(Booking, built.overdue.booking_id)
            assert Decimal(str(booking.total_cost)) == Decimal("7500.00")
        page.refresh()
        row = next(
            r
            for r in range(page.table.row_count())
            if page.table.cell_text(r, 0) == f"#{built.overdue.booking_id}"
        )
        assert page.table.cell_text(row, 7) == "1,000.00"
        assert page.table.cell_text(row, 8) == "6,500.00"

    def test_a_nonsense_amount_is_refused_and_says_so(self, shell, branch, monkeypatch):
        """A zero payment is a typo, and absorbing it would leave the balance
        looking settled when it is not."""
        import app.staff.pages.bookings as module

        messages = []
        monkeypatch.setattr(module, "toast", lambda *a, **k: messages.append(a))
        monkeypatch.setattr(
            module, "blocking_error", lambda parent, exc, **k: messages.append(exc)
        )
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        self._select(page, f"#{built.overdue.booking_id}")
        assert page.run(
            "record_payment",
            amount=Decimal("0.00"),
            method="cash",
            reference_no="",
            note="",
        ) is False
        assert any(m for m in messages), "the refusal was silent"

    def test_an_unknown_method_is_refused(self, shell, branch, monkeypatch):
        import app.staff.pages.bookings as module

        shown = []
        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        monkeypatch.setattr(
            module, "blocking_error", lambda parent, exc, **k: shown.append(exc)
        )
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        self._select(page, f"#{built.overdue.booking_id}")
        assert page.run(
            "record_payment",
            amount=Decimal("100.00"),
            method="goats",
            reference_no="",
            note="",
        ) is False
        assert shown

    def test_a_cancelled_booking_takes_no_payment(self, shell, branch, monkeypatch):
        """Money must not disappear into a cancelled rental. The service is
        what refuses it; the page only has to surface that."""
        import app.staff.pages.bookings as module

        shown = []
        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        monkeypatch.setattr(module, "confirm", lambda *a, **k: True)
        monkeypatch.setattr(
            module, "blocking_error", lambda parent, exc, **k: shown.append(exc)
        )
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        self._select(page, f"#{built.upcoming.booking_id}")
        assert page.cancel_booking("Customer withdrew") is True
        self._select(page, f"#{built.upcoming.booking_id}")
        assert page.run(
            "record_payment",
            amount=Decimal("100.00"),
            method="cash",
            reference_no="",
            note="",
        ) is False
        assert shown

    def test_a_vanished_booking_is_reported(self, shell, branch, monkeypatch):
        import app.staff.pages.bookings as module

        shown = []
        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        monkeypatch.setattr(
            module, "blocking_error", lambda parent, exc, **k: shown.append(exc)
        )
        built, context = branch
        page = BookingsPage(shell)
        page.refresh()
        self._select(page, f"#{built.upcoming.booking_id}")
        with context.session() as session:
            session.delete(session.get(Booking, built.upcoming.booking_id))
        assert page.run("confirm") is False
        assert shown, "a missing booking was refused silently"

    def test_the_selection_is_read_as_an_id_not_a_row(self, shell, branch):
        """`context.reading()` expires everything it hands back, so a row
        taken from it raises on the next attribute read. Only the id crosses
        the session boundary."""
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        self._select(page, f"#{built.upcoming.booking_id}")
        assert page.selected_booking_id() == built.upcoming.booking_id
        assert isinstance(page.selected_booking_id(), int)


# --------------------------------------------------------------------------
# Refresh behaviour
# --------------------------------------------------------------------------


class TestPageRefresh:
    def test_a_refresh_picks_up_a_colleagues_write(self, shell, branch):
        built, context = branch
        page = PaymentsPage(shell)
        page.refresh()
        before = page.takings.row_count()
        with context.session() as session:
            session.add(
                Payment(
                    booking_id=built.overdue.booking_id,
                    amount=Decimal("500.00"),
                    method="cash",
                    status="paid",
                    paid_at=datetime.combine(TODAY, datetime.min.time()),
                    recorded_by=built.counter.user_id,
                )
            )
        page.refresh()
        assert page.takings.row_count() == before + 1

    def test_a_table_failure_does_not_take_the_page_down(self, shell, branch, monkeypatch):
        """One broken query should show one failed table, not a dialog over
        every table and a window that has to be reopened."""
        page = PaymentsPage(shell)
        monkeypatch.setattr(
            page.takings, "_loader", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        page.refresh()
        assert page.takings.state is LoadState.FAILED
        assert page.outstanding.state is LoadState.LOADED


class TestTheFixtureBranch:
    """The dataset has to be built against the same day the pages read.

    A session that starts at 23:58 and crosses midnight is the only way this
    breaks, and it then fails on whichever date-based assertions happen to run
    afterwards -- once a day, unreproducible on request. Cheap to pin down.
    """

    def test_a_branch_is_built_against_the_clock_not_against_import(
        self, request, monkeypatch
    ):
        stale = date.today() - timedelta(days=1)
        monkeypatch.setattr(sys.modules[__name__], "TODAY", stale)
        assert TODAY == stale

        request.getfixturevalue("branch")

        assert TODAY == date.today(), (
            f"the branch was still built around {TODAY}, not {date.today()}"
        )
