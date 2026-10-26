"""Warm Bookings page and its six booking modals.

The page keeps the old async reload machinery, hidden status bridge, action
buttons, and deep links; these tests pin the visible warm shell and the
contracts the six modals still owe the rest of the app.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import (  # noqa: E402
    Booking,
    Branch,
    Inspection_Report,
    Payment,
    Users,
    Vehicle,
    Vehicle_Category,
)
from app.staff import prefs  # noqa: E402
from app.staff.booking_panels import (  # noqa: E402
    BookingDetailsPanel,
    CheckInPanel,
    CheckOutPanel,
    ExtendPanel,
    PaymentPanel,
    ReasonPanel,
)
from app.staff.context import (  # noqa: E402
    DatabaseSelection,
    DatabaseTarget,
    StaffContext,
    StaffUser,
)
from app.staff.pages.bookings import BookingsPage  # noqa: E402
from app.staff.tables import TableChip  # noqa: E402
from app.staff.ui_kit import (  # noqa: E402
    BookingModal,
    WarmHost,
    WarmPanel,
)
from app.utils.security import hash_password  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QLabel, QWidget  # noqa: E402


class _Shell(QWidget):
    """The page only needs a shell with a context for these tests."""

    def __init__(self, context: StaffContext) -> None:
        super().__init__()
        self.context = context


TODAY = date.today()


def _user(
    session,
    *,
    full_name: str,
    email: str,
    role: str,
    licence: date,
) -> Users:
    row = Users(
        full_name=full_name,
        email=email,
        phone="09170000000",
        password_hash=hash_password("counter-password"),
        address="1 Test Street",
        license_number=f"LIC{abs(hash(email)) % 10**7:07d}",
        license_expiry=licence,
        role=role,
        is_active=True,
        created_at=datetime(2026, 1, 1, 9, 0),
    )
    session.add(row)
    session.flush()
    return row


def _vehicle(session, category, plate: str, *, mileage: int = 10_000) -> Vehicle:
    row = Vehicle(
        category_id=category.category_id,
        make="Toyota",
        model="Vios",
        year=2022,
        plate_number=plate,
        daily_rate=Decimal("2500.00"),
        mileage=mileage,
        status="available",
        created_at=datetime(2026, 1, 1, 9, 0),
    )
    session.add(row)
    session.flush()
    return row


def _booking(
    session,
    user,
    vehicle,
    start: date,
    end: date,
    *,
    status: str,
    total: str = "7500.00",
    created_at: datetime | None = None,
    reference: str | None = None,
) -> Booking:
    row = Booking(
        user_id=user.user_id,
        vehicle_id=vehicle.vehicle_id,
        start_date=start,
        end_date=end,
        actual_return_date=end if status == "completed" else None,
        total_cost=Decimal(total),
        status=status,
        created_by=None,
        reference_code=reference,
        created_at=created_at or datetime(2026, 10, 20, 10, 0),
    )
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def branch(tmp_path):
    path = tmp_path / "bookings-ui.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    engine.dispose()

    context = StaffContext(
        DatabaseSelection(
            url=f"sqlite:///{path}", target=DatabaseTarget.LIVE, label="Test"
        )
    )
    data = SimpleNamespace()
    with context.session() as session:
        category = Vehicle_Category(
            category_name="Car", base_rate_multiplier=1.0
        )
        session.add(category)
        session.flush()

        admin = _user(
            session,
            full_name="Branch Admin",
            email="admin@example.com",
            role="admin",
            licence=TODAY + timedelta(days=365),
        )
        customer = _user(
            session,
            full_name="Ana Reyes",
            email="ana@example.com",
            role="customer",
            licence=TODAY + timedelta(days=400),
        )
        counter = _user(
            session,
            full_name="Counter Staff",
            email="counter@example.com",
            role="staff",
            licence=TODAY + timedelta(days=365),
        )
        branch_row = Branch(
            name="Main Branch",
            address="1 Main Street",
            open_time=time(7, 0),
            close_time=time(21, 0),
            is_active=True,
            created_at=datetime(2026, 1, 1, 8, 0),
        )
        session.add(branch_row)
        session.flush()

        promised = _vehicle(session, category, "PROM-001")
        out_car = _vehicle(session, category, "OUT-001", mileage=20_000)
        free_car = _vehicle(session, category, "FREE-001")

        pending = _booking(
            session,
            customer,
            promised,
            TODAY + timedelta(days=2),
            TODAY + timedelta(days=4),
            status="pending",
            created_at=datetime(2026, 10, 25, 12, 0),
            reference="RW-PENDING",
        )
        confirmed = _booking(
            session,
            customer,
            promised,
            TODAY + timedelta(days=7),
            TODAY + timedelta(days=9),
            status="confirmed",
            created_at=datetime(2026, 10, 24, 12, 0),
            reference="RW-CONFIRMED",
        )
        ongoing = _booking(
            session,
            customer,
            out_car,
            TODAY - timedelta(days=3),
            TODAY + timedelta(days=4),
            status="ongoing",
            created_at=datetime(2026, 10, 23, 12, 0),
            reference="RW-ONGOING",
        )
        finished = _booking(
            session,
            customer,
            free_car,
            TODAY - timedelta(days=20),
            TODAY - timedelta(days=15),
            status="completed",
            total="15000.00",
            created_at=datetime(2026, 10, 22, 12, 0),
            reference="RW-DETAILS42",
        )
        finished.pickup_dt = datetime.combine(
            finished.start_date, time(10, 0)
        )
        finished.return_dt = datetime.combine(
            finished.end_date, time(18, 0)
        )
        finished.pickup_branch_id = branch_row.branch_id
        finished.return_branch_id = branch_row.branch_id
        session.add(
            Payment(
                booking_id=finished.booking_id,
                amount=Decimal("15000.00"),
                method="cash",
                kind="rental",
                status="paid",
                paid_at=datetime.combine(
                    finished.end_date, datetime.min.time()
                ),
                recorded_by=counter.user_id,
                created_at=datetime.combine(
                    finished.end_date, datetime.min.time()
                ),
            )
        )
        session.add(
            Inspection_Report(
                booking_id=finished.booking_id,
                inspected_by=counter.user_id,
                inspection_type="pre-rental",
                mileage_reading=10_000,
                fuel_level="full",
                damage_notes=None,
                inspected_at=datetime.combine(
                    finished.start_date, datetime.min.time()
                ),
            )
        )

        data.admin = admin
        data.customer = customer
        data.counter = counter
        data.pending_id = pending.booking_id
        data.confirmed_id = confirmed.booking_id
        data.confirmed_start = confirmed.start_date
        data.confirmed_end = confirmed.end_date
        data.ongoing_id = ongoing.booking_id
        data.finished_id = finished.booking_id
        data.out_car_id = out_car.vehicle_id

    context.sign_in(
        StaffUser(
            data.admin.user_id,
            data.admin.full_name,
            data.admin.email,
            "admin",
        )
    )
    try:
        yield data, context
    finally:
        context.dispose()


@pytest.fixture
def page(qapp, branch):
    _data, context = branch
    page = BookingsPage(_Shell(context))
    page.refresh()
    _wait_for_bookings(page)
    try:
        yield page
    finally:
        page.deleteLater()
        qapp.processEvents()


def _wait_for_bookings(page, timeout_ms: int = 15000) -> None:
    """Block until the page's async reload has landed."""
    import time as _time

    if page._filter_timer.isActive():
        page._filter_timer.stop()
        page._on_filter_timeout()
    page._pool.waitForDone(15000)
    deadline = _time.monotonic() + timeout_ms / 1000
    while page._done_gen != page._load_gen and _time.monotonic() < deadline:
        QTest.qWait(5)
    assert page._done_gen == page._load_gen, "bookings reload never landed"


