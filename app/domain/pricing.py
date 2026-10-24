"""The branch's pricing rules as one immutable value.

The domain calculators are database-free on purpose, so callers that have a
session build a policy from Settings and pass it down. Omitting the policy
keeps the original code defaults, which remain the business rules a fresh
database runs with.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PricingPolicy:
    long_term_days: int = 7
    long_term_discount: Decimal = Decimal("0.90")
    moto_multiplier: Decimal = Decimal("0.70")


def policy_from_settings(session) -> PricingPolicy:
    """Read the three shared pricing values, defaulting per value."""
    from app.services import settings_service

    defaults = PricingPolicy()

    def read(reader, key, fallback):
        try:
            return reader(session, key)
        except Exception:  # noqa: BLE001 - one bad value falls back alone
            return fallback

    return PricingPolicy(
        long_term_days=read(
            settings_service.get_int,
            "long_term_days",
            defaults.long_term_days,
        ),
        long_term_discount=read(
            settings_service.get_decimal,
            "long_term_discount",
            defaults.long_term_discount,
        ),
        moto_multiplier=read(
            settings_service.get_decimal,
            "moto_multiplier",
            defaults.moto_multiplier,
        ),
    )
