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
    SIDEBAR_GAP,
    VEHICLE_BTN_RADIUS,
    VEHICLE_CARD_H,
    VEHICLE_CARD_MIN_W,
    VEHICLE_CARD_RADIUS,
    VEHICLE_IMAGE_RADIUS,
)
from app.staff.pages.base import StaffPage  # noqa: E402
from app.staff.pages.bookings import COUNTER_ACTIONS, BookingsPage  # noqa: E402
from app.staff.pages.customers import EXPIRING_SOON_DAYS, CustomersPage  # noqa: E402
from app.staff.pages.dashboard import DashboardPage, pesos  # noqa: E402
from app.staff.pages.fleet import FleetPage  # noqa: E402
from app.staff.pages.inspections import InspectionsPage  # noqa: E402
from app.staff.pages.maintenance import MaintenancePage  # noqa: E402
from app.staff.pages.reports import ReportsPage  # noqa: E402
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
from PySide6.QtCore import Qt, QPoint, QRect, QSize, QThreadPool  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QWidget  # noqa: E402
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

    from app.staff.workers import shutdown_pools

    app = QApplication.instance() or QApplication([])
    yield app
    shutdown_pools(1000)


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
                    maintenance_type="tire_brake",
                    estimate_cost=Decimal("4500.00"),
                    start_date=TODAY - timedelta(days=1),
                    end_date=TODAY + timedelta(days=2),
                    cost=Decimal("4500.00"),
                    status="in_progress",
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
            birthdate=date(1990, 1, 1),
            source="walk_in",
            is_active=True,
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
            "Car brand",
            "Car type",
            "Motor CC",
            "Price range",
            "Passengers",
            "Transmission type",
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


class TestPopoverPosition:
    """The dropdown must open fully on-screen, whichever monitor/DPI it is on."""

    def test_a_fitting_popup_opens_at_the_anchor(self):
        from app.staff.pages.new_rental import popover_position

        available = QRect(0, 0, 1920, 1040)
        assert popover_position(QPoint(900, 100), QSize(520, 420), available) == QPoint(
            900, 100
        )

    def test_a_popup_past_the_right_edge_is_pulled_back(self):
        from app.staff.pages.new_rental import popover_position

        available = QRect(0, 0, 1920, 1040)
        pos = popover_position(QPoint(1500, 100), QSize(520, 420), available)
        assert pos == QPoint(1400, 100)
        assert QRect(pos, QSize(520, 420)).right() <= available.right()

    def test_a_popup_past_the_left_or_top_edge_is_pushed_in(self):
        from app.staff.pages.new_rental import popover_position

        available = QRect(0, 0, 1920, 1040)
        assert popover_position(QPoint(-50, -20), QSize(520, 420), available) == QPoint(
            0, 0
        )

    def test_a_popup_taller_than_the_work_area_pins_to_the_top(self):
        from app.staff.pages.new_rental import popover_position

        available = QRect(0, 0, 1366, 300)
        assert popover_position(QPoint(200, 250), QSize(520, 420), available) == QPoint(
            200, 0
        )


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

    def test_new_rental_offers_only_plausibly_rentable_vehicles(
        self, shell, branch
    ):
        """Shop and retired cars are gone; rented/reserved stay for later dates."""
        built, context = branch
        reserved = _add_vehicle(
            context, make="Honda", model="Civic", plate="HON-001",
            status="reserved",
        )
        retired = _add_vehicle(
            context, make="Ford", model="Ranger", plate="FOR-001",
            status="retired",
        )

        page = NewRentalPage(shell)
        page.refresh()
        while not page._exhausted:
            page._load_page()

        shown = {slot.vehicle.vehicle_id for slot in page.slots}
        assert reserved in shown
        assert built.out_car.vehicle_id in shown  # currently rented
        assert built.free_car.vehicle_id in shown
        assert built.shop_car.vehicle_id not in shown  # maintenance
        assert retired not in shown

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
            visible = (
                session.query(Vehicle)
                .filter(Vehicle.status.in_(("available", "rented", "reserved")))
                .count()
            )
        page = NewRentalPage(shell)
        page.refresh()
        assert f"{visible:,}" in page.count_label.text()

    def test_the_count_excludes_shop_and_retired_cars(self, qapp, shell, branch):
        """The label and the rails describe the same set: no vanished cars."""
        _built, context = branch
        _add_vehicle(
            context, make="Ford", model="Ranger", plate="FOR-001",
            status="retired",
        )
        page = NewRentalPage(shell)
        page.refresh()
        while not page._exhausted:
            page._load_page()
        assert len(page.slots) == 6  # branch has 7 cars, 1 in the workshop
        assert page.count_label.text() == "6 vehicles"

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
        assert page.count_label.text() == "0 of 6 vehicles"

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

    def test_a_stale_signed_url_is_handed_over_as_none(self, branch):
        """CarImages signatures expire after an hour. A stale url must reach
        the page as None so the media pipeline renews it — a stale truthy
        url is downloaded as-is, 403s, and the card paints nothing."""
        from datetime import datetime, timedelta

        from app.models import Vehicle_Media
        from app.services.vehicle_service import showcase_vehicle_rows

        built, context = branch
        now = datetime.now()
        with context.session() as session:
            session.add(
                Vehicle_Media(
                    vehicle_id=built.free_car.vehicle_id,
                    source="carimagesapi",
                    view_angle="front34",
                    image_url="https://carimagesapi.com/image?sig=stale",
                    model_3d_url="",
                    cached_at=now - timedelta(hours=2),
                )
            )
            session.add(
                Vehicle_Media(
                    vehicle_id=built.collecting_car.vehicle_id,
                    source="carimagesapi",
                    view_angle="front34",
                    image_url="https://carimagesapi.com/image?sig=fresh",
                    model_3d_url="",
                    cached_at=now,
                )
            )
        with context.reading() as session:
            urls = {
                vehicle.vehicle_id: url
                for vehicle, url in showcase_vehicle_rows(
                    session, category=None, statuses=None
                )
            }
        assert urls[built.free_car.vehicle_id] is None
        assert (
            urls[built.collecting_car.vehicle_id]
            == "https://carimagesapi.com/image?sig=fresh"
        )


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
    @pytest.fixture(autouse=True)
    def offline_media(self, offline_media, monkeypatch):
        """Tests here hand real photo URLs to cards and slots, so the
        download transport gets a test double; the CarImages guards stay
        in force (the chained fixture still blocks/records actual API
        traffic and asserts at teardown)."""
        from app.staff import vehicle_cards

        class _FakeResponse:
            status_code = 200
            content = b""

            def raise_for_status(self) -> None:
                pass

        monkeypatch.setattr(
            vehicle_cards.requests, "get", lambda *a, **k: _FakeResponse()
        )
        yield

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

    def test_status_badge_shows_fleet_truth_on_the_photo(self, qapp):
        from app.staff import theme as staff_theme

        for status, colour in (
            ("available", staff_theme.STATUS_COLOURS["available"]),
            ("rented", staff_theme.STATUS_COLOURS["rented"]),
            ("maintenance", staff_theme.STATUS_COLOURS["maintenance"]),
            ("reserved", staff_theme.STATUS_COLOURS["reserved"]),
        ):
            car = RentalVehicle(
                vehicle_id=1,
                make="Testmake",
                model="Testmodel",
                year=1999,
                daily_rate=Decimal("2500"),
                seats=5,
                status=status,
            )
            card = VehicleCard(car)
            assert card.photo.status() == status
            assert card.photo._badge_text.text() == status.title()
            assert colour in card.photo._badge_chip.styleSheet()

    def test_status_badge_defaults_to_available(self, qapp):
        card = VehicleCard(self._vehicle())
        assert card.photo.status() == "available"
        assert card.photo._badge_text.text() == "Available"

    def test_status_badge_sits_top_left_and_lets_clicks_through(self, qapp):
        from PySide6.QtCore import Qt

        card = VehicleCard(self._vehicle())
        card.resize(240, card.height() or 300)
        card.show()
        qapp.processEvents()
        badge = card.photo._badge
        # Pinned top-left, fully inside the photo (width itself depends on
        # the platform font engine, so only containment is asserted).
        assert (badge.pos().x(), badge.pos().y()) == (8, 8)
        assert card.photo.rect().contains(badge.geometry())
        assert badge.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def test_status_badge_adds_no_height_to_the_card(self):
        from app.staff.metrics import VEHICLE_CARD_H

        card = VehicleCard(self._vehicle())
        # The badge is a photo overlay, not a body row: height math untouched.
        assert card.minimumHeight() == VEHICLE_CARD_H
        assert card.maximumHeight() == VEHICLE_CARD_H

    def test_converter_passes_status_through(self, qapp, branch):
        from app.staff.vehicle_cards import to_rental_vehicle

        _built, context = branch
        vid = _add_vehicle(
            context, make="Honda", model="Civic", plate="HON-001", status="rented"
        )
        with context.reading() as session:
            row = session.get(Vehicle, vid)
            assert to_rental_vehicle(row).status == "rented"

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
        _wait_for_fleet(page)
        assert page.table.row_count() == 7

    def test_status_is_shown_as_itself(self, shell, branch):
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
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
        _wait_for_fleet(page)
        blocked = {
            page.table.cell_text(r, 0): page.table.cell_text(r, 8)
            for r in range(page.table.row_count())
        }
        assert "Maintenance" in blocked["SHOP-001"]

    def test_a_free_car_is_not_marked_blocked(self, shell, branch):
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        blocked = {
            page.table.cell_text(r, 0): page.table.cell_text(r, 8)
            for r in range(page.table.row_count())
        }
        assert blocked["FREE-001"] == "-"

    def test_due_back_is_the_next_end_date(self, shell, branch):
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        due = {
            page.table.cell_text(r, 0): page.table.cell_text(r, 6)
            for r in range(page.table.row_count())
        }
        assert due["BACK-001"] == TODAY.strftime("%d %b")

    def test_only_a_late_car_is_flagged_overdue(self, shell, branch):
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        late = {
            page.table.cell_text(r, 0): page.table.cell_text(r, 7)
            for r in range(page.table.row_count())
        }
        assert late["LATE-001"] == "Yes"
        assert late["BACK-001"] == "-"
        assert late["FREE-001"] == "-"

    def test_tools_and_table_live_in_two_containers(self, shell, branch):
        from app.staff.metrics import CARD_GUTTER

        _built, _context = branch
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        assert page.HEADER is False
        assert page._tools_panel.objectName() == "pagePanel"
        assert page._data_panel.objectName() == "pagePanel"
        assert page.body.spacing() == CARD_GUTTER
        assert page.search in page._tools_panel.findChildren(type(page.search))
        assert page.table in page._data_panel.findChildren(type(page.table))
        margins = page.layout().contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (
            SIDEBAR_GAP, 0, SIDEBAR_GAP, 0,
        )

    def test_action_row_corners_follow_bookings(self, shell, branch):
        _built, _context = branch
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        assert page.search.property("corner") == "topLeft"
        assert page.clear_button.property("corner") == "topRight"
        assert page.add_button.property("corner") == "bottomLeft"

    def test_search_narrows_to_the_named_car(self, shell, branch):
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        page.search.setText("SHOP-001")
        _wait_for_fleet(page)
        assert page.table.row_count() == 1
        assert page.table.cell_text(0, 0) == "SHOP-001"

    def test_status_filter_narrows_the_list(self, shell, branch):
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        page.status_combo.setCurrentText("Maintenance")
        _wait_for_fleet(page)
        plates = {
            page.table.cell_text(r, 0) for r in range(page.table.row_count())
        }
        assert plates == {"SHOP-001"}

    def test_needs_attention_hides_clean_cars(self, shell, branch):
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        page.attention_check.setChecked(True)
        _wait_for_fleet(page)
        plates = {
            page.table.cell_text(r, 0) for r in range(page.table.row_count())
        }
        assert "FREE-001" not in plates
        assert "SHOP-001" in plates
        assert "LATE-001" in plates

    def test_refresh_shows_shimmer_immediately(self, shell, branch):
        _built, _context = branch
        page = FleetPage(shell)
        shell.show()
        page.show()
        page.refresh()
        assert page._skel_data.isVisible() is True
        assert page._data_panel.isVisible() is False
        _wait_for_fleet(page)
        assert page._skel_data.isVisible() is False
        assert page._data_panel.isVisible() is True
        assert page.table.state is LoadState.LOADED

    def test_writes_need_a_selection_first(self, shell, branch, monkeypatch):
        import app.staff.pages.fleet as module

        messages = []
        monkeypatch.setattr(module, "toast", lambda *a, **k: messages.append(a[1:]))
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        page.table.table.clearSelection()
        page.table.table.setCurrentCell(-1, -1)
        assert page.open_edit() is False
        assert page.open_details() is False
        assert "Select a vehicle" in messages[-1][0]

    def test_staff_may_not_write(self, shell, branch, monkeypatch):
        import app.staff.pages.fleet as module

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        as_staff(branch)
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        assert page.add_button.isEnabled() is False
        assert page.edit_button.isEnabled() is False
        assert page.bulk_button.isEnabled() is False
        assert page.retire_button.isEnabled() is False
        assert page.open_add() is False

    def test_adding_a_vehicle_lands_in_the_table(self, shell, branch, monkeypatch):
        import app.staff.pages.fleet as module

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        assert page.open_add() is True
        panel = page._fleet_overlay.scroll.widget()
        panel.plate_edit.setText("NEW-001")
        panel.make_edit.setText("Honda")
        panel.model_edit.setText("City")
        panel.color_edit.setText("Red")
        panel.rate_edit.setText("1800.00")
        panel._on_go()
        _wait_for_fleet(page)
        plates = {
            page.table.cell_text(r, 0) for r in range(page.table.row_count())
        }
        assert "NEW-001" in plates

    def test_retire_then_return_keeps_history(self, shell, branch, monkeypatch):
        import app.staff.pages.fleet as module

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        monkeypatch.setattr(module, "confirm", lambda *a, **k: True)
        built, _context = branch
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        for row in range(page.table.row_count()):
            if page.table.cell_text(row, 0) == "FREE-001":
                page.table.table.selectRow(row)
                break
        assert page.retire_button.text() == "Retire"
        page._on_retire_button()
        _wait_for_fleet(page)
        by_plate = {
            page.table.cell_text(r, 0): page.table.cell_text(r, 5)
            for r in range(page.table.row_count())
        }
        assert by_plate["FREE-001"] == "Retired"
        for row in range(page.table.row_count()):
            if page.table.cell_text(row, 0) == "FREE-001":
                page.table.table.selectRow(row)
                break
        assert page.retire_button.text() == "Return to service"
        page._on_retire_button()
        _wait_for_fleet(page)
        by_plate = {
            page.table.cell_text(r, 0): page.table.cell_text(r, 5)
            for r in range(page.table.row_count())
        }
        assert by_plate["FREE-001"] == "Available"

    def test_bulk_rate_preview_and_apply(self, shell, branch, monkeypatch):
        import app.staff.pages.fleet as module

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        assert page.open_bulk() is True
        panel = page._fleet_overlay.scroll.widget()
        panel.new_rate_edit.setText("3000.00")
        assert "→" in panel.preview.text()
        panel._on_go()
        _wait_for_fleet(page)
        rates = {
            page.table.cell_text(r, 0): page.table.cell_text(r, 3)
            for r in range(page.table.row_count())
        }
        assert all(value == "3,000.00" for value in rates.values())

    def test_details_panel_builds_sections(self, shell, branch, monkeypatch):
        import app.staff.fleet_panels as panels_mod
        from app.staff.fleet_panels import FleetDetailsPanel

        _built, _context = branch
        monkeypatch.setattr(
            panels_mod.media_service, "get_or_fetch_media", lambda *a, **k: []
        )
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        panel = FleetDetailsPanel(
            page.context, _built.free_car.vehicle_id, page
        )
        panel._pool.waitForDone(15000)
        qapp_process(panel)
        texts = [
            panel._body.itemAt(i).widget().text()
            for i in range(panel._body.count())
            if panel._body.itemAt(i).widget() is not None
            and hasattr(panel._body.itemAt(i).widget(), "text")
        ]
        joined = "\n".join(texts)
        assert "Utilization" in joined
        assert "Rented" in panel._stats_caption.text()
        panel.deleteLater()

    def _png_bytes(self) -> bytes:
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage

        image = QImage(64, 64, QImage.Format.Format_RGB32)
        image.fill(0x112233)
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")
        return bytes(buf.data())

    def test_details_auto_fetches_media_with_3d(self, shell, branch, monkeypatch):
        import app.staff.fleet_panels as panels_mod
        from app.staff.fleet_panels import FleetDetailsPanel

        seen = {}

        def _fake(session, vehicle, include_3d=False):
            seen["include_3d"] = include_3d
            return []

        monkeypatch.setattr(panels_mod.media_service, "get_or_fetch_media", _fake)
        built, _context = branch
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        panel = FleetDetailsPanel(page.context, built.free_car.vehicle_id, page)
        panel._pool.waitForDone(15000)
        qapp_process(panel)
        assert seen.get("include_3d") is True
        panel.deleteLater()

    def test_hero_shows_first_photo_without_click(self, shell, branch, monkeypatch):
        import app.staff.fleet_panels as panels_mod
        from app.staff.fleet_panels import FleetDetailsPanel

        png = self._png_bytes()
        monkeypatch.setattr(
            panels_mod.media_service, "get_or_fetch_media", lambda *a, **k: []
        )
        # Worker path downloads off-thread; bypass it with preloaded rows.
        monkeypatch.setattr(panels_mod, "_download", lambda *a, **k: png)
        built, _context = branch
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        panel = FleetDetailsPanel(page.context, built.free_car.vehicle_id, page)
        panel._pool.waitForDone(15000)
        # The constructor's silent refresh lands with an empty snapshot;
        # drain that queued landing first, or it overwrites the photo
        # injected below and the hero ends up empty.
        qapp_process(panel)
        # Simulate the worker landing with one photo row.
        panel._on_media_done(
            [{"view_angle": "front34", "image_url": "http://x/1.webp",
              "model_3d_url": "", "is_watermarked": False, "image_data": png}]
        )
        qapp_process(panel)
        pixmap = panel._hero.pixmap()
        assert pixmap is not None and not pixmap.isNull()
        panel.deleteLater()

    def test_history_inspections_maintenance_cap_at_five(self, shell, branch, monkeypatch):
        import app.staff.fleet_panels as panels_mod
        from app.staff.fleet_panels import FleetDetailsPanel

        monkeypatch.setattr(
            panels_mod.media_service, "get_or_fetch_media", lambda *a, **k: []
        )
        built, context = branch
        with context.session() as session:
            for index in range(7):
                session.add(
                    Booking(
                        user_id=built.customer.user_id,
                        vehicle_id=built.free_car.vehicle_id,
                        start_date=TODAY - timedelta(days=60 + index * 3),
                        end_date=TODAY - timedelta(days=58 + index * 3),
                        total_cost=Decimal("2500.00"),
                        status="completed",
                        created_at=datetime(2026, 1, 1, 9, 0),
                    )
                )
                session.flush()
            booking_ids = [
                row.booking_id
                for row in session.query(Booking).filter(
                    Booking.vehicle_id == built.free_car.vehicle_id,
                    Booking.status == "completed",
                ).all()
            ]
            for index, booking_id in enumerate(booking_ids[:6]):
                session.add(
                    Inspection_Report(
                        booking_id=booking_id,
                        inspected_by=built.counter.user_id,
                        inspection_type="post-rental",
                        mileage_reading=10000 + index,
                        fuel_level="full",
                        damage_notes=None,
                        inspected_at=datetime.combine(
                            TODAY - timedelta(days=index), datetime.min.time()
                        ),
                    )
                )
            for index in range(6):
                session.add(
                    Maintenance_Record(
                        vehicle_id=built.free_car.vehicle_id,
                        description=f"Shop visit {index}",
                        cost=Decimal("100.00"),
                        start_date=TODAY - timedelta(days=50 - index),
                        end_date=TODAY - timedelta(days=49 - index),
                        status="completed",
                    )
                )
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        panel = FleetDetailsPanel(page.context, built.free_car.vehicle_id, page)
        panel._pool.waitForDone(15000)
        qapp_process(panel)
        data = panel._read()
        assert len(data["history"]) == 5
        assert len(data["inspections"]) == 5
        assert len(data["maintenance"]) == 5
        panel.deleteLater()

    def test_details_overlay_has_no_scrollbar(self, shell, branch, monkeypatch):
        import app.staff.fleet_panels as panels_mod
        import app.staff.pages.fleet as module
        from PySide6.QtCore import Qt

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        monkeypatch.setattr(
            panels_mod.media_service, "get_or_fetch_media", lambda *a, **k: []
        )
        page = FleetPage(shell)
        shell.show()
        page.show()
        page.refresh()
        _wait_for_fleet(page)
        for row in range(page.table.row_count()):
            if page.table.cell_text(row, 0) == "FREE-001":
                page.table.table.selectRow(row)
                break
        assert page.open_details() is True
        overlay = page._fleet_overlay
        assert overlay.scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        overlay.hide_overlay()

    def test_viewer_has_no_cream_background(self, shell, branch, monkeypatch):
        from app.staff import fleet_panels
        from app.staff.fleet_panels import FleetDetailsPanel

        html = fleet_panels._THREE_HTML
        assert "background:transparent" in html.replace(" ", "")
        assert "alpha:true" in html.replace(" ", "")
        assert "0xEEEAE1" not in html
        css = theme.load_stylesheet()
        assert "QWidget#fleetGalleryStrip" in css
        assert "QWidget#fleetViewerHost" in css
        monkeypatch.setattr(
            fleet_panels.media_service, "get_or_fetch_media", lambda *a, **k: []
        )
        built, _context = branch
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        panel = FleetDetailsPanel(page.context, built.free_car.vehicle_id, page)
        panel._pool.waitForDone(15000)
        qapp_process(panel)
        hosts = panel.findChildren(QWidget)
        names = {w.objectName() for w in hosts}
        assert "fleetGalleryStrip" in names
        assert "fleetViewerHost" in names
        panel.deleteLater()

    def test_viewer_gets_a_playable_url_not_the_endpoint(self, shell, branch, monkeypatch):
        import app.staff.fleet_panels as panels_mod
        from app.staff.fleet_panels import FleetDetailsPanel
        from types import SimpleNamespace

        endpoint = (
            "https://carimagesapi.com/api/v1/vehicles/ford/explorer/g/model?redirect=1"
        )
        cdn = "https://cdn.carimagesapi.com/v/x.glb?sig=abc"
        monkeypatch.setattr(
            panels_mod.media_service, "get_or_fetch_media",
            lambda *a, **k: [SimpleNamespace(
                view_angle="model_3d", image_url="http://x/1.webp",
                model_3d_url=endpoint, is_watermarked=True,
            )],
        )
        monkeypatch.setattr(
            panels_mod.media_service.ci, "follow_model_redirect", lambda url: cdn
        )
        monkeypatch.setattr(panels_mod, "_download", lambda *a, **k: None)
        built, _context = branch
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        panel = FleetDetailsPanel(page.context, built.free_car.vehicle_id, page)
        panel._pool.waitForDone(15000)
        qapp_process(panel)
        assert panel._model_url() == cdn
        panel.deleteLater()

    def test_refresh_forces_a_true_refetch(self, shell, branch, monkeypatch):
        import app.staff.fleet_panels as panels_mod
        from app.models import Vehicle_Media
        from app.staff.fleet_panels import FleetDetailsPanel

        built, context = branch
        with context.session() as session:
            session.add(
                Vehicle_Media(
                    vehicle_id=built.free_car.vehicle_id,
                    source="carimagesapi",
                    view_angle="front34",
                    image_url="http://old/stale.webp",
                    model_3d_url="",
                    is_watermarked=True,
                    cached_at=datetime(2026, 1, 1, 9, 0),
                )
            )
        fresh = [
            {"view_angle": "front34", "image_url": "http://new/1.webp",
             "model_3d_url": "http://new/1.glb", "is_watermarked": False}
        ]
        from types import SimpleNamespace

        fresh_rows = [SimpleNamespace(**row) for row in fresh]
        monkeypatch.setattr(
            panels_mod.media_service, "get_or_fetch_media", lambda *a, **k: fresh_rows
        )
        monkeypatch.setattr(panels_mod, "_download", lambda *a, **k: None)
        page = FleetPage(shell)
        page.refresh()
        _wait_for_fleet(page)
        panel = FleetDetailsPanel(page.context, built.free_car.vehicle_id, page)
        panel._pool.waitForDone(15000)
        qapp_process(panel)
        panel.refresh_button.click()
        panel._pool.waitForDone(15000)
        qapp_process(panel)
        assert [r["image_url"] for r in panel._media_rows] == ["http://new/1.webp"]
        with context.reading() as session:
            urls = [
                row.image_url
                for row in session.query(Vehicle_Media).filter_by(
                    vehicle_id=built.free_car.vehicle_id
                ).all()
            ]
        assert "http://old/stale.webp" not in urls
        panel.deleteLater()


