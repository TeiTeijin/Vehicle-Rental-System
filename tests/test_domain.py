"""Tests for the DB-free domain layer: availability and pricing.

Nothing here touches a database or a network. These are the classes to defend
live, because every output is deterministic and provable on the spot.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.domain.availability import AvailabilityChecker
from app.domain.rental_calculator import RentalCalculator
from app.domain.vehicle_types import Car, Motorcycle, Vehicle

D1 = date(2026, 9, 1)
D3 = date(2026, 9, 3)
D4 = date(2026, 9, 4)
D5 = date(2026, 9, 5)
D6 = date(2026, 9, 6)
D8 = date(2026, 9, 8)
D9 = date(2026, 9, 9)
D10 = date(2026, 9, 10)


def booking(start, end):
    return SimpleNamespace(start_date=start, end_date=end)


class TestOverlapSemantics:
    def test_returns_touch_and_turn_around_same_day(self):
        # The headline business rule: a car back on the 5th is free on the 5th.
        existing = [booking(D1, D5)]
        assert AvailabilityChecker.is_available(None, D5, D6, existing) is True

    def test_leading_edge_touch_is_not_a_conflict(self):
        existing = [booking(D5, D9)]
        assert AvailabilityChecker.is_available(None, D1, D5, existing) is True

    def test_genuine_overlap_is_a_conflict(self):
        existing = [booking(D1, D5)]
        assert AvailabilityChecker.is_available(None, D4, D9, existing) is False

    def test_full_containment_is_a_conflict(self):
        existing = [booking(D1, D10)]
        assert AvailabilityChecker.is_available(None, D3, D5, existing) is False

    def test_being_fully_contained_is_a_conflict(self):
        existing = [booking(D3, D4)]
        assert AvailabilityChecker.is_available(None, D1, D10, existing) is False

    def test_identical_range_is_a_conflict(self):
        assert AvailabilityChecker.is_available(None, D1, D5, [booking(D1, D5)]) is False

    def test_adjacent_but_disjoint_is_fine(self):
        assert AvailabilityChecker.is_available(None, D6, D9, [booking(D1, D5)]) is True

    def test_no_bookings_means_available(self):
        assert AvailabilityChecker.is_available(None, D1, D5, []) is True

    @pytest.mark.parametrize("start,end", [(D5, D1), (D5, D5)])
    def test_zero_or_negative_range_rejected(self, start, end):
        with pytest.raises(ValueError, match="end_date must be after start_date"):
            AvailabilityChecker.is_available(None, start, end, [])

    def test_previous_inclusive_rule_would_have_blocked_turnaround(self):
        # Documents why the predicate changed. Old rule:
        #     start <= b.end and b.start <= end
        # For a re-rent starting the day the previous one ends, both sides are
        # true, so it reported a conflict and same-day turnaround was
        # impossible.
        old_predicate_says_conflict = (D5 <= D5) and (D1 <= D6)
        assert old_predicate_says_conflict is True

        assert AvailabilityChecker.overlaps(D5, D6, D1, D5) is False


class TestFindConflict:
    def test_returns_a_blocking_booking(self):
        blocker = booking(D1, D5)
        found = AvailabilityChecker.find_conflict(D4, D9, [blocker])
        assert found is blocker

    def test_returns_the_first_blocker_in_list_order(self):
        # Both overlap the requested range; find_conflict does not rank them,
        # it reports whichever the caller listed first.
        first = booking(D8, D9)
        second = booking(D1, D5)
        assert AvailabilityChecker.find_conflict(D4, D9, [first, second]) is first

    def test_returns_none_when_clear(self):
        assert AvailabilityChecker.find_conflict(D5, D6, [booking(D1, D5)]) is None

    def test_returns_none_with_no_bookings(self):
        assert AvailabilityChecker.find_conflict(D1, D5, []) is None


class TestRentalDays:
    def test_counts_days_in_the_half_open_range(self):
        assert RentalCalculator.rental_days(D1, D5) == 4

    def test_one_day_rental(self):
        assert RentalCalculator.rental_days(D1, D6) == 5

    @pytest.mark.parametrize("start,end", [(D5, D1), (D5, D5)])
    def test_rejects_empty_ranges(self, start, end):
        with pytest.raises(ValueError, match="end_date must be after start_date"):
            RentalCalculator.rental_days(start, end)

    def test_range_rule_matches_availability_rule(self):
        # These two used to disagree: availability allowed start == end while
        # pricing rejected it, so the same input blew up in the second call
        # with a message about a range the first call had already accepted.
        for start, end in [(D5, D5), (D5, D1), (D1, D5)]:
            availability_rejects = False
            pricing_rejects = False
            try:
                AvailabilityChecker.is_available(None, start, end, [])
            except ValueError:
                availability_rejects = True
            try:
                RentalCalculator.rental_days(start, end)
            except ValueError:
                pricing_rejects = True
            assert availability_rejects == pricing_rejects


class TestPolymorphicPricing:
    def test_same_rate_different_subclass_gives_different_total(self):
        car = Car("Toyota", "Vios", 2022, 2500)
        bike = Motorcycle("Honda", "Click", 2023, 2500)
        assert RentalCalculator.total_cost(car, D1, D6) == Decimal("12500.00")
        assert RentalCalculator.total_cost(bike, D1, D6) == Decimal("8750.00")

    def test_long_term_discount_applies_to_cars_at_seven_days(self):
        car = Car("Toyota", "Fortuner", 2023, 4500)
        assert RentalCalculator.total_cost(car, D1, date(2026, 9, 8)) == Decimal("28350.00")

    def test_no_discount_below_seven_days(self):
        car = Car("Toyota", "Fortuner", 2023, 4500)
        assert RentalCalculator.total_cost(car, D1, date(2026, 9, 7)) == Decimal("27000.00")

    def test_motorcycles_get_no_long_term_discount(self):
        bike = Motorcycle("Honda", "Click", 2023, 800)
        assert RentalCalculator.total_cost(bike, D1, date(2026, 9, 8)) == Decimal("3920.00")

    def test_total_is_always_quantised_to_two_places(self):
        # A rate that does not divide cleanly, times the long-term discount.
        car = Car("Toyota", "Vios", 2022, "999.99")
        total = RentalCalculator.total_cost(car, D1, date(2026, 9, 8))
        assert total == Decimal("6299.94")
        assert str(total) == "6299.94"

    def test_float_rate_does_not_leak_binary_noise(self):
        car = Car("Toyota", "Vios", 2022, 999.99)
        assert str(car.daily_rate) == "999.99"

    def test_base_class_cannot_be_instantiated(self):
        with pytest.raises(TypeError):
            Vehicle("Toyota", "Vios", 2022, 2500)

    def test_calculate_rate_is_abstract(self):
        assert "calculate_rate" in Vehicle.__abstractmethods__
