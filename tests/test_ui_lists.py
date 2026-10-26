"""Warm list-page pass for Fleet, Customers, and Maintenance.

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

from PySide6.QtTest import QTest  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import Maintenance_Record, Users, Vehicle, Vehicle_Category  # noqa: E402
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
from app.staff.shell import StaffShell  # noqa: E402
from app.staff.tables import LoadState  # noqa: E402
from app.staff.ui_kit import PageHeading  # noqa: E402
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
    "page_attr,title",
    [
        ("fleet_page", "Fleet"),
        ("customers_page", "Customers"),
        ("maintenance_page", "Maintenance"),
    ],
)
def test_each_page_has_a_warm_heading_and_panels(request, page_attr, title):
    page = request.getfixturevalue(page_attr)
    assert isinstance(page, StaffPage)
    assert isinstance(page.heading, PageHeading)
    assert page.heading.title_label.text() == title
    assert page.heading.subtitle_label is page.count_caption
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
