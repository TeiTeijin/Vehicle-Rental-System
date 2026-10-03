"""Tests for the staff pages.

Each page is built over a small, hand-made dataset rather than the demo seed,
so a test states exactly the situation it is about. Data is built relative to
`date.today()` because every page asks the clock what day it is -- Today, Fleet
and Customers all key off it, and a page that read a fixed date would be testing
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
)
from app.staff.context import (  # noqa: E402
    DatabaseSelection,
    DatabaseTarget,
    StaffContext,
    StaffUser,
)
from app.staff.pages.base import StaffPage  # noqa: E402
from app.staff.pages.bookings import COUNTER_ACTIONS, BookingsPage  # noqa: E402
from app.staff.pages.customers import EXPIRING_SOON_DAYS  # noqa: E402
from app.staff.pages.dashboard import DashboardPage, pesos  # noqa: E402
from app.staff.pages.fleet import FleetPage  # noqa: E402
from app.staff.pages.inspections import InspectionsPage  # noqa: E402
from app.staff.pages.payments import PaymentsPage  # noqa: E402
from app.staff.pages.today import TodayPage  # noqa: E402
from app.staff.shell import StaffShell  # noqa: E402
from app.staff.tables import LoadState  # noqa: E402
from app.utils.security import hash_password  # noqa: E402

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


def texts(table):
    return [table.cell_text(r, 0) for r in range(table.row_count())]


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
# Today
# --------------------------------------------------------------------------


class TestTodayPage:
    def test_overdue_is_separate_from_due_back(self, shell, branch):
        """Burying a late return in a chronological list is how a car stays
        out for three extra days."""
        page = TodayPage(shell)
        page.refresh()
        assert page.overdue.row_count() == 1
        assert page.due_back.row_count() == 1
        assert page.has_overdue is True

    def test_an_overdue_row_says_how_late(self, shell, branch):
        page = TodayPage(shell)
        page.refresh()
        assert page.overdue.cell_text(0, 5) == "2 day(s) late"

    def test_a_due_back_row_is_not_called_late(self, shell, branch):
        page = TodayPage(shell)
        page.refresh()
        assert page.due_back.cell_text(0, 5) == "-"

    def test_a_settled_branch_has_nothing_late(self, shell, branch, monkeypatch):
        monkeypatch.setattr(
            "app.staff.pages.today.date", frozen_date(TODAY - timedelta(days=100))
        )
        page = TodayPage(shell)
        page.refresh()
        assert page.overdue.state is LoadState.EMPTY
        assert page.has_overdue is False

    def test_the_workshop_lists_the_open_job(self, shell, branch):
        page = TodayPage(shell)
        page.refresh()
        assert page.workshop.row_count() == 1
        assert "SHOP-001" in page.workshop.cell_text(0, 0)
        # Vehicle, Work, State, Started, Expected, Cost
        assert page.workshop.cell_text(0, 2) == "Ongoing"

    def test_a_finished_job_leaves_the_workshop(self, shell, branch):
        built, context = branch
        with context.session() as session:
            record = (
                session.query(Maintenance_Record)
                .filter(Maintenance_Record.vehicle_id == built.shop_car.vehicle_id)
                .one()
            )
            record.status = "completed"
        page = TodayPage(shell)
        page.refresh()
        assert page.workshop.state is LoadState.EMPTY

    def test_collections_show_who_is_picking_up(self, shell, branch):
        page = TodayPage(shell)
        page.refresh()
        assert page.collections.row_count() >= 1
        assert all("#" in t for t in texts(page.collections))


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
        page = TodayPage(shell)
        monkeypatch.setattr(
            page.due_back, "_loader", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        page.refresh()
        assert page.due_back.state is LoadState.FAILED
        assert page.overdue.state is LoadState.LOADED


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


class _FrozenDate(date):
    """A `date` subclass that reports one fixed value from `today()`.

    Constructed as `_FrozenDate(2026, 1, 1)` rather than from another `date`,
    because `date.__new__` only accepts year/month/day -- passing a `date`
    instance raises inside `__new__`, before anything under test runs.
    """

    def __new__(cls, year, month, day):
        return super().__new__(cls, year, month, day)


def frozen_date(value: date):
    """A drop-in for the `date` name in a page module, pinned to `value`."""
    return type(
        "FrozenDate",
        (_FrozenDate,),
        {"today": classmethod(lambda cls: value)},
    )
