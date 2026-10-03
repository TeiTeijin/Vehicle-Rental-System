"""Tests for the rental lifecycle.

Each test here corresponds to a defect that was in the original
`booking_service`, or to a rule the staff app depends on. The regression tests
are named for the behaviour rather than the bug, so they keep reading as
specifications after the bugs are long forgotten.
"""

from datetime import date, timedelta

import pytest

from app.models import Booking, Maintenance_Record, Payment, Penalty, Vehicle
from app.services import booking_service, fleet_service, payment_service
from app.services.errors import ConflictError, StateError, ValidationError

TODAY = date(2026, 9, 10)
D1 = date(2026, 9, 1)
D3 = date(2026, 9, 3)
D5 = date(2026, 9, 5)
D6 = date(2026, 9, 6)
D8 = date(2026, 9, 8)


# ---------------------------------------------------------------------------
# Creating a booking
# ---------------------------------------------------------------------------


class TestCreateBooking:
    def test_creates_a_pending_booking_with_the_rental_total(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        assert booking.status == "pending"
        assert booking.total_cost == 12500
        assert booking.actual_return_date is None
        assert booking.user_id == customer.user_id

    def test_records_who_took_the_booking(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(
            session, customer, vehicle, D1, D6, created_by=staff.user_id
        )
        assert booking.created_by == staff.user_id

    def test_customer_app_leaves_created_by_empty(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        assert booking.created_by is None

    def test_rejects_a_return_on_or_before_pickup(self, session, customer, vehicle):
        with pytest.raises(ValidationError):
            booking_service.create_booking(session, customer, vehicle, D6, D6)
        with pytest.raises(ValidationError):
            booking_service.create_booking(session, customer, vehicle, D6, D1)

    def test_total_cost_is_decimal_not_text(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        assert booking.total_cost == 12500
        assert booking.total_cost.as_tuple().exponent == -2

    def test_refuses_a_vehicle_already_booked_for_those_dates(
        self, session, customer, vehicle
    ):
        booking_service.create_booking(session, customer, vehicle, D1, D6)
        with pytest.raises(ConflictError, match="already booked"):
            booking_service.create_booking(session, customer, vehicle, D5, D8)

    def test_allows_same_day_turnaround(self, session, customer, vehicle):
        booking_service.create_booking(session, customer, vehicle, D1, D5)
        second = booking_service.create_booking(session, customer, vehicle, D5, D8)
        assert second.booking_id is not None

    def test_allows_a_booking_that_starts_the_day_one_ends(self, session, customer, vehicle):
        booking_service.create_booking(session, customer, vehicle, D1, D5)
        assert booking_service.is_reserved_between(session, vehicle, D5, D8) is False
        assert booking_service.is_reserved_between(session, vehicle, D6, D8) is False

    def test_cancelled_booking_frees_the_dates(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.cancel_booking(session, booking, reason="Customer changed plans")
        again = booking_service.create_booking(session, customer, vehicle, D1, D6)
        assert again.booking_id != booking.booking_id

    def test_completed_booking_frees_the_dates(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.check_in(session, booking, staff, 42000, "full")
        payment_service.record_payment(
            session, booking, booking.total_cost, "cash", recorded_by=staff.user_id
        )
        booking_service.check_out(session, booking, staff, D6, 42100, "full")
        assert booking.status == "completed"
        assert booking_service.is_reserved_between(session, vehicle, D1, D6) is False

    def test_does_not_write_a_reserved_status(self, session, customer, vehicle):
        booking_service.create_booking(session, customer, vehicle, D1, D6)
        assert vehicle.status == "available"

    def test_reserved_between_tracks_a_future_booking(self, session, customer, vehicle):
        booking_service.create_booking(session, customer, vehicle, D1, D6)
        assert booking_service.is_reserved_between(session, vehicle, D1, D6) is True
        assert booking_service.is_reserved_between(session, vehicle, D8, D6) is False


class TestBookingRefusals:
    def test_refuses_a_vehicle_out_on_rent(self, session, customer, vehicle, staff):
        first = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.check_in(session, first, staff, 42000, "full")
        assert vehicle.status == "rented"
        with pytest.raises(ConflictError, match="rented"):
            booking_service.create_booking(session, customer, vehicle, D6, D8)

    def test_refuses_a_vehicle_in_the_workshop(self, session, vehicle):
        fleet_service.schedule_maintenance(
            session, vehicle, "Brake pads", D1, D5, 4500
        )
        assert vehicle.status == "maintenance"
        with pytest.raises(ConflictError, match="workshop"):
            booking_service.assert_available(session, vehicle, D3, D8)

    def test_a_workshop_conflict_names_the_job(self, session, vehicle):
        fleet_service.schedule_maintenance(session, vehicle, "Brake pads", D1, D5, 4500)
        with pytest.raises(ConflictError, match="Brake pads"):
            booking_service.assert_available(session, vehicle, D3, D8)

    def test_refuses_dates_that_touch_open_maintenance(self, session, vehicle):
        fleet_service.schedule_maintenance(session, vehicle, "Brake pads", D1, D5, 4500)
        with pytest.raises(ConflictError, match="workshop"):
            booking_service.assert_available(session, vehicle, D1, D5)

    def test_a_workshop_date_range_is_free_again_once_the_car_is_out_of_it(self, session, vehicle):
        fleet_service.schedule_maintenance(session, vehicle, "Brake pads", D1, D5, 4500)
        with pytest.raises(ConflictError, match="currently maintenance"):
            booking_service.assert_available(session, vehicle, D5, D8)

    def test_ignores_completed_maintenance(self, session, vehicle, customer):
        record = fleet_service.schedule_maintenance(session, vehicle, "Oil change", D1, D5, 500)
        fleet_service.complete_maintenance(session, record)
        assert vehicle.status == "available"
        booking_service.create_booking(session, customer, vehicle, D1, D5)
        assert vehicle.status == "available"

    def test_refuses_an_expired_licence(self, session, customer, vehicle):
        customer.license_expiry = D1
        with pytest.raises(ConflictError, match="Licence expired"):
            booking_service.create_booking(session, customer, vehicle, D6, D8)

    def test_licence_expiry_is_judged_against_the_start_date(self, session, customer, vehicle):
        customer.license_expiry = D6
        booking_service.create_booking(session, customer, vehicle, D1, D8)

    def test_licence_valid_for_the_start_date_is_accepted(self, session, customer, vehicle):
        customer.license_expiry = TODAY
        booking_service.create_booking(session, customer, vehicle, D1, D6)


# ---------------------------------------------------------------------------
# Confirm / cancel
# ---------------------------------------------------------------------------


class TestConfirmAndCancel:
    def test_confirms_a_pending_booking(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.confirm_booking(session, booking)
        assert booking.status == "confirmed"

    def test_cannot_confirm_a_cancelled_booking(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.cancel_booking(session, booking)
        with pytest.raises(StateError):
            booking_service.confirm_booking(session, booking)

    def test_cancel_stores_a_reason(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.cancel_booking(session, booking, reason="Customer sick")
        assert booking.cancel_reason == "Customer sick"

    def test_cancel_defaults_to_something_better_than_null(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.cancel_booking(session, booking)
        assert booking.cancel_reason

    def test_cannot_cancel_an_ongoing_rental(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.check_in(session, booking, staff, 42000, "full")
        with pytest.raises(StateError, match="cannot be cancelled"):
            booking_service.cancel_booking(session, booking)

    def test_cancel_does_not_change_the_vehicle_status(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.cancel_booking(session, booking)
        assert vehicle.status == "available"


# ---------------------------------------------------------------------------
# Check in
# ---------------------------------------------------------------------------


class TestCheckIn:
    def test_marks_the_vehicle_rented(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        report = booking_service.check_in(session, booking, staff, 42000, "full")
        assert vehicle.status == "rented"
        assert booking.status == "ongoing"
        assert report.inspection_type == "pre-rental"

    def test_records_the_odometer(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.check_in(session, booking, staff, 43500, "full")
        assert vehicle.mileage == 43500

    def test_accepts_a_confirmed_booking(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.confirm_booking(session, booking)
        booking_service.check_in(session, booking, staff, 42000, "full")
        assert booking.status == "ongoing"

    def test_cannot_check_in_twice(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.check_in(session, booking, staff, 42000, "full")
        with pytest.raises(StateError, match="cannot be checked in"):
            booking_service.check_in(session, booking, staff, 42100, "full")

    def test_cannot_check_in_a_cancelled_booking(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.cancel_booking(session, booking)
        with pytest.raises(StateError):
            booking_service.check_in(session, booking, staff, 42000, "full")

    def test_rejects_a_backwards_odometer(self, session, customer, vehicle, staff):
        vehicle.mileage = 50000
        session.flush()
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        with pytest.raises(ValidationError, match="below"):
            booking_service.check_in(session, booking, staff, 42000, "full")

    def test_rejects_an_unknown_fuel_level(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        with pytest.raises(ValidationError, match="fuel level"):
            booking_service.check_in(session, booking, staff, 42000, "almost empty")

    def test_refuses_a_vehicle_that_went_to_the_workshop_after_booking(
        self, session, customer, vehicle, staff
    ):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        fleet_service.schedule_maintenance(session, vehicle, "Windscreen", D1, D5, 2000)
        with pytest.raises(ConflictError, match="workshop"):
            booking_service.check_in(session, booking, staff, 42000, "full")

    def test_photo_is_optional(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        report = booking_service.check_in(session, booking, staff, 42000, "full")
        assert report.photo_url is None


# ---------------------------------------------------------------------------
# Check out
# ---------------------------------------------------------------------------


class TestCheckOut:
    @pytest.fixture
    def ongoing(self, session, customer, vehicle, staff):
        """A rental that is out, with nothing paid yet."""
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.check_in(session, booking, staff, 42000, "full")
        return booking

    @pytest.fixture
    def settled_ongoing(self, session, customer, vehicle, staff):
        """A rental that is out and paid for in full.

        Anything charged on top of the rental -- a late fee, damage -- lands as
        an outstanding balance against this, which is how a real return works.
        """
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        payment_service.record_payment(session, booking, booking.total_cost, "cash")
        booking_service.check_in(session, booking, staff, 42000, "full")
        return booking

    def test_completes_the_booking_and_frees_the_vehicle(
        self, session, ongoing, vehicle, staff
    ):
        report = booking_service.check_out(
            session, ongoing, staff, D6, 42100, "full", require_settlement=False
        )
        assert ongoing.status == "completed"
        assert ongoing.actual_return_date == D6
        assert vehicle.status == "available"
        assert report.inspection_type == "post-rental"

    def test_charges_a_damage_cost(self, session, ongoing, staff):
        booking_service.check_out(
            session,
            ongoing,
            staff,
            D6,
            42100,
            "full",
            damage_notes="Scratched rear bumper",
            damage_charge=2500,
            require_settlement=False,
        )
        damage = [p for p in ongoing.penalties if p.penalty_type == "damage"]
        assert len(damage) == 1
        assert damage[0].amount == 2500

    def test_notes_damage_without_a_charge(self, session, ongoing, staff):
        booking_service.check_out(
            session, ongoing, staff, D6, 42100, "full",
            damage_notes="Light scratch", damage_charge=0,
            require_settlement=False,
        )
        damage = [p for p in ongoing.penalties if p.penalty_type == "damage"]
        assert damage[0].amount == 0

    def test_untouched_damage_gets_no_penalty_row(self, session, ongoing, staff):
        booking_service.check_out(
            session, ongoing, staff, D6, 42100, "full", require_settlement=False
        )
        assert ongoing.penalties == []

    def test_charges_a_late_fee(self, session, ongoing, staff):
        booking_service.check_out(
            session, ongoing, staff, D8, 42100, "full", require_settlement=False
        )
        late = [p for p in ongoing.penalties if p.penalty_type == "late_return"]
        assert late[0].amount == 2000

    def test_no_late_fee_on_the_due_date(self, session, ongoing, staff):
        booking_service.check_out(
            session, ongoing, staff, D6, 42100, "full", require_settlement=False
        )
        assert [p for p in ongoing.penalties if p.penalty_type == "late_return"] == []

    def test_refuses_to_close_with_money_outstanding(self, session, ongoing, staff):
        with pytest.raises(ConflictError, match="outstanding"):
            booking_service.check_out(session, ongoing, staff, D6, 42100, "full")

    def test_a_settled_booking_closes_cleanly(self, session, settled_ongoing, staff):
        report = booking_service.check_out(session, settled_ongoing, staff, D6, 42100, "full")
        assert settled_ongoing.status == "completed"
        assert report.inspection_type == "post-rental"

    def test_can_close_unsettled_when_told_to(self, session, ongoing, staff):
        booking_service.check_out(
            session, ongoing, staff, D6, 42100, "full", require_settlement=False
        )
        assert ongoing.status == "completed"

    def test_damage_charge_counts_toward_the_balance(self, session, settled_ongoing, staff):
        with pytest.raises(ConflictError, match="outstanding"):
            booking_service.check_out(
                session, settled_ongoing, staff, D6, 42100, "full", damage_charge=1500
            )

    def test_returning_a_vehicle_sends_it_to_maintenance(
        self, session, ongoing, vehicle, staff
    ):
        fleet_service.schedule_maintenance(session, vehicle, "Service", TODAY, TODAY + timedelta(days=2), 3000)
        booking_service.check_out(
            session, ongoing, staff, D6, 42100, "full", require_settlement=False
        )
        assert vehicle.status == "maintenance"

    def test_cannot_check_out_a_booking_never_checked_in(
        self, session, customer, vehicle, staff
    ):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        with pytest.raises(StateError, match="ongoing"):
            booking_service.check_out(session, booking, staff, D6, 42100, "full")

    def test_rejects_a_return_before_pickup(self, session, ongoing, staff):
        with pytest.raises(ValidationError, match="before the pick-up"):
            booking_service.check_out(session, ongoing, staff, D1 - timedelta(days=1), 42100, "full")

    def test_rejects_a_backwards_return_odometer(self, session, ongoing, vehicle, staff):
        with pytest.raises(ValidationError, match="below"):
            booking_service.check_out(session, ongoing, staff, D6, 1, "full")

    def test_updates_the_odometer(self, session, ongoing, vehicle, staff):
        booking_service.check_out(
            session, ongoing, staff, D6, 99999, "full", require_settlement=False
        )
        assert vehicle.mileage == 99999


class TestLateFeePreview:
    def test_preview_is_zero_when_on_time(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        assert booking_service.late_fee_for(booking, D6) == 0

    def test_preview_uses_the_daily_rate(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        assert booking_service.late_fee_for(booking, D8) == 2000

    def test_preview_accepts_a_custom_rate(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        assert booking_service.late_fee_for(booking, D8, per_day=500) == 1000

    def test_preview_does_not_mutate_the_booking(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.late_fee_for(booking, D8)
        assert booking.penalties == []


# ---------------------------------------------------------------------------
# Penalties
# ---------------------------------------------------------------------------


class TestPenalties:
    def test_applies_a_valid_penalty(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        penalty = booking_service.apply_penalty(session, booking, "cleaning", 750, "Interior")
        assert penalty.amount == 750
        assert penalty.booking_id == booking.booking_id

    def test_rejects_an_unknown_type(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        with pytest.raises(ValidationError, match="Unknown penalty type"):
            booking_service.apply_penalty(session, booking, "vandalism", 100, "?")

    def test_rejects_a_negative_amount(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        with pytest.raises(ValidationError, match="negative"):
            booking_service.apply_penalty(session, booking, "damage", -100, "?")

    def test_rejects_a_zero_late_fee(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        with pytest.raises(ValidationError, match="needs an amount"):
            booking_service.apply_penalty(session, booking, "late_return", 0, "?")

    def test_allows_a_zero_damage_note(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        penalty = booking_service.apply_penalty(session, booking, "damage", 0, "Scratch")
        assert penalty.amount == 0


# ---------------------------------------------------------------------------
# Dashboard queries
# ---------------------------------------------------------------------------


class TestTodayQueries:
    def test_lists_bookings_that_should_be_out_today(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, TODAY, TODAY + timedelta(days=2))
        booking_service.confirm_booking(session, booking)
        assert booking_service.expected_vehicles_today(session, TODAY) == [booking]

    def test_a_booking_ending_today_is_not_still_out(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, TODAY - timedelta(days=2), TODAY)
        booking_service.confirm_booking(session, booking)
        assert booking_service.expected_vehicles_today(session, TODAY) == []

    def test_a_booking_ending_yesterday_is_not_out_today(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, TODAY - timedelta(days=3), TODAY - timedelta(days=1))
        booking_service.confirm_booking(session, booking)
        assert booking_service.expected_vehicles_today(session, TODAY) == []

    def test_lists_overdue_returns(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D5)
        booking_service.check_in(session, booking, staff, 42000, "full")
        assert booking in booking_service.overdue_returns(session, TODAY)

    def test_ignores_bookings_not_yet_collected(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D5)
        booking_service.confirm_booking(session, booking)
        assert booking_service.overdue_returns(session, TODAY) == []


# ---------------------------------------------------------------------------
# Money: one balance, one answer
# ---------------------------------------------------------------------------


class TestBalances:
    @pytest.fixture
    def booking(self, session, customer, vehicle):
        return booking_service.create_booking(session, customer, vehicle, D1, D6)

    def test_a_new_booking_owes_the_full_amount(self, session, booking):
        balance = payment_service.booking_balance(session, booking)
        assert balance.total_due == 12500
        assert balance.amount_paid == 0
        assert balance.balance == 12500
        assert balance.is_settled is False

    def test_a_deposit_reduces_the_balance(self, session, booking):
        payment_service.record_payment(session, booking, 5000, "gcash")
        assert payment_service.booking_balance(session, booking).balance == 7500

    def test_two_payments_settle_it(self, session, booking, staff):
        payment_service.record_payment(session, booking, 5000, "gcash")
        payment_service.record_payment(session, booking, 7500, "cash", recorded_by=staff.user_id)
        balance = payment_service.booking_balance(session, booking)
        assert balance.amount_paid == 12500
        assert balance.balance == 0
        assert balance.is_settled is True

    def test_penalties_add_to_what_is_owed(self, session, booking):
        booking_service.apply_penalty(session, booking, "cleaning", 750, "Interior")
        assert payment_service.booking_balance(session, booking).balance == 13250

    def test_a_pending_payment_does_not_count(self, session, booking):
        payment_service.record_payment(session, booking, 12500, "gcash", status="pending")
        assert payment_service.booking_balance(session, booking).balance == 12500

    def test_a_failed_payment_does_not_count(self, session, booking):
        payment_service.record_payment(session, booking, 12500, "card", status="failed")
        assert payment_service.booking_balance(session, booking).balance == 12500

    def test_a_refunded_payment_stops_counting(self, session, booking):
        payment = payment_service.record_payment(session, booking, 12500, "card")
        assert payment_service.booking_balance(session, booking).balance == 0
        payment_service.refund_payment(session, payment, reason="Vehicle unavailable")
        assert payment_service.booking_balance(session, booking).balance == 12500

    def test_overpayment_shows_as_a_negative_balance(self, session, booking):
        payment_service.record_payment(session, booking, 13000, "cash")
        assert payment_service.booking_balance(session, booking).balance == -500


class TestRecordPayment:
    def test_records_a_payment(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        payment = payment_service.record_payment(
            session, booking, 5000, "gcash", recorded_by=staff.user_id, reference_no="GC123"
        )
        assert payment.status == "paid"
        assert payment.method == "gcash"
        assert payment.recorded_by == staff.user_id
        assert payment.reference_no == "GC123"
        assert payment.paid_at is not None

    def test_a_pending_payment_has_no_paid_at(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        payment = payment_service.record_payment(session, booking, 5000, "gcash", status="pending")
        assert payment.paid_at is None

    def test_rejects_an_unknown_method(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        with pytest.raises(ValidationError, match="payment method"):
            payment_service.record_payment(session, booking, 5000, "bitcoin")

    def test_rejects_a_zero_payment(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        with pytest.raises(ValidationError, match="greater than zero"):
            payment_service.record_payment(session, booking, 0, "cash")

    def test_rejects_a_negative_payment(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        with pytest.raises(ValidationError, match="greater than zero"):
            payment_service.record_payment(session, booking, -100, "cash")

    def test_refuses_payment_on_a_cancelled_booking(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.cancel_booking(session, booking)
        with pytest.raises(StateError, match="cancelled"):
            payment_service.record_payment(session, booking, 5000, "cash")

    def test_refund_needs_a_reason(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        payment = payment_service.record_payment(session, booking, 5000, "cash")
        with pytest.raises(ValidationError, match="reason"):
            payment_service.refund_payment(session, payment, reason="  ")

    def test_cannot_refund_a_pending_payment(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        payment = payment_service.record_payment(session, booking, 5000, "gcash", status="pending")
        with pytest.raises(StateError, match="cannot be refunded"):
            payment_service.refund_payment(session, payment, reason="Duplicate")

    def test_a_refund_keeps_the_original_row(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        payment = payment_service.record_payment(session, booking, 5000, "cash")
        payment_service.refund_payment(session, payment, reason="Cancelled by customer")
        assert payment.payment_id is not None
        assert payment.status == "refunded"
        assert "Cancelled by customer" in payment.note


class TestStaleCollections:
    """A row added by id alone is invisible to an already-loaded relationship.

    `apply_penalty` used to construct `Penalty(booking_id=...)` instead of
    `Penalty(booking=...)`. That works right up until something has already read
    `booking.penalties` -- which `booking_balance` does on every call. The
    collection is then cached empty, the new penalty does not appear in it, and
    the balance silently comes out too low. A customer is told they owe
    nothing.
    """

    def test_a_penalty_applied_after_a_balance_check_is_counted(
        self, session, customer, vehicle
    ):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        before = payment_service.booking_balance(session, booking)
        assert before.balance == 12500

        booking_service.apply_penalty(session, booking, "damage", 2500, "Scratch")
        after = payment_service.booking_balance(session, booking)
        assert after.balance == 15000

    def test_a_penalty_is_visible_on_the_booking_immediately(
        self, session, customer, vehicle
    ):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        assert len(booking.penalties) == 0
        booking_service.apply_penalty(session, booking, "cleaning", 500, "Interior")
        assert len(booking.penalties) == 1

    def test_a_payment_recorded_after_a_balance_check_is_counted(
        self, session, customer, vehicle
    ):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        assert payment_service.booking_balance(session, booking).balance == 12500
        payment_service.record_payment(session, booking, 12500, "cash")
        assert payment_service.booking_balance(session, booking).balance == 0

    def test_an_inspection_is_visible_on_the_booking_immediately(
        self, session, customer, vehicle, staff
    ):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        assert len(booking.inspections) == 0
        booking_service.check_in(session, booking, staff, 42000, "full")
        assert len(booking.inspections) == 1

    def test_fees_added_during_check_out_reach_the_balance(
        self, session, customer, vehicle, staff
    ):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        payment_service.record_payment(session, booking, 12500, "cash")
        booking_service.check_in(session, booking, staff, 42000, "full")
        assert payment_service.booking_balance(session, booking).is_settled

        booking_service.check_out(
            session, booking, staff, D8, 42100, "full",
            damage_notes="Scratch", damage_charge=1500, require_settlement=False,
        )
        balance = payment_service.booking_balance(session, booking)
        assert balance.total_due == 16000
        assert balance.balance == 3500


class TestTakings:
    def test_counts_only_settled_payments(self, session, customer, vehicle):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        payment_service.record_payment(session, booking, 5000, "cash")
        payment_service.record_payment(session, booking, 1000, "gcash", status="pending")
        assert payment_service.takings(session) == 5000