def _row_for(page: BookingsPage, booking_id: int) -> int:
    wanted = f"#{booking_id}"
    for row in range(page.table.row_count()):
        if page.table.cell_text(row, 0) == wanted:
            return row
    raise AssertionError(f"{wanted} is not in the table")


def _select(page: BookingsPage, booking_id: int) -> int:
    row = _row_for(page, booking_id)
    page.table.table.selectRow(row)
    page._refresh_actions()
    return row


# -- page shell -------------------------------------------------------------


def test_bookings_page_has_warm_panels_without_a_second_heading(page):
    assert not hasattr(page, "heading")
    assert page.count_caption.isHidden()
    assert page.body.itemAt(0).widget() is page._tools_panel
    assert isinstance(page._tools_panel, WarmHost)
    assert page._tools_panel.property("variant") == "warmHost"
    assert isinstance(page.filter_card, WarmPanel)
    assert page.filter_card.property("variant") == "warm"
    assert isinstance(page._data_panel, WarmPanel)
    assert page._data_panel.property("variant") == "warm"
    assert page._data_panel in page.findChildren(WarmPanel)


def test_status_chips_and_the_hidden_combo_stay_in_sync(page):
    page.status_chips.buttons["pending"].click()
    _wait_for_bookings(page)
    assert page.status_combo.currentText() == "Pending"
    assert page.status_chips.selected == "pending"

    page.status_combo.setCurrentText("Confirmed")
    _wait_for_bookings(page)
    assert page.status_chips.selected == "confirmed"

    page.status_chips.buttons["completed"].click()
    _wait_for_bookings(page)
    assert page.status_combo.currentText() == "Completed"