# --------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------


class TestCustomersPage:
    def _rows(self, shell, branch):
        from app.staff.pages.customers import CustomersPage

        page = CustomersPage(shell)
        page.refresh()
        _wait_for_customers(page)
        return {
            page.table.cell_text(r, 0): (page.table.cell_text(r, 4), page.table.cell_text(r, 5))
            for r in range(page.table.row_count())
        }

    def _page(self, shell, branch):
        from app.staff.pages.customers import CustomersPage

        page = CustomersPage(shell)
        page.refresh()
        _wait_for_customers(page)
        return page

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
        _wait_for_customers(page)
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
        _wait_for_customers(page)
        yes_rows = {
            page.table.cell_text(r, 0)
            for r in range(page.table.row_count())
            if page.table.cell_text(r, 5) == "Yes"
        }
        assert set(page.blocked_customers()) == yes_rows == {"Already Lapsed"}

    def test_tools_and_table_live_in_two_containers(self, shell, branch):
        from app.staff.metrics import CARD_GUTTER
        from app.staff.pages.customers import CustomersPage

        _built, _context = branch
        page = CustomersPage(shell)
        page.refresh()
        _wait_for_customers(page)
        assert page.HEADER is False
        assert page._tools_panel.objectName() == "pagePanel"
        assert page._data_panel.objectName() == "pagePanel"
        assert page.body.spacing() == CARD_GUTTER
        assert page.search in page._tools_panel.findChildren(type(page.search))
        assert page.table in page._data_panel.findChildren(type(page.table))

    def test_action_row_corners_follow_bookings(self, shell, branch):
        from app.staff.pages.customers import CustomersPage

        _built, _context = branch
        page = CustomersPage(shell)
        page.refresh()
        _wait_for_customers(page)
        assert page.search.property("corner") == "topLeft"
        assert page.clear_button.property("corner") == "topRight"
        assert page.add_button.property("corner") == "bottomLeft"

    def test_search_narrows_to_the_named_customer(self, shell, branch):
        from app.staff.pages.customers import CustomersPage

        page = CustomersPage(shell)
        page.refresh()
        _wait_for_customers(page)
        page.search.setText("Already Lapsed")
        _wait_for_customers(page)
        assert page.table.row_count() == 1
        assert page.table.cell_text(0, 0) == "Already Lapsed"

    def test_licence_filter_narrows_the_list(self, shell, branch):
        from app.staff.pages.customers import CustomersPage

        page = CustomersPage(shell)
        page.refresh()
        _wait_for_customers(page)
        page.licence_combo.setCurrentText("Expired")
        _wait_for_customers(page)
        names = {
            page.table.cell_text(r, 0) for r in range(page.table.row_count())
        }
        assert names == {"Already Lapsed"}

    def test_blocked_only_hides_valid_drivers(self, shell, branch):
        from app.staff.pages.customers import CustomersPage

        page = CustomersPage(shell)
        page.refresh()
        _wait_for_customers(page)
        page.blocked_check.setChecked(True)
        _wait_for_customers(page)
        names = {
            page.table.cell_text(r, 0) for r in range(page.table.row_count())
        }
        assert "Ana Reyes" not in names
        assert "Already Lapsed" in names

    def test_refresh_shows_shimmer_immediately(self, shell, branch):
        from app.staff.pages.customers import CustomersPage

        _built, _context = branch
        page = CustomersPage(shell)
        shell.show()
        page.show()
        page.refresh()
        assert page._skel_data.isVisible() is True
        assert page._data_panel.isVisible() is False
        _wait_for_customers(page)
        assert page._skel_data.isVisible() is False
        assert page._data_panel.isVisible() is True
        assert page.table.state is LoadState.LOADED

    def _select(self, page, name: str) -> None:
        _wait_for_customers(page)
        for row in range(page.table.row_count()):
            if page.table.cell_text(row, 0) == name:
                page.table.table.selectRow(row)
                return
        raise AssertionError(f"{name} not in the table")

    def test_table_shows_owed_source_and_flag(self, shell, branch):
        from app.staff.pages.customers import CustomersPage

        page = CustomersPage(shell)
        page.refresh()
        _wait_for_customers(page)
        by_name = {
            page.table.cell_text(r, 0): (
                page.table.cell_text(r, 7),
                page.table.cell_text(r, 8),
                page.table.cell_text(r, 9),
            )
            for r in range(page.table.row_count())
        }
        owed, source, flag = by_name["Ana Reyes"]
        assert Decimal(owed.replace(",", "")) > 0
        assert source == "Walk In"
        assert flag == "-"

    def test_balance_filter_hides_settled(self, shell, branch):
        from app.staff.pages.customers import CustomersPage

        page = CustomersPage(shell)
        page.refresh()
        _wait_for_customers(page)
        page.balance_check.setChecked(True)
        _wait_for_customers(page)
        names = {
            page.table.cell_text(r, 0) for r in range(page.table.row_count())
        }
        assert "Ana Reyes" in names
        assert "Soon Lapsed" not in names

    def test_flagged_filter(self, shell, branch):
        from app.staff.pages.customers import CustomersPage
        from app.services import customer_service

        built, context = branch
        with context.session() as session:
            customer = session.get(Users, built.customer.user_id)
            customer_service.set_flag(session, customer, "ID problems", staff_id=1)
        page = CustomersPage(shell)
        page.refresh()
        _wait_for_customers(page)
        page.flagged_check.setChecked(True)
        _wait_for_customers(page)
        names = {
            page.table.cell_text(r, 0) for r in range(page.table.row_count())
        }
        assert names == {"Ana Reyes"}

    def test_writes_need_a_selection_first(self, shell, branch, monkeypatch):
        import app.staff.pages.customers as module

        messages = []
        monkeypatch.setattr(module, "toast", lambda *a, **k: messages.append(a[1:]))
        page = CustomersPage(shell)
        page.refresh()
        _wait_for_customers(page)
        page.table.table.clearSelection()
        page.table.table.setCurrentCell(-1, -1)
        assert page.open_edit() is False
        assert page.open_details() is False
        assert "Select a customer" in messages[-1][0]

    def test_adding_a_customer_lands_in_the_table(self, shell, branch, monkeypatch):
        import app.staff.pages.customers as module

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        page = CustomersPage(shell)
        page.refresh()
        _wait_for_customers(page)
        assert page.open_add() is True
        panel = page._customer_overlay.scroll.widget()
        panel.name_edit.setText("New Person")
        panel.email_edit.setText("new@example.com")
        panel.phone_edit.setText("09170001111")
        panel.address_edit.setText("2 Test Street")
        panel.licence_edit.setText("NEW-9999")
        panel._on_go()
        _wait_for_customers(page)
        names = {
            page.table.cell_text(r, 0) for r in range(page.table.row_count())
        }
        assert "New Person" in names

    def test_deactivate_then_reactivate(self, shell, branch, monkeypatch):
        import app.staff.pages.customers as module

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        monkeypatch.setattr(module, "confirm", lambda *a, **k: True)
        page = CustomersPage(shell)
        page.refresh()
        self._select(page, "Soon Lapsed")
        assert page.deactivate_button.text() == "Deactivate"
        page._on_deactivate_button()
        _wait_for_customers(page)
        off = {
            page.table.cell_text(r, 0): page.table.cell_text(r, 10)
            for r in range(page.table.row_count())
        }
        assert off["Soon Lapsed"] == "Yes"

    def test_settle_cascades_across_bookings(self, shell, branch, monkeypatch):
        import app.staff.pages.customers as module

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        page = CustomersPage(shell)
        page.refresh()
        self._select(page, "Ana Reyes")
        assert page.open_settle() is True
        panel = page._customer_overlay.scroll.widget()
        total = sum(owed for _, owed in panel._unpaid)
        assert total > 0
        panel.amount_edit.setText(f"{total:,.2f}")
        panel._on_go()
        _wait_for_customers(page)
        owed = {
            page.table.cell_text(r, 0): page.table.cell_text(r, 7)
            for r in range(page.table.row_count())
        }
        assert owed["Ana Reyes"] == "0.00"

    def test_export_writes_a_statement_pdf(self, shell, branch, monkeypatch, tmp_path):
        import app.staff.pages.customers as module
        from app.services import receipt_service

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        old_dir = receipt_service.RECEIPTS_DIR
        receipt_service.RECEIPTS_DIR = tmp_path
        try:
            page = CustomersPage(shell)
            page.refresh()
            self._select(page, "Ana Reyes")
            opened = []
            monkeypatch.setattr(
                module.QDesktopServices, "openUrl", lambda url: opened.append(url) or True
            )
            assert page.open_export() is True
            assert opened, "expected the statement to open"
            assert list(tmp_path.glob("statement_*.pdf")), "expected a statement PDF"
        finally:
            receipt_service.RECEIPTS_DIR = old_dir

    def test_new_rental_prefills_the_wizard(self, shell, branch, monkeypatch):
        import app.staff.pages.customers as module

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        built, _context = branch
        page = CustomersPage(shell)
        page.refresh()
        self._select(page, "Ana Reyes")
        assert page.open_details() is True
        details = page._customer_overlay.scroll.widget()
        details.rental_button.click()
        assert shell.current_page_key == "new_rental"
        pending = shell._page_widgets["new_rental"]._pending_customer_id
        assert pending == built.customer.user_id


