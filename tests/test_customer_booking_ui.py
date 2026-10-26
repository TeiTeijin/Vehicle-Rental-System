"""The customer booking screens no longer ask the customer for a branch."""

from __future__ import annotations

import os
from contextlib import nullcontext
from datetime import datetime, time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QLabel  # noqa: E402

from app.models import Branch  # noqa: E402
from app.services import branch_service  # noqa: E402
from app.ui import booking_schedule as schedule_ui  # noqa: E402
from app.ui import bookings as bookings_ui  # noqa: E402
from app.ui.session import BookingDraft, Identity  # noqa: E402
from app.ui.vehicle_photo import VehiclePhoto  # noqa: E402


def _branch(session, name: str = "Main Branch") -> Branch:
    row = Branch(
        name=name,
        address="1 Main Street",
        open_time=time(7, 0),
        close_time=time(21, 0),
        is_active=True,
        created_at=datetime(2026, 1, 1, 8, 0),
    )
    session.add(row)
    session.flush()
    return row


def test_schedule_has_no_branch_selector_and_uses_the_active_branch(
    qapp, monkeypatch, session, vehicle
):
    branch = _branch(session)
    monkeypatch.setattr(schedule_ui, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(VehiclePhoto, "set_vehicle", lambda self, ref: None)

    page = schedule_ui.BookingSchedulePage(
        BookingDraft(vehicle_id=vehicle.vehicle_id)
    )
    try:
        assert not hasattr(page, "different_branch")
        assert not hasattr(page.pickup, "branch_combo")
        assert not hasattr(page.return_moment, "branch_combo")
        assert page.draft.pickup_branch_id == branch.branch_id
        assert page.draft.return_branch_id is None
        expected_slots = len(branch_service.time_slots(branch))
        assert page.pickup.time_combo.count() == expected_slots
        assert page.return_moment.time_combo.count() == expected_slots
    finally:
        page.deleteLater()


def test_booking_card_does_not_show_the_branch(
    qapp, monkeypatch, session, customer, vehicle, booking_factory
):
    branch = _branch(session, name="Downtown Branch")
    booking_factory(
        customer,
        vehicle,
        pickup_branch_id=branch.branch_id,
        payment_method="cash",
    )
    monkeypatch.setattr(bookings_ui, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(VehiclePhoto, "set_vehicle", lambda self, ref: None)

    page = bookings_ui.BookingsPage()
    try:
        page.set_user(Identity.of(customer))

        assert len(page._cards) == 1
        labels = [label.text() for label in page._cards[0].findChildren(QLabel)]
        assert all("Downtown Branch" not in text for text in labels)
        assert any("Cash at the counter" in text for text in labels)
    finally:
        page.deleteLater()
