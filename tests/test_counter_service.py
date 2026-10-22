"""One settings-aware quote for the counter wizard's display and payment.

The quote must be the single source of truth: the fee, deposit and defaults
the staff member sees are exactly the values `complete_counter_rental`
records, even after branch settings change.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest

from app.services import counter_service, settings_service

TODAY = date(2026, 9, 29)


class TestCounterQuote:
    def test_quote_uses_configured_fee_deposit_and_defaults(self, session, vehicle):
        settings_service.set_value(session, "scheduling_fee", "500.00")
        settings_service.set_value(session, "deposit_suv", "4000.00")
        settings_service.set_value(session, "default_payment_method", "gcash")
        settings_service.set_value(session, "default_channel", "online")
        vehicle.vehicle_class = "suv"
        q = counter_service.quote(
            session, vehicle, date(2026, 10, 5), date(2026, 10, 7)
        )
        assert q.days == 2
        assert q.deposit == Decimal("4000.00")
        assert q.scheduling_fee == Decimal("500.00")
        assert q.default_method == "gcash"
        assert q.default_channel == "online"

    def test_quote_defaults_match_the_code_constants(self, session, vehicle):
        q = counter_service.quote(
            session, vehicle, TODAY, TODAY.replace(day=30)
        )
        assert q.days == 1
        assert q.total == Decimal("2500.00")
        assert q.deposit == Decimal("2500.00")
        assert q.scheduling_fee == Decimal("100.00")
        assert q.default_method == "cash"
        assert q.default_channel == "walk_in"

    def test_quote_is_immutable(self, session, vehicle):
        q = counter_service.quote(session, vehicle, TODAY, TODAY)
        with pytest.raises(FrozenInstanceError):
            q.total = Decimal("1.00")
