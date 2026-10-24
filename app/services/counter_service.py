"""One settings-aware counter quote.

The counter wizard and `booking_service.complete_counter_rental` must agree
on every peso the customer sees. This service is the single place that joins
the live price, the configured scheduling fee, the per-class deposit and the
branch defaults into one immutable quote. It never imports the Qt wizard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Vehicle
from app.services import booking_service, settings_service


@dataclass(frozen=True)
class CounterQuote:
    """Everything the counter needs to display and take payment for a rental."""

    days: int
    total: Decimal
    deposit: Decimal
    scheduling_fee: Decimal
    default_method: str
    default_channel: str


def quote(
    session: Session,
    vehicle: Vehicle,
    start_date: date,
    end_date: date,
) -> CounterQuote:
    """Price one counter rental through the same settings the Pay button uses."""
    days, total = booking_service.quote_for(session, vehicle, start_date, end_date)
    fee = settings_service.scheduling_fee(session)
    settings_service.validate_scheduling_fee(fee, total)
    return CounterQuote(
        days=days,
        total=total,
        deposit=settings_service.deposit_for_class(
            session, getattr(vehicle, "vehicle_class", None)
        ),
        scheduling_fee=fee,
        default_method=settings_service.get_raw(session, "default_payment_method"),
        default_channel=settings_service.get_raw(session, "default_channel"),
    )