def test_selection_bar_names_the_row_and_holds_every_action(page, branch):
    data, _context = branch
    row = _select(page, data.finished_id)
    assert page.selection_bar.property("active") is True
    summary = page.selection_bar.summary_label.text()
    assert f"#{data.finished_id}" in summary
    assert page.table.cell_text(row, 1) in summary
    assert page.table.cell_text(row, 2).splitlines()[0] in summary
    for button in page._action_buttons.values():
        assert page.selection_bar.actions.indexOf(button) >= 0
    assert "Cancel booking" == page.cancel_button.text()

    page.table.table.clearSelection()
    page.table.table.setCurrentCell(-1, -1)
    page._refresh_actions()
    assert page.selection_bar.property("active") in (False, 0)
    assert "Select a row" in page.selection_bar.summary_label.text()
    assert not any(
        button.isEnabled() for button in page._action_buttons.values()
    )


def test_booking_columns_keep_ref_paid_and_owed_indexes(page, branch):
    data, _context = branch
    headers = [
        page.table.table.horizontalHeaderItem(index).text()
        for index in range(page.table.table.columnCount())
    ]
    assert headers == [
        "Ref",
        "Customer",
        "Vehicle",
        "Dates",
        "Status",
        "Payment",
        "Total due",
        "Paid",
        "Owed",
        "Channel",
        "Booked",
    ]
    row = _select(page, data.finished_id)
    assert page.table.cell_text(row, 0) == f"#{data.finished_id}"
    assert page.table.cell_text(row, 7) == "15,000.00"
    assert page.table.cell_text(row, 8) == "0.00"


def test_warm_table_renders_status_and_payment_chips(page, branch):
    data, _context = branch
    row = _row_for(page, data.finished_id)
    status_chip = page.table.table.cellWidget(row, 4).findChild(TableChip)
    payment_chip = page.table.table.cellWidget(row, 5).findChild(TableChip)
    assert status_chip is not None and status_chip.objectName() == "tableChip"
    assert payment_chip is not None and payment_chip.objectName() == "tableChip"
    assert page.table.cell_text(row, 4) == "Completed"
    assert page.table.cell_text(row, 5) == "Paid in full"


def test_filters_still_narrow_the_rows(page, branch):
    data, _context = branch
    page.search.setText("no-such-customer-xyz")
    _wait_for_bookings(page)
    assert page.table.row_count() == 0
    page.search.setText("")
    _wait_for_bookings(page)
    page.status_combo.setCurrentText("Completed")
    _wait_for_bookings(page)
    refs = {
        page.table.cell_text(row, 0)
        for row in range(page.table.row_count())
    }
    assert f"#{data.finished_id}" in refs
    assert f"#{data.confirmed_id}" not in refs


def test_load_more_raises_the_local_cap_and_refreshes(monkeypatch, branch):
    data, context = branch
    monkeypatch.setattr(prefs, "rows_limit", lambda default=500: 5)
    with context.session() as session:
        for index in range(3):
            session.add(
                Booking(
                    user_id=data.customer.user_id,
                    vehicle_id=data.out_car_id,
                    start_date=TODAY + timedelta(days=40 + index),
                    end_date=TODAY + timedelta(days=42 + index),
                    total_cost=Decimal("1000.00"),
                    status="confirmed",
                    created_by=None,
                    created_at=datetime(2026, 10, 1, 9, index),
                )
            )
    page = BookingsPage(_Shell(context))
    try:
        page.refresh()
        _wait_for_bookings(page)
        assert page._row_cap == 5
        assert page.table.row_count() == 5
        assert page.load_more_button.isHidden() is False
        assert "Showing 5 of latest 5" == page.footer_count.text()

        page.load_more_button.click()
        _wait_for_bookings(page)
        assert page._row_cap == 55
        assert page.table.row_count() == 7
        assert page.load_more_button.isHidden() is True
        assert "Showing 7 of latest 55" == page.footer_count.text()
        assert "55" in page.count_caption.text()
    finally:
        page.deleteLater()


