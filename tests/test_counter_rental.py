"""Tests for the counter rental flow: quote, pay, deposit, receipt.

Covers the backend behind the wizard's Pay button: scheduling-fee minimums,
per-class deposits, the absorbed-deposit balance math, cancel/check-out
deposit settlement, same-day bookings, and the licence-first customer lookup.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models import Payment
from app.services import (
    booking_service,
    counter_service,
    customer_service,
    payment_service,
    settings_service,
)
from app.services.booking_service import SCHEDULING_FEE
from app.services.errors import ConflictError, StateError, ValidationError

TODAY = date(2026, 9, 29)


def _rental(start_offset=2, days=2):
    return TODAY + timedelta(days=start_offset), TODAY + timedelta(days=start_offset + days)


class TestQuote:
    def test_quote_matches_create_booking_total(self, session, customer, vehicle):
        start, end = _rental()
        days, total = booking_service.quote_for(session, vehicle, start, end)
        assert days == 2
        booking = booking_service.create_booking(session, customer, vehicle, start, end)
        assert booking.total_cost == total

    def test_same_day_quote_is_one_day(self, session, customer, vehicle):
        days, total = booking_service.quote_for(session, vehicle, TODAY, TODAY)
        assert days == 1
        assert total == Decimal("2500.00")


class TestDepositSchedule:
    def test_deposit_by_vehicle_class(self, session, vehicle_factory, car_category, moto_category):
        cases = [
            (vehicle_factory(car_category, vehicle_class="small"), "2000.00"),
            (vehicle_factory(car_category, vehicle_class="suv"), "3000.00"),
            (vehicle_factory(moto_category, vehicle_class="motorcycle"), "1500.00"),
        ]
        for car, expected in cases:
            assert booking_service.deposit_for_vehicle(car) == Decimal(expected)

    def test_missing_class_falls_back(self, session, vehicle):
        assert booking_service.deposit_for_vehicle(vehicle) == Decimal("2500.00")


class TestCompleteCounterRental:
    def test_full_payment_auto_confirms(
        self, session, customer_factory, staff, vehicle
    ):
        customer = customer_factory()
        start, end = _rental()
        _days, total = booking_service.quote_for(session, vehicle, start, end)
        deposit = booking_service.deposit_for_vehicle(vehicle)
        booking, confirmed = booking_service.complete_counter_rental(
            session,
            user=customer,
            vehicle=vehicle,
            start_date=start,
            end_date=end,
            rental_amount=total,
            method="cash",
            staff_id=staff.user_id,
        )
        assert confirmed is True
        assert booking.status == "confirmed"
        kinds = sorted(p.kind for p in booking.payments)
        assert kinds == ["deposit", "rental"]
        balance = payment_service.booking_balance(session, booking)
        assert balance.is_settled
        assert balance.deposit_held == deposit

    def test_fee_only_is_confirmed_with_outstanding(
        self, session, customer_factory, staff, vehicle
    ):
        # Walk-ins are verified in person: confirmed at once, balance due.
        customer = customer_factory()
        start, end = _rental()
        booking, confirmed = booking_service.complete_counter_rental(
            session,
            user=customer,
            vehicle=vehicle,
            start_date=start,
            end_date=end,
            rental_amount=SCHEDULING_FEE,
            method="cash",
            staff_id=staff.user_id,
        )
        assert confirmed is True
        assert booking.status == "confirmed"
        balance = payment_service.booking_balance(session, booking)
        assert balance.balance > 0

    def test_a_held_deposit_is_not_rental_payment(
        self, session, customer_factory, staff, vehicle
    ):
        customer = customer_factory()
        start, end = _rental()
        _days, total = booking_service.quote_for(session, vehicle, start, end)
        booking, _ = booking_service.complete_counter_rental(
            session,
            user=customer,
            vehicle=vehicle,
            start_date=start,
            end_date=end,
            rental_amount=total,
            method="cash",
            staff_id=staff.user_id,
            fee_floor=SCHEDULING_FEE,
            deposit=Decimal("3000.00"),
        )
        balance = payment_service.booking_balance(session, booking)
        assert balance.balance == Decimal("0.00")
        assert balance.amount_paid == total
        assert balance.deposit_held == Decimal("3000.00")
        assert balance.deposit_applied == Decimal("0.00")

    def test_below_scheduling_fee_is_rejected(
        self, session, customer_factory, staff, vehicle
    ):
        customer = customer_factory()
        start, end = _rental()
        with pytest.raises(ValidationError, match="scheduling fee"):
            booking_service.complete_counter_rental(
                session,
                user=customer,
                vehicle=vehicle,
                start_date=start,
                end_date=end,
                rental_amount=Decimal("50.00"),
                method="cash",
                staff_id=staff.user_id,
            )

    def test_overpaying_the_total_is_rejected(
        self, session, customer_factory, staff, vehicle
    ):
        customer = customer_factory()
        start, end = _rental()
        _days, total = booking_service.quote_for(session, vehicle, start, end)
        with pytest.raises(ValidationError, match="more than the rental total"):
            booking_service.complete_counter_rental(
                session,
                user=customer,
                vehicle=vehicle,
                start_date=start,
                end_date=end,
                rental_amount=total + Decimal("1.00"),
                method="cash",
                staff_id=staff.user_id,
            )

    def test_gcash_needs_a_reference(
        self, session, customer_factory, staff, vehicle
    ):
        customer = customer_factory()
        start, end = _rental()
        with pytest.raises(ValidationError, match="reference"):
            booking_service.complete_counter_rental(
                session,
                user=customer,
                vehicle=vehicle,
                start_date=start,
                end_date=end,
                rental_amount=SCHEDULING_FEE,
                method="gcash",
                staff_id=staff.user_id,
            )

    def test_failure_writes_nothing(self, session, customer_factory, staff, vehicle):
        customer = customer_factory()
        start, end = _rental()
        before = session.query(Payment).count()
        with pytest.raises(ValidationError):
            booking_service.complete_counter_rental(
                session,
                user=customer,
                vehicle=vehicle,
                start_date=start,
                end_date=end,
                rental_amount=Decimal("1.00"),
                method="cash",
                staff_id=staff.user_id,
            )
        session.rollback()
        # create_booking flushed before the amount check... but the atomic
        # caller rolls back: count bookings for this customer instead.
        mine = [
            b
            for b in session.query(payment_service.Booking).all()
            if b.user_id == customer.user_id
        ]
        assert mine == []
        assert session.query(Payment).count() == before


class TestCounterQuoteAgreement:
    """The quote the wizard shows is the quote the Pay button records."""

    def test_configured_deposit_is_both_displayed_and_recorded(
        self, session, customer, staff, vehicle
    ):
        settings_service.set_value(session, "deposit_suv", "4000.00")
        vehicle.vehicle_class = "suv"
        start, end = _rental()
        q = counter_service.quote(session, vehicle, start, end)
        booking, _ = booking_service.complete_counter_rental(
            session,
            user=customer,
            vehicle=vehicle,
            start_date=start,
            end_date=end,
            rental_amount=q.total,
            method="cash",
            staff_id=staff.user_id,
            fee_floor=q.scheduling_fee,
            deposit=q.deposit,
        )
        assert payment_service.deposit_held(session, booking) == Decimal("4000.00")
        deposit_row = next(p for p in booking.payments if p.kind == "deposit")
        assert deposit_row.amount == Decimal("4000.00")

    def test_configured_fee_is_the_recorded_minimum(
        self, session, customer, staff, vehicle
    ):
        settings_service.set_value(session, "scheduling_fee", "500.00")
        start, end = _rental()
        q = counter_service.quote(session, vehicle, start, end)
        assert q.scheduling_fee == Decimal("500.00")
        booking, _ = booking_service.complete_counter_rental(
            session,
            user=customer,
            vehicle=vehicle,
            start_date=start,
            end_date=end,
            rental_amount=q.scheduling_fee,
            method="cash",
            staff_id=staff.user_id,
            fee_floor=q.scheduling_fee,
            deposit=q.deposit,
        )
        rental_row = next(p for p in booking.payments if p.kind == "rental")
        assert rental_row.amount == Decimal("500.00")

    def test_explicit_fee_floor_overrides_settings(
        self, session, customer, staff, vehicle
    ):
        settings_service.set_value(session, "scheduling_fee", "500.00")
        start, end = _rental()
        booking, _ = booking_service.complete_counter_rental(
            session,
            user=customer,
            vehicle=vehicle,
            start_date=start,
            end_date=end,
            rental_amount=Decimal("100.00"),
            method="cash",
            staff_id=staff.user_id,
            fee_floor=Decimal("100.00"),
            deposit=Decimal("2500.00"),
        )
        rental_row = next(p for p in booking.payments if p.kind == "rental")
        assert rental_row.amount == Decimal("100.00")

    def test_zero_deposit_records_no_deposit_row(
        self, session, customer, staff, vehicle
    ):
        start, end = _rental()
        booking, _ = booking_service.complete_counter_rental(
            session,
            user=customer,
            vehicle=vehicle,
            start_date=start,
            end_date=end,
            rental_amount=SCHEDULING_FEE,
            method="cash",
            staff_id=staff.user_id,
            fee_floor=SCHEDULING_FEE,
            deposit=Decimal("0.00"),
        )
        assert [p.kind for p in booking.payments] == ["rental"]
        assert payment_service.deposit_held(session, booking) == Decimal("0.00")

    @pytest.mark.parametrize("keyword", ["fee_floor", "deposit"])
    def test_negative_overrides_are_rejected_without_writing(
        self, session, customer, staff, vehicle, keyword
    ):
        start, end = _rental()
        before = session.query(Payment).count()
        with pytest.raises(ValidationError, match="cannot be negative"):
            booking_service.complete_counter_rental(
                session,
                user=customer,
                vehicle=vehicle,
                start_date=start,
                end_date=end,
                rental_amount=SCHEDULING_FEE,
                method="cash",
                staff_id=staff.user_id,
                **{keyword: Decimal("-1.00")},
            )
        session.rollback()
        assert session.query(Payment).count() == before
        mine = [
            b
            for b in session.query(booking_service.Booking).all()
            if b.user_id == customer.user_id
        ]
        assert mine == []

    def test_channel_override_is_recorded(
        self, session, customer, staff, vehicle
    ):
        start, end = _rental()
        booking, _ = booking_service.complete_counter_rental(
            session,
            user=customer,
            vehicle=vehicle,
            start_date=start,
            end_date=end,
            rental_amount=SCHEDULING_FEE,
            method="cash",
            staff_id=staff.user_id,
            channel="online",
            fee_floor=SCHEDULING_FEE,
        )
        assert booking.channel == "online"

    def test_fee_above_total_still_allows_paying_the_total(
        self, session, customer, staff, vehicle
    ):
        settings_service.set_value(session, "scheduling_fee", "5000.00")
        start, end = _rental(days=1)  # 2,500 rental against a 5,000 fee
        q = counter_service.quote(session, vehicle, start, end)
        assert q.total == Decimal("2500.00")
        assert q.scheduling_fee == Decimal("5000.00")

        booking, confirmed = booking_service.complete_counter_rental(
            session,
            user=customer,
            vehicle=vehicle,
            start_date=start,
            end_date=end,
            rental_amount=q.total,
            method="cash",
            staff_id=staff.user_id,
            fee_floor=q.scheduling_fee,
            deposit=q.deposit,
        )
        assert confirmed is True
        rental_row = next(p for p in booking.payments if p.kind == "rental")
        assert rental_row.amount == Decimal("2500.00")


class TestDepositSettlement:
    def _paid_booking(self, session, customer_factory, staff, vehicle, days=2):
        customer = customer_factory()
        start, end = TODAY + timedelta(days=2), TODAY + timedelta(days=2 + days)
        _days, total = booking_service.quote_for(session, vehicle, start, end)
        booking, _ = booking_service.complete_counter_rental(
            session,
            user=customer,
            vehicle=vehicle,
            start_date=start,
            end_date=end,
            rental_amount=total,
            method="cash",
            staff_id=staff.user_id,
        )
        return booking

    def _check_in(self, session, staff, booking):
        return booking_service.check_in(
            session, booking, staff, mileage_reading=100, fuel_level="full"
        )

    def test_clean_return_refunds_deposit_in_full(
        self, session, customer_factory, staff, vehicle
    ):
        booking = self._paid_booking(session, customer_factory, staff, vehicle)
        deposit = payment_service.deposit_held(session, booking)
        assert deposit > 0
        self._check_in(session, staff, booking)
        booking_service.check_out(
            session, booking, staff, booking.end_date,
            mileage_reading=200, fuel_level="full",
        )
        balance = payment_service.booking_balance(session, booking)
        assert balance.deposit_held == 0
        assert balance.deposit_returned == deposit
        assert balance.is_settled

    def test_damage_consumes_deposit_and_reports_zero_held(
        self, session, customer_factory, staff, vehicle
    ):
        booking = self._paid_booking(session, customer_factory, staff, vehicle)
        deposit = payment_service.deposit_held(session, booking)
        self._check_in(session, staff, booking)
        booking_service.check_out(
            session, booking, staff, booking.end_date,
            mileage_reading=200, fuel_level="full",
            damage_notes="Scratch", damage_charge=Decimal("500.00"),
        )
        balance = payment_service.booking_balance(session, booking)
        assert balance.deposit_applied == Decimal("500.00")
        assert balance.deposit_held == Decimal("0.00")
        assert balance.deposit_returned == deposit - Decimal("500.00")
        assert balance.balance == Decimal("0.00")
        assert balance.is_settled

    def test_shortfall_stays_outstanding(
        self, session, customer_factory, staff, vehicle
    ):
        booking = self._paid_booking(session, customer_factory, staff, vehicle)
        deposit = payment_service.deposit_held(session, booking)
        self._check_in(session, staff, booking)
        with pytest.raises(ConflictError, match="outstanding"):
            booking_service.check_out(
                session, booking, staff, booking.end_date,
                mileage_reading=200, fuel_level="full",
                damage_notes="Wrecked", damage_charge=deposit + Decimal("1000.00"),
            )

    def test_cancel_refunds_deposit_but_keeps_the_fee(
        self, session, customer_factory, staff, vehicle
    ):
        booking = self._paid_booking(session, customer_factory, staff, vehicle)
        deposit = payment_service.deposit_held(session, booking)
        booking_service.cancel_booking(session, booking, reason="Changed mind")
        assert booking.status == "cancelled"
        balance = payment_service.booking_balance(session, booking)
        assert balance.deposit_held == 0
        assert balance.deposit_returned == deposit
        # The scheduling fee stays recorded: rental-kind rows are history now.
        rental_paid = sum(
            p.amount for p in booking.payments if p.kind == "rental"
        )
        assert rental_paid > 0

    def test_takings_exclude_held_deposits(
        self, session, customer_factory, staff, vehicle
    ):
        booking = self._paid_booking(session, customer_factory, staff, vehicle)
        takings = payment_service.takings(session)
        rental_paid = sum(
            p.amount
            for p in booking.payments
            if p.kind == "rental" and p.status == "paid"
        )
        assert takings == rental_paid

    def test_takings_exclude_applied_deposits(
        self, session, customer_factory, staff, vehicle
    ):
        booking = self._paid_booking(session, customer_factory, staff, vehicle)
        self._check_in(session, staff, booking)
        booking_service.check_out(
            session, booking, staff, booking.end_date,
            mileage_reading=200, fuel_level="full",
            damage_notes="Scratch", damage_charge=Decimal("500.00"),
        )
        takings = payment_service.takings(session)
        rental_paid = sum(
            p.amount
            for p in booking.payments
            if p.kind == "rental" and p.status == "paid"
        )
        assert payment_service.booking_balance(
            session, booking
        ).deposit_applied == Decimal("500.00")
        assert takings == rental_paid


class TestCustomerIdentity:
    def test_licence_lookup_ignores_case_and_dashes(self, session, customer_factory):
        customer_factory(license_number="D03-1234-567")
        found = customer_service.find_by_licence(session, "d031234567")
        assert found is not None

    def test_duplicate_licence_is_refused(self, session, customer_factory):
        customer_factory(license_number="DUP-001")
        with pytest.raises(ConflictError, match="already has an account"):
            customer_service.create_customer(
                session,
                full_name="Copy Cat",
                email="copy@example.com",
                phone=None,
                address="2 Test Street",
                license_number="dup 001",
                license_expiry=TODAY + timedelta(days=100),
            )

    def test_create_then_find_round_trip(self, session):
        made = customer_service.create_customer(
            session,
            full_name="Walk In",
            email="walk@example.com",
            phone="09170001111",
            address="3 Test Street",
            license_number="WALK-001",
            license_expiry=TODAY + timedelta(days=100),
        )
        assert made.role == "customer"
        assert customer_service.find_by_licence(session, "walk001") is made

    def test_search_finds_by_name_fragment(self, session, customer_factory):
        customer_factory(full_name="Unique Name Here")
        assert len(customer_service.search_customers(session, "Unique Name")) == 1
        assert customer_service.search_customers(session, "   ") == []


class TestExtendBooking:
    def test_extend_reprices_the_whole_range(
        self, session, customer, vehicle, staff
    ):
        start, end = TODAY, TODAY + timedelta(days=2)
        booking = booking_service.create_booking(
            session, customer, vehicle, start, end
        )
        old = booking.total_cost
        booking, old_total, new_total = booking_service.extend_booking(
            session, booking, TODAY + timedelta(days=7)
        )
        assert old_total == old
        # 7 days at 2500 with the 10% long-term car discount.
        assert new_total == Decimal("15750.00")
        assert booking.total_cost == new_total
        assert booking.end_date == TODAY + timedelta(days=7)

    def test_extend_refuses_shortening(self, session, customer, vehicle):
        start, end = TODAY, TODAY + timedelta(days=4)
        booking = booking_service.create_booking(
            session, customer, vehicle, start, end
        )
        with pytest.raises(ValidationError, match="after the current one"):
            booking_service.extend_booking(session, booking, TODAY)

    def test_extend_refuses_completed_history(
        self, session, customer, vehicle, staff
    ):
        start, end = TODAY, TODAY + timedelta(days=2)
        booking = booking_service.create_booking(
            session, customer, vehicle, start, end
        )
        booking_service.check_in(session, booking, staff, 100, "full")
        payment_service.record_payment(
            session, booking, booking.total_cost, "cash"
        )
        booking_service.check_out(session, booking, staff, end, 200, "full")
        with pytest.raises(StateError, match="cannot be extended"):
            booking_service.extend_booking(
                session, booking, TODAY + timedelta(days=5)
            )

    def test_extend_detects_a_clash_on_the_added_days(
        self, session, customer_factory, vehicle
    ):
        first = customer_factory()
        second = customer_factory()
        booking_service.create_booking(
            session, first, vehicle, TODAY, TODAY + timedelta(days=2)
        )
        first_booking = session.query(booking_service.Booking).filter_by(
            user_id=first.user_id
        ).one()
        booking_service.create_booking(
            session, second, vehicle, TODAY + timedelta(days=4),
            TODAY + timedelta(days=6),
        )
        with pytest.raises(ConflictError, match="already booked"):
            booking_service.extend_booking(
                session, first_booking, TODAY + timedelta(days=5)
            )
