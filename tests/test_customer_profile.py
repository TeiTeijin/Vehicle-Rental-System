"""The account information page and its profile payload."""

from __future__ import annotations

import os
from contextlib import nullcontext
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.services import payment_service, profile_service
from app.ui import profile as profile_ui
from app.ui import theme
from app.ui.session import Identity


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous_style = app.styleSheet()
    theme.apply_theme(app)
    yield app
    app.processEvents()
    app.setStyleSheet(previous_style)
    app.processEvents()


def test_profile_rolls_up_account_bookings_and_receipts(
    session, customer, vehicle, booking_factory
):
    booking = booking_factory(customer, vehicle, status="confirmed")
    payment_service.record_payment(
        session, booking, "1200.00", "gcash", reference_no="PROFILE-1"
    )

    profile = profile_service.profile_for(session, customer.user_id)

    assert profile is not None
    assert profile.full_name == customer.full_name
    assert profile.booking_count == 1
    assert profile.active_booking_count == 1
    assert profile.payment_count == 1
    assert profile.status_label == "Active Renter"
    assert profile.status_tone == "green"


def test_profile_scopes_counts_to_the_customer(
    session, customer, customer_factory, vehicle, booking_factory
):
    other = customer_factory()
    mine = booking_factory(customer, vehicle)
    theirs = booking_factory(other, vehicle)
    payment_service.record_payment(session, mine, "1000.00", "cash")
    payment_service.record_payment(session, theirs, "2000.00", "cash")

    profile = profile_service.profile_for(session, customer.user_id)

    assert profile is not None
    assert profile.booking_count == 1
    assert profile.payment_count == 1
    assert profile.active_booking_count == 1


def test_new_customer_has_a_new_customer_status(session, customer):
    profile = profile_service.profile_for(session, customer.user_id)
    assert profile is not None
    assert profile.booking_count == 0
    assert profile.payment_count == 0
    assert profile.status_label == "New Customer"


def test_missing_customer_has_no_profile(session):
    assert profile_service.profile_for(session, 99999) is None


def test_profile_page_draws_the_actual_database_values(
    qapp, monkeypatch, session, customer, vehicle, booking_factory
):
    customer.phone = "09171234567"
    customer.address = "1 Ayala Avenue, Makati City"
    customer.birthdate = date(1998, 4, 9)
    customer.emergency_contact = "Rita Reyes - 09180001111"
    session.flush()
    booking = booking_factory(customer, vehicle, status="confirmed")
    payment_service.record_payment(session, booking, "1200.00", "gcash")

    monkeypatch.setattr(profile_ui, "SessionLocal", lambda: nullcontext(session))
    page = profile_ui.ProfilePage()
    try:
        page.set_user(Identity.of(customer))

        assert page.name_label.text() == customer.full_name
        assert page.email_label.text() == customer.email
        assert page.status.text() == "ACTIVE RENTER"
        assert "1 booking" in page.meta_label.text()
        assert "1 payment" in page.meta_label.text()
        assert page._values["birthdate"].text() == "April 9, 1998"
        assert page._values["phone"].text() == "0917 123 4567"
        assert page._values["address"].text() == customer.address
        assert page.emergency_value.text() == customer.emergency_contact
        assert page._values["license_number"].text() == customer.license_number
        assert page._values["license_class"].text() == "Not provided"
        assert page.license_status.text() in {"VALID", "EXPIRED"}
        assert page.error.isHidden() is True
    finally:
        page.deleteLater()


def test_profile_page_uses_not_provided_for_empty_fields(
    qapp, monkeypatch, session, customer
):
    monkeypatch.setattr(profile_ui, "SessionLocal", lambda: nullcontext(session))
    page = profile_ui.ProfilePage()
    try:
        page.set_user(Identity.of(customer))

        assert page._values["birthdate"].text() == "Not provided"
        assert page._values["nationality"].text() == "Not provided"
        assert page._values["license_class"].text() == "Not provided"
        assert page.emergency_value.text() == "Not provided"
        assert "Add an emergency contact" in page.emergency_hint.text()
    finally:
        page.deleteLater()


def test_profile_strip_sends_bookings_and_payments_to_the_router(
    qapp, monkeypatch, session, customer
):
    monkeypatch.setattr(profile_ui, "SessionLocal", lambda: nullcontext(session))
    page = profile_ui.ProfilePage()
    seen: list[str] = []
    page.tab_requested.connect(seen.append)
    try:
        page.set_user(Identity.of(customer))
        page.tab_buttons["bookings"].click()
        page.tab_buttons["payments"].click()
        page.tab_buttons["personal"].click()
        assert seen == ["bookings", "payments"]
        assert page.tab_buttons["personal"].isChecked() is True
    finally:
        page.deleteLater()


def test_navbar_can_clear_its_active_tab(qapp):
    navbar = theme.NavBar()
    navbar.set_active("payments")
    assert navbar.tab_buttons["payments"].isChecked() is True

    navbar.clear_active()

    assert all(not button.isChecked() for button in navbar.tab_buttons.values())
    navbar.close()
    navbar.deleteLater()
    qapp.processEvents()