def test_booking_overlay_opens_the_modal_at_540(page, branch):
    from app.staff.overlay import FrostedOverlay

    data, _context = branch
    _select(page, data.confirmed_id)
    assert page.open_check_in() is True
    overlay = page._booking_overlay
    assert isinstance(overlay, FrostedOverlay)
    assert overlay.scroll.width() == 540
    assert isinstance(overlay.scroll.widget(), BookingModal)
    overlay.hide_overlay()


# -- the six modals ---------------------------------------------------------


def _modals(context, data):
    return [
        (BookingDetailsPanel(context, data.finished_id), "BOOKING"),
        (ReasonPanel(), "CANCEL BOOKING"),
        (CheckInPanel(), "CHECK IN"),
        (CheckOutPanel(Decimal("1500.00")), "CHECK OUT"),
        (
            ExtendPanel(context, data.confirmed_id, data.confirmed_end),
            "EXTEND BOOKING",
        ),
        (PaymentPanel(Decimal("1500.00"), show_kind=True), "RECORD PAYMENT"),
    ]


def test_each_modal_is_a_booking_modal_at_540(qapp, branch):
    data, context = branch
    for panel, eyebrow in _modals(context, data):
        try:
            assert isinstance(panel, BookingModal)
            assert panel.card.width() == 540
            assert panel.eyebrow_label.text() == eyebrow
        finally:
            panel.deleteLater()


def test_details_shows_money_tiles_and_keeps_every_fact(qapp, branch):
    data, context = branch
    panel = BookingDetailsPanel(context, data.finished_id)
    try:
        assert panel.total_tile.value_label.text() == "₱15,000.00"
        assert panel.paid_tile.value_label.text() == "₱15,000.00"
        assert panel.owed_tile.value_label.text() == "₱0.00"
        texts = "\n".join(
            label.text() for label in panel.findChildren(QLabel)
        )
        assert "RW-DETAILS42" in texts
        assert "10:00" in texts and "18:00" in texts
        assert texts.count("Main Branch") == 2
        lower = texts.lower()
        assert "customer" in lower and "vehicle" in lower
        assert "money" in lower and "inspections" in lower
        assert "penalties" in lower and "No penalties." in texts
    finally:
        panel.deleteLater()


def test_cancel_reasons_and_cash_summary(qapp):
    panel = ReasonPanel(
        paid_so_far=Decimal("4500.00"),
        refund_due=Decimal("2000.00"),
    )
    seen: list[dict] = []
    panel.submitted.connect(seen.append)
    try:
        assert panel.summary.rows[0][0].text() == "Paid so far"
        assert panel.summary.rows[0][1].text() == "₱4,500.00"
        assert panel.summary.rows[1][0].text() == "Refund due"
        assert panel.summary.rows[1][1].text() == "₱2,000.00"

        panel.reason_chips.buttons["customer_request"].click()
        panel.reason_edit.setText("moved away")
        panel._on_go()
        assert seen[-1]["reason"] == "Customer request — moved away"

        panel.reason_chips.buttons["other"].click()
        panel.reason_edit.clear()
        before = len(seen)
        panel._on_go()
        assert len(seen) == before
        assert panel._error.text()

        panel.reason_edit.setText("Fleet reshuffle")
        panel._on_go()
        assert seen[-1]["reason"] == "Other — Fleet reshuffle"
    finally:
        panel.deleteLater()


def test_check_in_fuel_and_damage_chips_write_the_old_fields(qapp):
    panel = CheckInPanel(current_mileage=10_000)
    seen: list[dict] = []
    panel.submitted.connect(seen.append)
    try:
        assert panel.fuel_combo.currentText() == "full"
        panel.fuel_chips.buttons["half"].click()
        assert panel.fuel_combo.currentText() == "half"

        panel.damage_chips.buttons["Scratch"].click()
        panel.damage_chips.buttons["Dent"].click()
        assert panel.damage_edit.text() == "Scratch, Dent"
        panel.damage_chips.buttons["No damage"].click()
        assert panel.damage_edit.text() == ""
        panel.damage_chips.buttons["Cracked glass"].click()
        assert panel.damage_edit.text() == "Cracked glass"

        panel.mileage_edit.setValue(12_000)
        panel._on_go()
        assert seen[-1]["mileage"] == 12_000
        assert seen[-1]["fuel_level"] == "half"
        assert seen[-1]["damage_notes"] == "Cracked glass"
    finally:
        panel.deleteLater()