# --------------------------------------------------------------------------
# Payments
# --------------------------------------------------------------------------


class TestPaymentsPage:
    def test_both_payments_are_listed(self, shell, branch):
        page = PaymentsPage(shell)
        page.refresh()
        _wait_for_payments(page)
        assert page.takings.row_count() == 2

    def test_an_uncleared_payment_says_not_cleared(self, shell, branch):
        page = PaymentsPage(shell)
        page.refresh()
        _wait_for_payments(page)
        rows = {
            page.takings.cell_text(r, 3): page.takings.cell_text(r, 7)
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
        _wait_for_payments(page)
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
        _wait_for_payments(page)
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
        _wait_for_payments(page)
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

    def test_takings_show_the_kind(self, shell, branch):
        """Rental vs deposit must be visible: deposits are held, never revenue."""
        page = PaymentsPage(shell)
        page.refresh()
        _wait_for_payments(page)
        kinds = {
            page.takings.cell_text(r, 5)
            for r in range(page.takings.row_count())
        }
        assert kinds == {"Rental"}

    def test_the_kind_filter_narrows_takings(self, shell, branch):
        built, context = branch
        with context.session() as session:
            session.add(
                Payment(
                    booking_id=built.overdue.booking_id,
                    amount=Decimal("2500.00"),
                    method="cash",
                    kind="deposit",
                    status="paid",
                    paid_at=datetime.combine(TODAY, datetime.min.time()),
                    recorded_by=built.counter.user_id,
                )
            )
        page = PaymentsPage(shell)
        page.refresh()
        _wait_for_payments(page)
        assert page.takings.row_count() == 3
        page.kind_combo.setCurrentIndex(1)  # Rental
        _wait_for_payments(page)
        assert page.takings.row_count() == 2
        page.kind_combo.setCurrentIndex(2)  # Deposit
        _wait_for_payments(page)
        assert page.takings.row_count() == 1
        assert page.takings.cell_text(0, 5) == "Deposit"

    def test_the_takings_total_excludes_deposits(self, shell, branch):
        from app.services import payment_service

        built, context = branch
        with context.session() as session:
            session.add(
                Payment(
                    booking_id=built.overdue.booking_id,
                    amount=Decimal("2500.00"),
                    method="cash",
                    kind="deposit",
                    status="paid",
                    paid_at=datetime.combine(TODAY, datetime.min.time()),
                    recorded_by=built.counter.user_id,
                )
            )
        page = PaymentsPage(shell)
        page.refresh()
        with context.reading() as session:
            expected = payment_service.takings(session)
        assert f"{expected:,.2f}" in page.takings_total.text()
        assert "rental only" in page.takings_total.text()
        # The deposit is listed but never counted: 15000 rental paid only.
        assert expected == Decimal("15000.00")

    def test_one_search_bar_filters_both_tables(self, shell, branch):
        built, _context = branch
        page = PaymentsPage(shell)
        page.refresh()
        _wait_for_payments(page)
        assert page.takings.row_count() == 2
        before_outstanding = page.outstanding.row_count()
        assert before_outstanding > 1
        page.search.setText(f"#{built.overdue.booking_id}")
        _wait_for_payments(page)
        assert page.takings.row_count() == 0
        assert page.outstanding.row_count() == 1
        assert (
            page.outstanding.cell_text(0, 0) == f"#{built.overdue.booking_id}"
        )

    def test_search_matches_customer_and_plate(self, shell, branch):
        built, _context = branch
        page = PaymentsPage(shell)
        page.refresh()
        _wait_for_payments(page)
        page.search.setText("ana")
        _wait_for_payments(page)
        assert page.takings.row_count() == 2
        assert page.outstanding.row_count() > 0
        page.search.setText("LATE-001")
        _wait_for_payments(page)
        takings_refs = {
            page.takings.cell_text(r, 1)
            for r in range(page.takings.row_count())
        }
        assert takings_refs == set()
        listed = {
            page.outstanding.cell_text(r, 0)
            for r in range(page.outstanding.row_count())
        }
        assert listed == {f"#{built.overdue.booking_id}"}

    def test_scope_limits_the_search_to_one_table(self, shell, branch):
        built, _context = branch
        page = PaymentsPage(shell)
        page.refresh()
        _wait_for_payments(page)
        takings_before = page.takings.row_count()
        outstanding_before = page.outstanding.row_count()
        page.search.setText(f"#{built.overdue.booking_id}")
        page.scope_combo.setCurrentIndex(1)  # All payments
        _wait_for_payments(page)
        assert page.takings.row_count() == 0
        assert page.outstanding.row_count() == outstanding_before
        page.scope_combo.setCurrentIndex(2)  # Outstanding
        _wait_for_payments(page)
        assert page.takings.row_count() == takings_before
        assert page.outstanding.row_count() == 1
        page.scope_combo.setCurrentIndex(0)  # All
        _wait_for_payments(page)
        assert page.takings.row_count() == 0
        assert page.outstanding.row_count() == 1

    def test_clear_resets_search_scope_and_filters(self, shell, branch):
        page = PaymentsPage(shell)
        page.refresh()
        _wait_for_payments(page)
        takings_before = page.takings.row_count()
        outstanding_before = page.outstanding.row_count()
        page.search.setText("ana")
        page.scope_combo.setCurrentIndex(2)
        page.kind_combo.setCurrentIndex(2)  # Deposit
        page.clear_button.click()
        _wait_for_payments(page)
        assert page.search.text() == ""
        assert page.scope_combo.currentIndex() == 0
        assert page.method_combo.currentIndex() == 0
        assert page.kind_combo.currentIndex() == 0
        assert page.status_combo.currentIndex() == 0
        assert page.takings.row_count() == takings_before
        assert page.outstanding.row_count() == outstanding_before

    def test_collect_needs_a_selection(self, shell, branch):
        page = PaymentsPage(shell)
        page.refresh()
        assert page.collect_button.isEnabled() is False
        _wait_for_payments(page)
        page.outstanding.table.selectRow(0)
        assert page.collect_button.isEnabled() is True

    def test_collect_opens_the_bookings_pay_form(self, shell, branch):
        """The ledger never pays: Collect lands in Bookings on the same booking."""
        built, _context = branch
        page = PaymentsPage(shell)
        page.refresh()
        _wait_for_payments(page)
        for row in range(page.outstanding.row_count()):
            if page.outstanding.cell_text(row, 0) == f"#{built.overdue.booking_id}":
                page.outstanding.table.selectRow(row)
                break
        assert page._on_collect() is True
        assert shell.current_page_key == "bookings"
        bookings = shell._page_widgets["bookings"]
        assert bookings._booking_overlay is not None

    def test_the_ledger_has_no_pay_form_of_its_own(self, shell, branch):
        from PySide6.QtWidgets import QComboBox

        page = PaymentsPage(shell)
        assert not hasattr(page, "open_record_payment")
        assert page.findChildren(QComboBox) != []

    def test_the_page_uses_the_bookings_containers(self, shell, branch):
        """Same recipe, three boxes: tools hug, two data boxes share the rest."""
        from PySide6.QtWidgets import QSizePolicy

        from app.staff.cards import Card
        from app.staff.metrics import CARD_GUTTER
        from app.staff.pages.bookings import BookingsPage

        _built, _context = branch
        page = PaymentsPage(shell)
        page.refresh()
        assert page.HEADER is False
        assert page._header.isVisible() is False
        assert page.PANEL is not True
        assert page.panel is None
        assert page.findChildren(Card) == []
        assert page._tools_panel.objectName() == "pagePanel"
        assert page._takings_panel.objectName() == "pagePanel"
        assert page._outstanding_panel.objectName() == "pagePanel"
        assert page._tools_panel.parent() is page._takings_panel.parent()
        assert page._takings_panel.parent() is page._outstanding_panel.parent()
        assert page.body.spacing() == CARD_GUTTER
        assert (
            page._tools_panel.sizePolicy().verticalPolicy()
            == QSizePolicy.Policy.Preferred
        )
        assert (
            page._takings_panel.sizePolicy().verticalPolicy()
            == QSizePolicy.Policy.Expanding
        )
        assert (
            page._outstanding_panel.sizePolicy().verticalPolicy()
            == QSizePolicy.Policy.Expanding
        )
        # Filters + Collect live in tools; each table lives in its own box.
        assert page.method_combo in page._tools_panel.findChildren(type(page.method_combo))
        assert page.collect_button in page._tools_panel.findChildren(type(page.collect_button))
        assert page.takings in page._takings_panel.findChildren(type(page.takings))
        assert page.takings not in page._outstanding_panel.findChildren(type(page.takings))
        assert page.outstanding in page._outstanding_panel.findChildren(type(page.outstanding))
        assert page.outstanding not in page._takings_panel.findChildren(type(page.outstanding))
        margins = page.layout().contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (
            SIDEBAR_GAP, 0, SIDEBAR_GAP, 0,
        )
        # Same boxes as a Bookings page, same spacing.
        bookings = BookingsPage(shell)
        assert page._tools_panel.objectName() == bookings._tools_panel.objectName()
        assert page.body.spacing() == bookings.body.spacing()
        # Even split: both ledger boxes share the leftover height equally.
        assert page.body.stretch(page.body.indexOf(page._takings_panel)) == 1
        assert page.body.stretch(page.body.indexOf(page._outstanding_panel)) == 1


# --------------------------------------------------------------------------
# Inspections
# --------------------------------------------------------------------------


class TestInspectionsPage:
    def test_reports_are_listed(self, shell, branch):
        page = InspectionsPage(shell)
        page.refresh()
        _wait_for_inspections(page)
        assert page.table.row_count() == 1
        # Booking, Vehicle, Customer, Type, When, Odometer, Fuel, Damage, Photo, Inspector
        assert page.table.cell_text(0, 0) == f"#{branch[0].out_booking.booking_id}"
        assert page.table.cell_text(0, 3) == "Pre Rental"

    def test_the_inspector_is_named(self, shell, branch):
        page = InspectionsPage(shell)
        page.refresh()
        _wait_for_inspections(page)
        assert page.table.cell_text(0, 9) == "Counter Staff"

    def test_a_missing_photo_is_shown_not_hidden(self, shell, branch):
        page = InspectionsPage(shell)
        page.refresh()
        _wait_for_inspections(page)
        assert page.table.cell_text(0, 8) == "No"

    def test_an_empty_branch_is_empty_not_broken(self, shell, branch):
        built, context = branch
        with context.session() as session:
            session.query(Inspection_Report).delete()
        page = InspectionsPage(shell)
        page.refresh()
        _wait_for_inspections(page)
        assert page.table.state is LoadState.EMPTY

    def test_the_page_uses_the_bookings_containers(self, shell, branch):
        """Same recipe: tools hug, data fills, no header, no outer sheet."""
        from PySide6.QtWidgets import QSizePolicy

        from app.staff.cards import Card
        from app.staff.metrics import CARD_GUTTER

        _built, _context = branch
        page = InspectionsPage(shell)
        page.refresh()
        assert page.HEADER is False
        assert page._header.isVisible() is False
        assert page.PANEL is not True
        assert page.panel is None
        assert page.findChildren(Card) == []
        assert page._tools_panel.objectName() == "pagePanel"
        assert page._data_panel.objectName() == "pagePanel"
        assert page._tools_panel.parent() is page._data_panel.parent()
        assert page.body.spacing() == CARD_GUTTER
        assert (
            page._tools_panel.sizePolicy().verticalPolicy()
            == QSizePolicy.Policy.Preferred
        )
        assert (
            page._data_panel.sizePolicy().verticalPolicy()
            == QSizePolicy.Policy.Expanding
        )
        assert page.search in page._tools_panel.findChildren(type(page.search))
        assert page.open_button in page._tools_panel.findChildren(type(page.open_button))
        assert page.table in page._data_panel.findChildren(type(page.table))
        margins = page.layout().contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (
            SIDEBAR_GAP, 0, SIDEBAR_GAP, 0,
        )

    def test_the_type_filter_narrows_the_list(self, shell, branch):
        built, context = branch
        with context.session() as session:
            session.add(
                Inspection_Report(
                    booking_id=built.out_booking.booking_id,
                    inspection_type="post-rental",
                    inspected_by=built.counter.user_id,
                    mileage_reading=12500,
                    fuel_level="half",
                    damage_notes=None,
                    photo_url=None,
                    inspected_at=datetime.combine(TODAY, datetime.min.time()),
                )
            )
        page = InspectionsPage(shell)
        page.refresh()
        _wait_for_inspections(page)
        assert page.table.row_count() == 2
        page.type_combo.setCurrentIndex(1)  # Pre rental
        _wait_for_inspections(page)
        assert page.table.row_count() == 1
        assert page.table.cell_text(0, 3) == "Pre Rental"
        page.type_combo.setCurrentIndex(2)  # Post rental
        _wait_for_inspections(page)
        assert page.table.row_count() == 1
        assert page.table.cell_text(0, 3) == "Post Rental"

    def test_damage_only_hides_clean_handovers(self, shell, branch):
        built, context = branch
        with context.session() as session:
            session.add(
                Inspection_Report(
                    booking_id=built.out_booking.booking_id,
                    inspection_type="post-rental",
                    inspected_by=built.counter.user_id,
                    mileage_reading=12500,
                    fuel_level="half",
                    damage_notes="Scratched bumper",
                    photo_url=None,
                    inspected_at=datetime.combine(TODAY, datetime.min.time()),
                )
            )
        page = InspectionsPage(shell)
        page.refresh()
        _wait_for_inspections(page)
        assert page.table.row_count() == 2
        page.damage_check.setChecked(True)
        _wait_for_inspections(page)
        assert page.table.row_count() == 1
        assert page.table.cell_text(0, 7) == "Scratched bumper"

    def test_search_finds_booking_customer_and_plate(self, shell, branch):
        built, _context = branch
        page = InspectionsPage(shell)
        page.refresh()
        _wait_for_inspections(page)
        page.search.setText(f"#{built.out_booking.booking_id}")
        _wait_for_inspections(page)
        assert page.table.row_count() == 1
        page.search.setText("OUT-001")
        _wait_for_inspections(page)
        assert page.table.row_count() == 1
        page.search.setText("ana")
        _wait_for_inspections(page)
        assert page.table.row_count() == 1
        page.search.setText("no-such-report")
        _wait_for_inspections(page)
        assert page.table.row_count() == 0

    def test_clear_restores_the_list(self, shell, branch):
        page = InspectionsPage(shell)
        page.refresh()
        _wait_for_inspections(page)
        page.search.setText("ana")
        page.type_combo.setCurrentIndex(2)
        page.damage_check.setChecked(True)
        page.clear_button.click()
        _wait_for_inspections(page)
        assert page.search.text() == ""
        assert page.type_combo.currentIndex() == 0
        assert page.damage_check.isChecked() is False
        assert page.table.row_count() == 1

    def test_open_booking_lands_on_the_report_booking(self, shell, branch):
        built, _context = branch
        page = InspectionsPage(shell)
        page.refresh()
        _wait_for_inspections(page)
        page.table.table.selectRow(0)
        assert page._on_open_booking() is True
        assert shell.current_page_key == "bookings"
        bookings = shell._page_widgets["bookings"]
        _wait_for_bookings(bookings)
        assert bookings.selected_booking_id() == built.out_booking.booking_id

    def test_open_booking_needs_a_selection(self, shell, branch):
        page = InspectionsPage(shell)
        page.refresh()
        assert page.open_button.isEnabled() is False
        _wait_for_inspections(page)
        page.table.table.selectRow(0)
        assert page.open_button.isEnabled() is True


# --------------------------------------------------------------------------
# Maintenance
# --------------------------------------------------------------------------


class TestMaintenancePage:
    def test_the_shop_record_is_listed(self, shell, branch):
        built, _context = branch
        page = MaintenancePage(shell)
        page.refresh()
        _wait_for_maintenance(page)
        assert page.table.row_count() == 1
        # Ref, Vehicle, Type, Description, From, To, Cost, Status
        assert built.shop_car.plate_number in page.table.cell_text(0, 1)
        assert page.table.cell_text(0, 2) == "Tire / brake"
        assert page.table.cell_text(0, 3) == "Brake pads"
        assert page.table.cell_text(0, 6) == "4,500.00"

    def test_the_page_uses_the_bookings_containers(self, shell, branch):
        """Same recipe: tools hug, data fills, no header, no outer sheet."""
        from PySide6.QtWidgets import QSizePolicy

        from app.staff.cards import Card
        from app.staff.metrics import CARD_GUTTER

        _built, _context = branch
        page = MaintenancePage(shell)
        page.refresh()
        assert page.HEADER is False
        assert page._header.isVisible() is False
        assert page.PANEL is not True
        assert page.panel is None
        assert page.findChildren(Card) == []
        assert page._tools_panel.objectName() == "pagePanel"
        assert page._due_panel.objectName() == "pagePanel"
        assert page._log_panel.objectName() == "pagePanel"
        assert page._tools_panel.parent() is page._due_panel.parent()
        assert page._due_panel.parent() is page._log_panel.parent()
        assert page.body.spacing() == CARD_GUTTER
        assert (
            page._tools_panel.sizePolicy().verticalPolicy()
            == QSizePolicy.Policy.Preferred
        )
        assert (
            page._log_panel.sizePolicy().verticalPolicy()
            == QSizePolicy.Policy.Expanding
        )
        assert page.search in page._tools_panel.findChildren(type(page.search))
        assert page.open_button in page._tools_panel.findChildren(type(page.open_button))
        assert page.table in page._log_panel.findChildren(type(page.table))
        assert page.due in page._due_panel.findChildren(type(page.due))
        margins = page.layout().contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (
            SIDEBAR_GAP, 0, SIDEBAR_GAP, 0,
        )

    def test_the_status_filter_narrows_the_list(self, shell, branch):
        built, context = branch
        with context.session() as session:
            session.add(
                Maintenance_Record(
                    vehicle_id=built.shop_car.vehicle_id,
                    description="Oil change",
                    start_date=TODAY - timedelta(days=30),
                    end_date=TODAY - timedelta(days=29),
                    cost=Decimal("1500.00"),
                    status="completed",
                )
            )
        page = MaintenancePage(shell)
        page.refresh()
        _wait_for_maintenance(page)
        assert page.table.row_count() == 2
        page.status_combo.setCurrentIndex(2)  # In Progress
        _wait_for_maintenance(page)
        assert page.table.row_count() == 1
        assert "Brake pads" in page.table.cell_text(0, 3)
        page.status_combo.setCurrentIndex(3)  # Completed
        _wait_for_maintenance(page)
        assert page.table.row_count() == 1
        assert "Oil change" in page.table.cell_text(0, 3)

    def test_search_finds_plate_vehicle_and_job(self, shell, branch):
        page = MaintenancePage(shell)
        page.refresh()
        _wait_for_maintenance(page)
        page.search.setText("SHOP-001")
        _wait_for_maintenance(page)
        assert page.table.row_count() == 1
        page.search.setText("brake")
        _wait_for_maintenance(page)
        assert page.table.row_count() == 1
        page.search.setText("no-such-job")
        _wait_for_maintenance(page)
        assert page.table.row_count() == 0

    def test_clear_restores_the_list(self, shell, branch):
        page = MaintenancePage(shell)
        page.refresh()
        _wait_for_maintenance(page)
        page.search.setText("brake")
        page.status_combo.setCurrentIndex(3)
        page.clear_button.click()
        _wait_for_maintenance(page)
        assert page.search.text() == ""
        assert page.status_combo.currentIndex() == 0
        assert page.table.row_count() == 1

    def test_open_vehicle_lands_on_the_fleet_row(self, shell, branch):
        built, _context = branch
        page = MaintenancePage(shell)
        page.refresh()
        _wait_for_maintenance(page)
        page.table.table.selectRow(0)
        assert page._on_open_vehicle() is True
        assert shell.current_page_key == "fleet"
        fleet = shell._page_widgets["fleet"]
        _wait_for_fleet(fleet)
        assert fleet.selected_plate() == built.shop_car.plate_number

    def test_open_vehicle_needs_a_selection(self, shell, branch):
        page = MaintenancePage(shell)
        page.refresh()
        assert page.open_button.isEnabled() is False
        _wait_for_maintenance(page)
        page.table.table.selectRow(0)
        assert page.open_button.isEnabled() is True

    def test_add_panel_warns_about_clashes_without_crashing(self, shell, branch):
        """Regression: the clash preview once read detached booking rows
        after the session closed (DetachedInstanceError on open)."""
        from app.services import booking_service
        from app.staff.maintenance_panels import RecordPanel

        built, context = branch
        with context.session() as session:
            customer = session.get(Users, built.customer.user_id)
            car = session.get(Vehicle, built.free_car.vehicle_id)
            booking = booking_service.create_booking(
                session, customer, car,
                TODAY, TODAY + timedelta(days=3),
            )
            booking_id = booking.booking_id
        page = MaintenancePage(shell)
        page.refresh()
        panel = RecordPanel(context, page)
        try:
            index = next(
                i for i, (vid, _label) in enumerate(panel._vehicles)
                if vid == built.free_car.vehicle_id
            )
            panel.vehicle_combo.setCurrentIndex(index)
            assert f"#{booking_id}" in panel.clash_note.text()
        finally:
            panel.deleteLater()

    def _select_ref(self, page, ref: int) -> None:
        for row in range(page.table.row_count()):
            if page.table.cell_text(row, 0) == str(ref):
                page.table.table.selectRow(row)
                return
        raise AssertionError(f"ref {ref} not in the table")

    def test_logging_a_job_puts_the_car_in_the_shop(self, shell, branch, monkeypatch):
        import app.staff.pages.maintenance as module

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        built, context = branch
        page = MaintenancePage(shell)
        page.refresh()
        assert page.run(
            "add",
            vehicle_id=built.free_car.vehicle_id,
            maintenance_type="routine",
            status="in_progress",
            start_date=TODAY,
            end_date=None,
            estimate_cost=Decimal("3000.00"),
            odometer=None,
            shop="Shop",
            booking_id=None,
            description="Oil change",
        ) is True
        with context.reading() as session:
            car = session.get(Vehicle, built.free_car.vehicle_id)
            assert car.status == "maintenance"

    def test_mark_completed_releases_the_car(self, shell, branch, monkeypatch):
        import app.staff.pages.maintenance as module
        from sqlalchemy import select as _select

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        built, context = branch
        page = MaintenancePage(shell)
        page.refresh()
        assert page.run(
            "add",
            vehicle_id=built.free_car.vehicle_id,
            maintenance_type="repair",
            status="in_progress",
            start_date=TODAY,
            end_date=None,
            estimate_cost=Decimal("3000.00"),
            odometer=None,
            shop="",
            booking_id=None,
            description="Fix bumper",
        ) is True
        with context.reading() as session:
            record = session.execute(
                _select(Maintenance_Record).where(
                    Maintenance_Record.vehicle_id == built.free_car.vehicle_id,
                    Maintenance_Record.status == "in_progress",
                )
            ).scalar_one()
            ref = record.maintenance_id
        page.refresh()
        _wait_for_maintenance(page)
        self._select_ref(page, ref)
        assert page.run(
            "complete",
            end_date=TODAY,
            cost=Decimal("3200.00"),
            odometer=11000,
            next_due_date=TODAY + timedelta(days=90),
            next_due_odometer=None,
        ) is True
        with context.reading() as session:
            car = session.get(Vehicle, built.free_car.vehicle_id)
            assert car.status == "available"
            assert car.mileage == 11000

    def test_cancel_keeps_the_row_on_the_log(self, shell, branch, monkeypatch):
        import app.staff.pages.maintenance as module

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        built, context = branch
        page = MaintenancePage(shell)
        page.refresh()
        _wait_for_maintenance(page)
        self._select_ref(page, 1)
        assert page.run("cancel", reason="Parts unavailable") is True
        with context.reading() as session:
            record = session.get(Maintenance_Record, 1)
            assert record.status == "cancelled"
            car = session.get(Vehicle, built.shop_car.vehicle_id)
            assert car.status == "available"

    def test_due_soon_flags_a_recent_completion(self, shell, branch, monkeypatch):
        import app.staff.pages.maintenance as module

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        built, _context = branch
        page = MaintenancePage(shell)
        page.refresh()
        _wait_for_maintenance(page)
        # Next-due dates are stamped at completion: close the shop job with
        # one three days out, and the due list picks it up.
        self._select_ref(page, 1)
        assert page.run(
            "complete",
            end_date=TODAY,
            cost=Decimal("4500.00"),
            odometer=None,
            next_due_date=TODAY + timedelta(days=3),
            next_due_odometer=None,
        ) is True
        page.refresh()
        _wait_for_maintenance(page)
        assert page.due.row_count() >= 1
        assert built.shop_car.plate_number in page.due.cell_text(0, 0)
        assert "3 day" in page.due.cell_text(0, 1)

    def test_spend_line_totals_completed_work(self, shell, branch):
        page = MaintenancePage(shell)
        page.refresh()
        assert "No completed spend" in page.spend_line.text()


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


class TestReportsPage:
    def test_the_page_uses_the_bookings_containers(self, shell, branch):
        """Same recipe: tools hug, data fills, no header, no outer sheet."""
        from PySide6.QtWidgets import QSizePolicy

        from app.staff.cards import Card
        from app.staff.metrics import CARD_GUTTER

        _built, _context = branch
        page = ReportsPage(shell)
        page.refresh()
        assert page.HEADER is False
        assert page._header.isVisible() is False
        assert page.PANEL is not True
        assert page.panel is None
        assert page.findChildren(Card) == []
        assert page._tools_panel.objectName() == "pagePanel"
        assert page._data_panel.objectName() == "pagePanel"
        assert page._tools_panel.parent() is page._data_panel.parent()
        assert page.body.spacing() == CARD_GUTTER
        assert (
            page._tools_panel.sizePolicy().verticalPolicy()
            == QSizePolicy.Policy.Preferred
        )
        assert (
            page._data_panel.sizePolicy().verticalPolicy()
            == QSizePolicy.Policy.Expanding
        )
        assert page.preset_combo in page._tools_panel.findChildren(type(page.preset_combo))
        assert page.table in page._data_panel.findChildren(type(page.table))
        margins = page.layout().contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (
            SIDEBAR_GAP, 0, SIDEBAR_GAP, 0,
        )

    def test_the_default_month_counts_the_settled_rental(self, shell, branch):
        """The branch paid 15,000 fifteen days ago: inside Last 30 days."""
        page = ReportsPage(shell)
        page.refresh()
        _wait_for_reports(page)
        assert page.preset_combo.currentText() == "Last 30 days"
        assert page.table.row_count() == 30
        assert "15,000.00" in page.summary.text()
        assert "= rental" in page.summary.text()

    def test_today_is_a_single_quiet_day(self, shell, branch):
        page = ReportsPage(shell)
        page.refresh()
        _wait_for_reports(page)
        page.preset_combo.setCurrentText("Today")
        _wait_for_reports(page)
        assert page.table.row_count() == 1
        assert TODAY.strftime("%d %b") in page.table.cell_text(0, 0)
        # Only a pending GCash transfer exists today: nothing collected.
        assert page.table.cell_text(0, 1) == "0.00"

    def test_an_inverted_range_is_refused_not_crashed(self, shell, branch):
        from PySide6.QtCore import QDate

        page = ReportsPage(shell)
        page.refresh()
        _wait_for_reports(page)
        before = page.table.row_count()
        page.from_edit.setDate(QDate.currentDate().addDays(5))
        page.to_edit.setDate(QDate.currentDate())
        # isHidden, not isVisible: the page itself is never shown in tests,
        # so nothing in it can be "visible" -- hidden state is the signal.
        assert page._error.isHidden() is False
        assert "must not be after" in page._error.text()
        assert page.table.row_count() == before

    def test_touching_a_date_marks_the_preset_custom(self, shell, branch):
        page = ReportsPage(shell)
        page.refresh()
        _wait_for_reports(page)
        assert page.preset_combo.currentText() == "Last 30 days"
        page.from_edit.setDate(page.from_edit.date().addDays(1))
        assert page.preset_combo.currentText() == "Custom"
        _wait_for_reports(page)
        assert page.table.row_count() == 29

    def test_switching_reports_swaps_table_and_summary(self, shell, branch):
        built, context = branch
        with context.session() as session:
            booking = session.get(Booking, built.out_booking.booking_id)
            booking.created_at = datetime.combine(TODAY, datetime.min.time())
        page = ReportsPage(shell)
        page.refresh()
        _wait_for_reports(page)
        assert page._report == "revenue"
        page._pick_report("bookings")
        _wait_for_reports(page)
        assert page._report == "bookings"
        assert "Bookings" in page.summary.text()
        assert page.table.row_count() >= 1
        refs = {
            page.table.cell_text(r, 0)
            for r in range(page.table.row_count())
        }
        assert f"#{built.out_booking.booking_id}" in refs
        page._pick_report("revenue")
        _wait_for_reports(page)
        assert "Revenue" in page.summary.text()

    def test_granularity_buckets_the_revenue(self, shell, branch):
        page = ReportsPage(shell)
        page.refresh()
        _wait_for_reports(page)
        panel = page._panels["revenue"]
        panel.granularity_combo.setCurrentText("Week")
        _wait_for_reports(page)
        # Week and month counts depend on the weekday: totals must agree,
        # and the paid rental must land in exactly one bucket of each
        # granularity. Rows list oldest first, so row 0 is not "current" —
        # the single payment sits mid-window by construction.
        week_totals = [
            panel.table.cell_text(r, 3) for r in range(panel.table.row_count())
        ]
        week_sum = sum(float(value.replace(",", "")) for value in week_totals)
        assert panel.table.row_count() in (4, 5, 6)
        assert sum(1 for value in week_totals if value != "0.00") == 1
        panel.granularity_combo.setCurrentText("Month")
        _wait_for_reports(page)
        month_totals = [
            panel.table.cell_text(r, 3) for r in range(panel.table.row_count())
        ]
        month_sum = sum(float(value.replace(",", "")) for value in month_totals)
        assert month_sum == week_sum
        assert sum(1 for value in month_totals if value != "0.00") == 1

    def test_channel_filter_narrows_both_reports(self, shell, branch):
        built, context = branch
        with context.session() as session:
            booking = session.get(Booking, built.finished.booking_id)
            booking.channel = "walk_in"
            booking.created_at = datetime.combine(TODAY, datetime.min.time())
        page = ReportsPage(shell)
        page.refresh()
        _wait_for_reports(page)
        revenue = page._panels["revenue"]
        revenue.channel_combo.setCurrentIndex(1)  # Walk-in
        _wait_for_reports(page)
        assert "15,000.00" in revenue.summary.text()
        revenue.channel_combo.setCurrentIndex(2)  # Online
        _wait_for_reports(page)
        assert revenue.table.row_count() == 30
        assert revenue.table.cell_text(0, 3) == "0.00"
        page._pick_report("bookings")
        _wait_for_reports(page)
        bookings = page._panels["bookings"]
        bookings.channel_combo.setCurrentIndex(1)  # Walk-in
        _wait_for_reports(page)
        assert bookings.table.row_count() >= 1

    def test_export_writes_csv_and_pdf(self, shell, branch, tmp_path, monkeypatch):
        import app.services.export_service as exports
        from PySide6.QtGui import QDesktopServices

        monkeypatch.setattr(
            exports, "EXPORTS_DIR", tmp_path,
            raising=False,
        )
        opened = []
        monkeypatch.setattr(
            QDesktopServices, "openUrl", lambda url: opened.append(url) or True
        )
        page = ReportsPage(shell)
        page.refresh()
        assert page._export("csv") is True
        assert page._export("pdf") is True
        names = sorted(p.name for p in tmp_path.iterdir())
        assert any(n.startswith("report_revenue") and n.endswith(".csv") for n in names)
        assert any(n.startswith("report_revenue") and n.endswith(".pdf") for n in names)
        assert len(opened) == 2

    def test_export_receives_all_501_bookings(
        self, shell, branch, tmp_path, monkeypatch,
    ):
        import app.services.export_service as exports
        from PySide6.QtGui import QDesktopServices

        built, context = branch
        created = datetime.combine(TODAY, datetime.min.time()).replace(hour=12)
        with context.session() as session:
            session.add_all(
                [
                    Booking(
                        user_id=built.customer.user_id,
                        vehicle_id=built.free_car.vehicle_id,
                        start_date=TODAY - timedelta(days=2),
                        end_date=TODAY - timedelta(days=1),
                        actual_return_date=None,
                        total_cost=Decimal("7500.00"),
                        status="completed",
                        created_at=created,
                    )
                    for _ in range(501)
                ]
            )
        monkeypatch.setattr(exports, "EXPORTS_DIR", tmp_path, raising=False)
        monkeypatch.setattr(
            QDesktopServices, "openUrl", lambda _url: True,
        )
        captured: dict[str, int] = {}

        def fake_csv(path, headers, rows):
            captured["csv"] = len(rows)
            return path

        def fake_pdf(path, *, rows, **_kwargs):
            captured["pdf"] = len(rows)
            return path

        monkeypatch.setattr(exports, "write_csv", fake_csv)
        monkeypatch.setattr(exports, "write_pdf", fake_pdf)
        page = ReportsPage(shell)
        page.refresh()
        page._pick_report("bookings")
        assert page._export("csv") is True
        assert page._export("pdf") is True
        assert captured == {"csv": 501, "pdf": 501}

    def test_every_report_loads_a_summary_and_a_table(self, shell, branch):
        page = ReportsPage(shell)
        page.refresh()
        _wait_for_reports(page)
        for key in (
            "revenue", "bookings", "utilization", "performance",
            "maintenance", "receivables", "customers", "staff",
        ):
            page._pick_report(key)
            _wait_for_reports(page)
            assert page._report == key
            assert page.summary.text().strip() != ""
            assert page.table.row_count() >= 0

    def test_every_report_exports_csv_and_pdf(self, shell, branch, tmp_path, monkeypatch):
        import app.services.export_service as exports
        from PySide6.QtGui import QDesktopServices

        monkeypatch.setattr(exports, "EXPORTS_DIR", tmp_path, raising=False)
        opened = []
        monkeypatch.setattr(
            QDesktopServices, "openUrl", lambda url: opened.append(url) or True
        )
        page = ReportsPage(shell)
        page.refresh()
        _wait_for_reports(page)
        for key in (
            "revenue", "bookings", "utilization", "performance",
            "maintenance", "receivables", "customers", "staff",
        ):
            page._pick_report(key)
            _wait_for_reports(page)
            assert page._export("csv") is True
            assert page._export("pdf") is True
        names = sorted(p.name for p in tmp_path.iterdir())
        for key in (
            "revenue", "bookings", "utilization", "performance",
            "maintenance_cost", "receivables", "customers", "staff",
        ):
            assert any(
                n.startswith(f"report_{key}") and n.endswith(".csv") for n in names
            ), key
            assert any(
                n.startswith(f"report_{key}") and n.endswith(".pdf") for n in names
            ), key

    def test_utilization_ranks_the_fleet(self, shell, branch):
        page = ReportsPage(shell)
        page.refresh()
        page._pick_report("utilization")
        _wait_for_reports(page)
        assert page.table.row_count() > 0
        assert "vehicle(s)" in page.summary.text()

    def test_receivables_show_what_is_owed(self, shell, branch):
        page = ReportsPage(shell)
        page.refresh()
        page._pick_report("receivables")
        _wait_for_reports(page)
        assert page.table.row_count() >= 1
        assert "Owed" in page.summary.text()

    def test_report_chrome_is_transparent_not_cream(self, shell, branch):
        """Only data tables paint a background: the list host, rows, stack,
        panel roots, and charts all show the white data box through."""
        from app.staff import theme

        page = ReportsPage(shell)
        page.setStyleSheet(theme.load_stylesheet())
        for row in page._rows.values():
            assert row.objectName() == "reportRow"
        for panel in page._panels.values():
            assert panel.objectName() == "reportPanelHost"
        charts = page.findChildren(QWidget, "reportChart")
        assert charts != []
        assert page._stack.objectName() == "reportStack"

    def test_data_box_has_no_cream_pixels(self, shell, branch, qapp):
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QWidget
        from app.staff import theme

        page = ReportsPage(shell)
        page.setStyleSheet(theme.load_stylesheet())
        page.refresh()
        page.show()
        page.resize(1440, 900)
        qapp_process(page)
        # The report-type sidebar: plain host + rows on the white data box.
        # (Table headers keep their long-standing look -- tables are out of
        # scope by request -- so the scan covers the list, not the tables.)
        host = page.findChild(QWidget, "reportListHost")
        img = host.grab().toImage()
        cream = QColor(241, 232, 216)
        found = 0
        for y in range(img.height()):
            for x in range(img.width()):
                pixel = img.pixelColor(x, y)
                if (
                    abs(pixel.red() - cream.red()) <= 6
                    and abs(pixel.green() - cream.green()) <= 6
                    and abs(pixel.blue() - cream.blue()) <= 6
                ):
                    found += 1
        assert found == 0, f"{found} cream pixels in the report list"


# --------------------------------------------------------------------------
# Async shimmer: every reloaded table shows shimmer synchronously
# --------------------------------------------------------------------------


class TestAsyncShimmer:
    def test_payments_shimmers_both_tables(self, shell, branch):
        page = PaymentsPage(shell)
        page.refresh()
        assert page.takings._stack.currentIndex() == 3
        assert page.outstanding._stack.currentIndex() == 3
        _wait_for_payments(page)
        assert page.takings._stack.currentIndex() == 0
        assert page.outstanding._stack.currentIndex() == 0

    def test_inspections_shimmers(self, shell, branch):
        page = InspectionsPage(shell)
        page.refresh()
        assert page.table._stack.currentIndex() == 3
        _wait_for_inspections(page)
        assert page.table._stack.currentIndex() == 0

    def test_maintenance_shimmers_both_tables(self, shell, branch):
        page = MaintenancePage(shell)
        page.refresh()
        assert page.due._stack.currentIndex() == 3
        assert page.table._stack.currentIndex() == 3
        _wait_for_maintenance(page)
        assert page.due._stack.currentIndex() != 3
        assert page.table._stack.currentIndex() == 0

    def test_reports_shimmer_the_visible_table(self, shell, branch):
        page = ReportsPage(shell)
        page.refresh()
        assert page.table._stack.currentIndex() == 3
        _wait_for_reports(page)
        assert page.table._stack.currentIndex() == 0
        page._pick_report("staff")
        assert page.table._stack.currentIndex() == 3
        _wait_for_reports(page)


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




def _wait_for_bookings(page, timeout_ms: int = 15000) -> None:
    """Block until the page's async reload has landed.

    refresh()/run()/filter edits return immediately now (shimmer shows,
    rows land on the pool signal). Flush a pending debounced filter,
    drain the pool, then pump until the generation catches up.
    """
    import time

    if page._filter_timer.isActive():
        page._filter_timer.stop()
        page._on_filter_timeout()
    page._pool.waitForDone(15000)
    deadline = time.monotonic() + timeout_ms / 1000
    while page._done_gen != page._load_gen and time.monotonic() < deadline:
        QTest.qWait(5)
    assert page._done_gen == page._load_gen, "bookings reload never landed"


def _wait_for_fleet(page, timeout_ms: int = 15000) -> None:
    """Block until the fleet async reload has landed."""
    import time

    if page._filter_timer.isActive():
        page._filter_timer.stop()
        page._on_filter_timeout()
    page._pool.waitForDone(15000)
    deadline = time.monotonic() + timeout_ms / 1000
    while page._done_gen != page._load_gen and time.monotonic() < deadline:
        QTest.qWait(5)
    assert page._done_gen == page._load_gen, "fleet reload never landed"


def _wait_for_customers(page, timeout_ms: int = 15000) -> None:
    """Block until the customers async reload has landed."""
    import time

    if page._filter_timer.isActive():
        page._filter_timer.stop()
        page._on_filter_timeout()
    page._pool.waitForDone(15000)
    deadline = time.monotonic() + timeout_ms / 1000
    while page._done_gen != page._load_gen and time.monotonic() < deadline:
        QTest.qWait(5)
    assert page._done_gen == page._load_gen, "customers reload never landed"


def _wait_for_tables(page, *controllers, timeout_ms: int = 15000) -> None:
    """Block until shared-helper (AsyncTable) reloads have landed.

    Flushes a pending debounced search first (mirroring _wait_for_bookings),
    drains the pool, then pumps until every generation catches up.
    """
    import time

    timer = getattr(page, "_search_timer", None)
    if timer is not None and timer.isActive():
        timer.stop()
        page._apply_filters()
    page._pool.waitForDone(15000)
    deadline = time.monotonic() + timeout_ms / 1000
    while (
        any(c.done_gen != c.load_gen for c in controllers)
        and time.monotonic() < deadline
    ):
        QTest.qWait(5)
    assert all(c.done_gen == c.load_gen for c in controllers), (
        "async reload never landed"
    )


def _wait_for_payments(page, timeout_ms: int = 15000) -> None:
    _wait_for_tables(page, page._takings_async, page._outstanding_async, timeout_ms=timeout_ms)


def _wait_for_inspections(page, timeout_ms: int = 15000) -> None:
    _wait_for_tables(page, page._table_async, timeout_ms=timeout_ms)


def _wait_for_maintenance(page, timeout_ms: int = 15000) -> None:
    _wait_for_tables(page, page._due_async, page._table_async, timeout_ms=timeout_ms)


def _wait_for_reports(page, timeout_ms: int = 15000) -> None:
    _wait_for_tables(page, page._async[page._report], timeout_ms=timeout_ms)


class TestBookingsPageActions:
    def _select(self, page: BookingsPage, booking_ref: str) -> None:
        _wait_for_bookings(page)
        for row in range(page.table.row_count()):
            if page.table.cell_text(row, 0) == booking_ref:
                page.table.table.selectRow(row)
                return
        raise AssertionError(f"{booking_ref} not in the table")

    def test_the_list_shows_the_balance(self, shell, branch):
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
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
        _wait_for_bookings(page)
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
        _wait_for_bookings(page)
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
        _wait_for_bookings(page)
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
        _wait_for_bookings(page)
        self._select(page, f"#{built.upcoming.booking_id}")
        assert page.can("cancel") is True
        assert page.cancel_booking("Customer changed their mind") is True
        with context.reading() as session:
            booking = session.get(Booking, built.upcoming.booking_id)
            assert booking.status == "cancelled"

    def test_a_cancelled_booking_cannot_be_cancelled_again(self, shell, branch, monkeypatch):
        """A service refusal has to surface, not quietly succeed."""
        import app.staff.pages.bookings as module

        shown = []
        monkeypatch.setattr(module, "toast", lambda *a, **k: shown.append(a))
        monkeypatch.setattr(module, "confirm", lambda *a, **k: True)
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)

        self._select(page, f"#{built.upcoming.booking_id}")
        assert page.cancel_booking("First reason") is True
        # The table reloads after the cancel, so the row must be found again.
        self._select(page, f"#{built.upcoming.booking_id}")
        assert page.cancel_booking("Second reason") is False
        assert any(
            len(a) > 2 and a[2] == "error" for a in shown
        ), "the second cancel was refused silently"

    def test_recording_a_payment_updates_the_balance(self, shell, branch, monkeypatch):
        import app.staff.pages.bookings as module

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        built, context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
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
        _wait_for_bookings(page)
        row = next(
            r
            for r in range(page.table.row_count())
            if page.table.cell_text(r, 0) == f"#{built.overdue.booking_id}"
        )
        assert page.table.cell_text(row, 7) == "1,000.00"
        assert page.table.cell_text(row, 8) == "6,500.00"

    def test_payment_for_opens_without_a_selection(self, shell, branch, monkeypatch):
        """The Payments ledger deep-link: pay by id, not by selected row."""
        import app.staff.pages.bookings as module

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        built, context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
        page.table.table.clearSelection()
        page.table.table.setCurrentCell(-1, -1)
        assert page.open_payment_for(built.overdue.booking_id) is True
        assert page._booking_overlay is not None
        assert page.run_for(
            built.overdue.booking_id,
            "record_payment",
            amount=Decimal("1000.00"),
            method="cash",
            reference_no="",
            note="",
        ) is True
        with context.reading() as session:
            booking = session.get(Booking, built.overdue.booking_id)
            assert len(booking.payments) == 1

    def test_a_nonsense_amount_is_refused_and_says_so(self, shell, branch, monkeypatch):
        """A zero payment is a typo, and absorbing it would leave the balance
        looking settled when it is not."""
        import app.staff.pages.bookings as module

        messages = []
        monkeypatch.setattr(module, "toast", lambda *a, **k: messages.append(a))
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
        self._select(page, f"#{built.overdue.booking_id}")
        assert page.run(
            "record_payment",
            amount=Decimal("0.00"),
            method="cash",
            reference_no="",
            note="",
        ) is False
        assert any(
            len(m) > 2 and m[2] == "error" for m in messages
        ), "the refusal was silent"

    def test_an_unknown_method_is_refused(self, shell, branch, monkeypatch):
        import app.staff.pages.bookings as module

        shown = []
        monkeypatch.setattr(module, "toast", lambda *a, **k: shown.append(a))
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
        self._select(page, f"#{built.overdue.booking_id}")
        assert page.run(
            "record_payment",
            amount=Decimal("100.00"),
            method="goats",
            reference_no="",
            note="",
        ) is False
        assert any(len(a) > 2 and a[2] == "error" for a in shown)

    def test_a_cancelled_booking_takes_no_payment(self, shell, branch, monkeypatch):
        """Money must not disappear into a cancelled rental. The service is
        what refuses it; the page only has to surface that."""
        import app.staff.pages.bookings as module

        shown = []
        monkeypatch.setattr(module, "toast", lambda *a, **k: shown.append(a))
        monkeypatch.setattr(module, "confirm", lambda *a, **k: True)
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
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
        assert any(len(a) > 2 and a[2] == "error" for a in shown)

    def test_a_vanished_booking_is_reported(self, shell, branch, monkeypatch):
        import app.staff.pages.bookings as module

        shown = []
        monkeypatch.setattr(module, "toast", lambda *a, **k: shown.append(a))
        built, context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
        self._select(page, f"#{built.upcoming.booking_id}")
        with context.session() as session:
            session.delete(session.get(Booking, built.upcoming.booking_id))
        assert page.run("confirm") is False
        assert any(
            len(a) > 2 and a[2] == "error" for a in shown
        ), "a missing booking was refused silently"

    def test_the_selection_is_read_as_an_id_not_a_row(self, shell, branch):
        """`context.reading()` expires everything it hands back, so a row
        taken from it raises on the next attribute read. Only the id crosses
        the session boundary."""
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
        self._select(page, f"#{built.upcoming.booking_id}")
        assert page.selected_booking_id() == built.upcoming.booking_id
        assert isinstance(page.selected_booking_id(), int)

    def test_filter_by_status_narrows_the_list(self, shell, branch):
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
        all_refs = {
            page.table.cell_text(r, 0) for r in range(page.table.row_count())
        }
        assert f"#{built.upcoming.booking_id}" in all_refs
        page.status_combo.setCurrentText("Completed")
        _wait_for_bookings(page)
        refs = {
            page.table.cell_text(r, 0) for r in range(page.table.row_count())
        }
        assert f"#{built.finished.booking_id}" in refs
        assert f"#{built.upcoming.booking_id}" not in refs

    def test_search_finds_a_customer(self, shell, branch):
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
        page.search.setText("no-such-customer-xyz")
        _wait_for_bookings(page)
        assert page.table.row_count() == 0
        page.search.setText("Ana")
        _wait_for_bookings(page)
        refs = {
            page.table.cell_text(r, 0) for r in range(page.table.row_count())
        }
        assert f"#{built.upcoming.booking_id}" in refs

    def test_overdue_only_shows_overdue(self, shell, branch):
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
        page.overdue_check.setChecked(True)
        _wait_for_bookings(page)
        refs = {
            page.table.cell_text(r, 0) for r in range(page.table.row_count())
        }
        assert f"#{built.overdue.booking_id}" in refs
        assert f"#{built.upcoming.booking_id}" not in refs

    def test_action_buttons_follow_the_role(self, shell, branch):
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
        assert page.confirm_button.isEnabled() is True
        assert page.cancel_button.isEnabled() is True
        as_staff(branch)
        staff_page = BookingsPage(shell)
        staff_page.refresh()
        _wait_for_bookings(staff_page)
        assert staff_page.confirm_button.isEnabled() is False
        assert staff_page.cancel_button.isEnabled() is False
        assert staff_page.check_in_button.isEnabled() is True
        assert staff_page.pay_button.isEnabled() is True

    def test_focus_booking_selects_and_finds_the_row(self, shell, branch):
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
        assert page.focus_booking(built.overdue.booking_id) is True
        assert page.selected_booking_id() == built.overdue.booking_id
        assert page.focus_booking(999_999) is False

    def test_check_in_then_out_closes_the_loop(self, shell, branch, monkeypatch):
        import app.staff.pages.bookings as module

        from app.models import Vehicle

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        built, context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
        # upcoming is confirmed: check it out for pickup first.
        self._select(page, f"#{built.upcoming.booking_id}")
        with context.session() as session:
            vehicle = session.get(
                Vehicle, built.promised_car.vehicle_id
            )
            vehicle.mileage = 10_000
        assert page.run(
            "check_in", mileage=12_000, fuel_level="full", damage_notes=""
        ) is True
        with context.reading() as session:
            booking = session.get(Booking, built.upcoming.booking_id)
            assert booking.status == "ongoing"
        # out_booking is already out on the road: settle it and bring it back.
        self._select(page, f"#{built.out_booking.booking_id}")
        with context.session() as session:
            vehicle = session.get(Vehicle, built.out_car.vehicle_id)
            vehicle.mileage = 20_000
        assert page.run(
            "record_payment",
            amount=Decimal("7500.00"),
            method="cash",
            reference_no="",
            note="",
        ) is True
        assert page.run(
            "check_out",
            mileage=20_500,
            fuel_level="full",
            damage_notes="",
            damage_charge=None,
        ) is True
        with context.reading() as session:
            booking = session.get(Booking, built.out_booking.booking_id)
            assert booking.status == "completed"

    def test_extend_pushes_the_return_date(self, shell, branch, monkeypatch):
        import app.staff.pages.bookings as module

        monkeypatch.setattr(module, "toast", lambda *a, **k: None)
        built, context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
        self._select(page, f"#{built.upcoming.booking_id}")
        with context.reading() as session:
            old_end = session.get(Booking, built.upcoming.booking_id).end_date
        assert page.run("extend", new_end_date=old_end + timedelta(days=3)) is True
        with context.reading() as session:
            booking = session.get(Booking, built.upcoming.booking_id)
            assert booking.end_date == old_end + timedelta(days=3)
            assert booking.total_cost > Decimal("7500.00")

    def test_details_panel_builds_sections(self, shell, branch):
        from app.staff.booking_panels import BookingDetailsPanel

        _built, context = branch
        panel = BookingDetailsPanel(context, _built.finished.booking_id)
        texts = [
            panel._body.itemAt(i).widget().text()
            for i in range(panel._body.count())
            if panel._body.itemAt(i).widget() is not None
        ]
        joined = "\n".join(texts)
        assert "Booking" in joined and "Customer" in joined
        assert "Money" in joined and "No penalties." in joined
        panel.deleteLater()

    def test_extend_panel_previews_old_and_new(self, shell, branch):
        from app.staff.booking_panels import ExtendPanel

        built, context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
        self._select(page, f"#{built.upcoming.booking_id}")
        with context.reading() as session:
            end = session.get(Booking, built.upcoming.booking_id).end_date
        panel = ExtendPanel(context, built.upcoming.booking_id, end)
        assert "Was ₱" in panel.preview.text()
        assert "due" in panel.preview.text()
        panel.deleteLater()

    def test_payment_panel_requires_gcash_reference(self, shell, branch):
        from decimal import Decimal as _Decimal

        from app.staff.booking_panels import PaymentPanel

        _built, context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
        seen = []
        panel = PaymentPanel(_Decimal("100.00"))
        panel.submitted.connect(seen.append)
        panel.method_combo.setCurrentText("gcash")
        panel.amount_edit.setText("100.00")
        panel.ref_edit.setText("")
        panel._on_go()
        assert seen == []
        assert panel._error.text() != ""
        panel.ref_edit.setText("GC-1")
        panel._on_go()
        assert len(seen) == 1 and seen[0]["method"] == "gcash"
        panel.deleteLater()

    def test_action_panels_open_in_glass_not_windows(self, shell, branch):
        from app.staff.overlay import FrostedOverlay

        built, _context = branch
        page = BookingsPage(shell)
        shell.show()
        page.show()
        page.refresh()
        _wait_for_bookings(page)
        self._select(page, f"#{built.upcoming.booking_id}")
        assert page.open_check_in() is True
        overlay = page._booking_overlay
        assert isinstance(overlay, FrostedOverlay)
        assert overlay.isWindow() is False
        assert overlay.isVisible() is True
        overlay.hide_overlay()

    def test_controls_and_table_live_in_two_containers(self, shell, branch):
        from app.staff.metrics import CARD_GUTTER

        _built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
        # Two white main containers: tools on top (controls transparent),
        # data below, thin gap. Table keeps its own white box.
        assert page._tools_panel.objectName() == "pagePanel"
        assert page._data_panel.objectName() == "pagePanel"
        assert page.body.spacing() == CARD_GUTTER
        assert page.search in page._tools_panel.findChildren(type(page.search))
        assert page.table in page._data_panel.findChildren(type(page.table))
        assert page.table not in page._tools_panel.findChildren(type(page.table))
        # 8+8 split with the shell gap: sidebar-right (8) + page-left
        # (8) = 16, visually equal to the window-to-sidebar margin.
        margins = page.layout().contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (
            SIDEBAR_GAP, 0, SIDEBAR_GAP, 0,
        )

    def test_the_page_has_two_main_containers(self, shell, branch):
        from PySide6.QtWidgets import QSizePolicy

        from app.staff.cards import Card

        _built, _context = branch
        page = BookingsPage(shell)
        # No outer sheet: exactly two sibling boxes, tools hug, data fills.
        assert page.PANEL is not True
        assert page.panel is None
        assert page.findChildren(Card) == []
        assert page._tools_panel.parent() is page._data_panel.parent()
        assert (
            page._tools_panel.sizePolicy().verticalPolicy()
            == QSizePolicy.Policy.Preferred
        )
        assert (
            page._data_panel.sizePolicy().verticalPolicy()
            == QSizePolicy.Policy.Expanding
        )
        assert page.HEADER is False
        assert page._header.isVisible() is False

    def test_status_pills_are_boxes_not_ovals(self, shell, branch):
        from app.staff import theme as staff_theme

        _built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
        assert "border-radius: 6px" in staff_theme.load_stylesheet().split(
            "QLabel#statusPill", 1
        )[1].split("}", 1)[0]


