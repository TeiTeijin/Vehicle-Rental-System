"""Warm list-page pass for Fleet, Customers, Maintenance, and Payments.

These tests pin the presentation contract added by the staff-UI pass
without weakening the existing behaviour suites in
``tests/test_staff_pages.py``: the same page attributes, filters, action
rules, and table indexes are exercised through the new warm widgets.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import (  # noqa: E402
    Booking,
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
from app.staff.metrics import CARD_GUTTER  # noqa: E402
from app.staff.pages.base import StaffPage  # noqa: E402
from app.staff.pages.customers import (  # noqa: E402
    EXPIRING_SOON_DAYS,
    CustomersPage,
)
from app.staff.pages.fleet import FleetPage  # noqa: E402
from app.staff.pages.maintenance import MaintenancePage  # noqa: E402
from app.staff.pages.payments import PaymentsPage  # noqa: E402
from app.staff.shell import StaffShell  # noqa: E402
from app.staff.tables import LoadState  # noqa: E402
from app.staff.ui_kit import SelectionBar, WarmHost, WarmPanel  # noqa: E402
from app.utils.security import hash_password  # noqa: E402

TODAY = date.today()


# --------------------------------------------------------------------------
# A small branch, deliberately self-contained so this suite can run alone.
# --------------------------------------------------------------------------


class _Branch:
    def __init__(self, context: StaffContext) -> None:
        with context.session() as session:
            category = Vehicle_Category(
                category_name="Car", base_rate_multiplier=Decimal("1.00")
            )
            session.add(category)
            session.flush()

            self.admin = self._user(
                session,
                full_name="Branch Admin",
                email="admin@example.com",
                role="admin",
                licence=TODAY + timedelta(days=400),
                licence_number="ADMIN-001",
            )
            self.counter = self._user(
                session,
                full_name="Counter Staff",
                email="counter@example.com",
                role="staff",
                licence=TODAY + timedelta(days=400),
                licence_number="STAFF-001",
            )
            self.customer = self._user(
                session,
                full_name="Ana Reyes",
                email="ana@example.com",
                role="customer",
                licence=TODAY + timedelta(days=400),
                licence_number="ANA-001",
            )
            self.expiring = self._user(
                session,
                full_name="Soon Lapsed",
                email="soon@example.com",
                role="customer",
                licence=TODAY + timedelta(days=EXPIRING_SOON_DAYS - 1),
                licence_number="SOON-001",
            )
            self.lapsed = self._user(
                session,
                full_name="Already Lapsed",
                email="lapsed@example.com",
                role="customer",
                licence=TODAY - timedelta(days=10),
                licence_number="LAPSED-001",
            )
            # The licence column is mandatory in the schema, but the number
            # is not: this customer exercises the email fallback in the
            # selection summary.
            self.no_licence = self._user(
                session,
                full_name="No Licence",
                email="no.licence@example.com",
                role="customer",
                licence=TODAY + timedelta(days=400),
                licence_number=None,
            )

            self.free_car = self._car(
                session, category, "FREE-001", status="available"
            )
            self.out_car = self._car(
                session, category, "OUT-001", status="rented"
            )
            self.reserved_car = self._car(
                session, category, "RES-001", status="reserved"
            )
            self.shop_car = self._car(
                session, category, "SHOP-001", status="maintenance"
            )
            self.retired_car = self._car(
                session, category, "RET-001", status="retired"
            )

            # One settled-in-part booking gives the Payments page a ledger row
            # and an outstanding row without touching the Fleet list.
            self.rental = Booking(
                user_id=self.customer.user_id,
                vehicle_id=self.free_car.vehicle_id,
                start_date=TODAY - timedelta(days=7),
                end_date=TODAY - timedelta(days=4),
                total_cost=Decimal("5000.00"),
                status="completed",
                created_by=self.counter.user_id,
                created_at=datetime(2026, 1, 2, 9, 0),
            )
            session.add(self.rental)
            session.flush()
            session.add_all(
                [
                    Payment(
                        booking_id=self.rental.booking_id,
                        amount=Decimal("2000.00"),
                        method="cash",
                        kind="rental",
                        status="paid",
                        paid_at=datetime(2026, 1, 2, 10, 0),
                        recorded_by=self.counter.user_id,
                        reference_no="OR-1001",
                        created_at=datetime(2026, 1, 2, 10, 0),
                    ),
                    Payment(
                        booking_id=self.rental.booking_id,
                        amount=Decimal("1000.00"),
                        method="gcash",
                        kind="deposit",
                        status="paid",
                        paid_at=datetime(2026, 1, 2, 10, 5),
                        recorded_by=self.counter.user_id,
                        reference_no="GC-1002",
                        created_at=datetime(2026, 1, 2, 10, 5),
                    ),
                ]
            )

            session.add_all(
                [
                    Maintenance_Record(
                        vehicle_id=self.shop_car.vehicle_id,
                        description="Brake pads",
                        maintenance_type="tire_brake",
                        start_date=TODAY - timedelta(days=1),
                        end_date=TODAY + timedelta(days=2),
                        cost=Decimal("4500.00"),
                        status="in_progress",
                    ),
                    Maintenance_Record(
                        vehicle_id=self.out_car.vehicle_id,
                        description="Oil change",
                        maintenance_type="routine",
                        start_date=TODAY - timedelta(days=30),
                        end_date=TODAY - timedelta(days=29),
                        cost=Decimal("1500.00"),
                        status="completed",
                    ),
                    Maintenance_Record(
                        vehicle_id=self.free_car.vehicle_id,
                        description="Wash and vacuum",
                        maintenance_type="cleaning",
                        start_date=TODAY - timedelta(days=10),
                        end_date=None,
                        cost=None,
                        status="cancelled",
                    ),
                ]
            )

    @staticmethod
    def _user(
        session,
        *,
        full_name: str,
        email: str,
        role: str,
        licence: date | None,
        licence_number: str | None,
    ) -> Users:
        row = Users(
            full_name=full_name,
            email=email,
            phone="09170000000",
            password_hash=hash_password("counter-password"),
            address="1 Test Street",
            license_number=licence_number,
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
    def _car(
        session,
        category: Vehicle_Category,
        plate: str,
        *,
        status: str = "available",
    ) -> Vehicle:
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


@pytest.fixture
def branch(tmp_path):
    path = tmp_path / "ui-lists.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    engine.dispose()

    context = StaffContext(
        DatabaseSelection(
            url=f"sqlite:///{path}", target=DatabaseTarget.LIVE, label="Test"
        )
    )
    built = _Branch(context)
    context.sign_in(
        StaffUser(
            built.admin.user_id,
            "Branch Admin",
            "admin@example.com",
            "admin",
        )
    )
    yield built, context
    context.dispose()


@pytest.fixture
def shell(qapp, branch):
    window = StaffShell(branch[1])
    yield window
    window.deleteLater()
    qapp.processEvents()


def _wait_for_fleet(page, timeout_ms: int = 15000) -> None:
    if page._filter_timer.isActive():
        page._filter_timer.stop()
        page._on_filter_timeout()
    page._pool.waitForDone(timeout_ms)
    deadline = time.monotonic() + timeout_ms / 1000
    while page._done_gen != page._load_gen and time.monotonic() < deadline:
        QTest.qWait(5)
    assert page._done_gen == page._load_gen, "fleet reload never landed"


def _wait_for_customers(page, timeout_ms: int = 15000) -> None:
    if page._filter_timer.isActive():
        page._filter_timer.stop()
        page._on_filter_timeout()
    page._pool.waitForDone(timeout_ms)
    deadline = time.monotonic() + timeout_ms / 1000
    while page._done_gen != page._load_gen and time.monotonic() < deadline:
        QTest.qWait(5)
    assert page._done_gen == page._load_gen, "customers reload never landed"


def _wait_for_maintenance(page, timeout_ms: int = 15000) -> None:
    timer = getattr(page, "_search_timer", None)
    if timer is not None and timer.isActive():
        timer.stop()
        page._apply_filters()
    page._pool.waitForDone(timeout_ms)
    deadline = time.monotonic() + timeout_ms / 1000
    controllers = (page._due_async, page._table_async)
    while (
        any(c.done_gen != c.load_gen for c in controllers)
        and time.monotonic() < deadline
    ):
        QTest.qWait(5)
    assert all(c.done_gen == c.load_gen for c in controllers), (
        "maintenance reload never landed"
    )


@pytest.fixture
def fleet_page(shell, branch):
    page = FleetPage(shell)
    page.refresh()
    _wait_for_fleet(page)
    return page


@pytest.fixture
def customers_page(shell, branch):
    page = CustomersPage(shell)
    page.refresh()
    _wait_for_customers(page)
    return page


@pytest.fixture
def maintenance_page(shell, branch):
    page = MaintenancePage(shell)
    page.refresh()
    _wait_for_maintenance(page)
    return page


def _wait_for_payments(page, timeout_ms: int = 15000) -> None:
    timer = getattr(page, "_search_timer", None)
    if timer is not None and timer.isActive():
        timer.stop()
        page._apply_filters()
    page._pool.waitForDone(timeout_ms)
    deadline = time.monotonic() + timeout_ms / 1000
    controllers = (page._takings_async, page._outstanding_async)
    while (
        any(c.done_gen != c.load_gen for c in controllers)
        and time.monotonic() < deadline
    ):
        QTest.qWait(5)
    assert all(c.done_gen == c.load_gen for c in controllers), (
        "payments reload never landed"
    )


@pytest.fixture
def payments_page(shell, branch):
    page = PaymentsPage(shell)
    page.refresh()
    _wait_for_payments(page)
    return page


def _select_row(table, column: int, text: str) -> int:
    for row in range(table.row_count()):
        if table.cell_text(row, column) == text:
            table.table.selectRow(row)
            return row
    raise AssertionError(f"{text!r} not in the table")


def _panel_variant(widget) -> str | None:
    return widget.property("variant")


# --------------------------------------------------------------------------
# Shared page recipe
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "page_attr",
    ["fleet_page", "customers_page", "maintenance_page"],
)
def test_each_page_has_warm_panels_without_a_second_heading(request, page_attr):
    page = request.getfixturevalue(page_attr)
    assert isinstance(page, StaffPage)
    assert not hasattr(page, "heading")
    assert page.count_caption.isHidden()
    assert page.body.itemAt(0).widget() is page._tools_panel
    assert page._tools_panel.objectName() == "pagePanel"
    assert _panel_variant(page._tools_panel) in ("warm", "warmHost")
    assert _panel_variant(page._data_panel) == "warm"
    assert page.body.spacing() == CARD_GUTTER


# --------------------------------------------------------------------------
# Fleet
# --------------------------------------------------------------------------


def test_fleet_chips_sync_the_hidden_status_combo(fleet_page):
    page = fleet_page
    assert page.status_combo.isHidden()
    # Every stored status code in the old dropdown has a chip.
    assert {
        "all",
        "available",
        "rented",
        "reserved",
        "maintenance",
        "retired",
    } <= set(page.status_chips.buttons)

    page.status_chips.buttons["maintenance"].click()
    _wait_for_fleet(page)
    assert page.status_combo.currentText() == "Maintenance"
    assert page.table.row_count() == 1
    assert page.table.cell_text(0, 0) == "SHOP-001"

    page.status_combo.setCurrentText("Retired")
    _wait_for_fleet(page)
    assert page.status_chips.selected == "retired"
    assert page.table.row_count() == 1
    assert page.table.cell_text(0, 0) == "RET-001"


def test_fleet_selection_bar_names_the_vehicle_and_keeps_the_action_rules(
    fleet_page,
):
    page = fleet_page
    assert page.selection_bar.property("active") in (False, 0)
    assert page.add_button.isEnabled()
    assert page.bulk_button.isEnabled()
    assert not page.edit_button.isEnabled()
    assert not page.details_button.isEnabled()
    assert not page.retire_button.isEnabled()

    _select_row(page.table, 0, "FREE-001")
    page._refresh_actions()

    assert page.selection_bar.property("active") is True
    summary = page.selection_bar.summary_label.text()
    assert "FREE-001" in summary
    assert "Toyota" in summary
    assert "Vios" in summary
    assert page.edit_button.isEnabled()
    assert page.details_button.isEnabled()
    assert page.retire_button.isEnabled()
    assert page.retire_button.objectName() == "barDanger"
    for button in (
        page.add_button,
        page.edit_button,
        page.details_button,
        page.bulk_button,
        page.retire_button,
    ):
        assert button.parentWidget() is page.selection_bar


def test_fleet_table_and_skeleton_use_the_warm_variant(fleet_page):
    page = fleet_page
    assert page.table.state is LoadState.LOADED
    assert page.table.row_count() == 5
    assert page.table._table.property("variant") == "warm"
    assert page.table._table.alternatingRowColors() is False
    assert page.table.table.cellWidget(0, 5) is not None  # status chip
    assert page._skel_tools.property("variant") == "warm"
    assert page._skel_data.property("variant") == "warm"


# --------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------


def test_customer_licence_chips_sync_the_hidden_combo(customers_page):
    page = customers_page
    assert page.licence_combo.isHidden()
    assert {
        "all",
        "valid",
        "expiring",
        "expired",
        "missing",
    } <= set(page.licence_chips.buttons)

    page.licence_chips.buttons["expired"].click()
    _wait_for_customers(page)
    assert page.licence_combo.currentText() == "Expired"
    assert {
        page.table.cell_text(r, 0) for r in range(page.table.row_count())
    } == {"Already Lapsed"}

    page.licence_combo.setCurrentText("Expiring soon")
    _wait_for_customers(page)
    assert page.licence_chips.selected == "expiring"
    assert {
        page.table.cell_text(r, 0) for r in range(page.table.row_count())
    } == {"Soon Lapsed"}


def test_customer_selection_bar_names_the_customer_and_licence(customers_page):
    page = customers_page
    assert page.selection_bar.property("active") in (False, 0)
    assert page.add_button.isEnabled()
    assert not page.edit_button.isEnabled()
    assert not page.details_button.isEnabled()
    assert not page.settle_button.isEnabled()
    assert not page.deactivate_button.isEnabled()
    assert not page.export_button.isEnabled()

    row = _select_row(page.table, 0, "Ana Reyes")
    page._refresh_actions()

    assert page.selection_bar.property("active") is True
    summary = page.selection_bar.summary_label.text()
    assert "Ana Reyes" in summary
    assert page.table.cell_text(row, 3) in summary
    assert page.edit_button.isEnabled()
    assert page.details_button.isEnabled()
    assert page.settle_button.isEnabled()
    assert page.deactivate_button.isEnabled()
    assert page.export_button.isEnabled()
    assert page.deactivate_button.objectName() == "barDanger"

    # A customer without a licence falls back to the email identifier.
    _select_row(page.table, 0, "No Licence")
    page._refresh_actions()
    fallback = page.selection_bar.summary_label.text()
    assert "No Licence" in fallback
    assert "no.licence@example.com" in fallback


def test_customer_table_and_skeleton_use_the_warm_variant(customers_page):
    page = customers_page
    assert page.table.state is LoadState.LOADED
    assert page.table.row_count() == 4
    assert page.table._table.property("variant") == "warm"
    assert page.table._table.alternatingRowColors() is False
    assert page._skel_tools.property("variant") == "warm"
    assert page._skel_data.property("variant") == "warm"


# --------------------------------------------------------------------------
# Maintenance
# --------------------------------------------------------------------------


def test_maintenance_status_chips_and_type_dropdown_still_filter(
    maintenance_page,
):
    page = maintenance_page
    assert page.status_combo.isHidden()
    assert not page.type_combo.isHidden()
    assert page.table.row_count() == 3

    page.status_chips.buttons["completed"].click()
    _wait_for_maintenance(page)
    assert page.status_combo.currentText() == "Completed"
    assert page.table.row_count() == 1
    assert page.table.cell_text(0, 3) == "Oil change"

    page.status_combo.setCurrentIndex(2)  # In Progress
    _wait_for_maintenance(page)
    assert page.status_chips.selected == "in_progress"
    assert page.table.row_count() == 1
    assert page.table.cell_text(0, 3) == "Brake pads"

    page.status_chips.buttons["all"].click()
    _wait_for_maintenance(page)
    page.type_combo.setCurrentText("Routine service")
    _wait_for_maintenance(page)
    assert page.table.row_count() == 1
    assert page.table.cell_text(0, 2) == "Routine service"
    assert page.table.cell_text(0, 3) == "Oil change"


def test_maintenance_selection_bar_names_the_job_and_vehicle(maintenance_page):
    page = maintenance_page
    assert page.selection_bar.property("active") in (False, 0)
    assert page.add_button.isEnabled()
    assert not page.complete_button.isEnabled()
    assert not page.cancel_button.isEnabled()
    assert not page.history_button.isEnabled()
    assert not page.open_button.isEnabled()

    row = _select_row(page.table, 3, "Brake pads")
    page._refresh_actions()

    assert page.selection_bar.property("active") is True
    summary = page.selection_bar.summary_label.text()
    assert f"#{page.table.cell_text(row, 0)}" in summary
    assert "SHOP-001" in summary
    assert "Brake pads" in summary
    assert page.complete_button.isEnabled()
    assert page.cancel_button.isEnabled()
    assert page.history_button.isEnabled()
    assert page.open_button.isEnabled()
    assert page.cancel_button.objectName() == "barDanger"
    assert page.hint_label.text() == "Completing releases the vehicle."


def test_maintenance_both_tables_are_warm(maintenance_page):
    page = maintenance_page
    assert page._due_panel.property("variant") == "warm"
    assert page._log_panel.property("variant") == "warm"
    assert page._data_panel is page._log_panel
    assert page.table._table.property("variant") == "warm"
    assert page.due._table.property("variant") == "warm"
    assert page.table.state is LoadState.LOADED
    assert page.due.state in (LoadState.LOADED, LoadState.EMPTY)


# --------------------------------------------------------------------------
# Payments
# --------------------------------------------------------------------------


def test_payments_page_uses_the_reference_warm_recipe(payments_page):
    page = payments_page
    assert page.body.itemAt(0).widget() is page._tools_panel
    assert isinstance(page._tools_panel, WarmHost)
    assert _panel_variant(page._tools_panel) == "warmHost"
    assert isinstance(page.filter_card, WarmPanel)
    assert _panel_variant(page.filter_card) == "warm"
    assert _panel_variant(page._takings_panel) == "warm"
    assert _panel_variant(page._outstanding_panel) == "warm"
    assert isinstance(page.selection_bar, SelectionBar)
    assert page.body.spacing() == CARD_GUTTER
    # Reference proportions: the payments card is taller than outstanding.
    assert page.body.stretch(page.body.indexOf(page._takings_panel)) == 6
    assert page.body.stretch(page.body.indexOf(page._outstanding_panel)) == 5
    # Both cards are visible with their own range footer.
    assert page.takings_caption.text().startswith("Latest ")
    assert page.outstanding_caption.text().startswith("Largest ")
    assert page.takings_footer_count.text().endswith(
        f"of {page._takings_cap}"
    )
    assert page.outstanding_footer_count.text().endswith(
        f"of {page._outstanding_cap}"
    )
    assert page.takings_load_more.text() == "Load more"
    assert page.outstanding_load_more.text() == "Load more"


def test_payments_kind_chips_sync_the_hidden_combo(payments_page):
    page = payments_page
    assert page.kind_combo.isHidden()
    assert {
        "all",
        "rental",
        "deposit",
        "deposit_applied",
    } <= set(page.kind_chips.buttons)

    page.kind_chips.buttons["rental"].click()
    _wait_for_payments(page)
    assert page.kind_combo.currentText() == "Rental"
    assert {
        page.takings.cell_text(r, 5)
        for r in range(page.takings.row_count())
    } == {"Rental"}

    page.kind_combo.setCurrentText("Deposit")
    _wait_for_payments(page)
    assert page.kind_chips.selected == "deposit"
    assert {
        page.takings.cell_text(r, 5)
        for r in range(page.takings.row_count())
    } == {"Deposit"}

    page.kind_chips.buttons["all"].click()
    _wait_for_payments(page)
    assert page.kind_combo.currentIndex() == 0


def test_payments_tables_use_the_warm_variant_and_chips(payments_page):
    from app.staff.tables import ChipCell, TableChip

    page = payments_page
    for table in (page.takings, page.outstanding):
        assert table._table.property("variant") == "warm"
        assert table._table.alternatingRowColors() is False
        assert table._table.wordWrap() is True
        assert table._table.verticalHeader().defaultSectionSize() == 56
        assert table._table.horizontalHeader().objectName() == "warmHeader"

    takings_row = 0
    amount = page.takings.table.item(takings_row, 3)
    assert amount is not None
    assert amount.textAlignment() & Qt.AlignmentFlag.AlignRight
    kind_host = page.takings.table.cellWidget(takings_row, 5)
    assert isinstance(kind_host, ChipCell)
    kind_chip = kind_host.findChild(TableChip)
    assert kind_chip is not None
    assert kind_chip.property("tone") == "outline"
    status_host = page.takings.table.cellWidget(takings_row, 6)
    assert isinstance(status_host, ChipCell)

    outstanding_row = 0
    owed = page.outstanding.table.item(outstanding_row, 8)
    assert owed is not None
    assert owed.textAlignment() & Qt.AlignmentFlag.AlignRight
    assert owed.foreground().color().name() == "#9b2c1f"
    assert isinstance(
        page.outstanding.table.cellWidget(outstanding_row, 3), ChipCell
    )
    # The vehicle cell keeps the reference's two-line name + plate.
    assert "\n" in page.outstanding.cell_text(outstanding_row, 2)


def test_payments_selection_bar_names_the_selected_row(payments_page):
    page = payments_page
    assert page.selection_bar.property("active") in (False, 0)
    assert not page.collect_button.isEnabled()
    assert not page.view_booking_button.isEnabled()

    page.outstanding.table.selectRow(0)
    page._refresh_selection()
    booking_id = page.outstanding.cell_text(0, 0)
    summary = page.selection_bar.summary_label.text()
    assert page.selection_bar.property("active") is True
    assert booking_id in summary
    assert "Ana Reyes" in summary
    assert "owed" in summary
    assert page.collect_button.isEnabled()
    assert page.view_booking_button.isEnabled()
    assert page.collect_button.parentWidget() is page.selection_bar
    assert page.view_booking_button.parentWidget() is page.selection_bar

    # Selecting a payment row clears the other card, so the bar names one row.
    page.takings.table.selectRow(0)
    page._refresh_selection()
    assert page.outstanding.table.selectionModel().hasSelection() is False
    summary = page.selection_bar.summary_label.text()
    assert booking_id in summary
    assert "Ana Reyes" in summary
    assert "Ref" in summary