def test_check_out_distance_and_floor_guard(qapp):
    panel = CheckOutPanel(
        Decimal("1500.00"), pickup_mileage=10_000
    )
    seen: list[dict] = []
    panel.submitted.connect(seen.append)
    try:
        assert "not below 10,000 km" in panel.floor_label.text()
        panel.mileage_edit.setValue(10_500)
        assert panel.distance_tile.value_label.text() == "500 km"
        panel.mileage_edit.setValue(9_000)
        panel._on_go()
        assert seen == []
        assert "below the 10,000 km" in panel._error.text()

        panel.mileage_edit.setValue(10_500)
        panel._on_go()
        assert seen[-1]["mileage"] == 10_500
        assert seen[-1]["damage_charge"] is None
    finally:
        panel.deleteLater()


def test_extend_quick_chips_move_the_date_and_refresh_the_preview(
    qapp, branch
):
    data, context = branch
    panel = ExtendPanel(context, data.confirmed_id, data.confirmed_end)
    try:
        panel.quick_chips.buttons["7"].click()
        assert (
            panel.end_edit.date().toPython()
            == data.confirmed_end + timedelta(days=7)
        )
        assert "Was ₱" in panel.preview.text()
        assert "due" in panel.preview.text()
        assert len(panel.summary.rows) == 3
        assert panel.was_value.text().startswith("₱")
        assert panel.now_value.text().startswith("₱")
        assert panel.extra_value.text().startswith("₱")
        assert panel.go_button.text().startswith("Extend to ")
    finally:
        panel.deleteLater()


def test_payment_kind_method_sync_and_gcash_gate(qapp):
    panel = PaymentPanel(
        Decimal("1500.00"),
        total=Decimal("7500.00"),
        paid=Decimal("6000.00"),
        show_kind=True,
    )
    seen: list[dict] = []
    panel.submitted.connect(seen.append)
    try:
        assert panel.total_tile.value_label.text() == "₱7,500.00"
        assert panel.paid_tile.value_label.text() == "₱6,000.00"
        assert panel.owed_tile.value_label.text() == "₱1,500.00"

        panel.kind_chips.buttons["deposit"].click()
        assert panel.kind_combo.currentData() == "deposit"

        panel.amount_edit.setText("1,500.00")
        assert panel.go_button.text() == "Record ₱1,500.00"

        panel.method_chips.buttons["gcash"].click()
        assert panel.method_combo.currentText() == "gcash"
        assert panel.ref_edit.isEnabled() is True
        panel.ref_edit.clear()
        panel._on_go()
        assert seen == []
        assert panel._error.text()

        panel.ref_edit.setText("GC-42")
        panel._on_go()
        assert seen[-1]["method"] == "gcash"
        assert seen[-1]["kind"] == "deposit"

        panel.method_combo.setCurrentText("cash")
        assert panel.ref_edit.isEnabled() is False
        assert panel.ref_edit.text() == ""
        assert panel.method_chips.selected == "cash"
    finally:
        panel.deleteLater()


def test_settlement_payment_hides_kind_chips_and_forces_rental(qapp):
    panel = PaymentPanel(
        Decimal("1500.00"),
        total=Decimal("9000.00"),
        paid=Decimal("7500.00"),
        show_kind=False,
    )
    seen: list[dict] = []
    panel.submitted.connect(seen.append)
    try:
        assert panel.kind_chips is None
        assert panel.kind_combo.isHidden() is True
        assert panel.method_chips is not None
        assert panel.owed_tile.value_label.text() == "₱1,500.00"

        # Even a programmatic flip of the compatibility combo cannot turn a
        # checkout settlement into a deposit.
        panel.kind_combo.setCurrentIndex(1)
        panel.method_combo.setCurrentText("cash")
        panel.amount_edit.setText("1,500.00")
        panel._on_go()
        assert seen[-1]["kind"] == "rental"
    finally:
        panel.deleteLater()