class TestBookingsSkeleton:
    def test_refresh_shows_shimmer_immediately(self, shell, branch):
        _built, _context = branch
        page = BookingsPage(shell)
        shell.show()
        page.show()
        page.refresh()
        assert page._skel_data.isVisible() is True
        assert page._data_panel.isVisible() is False
        assert page._tools_panel.isVisible() is False
        _wait_for_bookings(page)
        assert page._skel_data.isVisible() is False
        assert page._data_panel.isVisible() is True
        assert page._tools_panel.isVisible() is True

    def test_rows_land_after_shimmer(self, shell, branch):
        built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        _wait_for_bookings(page)
        assert page.table.state is LoadState.LOADED
        refs = {
            page.table.cell_text(r, 0) for r in range(page.table.row_count())
        }
        assert f"#{built.upcoming.booking_id}" in refs

    def test_stale_results_are_dropped(self, shell, branch):
        _built, _context = branch
        page = BookingsPage(shell)
        page.refresh()
        page.refresh()
        _wait_for_bookings(page)
        assert page._done_gen == page._load_gen
        assert page.table.state is LoadState.LOADED

    def test_empty_hides_shimmer_and_says_so(self, shell, branch):
        _built, _context = branch
        page = BookingsPage(shell)
        shell.show()
        page.show()
        page.refresh()
        _wait_for_bookings(page)
        page.search.setText("no-such-customer-xyz")
        _wait_for_bookings(page)
        assert page.table.state is LoadState.EMPTY
        assert page._skel_data.isVisible() is False
        assert page.table._placeholder.isVisible() is True

    def test_failed_hides_shimmer_and_offers_retry(self, shell, branch, monkeypatch):
        import app.staff.pages.bookings as module

        def _boom(_context, _filtr):
            raise RuntimeError("db is gone")

        _built, _context = branch
        page = BookingsPage(shell)
        shell.show()
        page.show()
        page.refresh()
        _wait_for_bookings(page)
        assert page.table.state is LoadState.LOADED
        # Patched after construction: the worker resolves _booking_rows
        # at run time (fails), the table retry uses its bound loader.
        monkeypatch.setattr(module, "_booking_rows", _boom)
        page.refresh()
        _wait_for_bookings(page)
        assert page.table.state is LoadState.FAILED
        assert page._skel_data.isVisible() is False
        assert page.table._error_panel.isVisible() is True
        assert page.table.retry_count == 0
        # Retry goes through the table's own loader and recovers.
        page.table.retry()
        assert page.table.retry_count == 1
        assert page.table.state is LoadState.LOADED

