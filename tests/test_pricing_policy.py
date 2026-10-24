"""Pricing policy and operational settings wiring.

The calculator's defaults are the old business rules; these tests prove the
values a branch can change in Settings actually reach the quote, the booking,
and the daily operational surfaces.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.domain.pricing import PricingPolicy, policy_from_settings
from app.domain.rental_calculator import RentalCalculator
from app.domain.vehicle_types import Car, Motorcycle
from app.services import booking_service, counter_service, settings_service
from app.services.errors import ValidationError
from tests.conftest import TODAY


def _window(days: int = 3, offset: int = 2):
    start = TODAY + timedelta(days=offset)
    return start, start + timedelta(days=days)


class TestPricingPolicy:
    def test_long_term_setting_changes_quote(self, session, vehicle):
        settings_service.set_value(session, "long_term_days", "3")
        settings_service.set_value(session, "long_term_discount", "0.50")
        start, end = _window(days=3)
        days, total = booking_service.quote_for(session, vehicle, start, end)
        assert days == 3
        assert total == Decimal("3750.00")

    def test_moto_multiplier_setting_changes_quote(self, session, moto):
        settings_service.set_value(session, "moto_multiplier", "0.80")
        start, end = _window(days=2)
        days, total = booking_service.quote_for(session, moto, start, end)
        assert days == 2
        assert total == Decimal("4000.00")

    def test_create_booking_uses_the_same_policy(self, session, customer, vehicle):
        settings_service.set_value(session, "long_term_days", "3")
        settings_service.set_value(session, "long_term_discount", "0.50")
        start, end = _window(days=3)
        booking = booking_service.create_booking(
            session, customer, vehicle, start, end
        )
        assert booking.total_cost == Decimal("3750.00")

    def test_extension_uses_the_same_policy(self, session, customer, vehicle):
        start, end = _window(days=2)
        booking = booking_service.create_booking(
            session, customer, vehicle, start, end
        )
        settings_service.set_value(session, "long_term_days", "3")
        settings_service.set_value(session, "long_term_discount", "0.50")
        _old, new_total = booking_service.extension_quote(
            session, booking, start + timedelta(days=4)
        )
        assert new_total == Decimal("5000.00")  # 4 days x 2,500 x 0.50

    def test_default_policy_preserves_the_domain_rules(self):
        car = Car("Toyota", "Vios", 2022, 2500)
        assert RentalCalculator.total_cost(
            car, TODAY, TODAY + timedelta(days=7)
        ) == Decimal("15750.00")
        bike = Motorcycle("Honda", "Click", 2023, 2500)
        assert RentalCalculator.total_cost(
            bike, TODAY, TODAY + timedelta(days=5)
        ) == Decimal("8750.00")

    def test_explicit_policy_overrides_the_defaults(self):
        car = Car("Toyota", "Vios", 2022, 2500)
        policy = PricingPolicy(
            long_term_days=2,
            long_term_discount=Decimal("0.50"),
        )
        total = RentalCalculator.total_cost(
            car, TODAY, TODAY + timedelta(days=2), policy=policy
        )
        assert total == Decimal("2500.00")

    def test_policy_from_settings_reads_all_three_values(self, session):
        settings_service.set_value(session, "long_term_days", "4")
        settings_service.set_value(session, "long_term_discount", "0.80")
        settings_service.set_value(session, "moto_multiplier", "0.60")
        policy = policy_from_settings(session)
        assert policy.long_term_days == 4
        assert policy.long_term_discount == Decimal("0.80")
        assert policy.moto_multiplier == Decimal("0.60")


class TestOperationalSettings:
    def test_same_day_is_rejected_when_the_branch_disables_it(
        self, session, customer, vehicle
    ):
        settings_service.set_value(session, "allow_same_day", "0")
        with pytest.raises(ValidationError, match="Same-day"):
            booking_service.quote_for(session, vehicle, TODAY, TODAY)
        with pytest.raises(ValidationError, match="Same-day"):
            booking_service.create_booking(
                session, customer, vehicle, TODAY, TODAY
            )

    def test_same_day_still_prices_when_enabled(self, session, vehicle):
        settings_service.set_value(session, "allow_same_day", "1")
        days, total = booking_service.quote_for(session, vehicle, TODAY, TODAY)
        assert days == 1
        assert total == Decimal("2500.00")

    def test_quote_reads_the_default_payment_method_and_channel(
        self, session, vehicle
    ):
        settings_service.set_value(session, "default_payment_method", "gcash")
        settings_service.set_value(session, "default_channel", "online")
        quote = counter_service.quote(session, vehicle, *_window(days=2))
        assert quote.default_method == "gcash"
        assert quote.default_channel == "online"
