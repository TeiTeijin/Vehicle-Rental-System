"""Booking-derived notifications and the bell's top-bar behaviour."""

from __future__ import annotations

import os
from datetime import date, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QApplication

from app.services import notification_service
from app.ui import theme
from app.ui.home import CARD_WIDTH, Shelf
from app.ui.notifications import NotificationPopup
from app.ui.vehicles import Card, VehicleCard


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous_style = app.styleSheet()
    theme.apply_theme(app)
    yield app
    app.processEvents()
    app.setStyleSheet(previous_style)
    app.processEvents()


def test_booking_updates_cover_success_pickup_and_completion(
    session, customer, vehicle, booking_factory
):
    today = date(2026, 10, 24)
    pickup = booking_factory(
        customer,
        vehicle,
        start_date=today,
        status="confirmed",
        reference_code="RW-TODAY1",
    )
    upcoming = booking_factory(
        customer,
        vehicle,
        start_date=today + timedelta(days=5),
        status="confirmed",
        reference_code="RW-UPCOM1",
    )
    done = booking_factory(
        customer,
        vehicle,
        start_date=today - timedelta(days=10),
        end_date=today - timedelta(days=8),
        actual_return_date=today - timedelta(days=8),
        status="completed",
        reference_code="RW-DONE01",
    )
    booking_factory(
        customer,
        vehicle,
        start_date=today + timedelta(days=2),
        status="cancelled",
        reference_code="RW-CANCEL",
    )

    items = notification_service.notifications_for(
        session, customer.user_id, today=today
    )

    assert [item.kind for item in items] == [
        notification_service.KIND_PICKUP,
        notification_service.KIND_SUCCESS,
        notification_service.KIND_COMPLETED,
    ]
    assert items[0].key == f"booking:{pickup.booking_id}:pickup"
    assert "Today" in items[0].meta
    assert items[1].key == f"booking:{upcoming.booking_id}:confirmed"
    assert "RW-UPCOM1" in items[1].detail
    assert items[2].key == f"booking:{done.booking_id}:completed"
    assert "Returned" in items[2].meta


def test_notifications_are_scoped_to_the_customer(
    session, customer, customer_factory, vehicle, booking_factory
):
    other = customer_factory()
    mine = booking_factory(customer, vehicle, status="confirmed")
    booking_factory(other, vehicle, status="confirmed")

    items = notification_service.notifications_for(
        session, customer.user_id, today=date(2026, 10, 24)
    )

    assert [item.booking_id for item in items] == [mine.booking_id]


def test_navbar_bell_is_account_only_and_rings_while_unread(qapp):
    navbar = theme.NavBar()
    navbar.show()
    qapp.processEvents()

    assert navbar.notifications_button.isHidden() is True
    assert list(navbar.tab_buttons) == ["vehicles", "bookings", "payments"]

    navbar.set_user("Demo")
    assert navbar.notifications_button.isHidden() is False
    assert navbar.notifications_button.toolTip() == "Notifications"

    navbar.set_notification_state(True, 2)
    assert navbar.notifications_button.property("unread") is True
    assert "2 new" in navbar.notifications_button.toolTip()
    assert (
        navbar._bell_animation.state()
        == QAbstractAnimation.State.Running
    )

    navbar.set_notification_state(False, 0)
    assert navbar.notifications_button.property("unread") is False
    assert navbar._bell_animation.state() == QAbstractAnimation.State.Stopped

    navbar.set_user(None)
    assert navbar.notifications_button.isHidden() is True
    navbar.hide()
    navbar.deleteLater()
    qapp.processEvents()


def test_notification_popup_lists_updates_and_opens_bookings(qapp):
    note = notification_service.Notification(
        key="booking:1:confirmed",
        kind=notification_service.KIND_SUCCESS,
        title="Booking confirmed",
        detail="Toyota Vios \u00b7 RW-ABC123",
        meta="Pickup 25 Oct 2026 \u00b7 Makati City",
        when=date(2026, 10, 25),
        booking_id=1,
        vehicle_id=2,
    )
    popup = NotificationPopup(None, [note], unread_keys={note.key})
    popup.show()
    qapp.processEvents()

    assert len(popup.items) == 1
    assert popup.items[0].notification == note
    assert popup.items[0].property("unread") is True
    seen = []
    popup.bookings_requested.connect(lambda: seen.append(True))
    popup.items[0].clicked.emit()

    assert seen == [True]
    assert popup.isVisible() is False
    popup.deleteLater()
    qapp.processEvents()


def test_shelf_reserves_room_for_the_card_rounded_bottom(qapp):
    shelf = Shelf()
    card = VehicleCard(
        Card(
            1,
            "TOYOTA INNOVA",
            "2024 \u00b7 Automatic \u00b7 8 seats",
            "\u20b13,200 / day",
            "available",
            None,
        ),
        width=CARD_WIDTH,
        defer_photo=True,
    )
    shelf.add(card)
    shelf.finalize()
    shelf.show()
    qapp.processEvents()

    assert shelf.viewport().height() >= card.minimumSizeHint().height() + 4
    assert card.geometry().bottom() <= shelf.viewport().rect().bottom() - 1

    shelf.hide()
    shelf.deleteLater()
    qapp.processEvents()