# --------------------------------------------------------------------------
# Refresh behaviour
# --------------------------------------------------------------------------


class TestPageRefresh:
    def test_a_refresh_picks_up_a_colleagues_write(self, shell, branch):
        built, context = branch
        page = PaymentsPage(shell)
        page.refresh()
        _wait_for_payments(page)
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
        _wait_for_payments(page)
        assert page.takings.row_count() == before + 1

    def test_a_table_failure_does_not_take_the_page_down(self, shell, branch, monkeypatch):
        """One broken query should show one failed table, not a dialog over
        every table and a window that has to be reopened."""
        page = PaymentsPage(shell)
        monkeypatch.setattr(
            page, "_load_takings",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        page.refresh()
        _wait_for_payments(page)
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


class TestVehicleDetailsDialog:
    """Check-details popup: dashboard Card styling over the full vehicle row."""

    def _dialog(self, context, vehicle_id):
        from app.staff.vehicle_details import VehicleDetailsDialog

        return VehicleDetailsDialog(context, vehicle_id)

    def test_the_dialog_shows_identity_rate_and_plate(self, qapp, branch):
        _built, context = branch
        vid = _add_vehicle(
            context,
            make="Honda",
            model="Civic",
            plate="HON-001",
            vehicle_class="medium",
            daily_rate="3000.00",
            seats=5,
            transmission="Automatic",
            fuel_type="Petrol",
        )
        dialog = self._dialog(context, vid)
        assert dialog.name_label.text() == "Honda Civic"
        assert "HON-001" in dialog.subtitle_label.text()
        assert dialog.spec_values["Plate"] == "HON-001"
        assert dialog.spec_values["Seats"] == "5"
        assert dialog.spec_values["Transmission"] == "Automatic"

    def test_null_optionals_render_as_dashes_not_crashes(self, qapp, branch):
        _built, context = branch
        vid = _add_vehicle(context, make="Ford", model="Ranger", plate="FOR-001")
        dialog = self._dialog(context, vid)
        assert dialog.spec_values["Seats"] == "\u2014"
        assert dialog.spec_values["Engine"] == "\u2014"
        assert dialog.rent_button.isEnabled() is True

    def test_a_missing_vehicle_offers_only_close(self, qapp, branch):
        _built, context = branch
        dialog = self._dialog(context, 999_999)
        assert dialog.missing_label.isHidden() is False
        assert dialog.rent_button.isEnabled() is False

    def test_rent_publishes_the_vehicle_and_accepts(self, qapp, branch):
        _built, context = branch
        vid = _add_vehicle(context, make="Honda", model="City", plate="HON-002")
        dialog = self._dialog(context, vid)
        seen = []
        dialog.rent_requested.connect(seen.append)
        dialog.rent_button.click()
        assert seen == [vid]
        assert dialog.result() == dialog.DialogCode.Accepted

    def test_close_rejects_without_publishing(self, qapp, branch):
        _built, context = branch
        vid = _add_vehicle(context, make="Kia", model="Rio", plate="KIA-001")
        dialog = self._dialog(context, vid)
        seen = []
        dialog.rent_requested.connect(seen.append)
        dialog.close_button.click()
        assert seen == []
        assert dialog.result() == dialog.DialogCode.Rejected

    def test_clicking_check_details_embeds_that_vehicles_overlay(
        self, qapp, shell, branch
    ):
        _built, context = branch
        _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        page = NewRentalPage(shell)
        shell.show()
        page.show()
        page.resize(1440, 900)
        page.refresh()
        while not page._exhausted:
            page._load_page()
        slot = next(
            s
            for s in page.slots
            if s.vehicle.make == "Honda" and s.vehicle.model == "Civic"
        )
        for section in page._sections:
            section.rail.build_now()
        qapp_process(page)
        assert slot.card is not None

        slot.card.details_requested.emit(slot.vehicle.vehicle_id)
        qapp_process(page)

        overlay = page._details_overlay
        assert overlay is not None
        # Embedded in the staff window: no new OS window ever opens.
        assert overlay.isWindow() is False
        assert overlay.isVisible() is True
        assert overlay.panel._vehicle_id == slot.vehicle.vehicle_id
        assert overlay.panel.name_label.text() == "Honda Civic"
        # Hygiene: an open overlay keeps an event filter installed, whose
        # teardown-time events would otherwise bleed into the next test.
        overlay.hide_overlay()

    def test_rent_records_the_pick_on_the_page(self, qapp, shell, branch):
        from app.staff.rental_wizard import SchedulePanel

        _built, context = branch
        _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        page = NewRentalPage(shell)
        shell.show()
        page.show()
        page.refresh()
        assert page.selected_vehicle_id is None
        vid = next(s.vehicle.vehicle_id for s in page.slots)
        page._open_vehicle_details(vid)
        qapp_process(page)
        overlay = page._details_overlay
        overlay.panel.rent_button.click()
        qapp_process(page)
        assert page.selected_vehicle_id == vid
        assert isinstance(page._wizard._current, SchedulePanel)
        overlay.hide_overlay()

    def test_photo_box_is_white_and_tall(self, qapp, branch):
        from app.staff.vehicle_details import PHOTO_H

        _built, context = branch
        vid = _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        dialog = self._dialog(context, vid)
        assert PHOTO_H == 300
        assert "FFFFFF" in dialog.photo.styleSheet()
        assert dialog.photo.height() == PHOTO_H

    def test_check_button_shares_the_heading_row(self, qapp, branch):
        _built, context = branch
        vid = _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        dialog = self._dialog(context, vid)
        dialog.show()
        qapp_process(dialog)
        assert dialog.panel.check_button.isVisible() is True
        assert (
            abs(
                dialog.panel.check_button.mapToGlobal(
                    dialog.panel.check_button.rect().center()
                ).y()
                - dialog.panel.check_heading.mapToGlobal(
                    dialog.panel.check_heading.rect().center()
                ).y()
            )
            < dialog.panel.check_button.height()
        )

    def test_action_buttons_live_inside_the_card(self, qapp, branch):
        _built, context = branch
        vid = _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        dialog = self._dialog(context, vid)
        card = dialog.panel.card
        assert dialog.panel.rent_button.parent() is card
        assert dialog.panel.close_button.parent() is card

    def test_overlay_has_scoped_dim_backdrop(self, qapp, shell, branch):
        _built, context = branch
        _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        page = NewRentalPage(shell)
        page.refresh()
        vid = next(s.vehicle.vehicle_id for s in page.slots)
        page._open_vehicle_details(vid)
        overlay = page._details_overlay
        # Frosted glass (v9): the dim is scoped to the overlay itself so it
        # can never cascade onto the white card.
        assert "VehicleDetailsOverlay" in overlay.styleSheet()
        assert "rgba" in overlay.styleSheet()
        overlay.hide_overlay()

    def test_overlay_backdrop_is_frosted_and_click_through(
        self, qapp, shell, branch
    ):
        from PySide6.QtCore import Qt

        _built, context = branch
        _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        page = NewRentalPage(shell)
        shell.show()
        shell.resize(1000, 700)
        page.refresh()
        qapp_process(shell)
        vid = next(s.vehicle.vehicle_id for s in page.slots)
        page._open_vehicle_details(vid)
        overlay = page._details_overlay
        qapp_process(overlay)
        # The frosted picture sits behind the card and never eats clicks:
        # outside-click-to-close is handled by the overlay itself.
        assert overlay.backdrop.objectName() == "vehicleDetailsBackdrop"
        assert overlay.backdrop.testAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        assert overlay.backdrop.geometry().width() > 0
        # Card stays crisp and round on top of the frosted glass.
        assert overlay.panel.card.objectName() == "vehicleDetailsCard"
        assert overlay.panel.card._radius == 8.0
        # Hiding frees the frozen frame.
        overlay.hide_overlay()
        assert overlay.backdrop.pixmap() is None or overlay.backdrop.pixmap().isNull()

    def test_price_figure_has_no_background_box(self, qapp, branch):
        _built, context = branch
        vid = _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        dialog = self._dialog(context, vid)
        assert "transparent" in dialog.price.styleSheet()

    def test_details_card_uses_8px_corners(self, qapp, branch):
        _built, context = branch
        vid = _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        dialog = self._dialog(context, vid)
        assert dialog.panel.card._radius == 8.0
        assert dialog.panel.card.objectName() == "vehicleDetailsCard"

    def test_card_corners_are_truly_round_not_square(self, qapp):
        from PySide6.QtWidgets import QWidget, QVBoxLayout
        from app.staff.cards import Card, SectionLabel

        host = QWidget()
        host.resize(300, 200)
        host.setStyleSheet("QWidget { background: #FF0000; }")
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        card = Card(host, radius=8)
        body = card.body()
        card.layout().addLayout(body)
        body.addWidget(SectionLabel("Hi", parent=card))
        host.show()
        qapp_process(host)
        img = card.grab().toImage()
        # Outside the 8px curve the red host shows through (transparent);
        # just inside, the rounded white fill paints.
        assert img.pixelColor(2, 2).red() > 200
        assert img.pixelColor(2, 2).green() < 210
        inside = img.pixelColor(20, 20)
        assert (inside.red(), inside.green(), inside.blue()) == (255, 255, 255)

    def test_text_on_a_card_carries_no_cream_patch(self, qapp):
        """The blanket cream QWidget rule must not paint behind text.

        Regression for the cream rectangles behind captions and check-box
        labels on white modal cards: text paints transparent and shows the
        card white through. Scans every pixel of the card for cream.
        """
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QCheckBox, QVBoxLayout, QWidget
        from app.staff import theme
        from app.staff.cards import Caption, Card, SectionLabel

        host = QWidget()
        host.resize(340, 260)
        # The real app stylesheet, not a probe colour: this test pins how
        # the blanket cream QWidget rule interacts with text on cards.
        host.setStyleSheet(theme.load_stylesheet())
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        card = Card(host, radius=8)
        lay.addWidget(card, 1)
        body = card.body()
        card.layout().addLayout(body)
        body.addWidget(SectionLabel("Log maintenance", parent=card))
        body.addWidget(Caption("Put a vehicle in the shop", parent=card))
        box = QCheckBox("Open-ended (no end date yet)", card)
        body.addWidget(box)
        host.show()
        qapp_process(host)
        img = card.grab().toImage()
        cream = QColor(241, 232, 216)
        found = 0
        for y in range(img.height()):
            for x in range(img.width()):
                pixel = img.pixelColor(x, y)
                if (
                    abs(pixel.red() - cream.red()) <= 6
                    and abs(pixel.green() - cream.green()) <= 6
                    and abs(pixel.blue() - cream.blue()) <= 6
                ):
                    found += 1
        assert found == 0, f"{found} cream pixels behind card text"

    def test_status_badges_are_boxes_not_pills(self, qapp, branch):
        from app.staff.cards import Pill

        _built, context = branch
        vid = _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        dialog = self._dialog(context, vid)
        badges = dialog.findChildren(Pill)
        assert badges, "expected status badges in the dialog"
        assert all(b._radius == 8 for b in badges)

    def test_name_is_big_and_bold(self, qapp, branch):
        from PySide6.QtGui import QFont

        _built, context = branch
        vid = _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        dialog = self._dialog(context, vid)
        assert dialog.name_label.font().pixelSize() == 22
        assert dialog.name_label.font().weight() == QFont.Weight.Bold

    def test_end_before_start_is_rejected(self, qapp, branch):
        from PySide6.QtCore import QDate

        _built, context = branch
        vid = _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        dialog = self._dialog(context, vid)
        today = QDate.currentDate()
        dialog.start_edit.setDate(today.addDays(2))
        # Lift End's floor: it normally follows Start and would refuse this.
        dialog.end_edit.setMinimumDate(today.addDays(-30))
        dialog.end_edit.setDate(today.addDays(1))
        dialog.check_button.click()
        assert dialog.check_error.text() == "End cannot be before start."
        assert dialog.checked_window is None
        assert dialog.check_result.isHidden() is True

    def test_a_past_start_is_rejected(self, qapp, branch):
        from PySide6.QtCore import QDate

        _built, context = branch
        vid = _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        dialog = self._dialog(context, vid)
        today = QDate.currentDate()
        # Lift the calendar floor: the picker blocks past days, but a
        # hand-typed past date must still fail validation.
        dialog.start_edit.setMinimumDate(today.addDays(-30))
        dialog.end_edit.setMinimumDate(today.addDays(-30))
        dialog.start_edit.setDate(today.addDays(-1))
        dialog.end_edit.setDate(today.addDays(1))
        dialog.check_button.click()
        assert dialog.check_error.text() == "Start is in the past."
        assert dialog.checked_window is None

    def test_past_days_are_unclickable_in_the_calendar(self, qapp, branch):
        from PySide6.QtCore import QDate

        _built, context = branch
        vid = _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        dialog = self._dialog(context, vid)
        today = QDate.currentDate()
        assert dialog.start_edit.minimumDate() == today
        assert dialog.end_edit.minimumDate() == today

    def test_end_minimum_follows_start(self, qapp, branch):
        from PySide6.QtCore import QDate

        _built, context = branch
        vid = _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        dialog = self._dialog(context, vid)
        today = QDate.currentDate()
        dialog.start_edit.setDate(today.addDays(5))
        assert dialog.end_edit.minimumDate() == today.addDays(5)

    def test_same_day_counts_as_a_one_day_rental(self, qapp, branch):
        from PySide6.QtCore import QDate

        _built, context = branch
        vid = _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        dialog = self._dialog(context, vid)
        today = QDate.currentDate()
        dialog.start_edit.setDate(today.addDays(9))
        dialog.end_edit.setDate(today.addDays(9))
        dialog.check_button.click()
        assert dialog.check_error.isHidden() is True
        assert "(1 day)" in dialog.check_result.text()
        assert dialog.checked_window is not None

    def _booked_vehicle(self, branch):
        built, context = branch
        vid = _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        with context.session() as session:
            vehicle = session.get(Vehicle, vid)
            Branch._booking(
                session,
                built.customer,
                vehicle,
                TODAY + timedelta(days=2),
                TODAY + timedelta(days=4),
                status="confirmed",
            )
        return vid

    def test_a_free_window_reports_available(self, qapp, branch):
        from PySide6.QtCore import QDate

        vid = self._booked_vehicle(branch)
        _built, context = branch
        dialog = self._dialog(context, vid)
        today = QDate.currentDate()
        dialog.start_edit.setDate(today.addDays(10))
        dialog.end_edit.setDate(today.addDays(11))
        dialog.check_button.click()
        assert dialog.check_error.isHidden() is True
        assert "Available" in dialog.check_result.text()
        assert "(1 day)" in dialog.check_result.text()
        assert dialog.checked_window is not None

    def test_a_clashing_window_names_the_booking(self, qapp, branch):
        from PySide6.QtCore import QDate

        vid = self._booked_vehicle(branch)
        _built, context = branch
        dialog = self._dialog(context, vid)
        today = QDate.currentDate()
        dialog.start_edit.setDate(today.addDays(2))
        dialog.end_edit.setDate(today.addDays(3))
        dialog.check_button.click()
        assert "Not available" in dialog.check_result.text()
        # No prefill: the wizard must not silently reuse the clashing dates.
        assert dialog.checked_window is None

    def test_the_booking_end_day_is_free_again(self, qapp, branch):
        """The shared rule is half-open: returning on the 4th frees the 4th."""
        from PySide6.QtCore import QDate

        vid = self._booked_vehicle(branch)
        _built, context = branch
        dialog = self._dialog(context, vid)
        today = QDate.currentDate()
        dialog.start_edit.setDate(today.addDays(4))
        dialog.end_edit.setDate(today.addDays(5))
        dialog.check_button.click()
        assert dialog.check_error.isHidden() is True
        assert "Available" in dialog.check_result.text()
        assert dialog.checked_window is not None

    def test_the_checker_blocks_a_vehicle_in_the_workshop(self, qapp, branch):
        from PySide6.QtCore import QDate

        built, context = branch
        dialog = self._dialog(context, built.shop_car.vehicle_id)
        today = QDate.currentDate()
        dialog.start_edit.setDate(today.addDays(30))
        dialog.end_edit.setDate(today.addDays(31))
        dialog.check_button.click()
        assert "Not available" in dialog.check_result.text()
        assert "maintenance" in dialog.check_result.text()
        assert dialog.checked_window is None

    def test_overlay_rent_carries_the_checked_window(self, qapp, shell, branch):
        from PySide6.QtCore import QDate

        _built, context = branch
        _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        page = NewRentalPage(shell)
        shell.show()
        page.show()
        page.resize(1440, 900)
        page.refresh()
        vid = next(s.vehicle.vehicle_id for s in page.slots)

        page._open_vehicle_details(vid)
        qapp_process(page)
        overlay = page._details_overlay
        assert overlay is not None
        assert overlay.isVisible() is True

        now = QDate.currentDate()
        overlay.panel.start_edit.setDate(now.addDays(10))
        overlay.panel.end_edit.setDate(now.addDays(11))
        overlay.panel.check_button.click()
        overlay.panel.rent_button.click()
        qapp_process(page)

        # Rent now opens the scheduling step in the same overlay (no toast,
        # no close): the checked window carries over prefilling the dates.
        assert page.selected_vehicle_id == vid
        assert overlay.isVisible() is True
        from app.staff.rental_wizard import SchedulePanel

        assert isinstance(page._wizard._current, SchedulePanel)
        assert page._wizard._current.start_edit.date() == now.addDays(10)
        assert page._wizard._current.end_edit.date() == now.addDays(11)
        overlay.hide_overlay()

    def test_overlay_close_and_escape_hide_it(self, qapp, shell, branch):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        _built, context = branch
        _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        page = NewRentalPage(shell)
        shell.show()
        page.show()
        page.refresh()
        vid = next(s.vehicle.vehicle_id for s in page.slots)

        page._open_vehicle_details(vid)
        qapp_process(page)
        overlay = page._details_overlay
        assert overlay.isVisible() is True

        QTest.keyClick(overlay, Qt.Key.Key_Escape)
        qapp_process(page)
        assert overlay.isHidden() is True

        page._open_vehicle_details(vid)
        qapp_process(page)
        overlay = page._details_overlay
        overlay.panel.close_button.click()
        qapp_process(page)
        assert overlay.isHidden() is True
        assert page.selected_vehicle_id is None

    def test_click_inside_the_card_does_nothing(self, qapp, shell, branch):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        _built, context = branch
        _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        page = NewRentalPage(shell)
        shell.show()
        page.show()
        page.resize(1440, 900)
        page.refresh()
        vid = next(s.vehicle.vehicle_id for s in page.slots)
        page._open_vehicle_details(vid)
        qapp_process(page)
        overlay = page._details_overlay
        try:
            # The hero photo ignores presses, so this is the exact reported
            # bug: a press on card content propagating up to the overlay.
            photo = overlay.panel.photo
            at = photo.mapTo(overlay, photo.rect().center())
            QTest.mouseClick(
                overlay,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                at,
            )
            qapp_process(page)
            assert overlay.isVisible() is True
        finally:
            overlay.hide_overlay()

    def test_left_click_on_the_dim_closes(self, qapp, shell, branch):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        _built, context = branch
        _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        page = NewRentalPage(shell)
        shell.show()
        page.show()
        page.resize(1440, 900)
        page.refresh()
        vid = next(s.vehicle.vehicle_id for s in page.slots)
        page._open_vehicle_details(vid)
        qapp_process(page)
        overlay = page._details_overlay
        QTest.mouseClick(
            overlay,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(10, 10),
        )
        qapp_process(page)
        assert overlay.isHidden() is True

    def test_right_click_closes_nothing(self, qapp, shell, branch):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        _built, context = branch
        _add_vehicle(context, make="Honda", model="Civic", plate="HON-001")
        page = NewRentalPage(shell)
        shell.show()
        page.show()
        page.resize(1440, 900)
        page.refresh()
        vid = next(s.vehicle.vehicle_id for s in page.slots)
        page._open_vehicle_details(vid)
        qapp_process(page)
        overlay = page._details_overlay
        try:
            QTest.mouseClick(
                overlay,
                Qt.MouseButton.RightButton,
                Qt.KeyboardModifier.NoModifier,
                QPoint(10, 10),
            )
            qapp_process(page)
            assert overlay.isVisible() is True
        finally:
            overlay.hide_overlay()


class TestDimOverlay:
    def test_overlay_covers_its_window_and_hides_cleanly(self, qapp, shell):
        from app.staff.widgets import DimOverlay

        shell.show()
        qapp_process(shell)
        dim = DimOverlay(shell)
        dim.show_for()
        assert dim.geometry() == shell.rect()
        dim.hide()
        assert dim.isHidden() is True


class TestRentalWizard:
    """Counter flow: schedule -> licence -> pay -> done, one overlay."""

    def _open_wizard(self, qapp, shell, branch, plate="HON-001"):
        from PySide6.QtCore import QDate

        _built, context = branch
        _add_vehicle(context, make="Honda", model="Civic", plate=plate)
        page = NewRentalPage(shell)
        shell.show()
        page.show()
        page.resize(1440, 900)
        page.refresh()
        vid = next(s.vehicle.vehicle_id for s in page.slots)
        page._open_vehicle_details(vid)
        qapp_process(page)
        overlay = page._details_overlay
        today = QDate.currentDate()
        overlay.panel.start_edit.setDate(today.addDays(10))
        overlay.panel.end_edit.setDate(today.addDays(11))
        overlay.panel.check_button.click()
        overlay.panel.rent_button.click()
        qapp_process(page)
        return page, overlay, vid

    def test_schedule_prefills_checked_window_and_quotes(
        self, qapp, shell, branch
    ):
        from app.staff.rental_wizard import SchedulePanel

        page, overlay, _vid = self._open_wizard(qapp, shell, branch)
        try:
            step = page._wizard._current
            assert isinstance(step, SchedulePanel)
            assert "1 day" in step.total_caption.text()
            assert step.next_button.isEnabled() is True
        finally:
            overlay.hide_overlay()

    def test_payment_panel_uses_configured_fee_and_defaults(
        self, qapp, shell, branch
    ):
        from PySide6.QtWidgets import QLabel

        from app.services import settings_service
        from app.staff.rental_wizard import PaymentPanel

        _built, context = branch
        with context.session() as session:
            settings_service.set_value(session, "scheduling_fee", "500.00")
            settings_service.set_value(session, "default_payment_method", "gcash")
            settings_service.set_value(session, "default_channel", "online")
        page, overlay, _vid = self._open_wizard(qapp, shell, branch)
        try:
            wizard = page._wizard
            schedule_step = wizard._current
            assert "500.00" in schedule_step.fee_caption.text()

            schedule_step._on_continue()
            qapp_process(page)
            licence_step = wizard._current
            with context.reading() as session:
                licence = (
                    session.query(Users)
                    .filter(
                        Users.role == "customer",
                        Users.license_expiry > TODAY,
                    )
                    .first()
                    .license_number
                )
            licence_step.licence_edit.setText(licence)
            licence_step.find_button.click()
            qapp_process(page)
            licence_step.next_button.click()
            qapp_process(page)

            pay_step = wizard._current
            assert isinstance(pay_step, PaymentPanel)
            assert wizard.state.scheduling_fee == Decimal("500.00")
            assert wizard.state.deposit == Decimal("2500.00")
            assert wizard.state.default_method == "gcash"
            assert wizard.state.default_channel == "online"
            assert "3,000.00" in pay_step.min_button.text()  # 500 fee + 2,500 deposit
            fee_captions = [
                label.text()
                for label in pay_step.findChildren(QLabel)
                if label.text().startswith("Includes")
            ]
            assert fee_captions and "500.00" in fee_captions[0]
            assert pay_step.method_buttons["gcash"].isChecked() is True
            assert pay_step.ref_edit.isEnabled() is True
        finally:
            overlay.hide_overlay()

    def test_fee_above_total_shows_and_records_the_total(
        self, qapp, shell, branch
    ):
        from app.services import settings_service
        from app.staff.rental_wizard import PaymentPanel

        _built, context = branch
        with context.session() as session:
            settings_service.set_value(session, "scheduling_fee", "5000.00")
        page, overlay, _vid = self._open_wizard(qapp, shell, branch)
        try:
            wizard = page._wizard
            wizard._current._on_continue()
            qapp_process(page)
            licence_step = wizard._current
            with context.reading() as session:
                customer = (
                    session.query(Users)
                    .filter(
                        Users.role == "customer",
                        Users.license_expiry > TODAY,
                    )
                    .first()
                )
                licence = customer.license_number
                customer_id = customer.user_id
            licence_step.licence_edit.setText(licence)
            licence_step.find_button.click()
            qapp_process(page)
            licence_step.next_button.click()
            qapp_process(page)

            pay_step = wizard._current
            assert isinstance(pay_step, PaymentPanel)
            assert wizard.state.total == Decimal("3000.00")
            assert wizard.state.scheduling_fee == Decimal("5000.00")
            # The panel caps the offered minimum at the rental total.
            assert "5,500.00" in pay_step.min_button.text()  # 3,000 + 2,500 deposit
            pay_step.pay_button.click()
            qapp_process(page)

            with context.reading() as session:
                booking = (
                    session.query(Booking)
                    .filter_by(user_id=customer_id)
                    .order_by(Booking.booking_id.desc())
                    .first()
                )
                rental = next(p for p in booking.payments if p.kind == "rental")
                assert rental.amount == Decimal("3000.00")
        finally:
            overlay.hide_overlay()

    def test_schedule_back_returns_to_details(self, qapp, shell, branch):
        from PySide6.QtWidgets import QPushButton

        from app.staff.rental_wizard import SchedulePanel
        from app.staff.vehicle_details import VehicleDetailsPanel

        page, overlay, _vid = self._open_wizard(qapp, shell, branch)
        step = page._wizard._current
        assert isinstance(step, SchedulePanel)
        # Back is the secondary button in the step's button row.
        back = next(
            b
            for b in page._wizard._current.findChildren(QPushButton)
            if b.text() == "Back"
        )
        back.click()
        qapp_process(page)
        assert isinstance(page._wizard._current, VehicleDetailsPanel)
        overlay.hide_overlay()

    def test_licence_finds_returning_customer(self, qapp, shell, branch):
        from app.staff.rental_wizard import LicencePanel

        page, overlay, _vid = self._open_wizard(qapp, shell, branch)
        try:
            wizard = page._wizard
            wizard._current._on_continue()
            qapp_process(page)
            step = wizard._current
            assert isinstance(step, LicencePanel)
            with branch[1].reading() as session:
                customer = (
                    session.query(Users)
                    .filter(
                        Users.role == "customer",
                        Users.license_expiry > TODAY,
                    )
                    .first()
                )
                licence = customer.license_number
            step.licence_edit.setText(licence)
            step.find_button.click()
            qapp_process(page)
            assert step.next_button.isEnabled() is True
        finally:
            overlay.hide_overlay()

    def test_licence_creates_new_customer_once(self, qapp, shell, branch):
        from app.staff.rental_wizard import LicencePanel

        page, overlay, _vid = self._open_wizard(qapp, shell, branch)
        try:
            wizard = page._wizard
            wizard._current._on_continue()
            qapp_process(page)
            step = wizard._current
            assert isinstance(step, LicencePanel)
            step.licence_edit.setText("NEW-0001")
            step.find_button.click()
            qapp_process(page)
            assert step._form.isVisible() is True
            form = step._form_panel
            form.name_edit.setText("First Timer")
            form.email_edit.setText("first@example.com")
            form.phone_edit.setText("09170009999")
            form.address_edit.setText("9 Test Street")
            form._go.click()
            qapp_process(page)
            assert step.next_button.isEnabled() is True
            first_id = step._picked
            # Finding the same licence again reuses the row, never duplicates.
            step.licence_edit.setText("new 0001")
            step.find_button.click()
            qapp_process(page)
            assert step._picked == first_id
        finally:
            overlay.hide_overlay()

    def test_flagged_customer_needs_an_override_note(self, qapp, shell, branch):
        from app.services import customer_service
        from app.staff.rental_wizard import LicencePanel

        built, context = branch
        with context.session() as session:
            customer = session.get(Users, built.customer.user_id)
            customer_service.set_flag(session, customer, "Unpaid penalty", staff_id=1)
            licence = customer.license_number
        page, overlay, _vid = self._open_wizard(qapp, shell, branch)
        try:
            wizard = page._wizard
            wizard._current._on_continue()
            qapp_process(page)
            step = wizard._current
            assert isinstance(step, LicencePanel)
            step.licence_edit.setText(licence)
            step.find_button.click()
            qapp_process(page)
            assert step._flagged is True
            assert step.override_edit.isVisible() is True
            assert step.next_button.isEnabled() is False
            step.override_edit.setText("Manager approved by phone.")
            assert step.next_button.isEnabled() is True
            step.next_button.click()
            qapp_process(page)
            assert wizard.state.flag_override_note == "Manager approved by phone."
        finally:
            overlay.hide_overlay()

    def test_missing_birthdate_blocks_with_inline_fix(self, qapp, shell, branch):
        from app.staff.rental_wizard import LicencePanel

        built, context = branch
        with context.session() as session:
            customer = session.get(Users, built.customer.user_id)
            customer.birthdate = None
            licence = customer.license_number
        page, overlay, _vid = self._open_wizard(qapp, shell, branch)
        try:
            wizard = page._wizard
            wizard._current._on_continue()
            qapp_process(page)
            step = wizard._current
            step.licence_edit.setText(licence)
            step.find_button.click()
            qapp_process(page)
            assert step.next_button.isEnabled() is False
            assert step._dob_host.isVisible() is True
            from PySide6.QtCore import QDate

            step.dob_edit.setDate(QDate(1995, 5, 5))
            step.dob_save.click()
            qapp_process(page)
            assert step.next_button.isEnabled() is True
        finally:
            overlay.hide_overlay()

    def test_expiry_before_return_blocks(self, qapp, shell, branch):
        from datetime import timedelta as _timedelta

        from app.services import customer_service
        from app.staff.rental_wizard import LicencePanel

        built, context = branch
        with context.session() as session:
            customer = customer_service.create_customer(
                session,
                full_name="Short Licence",
                email="short@example.com",
                phone=None,
                address="1 Test Street",
                license_number="SHORT-001",
                license_expiry=TODAY + _timedelta(days=10),
                birthdate=TODAY - _timedelta(days=365 * 30),
            )
            licence = customer.license_number
        page, overlay, _vid = self._open_wizard(qapp, shell, branch)
        try:
            wizard = page._wizard
            wizard._current._on_continue()
            qapp_process(page)
            step = wizard._current
            step.licence_edit.setText(licence)
            step.find_button.click()
            qapp_process(page)
            assert step.next_button.isEnabled() is False
        finally:
            overlay.hide_overlay()

    def test_preset_customer_skips_lookup_but_keeps_checks(
        self, qapp, shell, branch
    ):
        from PySide6.QtCore import QDate

        from app.staff.rental_wizard import LicencePanel, WizardState

        built, context = branch
        state = WizardState(
            vehicle_id=built.free_car.vehicle_id,
            start=QDate.currentDate().addDays(10),
            end=QDate.currentDate().addDays(11),
        )
        with context.reading() as session:
            customer = session.get(Users, built.customer.user_id)
            licence = customer.license_number
            customer_id = customer.user_id
        step = LicencePanel(
            context, state, preset_customer_id=customer_id
        )
        try:
            assert step.licence_edit.text() == licence
            assert step._picked == customer_id
            assert step.next_button.isEnabled() is True
        finally:
            step.deleteLater()

    def test_full_pay_books_confirmed_with_receipt(
        self, qapp, shell, branch, tmp_path
    ):
        import os

        from app.services import receipt_service

        page, overlay, vid = self._open_wizard(qapp, shell, branch)
        old_dir = receipt_service.RECEIPTS_DIR
        receipt_service.RECEIPTS_DIR = tmp_path
        try:
            wizard = page._wizard
            wizard._current._on_continue()
            qapp_process(page)
            licence_step = wizard._current
            with branch[1].reading() as session:
                licence = (
                    session.query(Users)
                    .filter(
                        Users.role == "customer",
                        Users.license_expiry > TODAY,
                    )
                    .first()
                    .license_number
                )
            licence_step.licence_edit.setText(licence)
            licence_step.find_button.click()
            qapp_process(page)
            licence_step.next_button.click()
            qapp_process(page)
            pay_step = wizard._current
            pay_step.full_button.setChecked(True)
            pay_step.pay_button.click()
            qapp_process(page)
            done = wizard._current
            assert "booked" in done._body.itemAt(1).widget().text()
            assert os.listdir(tmp_path), "expected a receipt PDF"
        finally:
            receipt_service.RECEIPTS_DIR = old_dir
            overlay.hide_overlay()

    def test_receipt_failure_keeps_the_booking_and_offers_done(
        self, qapp, shell, branch, monkeypatch
    ):
        from app.staff import rental_wizard
        from app.staff.rental_wizard import DonePanel
        from PySide6.QtWidgets import QLabel

        def _read_only(_session, _booking):
            raise OSError("read-only")

        monkeypatch.setattr(
            rental_wizard, "generate_receipt_pdf", _read_only
        )
        page, overlay, _vid = self._open_wizard(qapp, shell, branch)
        try:
            wizard = page._wizard
            wizard._current._on_continue()
            qapp_process(page)
            licence_step = wizard._current
            with branch[1].reading() as session:
                customer = (
                    session.query(Users)
                    .filter(
                        Users.role == "customer",
                        Users.license_expiry > TODAY,
                    )
                    .first()
                )
                licence = customer.license_number
                customer_id = customer.user_id
            licence_step.licence_edit.setText(licence)
            licence_step.find_button.click()
            qapp_process(page)
            licence_step.next_button.click()
            qapp_process(page)

            pay_step = wizard._current
            pay_step.full_button.setChecked(True)
            pay_step.pay_button.click()
            qapp_process(page)

            done = wizard._current
            assert isinstance(done, DonePanel)
            assert done.receipt_path is None
            assert done.receipt_error
            assert done.print_button.isEnabled() is False
            assert done.view_button.isEnabled() is True
            assert done.done_button.isEnabled() is True
            warnings = [
                label
                for label in done.findChildren(QLabel)
                if "Receipt could not be generated" in label.text()
            ]
            assert warnings and warnings[0].isVisible()
            assert "saved" in warnings[0].text().lower()

            # The booking and its payments survived the receipt failure.
            with branch[1].reading() as session:
                booking = (
                    session.query(Booking)
                    .filter_by(user_id=customer_id)
                    .order_by(Booking.booking_id.desc())
                    .first()
                )
                assert booking is not None
                assert booking.payments
                booking_id = booking.booking_id

            # The helper never raises: it reports the failure instead.
            path, error = wizard._generate_receipt_safely(booking_id)
            assert path is None
            assert error
        finally:
            overlay.hide_overlay()

    def test_wizard_has_no_stadium_shapes(self, qapp, shell, branch):
        from PySide6.QtWidgets import QPushButton

        from app.staff import rental_wizard

        page, overlay, _vid = self._open_wizard(qapp, shell, branch)
        try:
            assert "999" not in open(rental_wizard.__file__, encoding="utf-8").read()
            for button in overlay.findChildren(QPushButton):
                assert button.objectName() in (
                    "primaryButton",
                    "secondaryButton",
                    "wizardChoice",
                    "",
                )
        finally:
            overlay.hide_overlay()

    def test_wizard_steps_use_compact_density(self, qapp, shell, branch):
        from PySide6.QtWidgets import QSizePolicy

        from app.staff import rental_wizard
        from app.staff.cards import SectionLabel
        from app.staff.rental_wizard import SchedulePanel

        page, overlay, _vid = self._open_wizard(qapp, shell, branch)
        try:
            step = page._wizard._current
            assert isinstance(step, SchedulePanel)
            margins = step._body.contentsMargins()
            assert (margins.left(), margins.top()) == (
                rental_wizard.WIZARD_PADDING,
                rental_wizard.WIZARD_PADDING,
            )
            assert step._body.spacing() == rental_wizard.WIZARD_SPACING
            # Step titles are section-head size, not hero size.
            titles = [
                w
                for w in step.findChildren(SectionLabel)
                if w.text() == "When is it out?"
            ]
            assert titles
            assert titles[0].font().pixelSize() == 16
        finally:
            overlay.hide_overlay()

    def test_wizard_card_hugs_content_and_centers(self, qapp, shell, branch):
        from PySide6.QtWidgets import QSizePolicy, QSpacerItem

        from app.staff.rental_wizard import SchedulePanel

        page, overlay, _vid = self._open_wizard(qapp, shell, branch)
        try:
            step = page._wizard._current
            assert isinstance(step, SchedulePanel)
            # Hug: the shared Card is Expanding, the wizard opts back out.
            assert (
                step.card.sizePolicy().verticalPolicy()
                == QSizePolicy.Policy.Preferred
            )
            # Center: one stretch above the card, one below.
            outer = step.layout()
            stretches = [
                outer.itemAt(i)
                for i in range(outer.count())
                if isinstance(outer.itemAt(i), QSpacerItem)
            ]
            assert len(stretches) == 2
            # No ballooning: short content stays far shorter than the view.
            qapp_process(page)
            assert (
                step.card.height()
                < overlay.scroll.viewport().height() // 2
            )
        finally:
            overlay.hide_overlay()
