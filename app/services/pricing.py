"""Pricing for a customer rental: the one place a total is computed.

Screens 3 (date & time) and 4 (checkout) both read :func:`quote`; nothing in
the UI recomputes money.

Billing rule
------------
A rental is billed in whole days: every started 24-hour period counts in full,
with an optional grace window (``GRACE_HOURS``) so a return a little late does
not buy another day.

    total_hours = return - pickup, rounded up to the next whole hour
    billed_days = max(1, ceil((total_hours - GRACE_HOURS) / 24))
    subtotal    = daily_rate * billed_days

The duration line on screen 3 shows the *elapsed* time ("3 days, 9 hours"),
the price is the *billed* days -- one function returns both so they can never
disagree.

Fee schedule
------------
``INSURANCE_FEE``, ``SERVICE_FEE`` and ``VAT_RATE`` are the tunable constants;
VAT is charged on the rental plus both fees, as the BIR requires. Change a
number here and every screen follows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import ceil

from app.services.errors import ValidationError
from app.utils.money import PESO_SIGN, ZERO, money, pesos

#: Standard insurance, per rental. Flat: the cover is per booking, not per day.
INSURANCE_FEE = money("1250.00", field="insurance_fee")

#: Service fee, per rental (booking, handover, cleaning).
SERVICE_FEE = money("420.00", field="service_fee")

#: VAT charged on (rental + insurance + service fee).
VAT_RATE = Decimal("0.12")

#: Hours of a late return that do not buy another day. 0 = strict 24-hour days.
GRACE_HOURS = Decimal("0")

#: Everything below this is "same day" and still bills one day.
MIN_CHARGEABLE_DAYS = 1


@dataclass(frozen=True)
class Duration:
    """One rental's length, in both readings of the word."""

    hours: Decimal
    days: int
    extra_hours: int
    billed_days: int

    @property
    def label(self) -> str:
        """'3 days, 9 hours' / '2 days' -- the summary-bar line."""
        if self.extra_hours <= 0:
            return f"{self.billed_days} day{'' if self.billed_days == 1 else 's'}"
        return (
            f"{self.days} day{'' if self.days == 1 else 's'}, "
            f"{self.extra_hours} hour{'' if self.extra_hours == 1 else 's'}"
        )


@dataclass(frozen=True)
class Quote:
    """A priced rental: every line item on screen 4, plus the grand total."""

    daily_rate: Decimal
    duration: Duration
    subtotal: Decimal
    insurance: Decimal
    service_fee: Decimal
    vat: Decimal
    total: Decimal

    @property
    def days(self) -> int:
        return self.duration.billed_days

    @property
    def days_label(self) -> str:
        return f"{self.days} rental day{'' if self.days == 1 else 's'}"

    @property
    def subtotal_text(self) -> str:
        return display_money(self.subtotal)

    @property
    def insurance_text(self) -> str:
        return display_money(self.insurance)

    @property
    def service_fee_text(self) -> str:
        return display_money(self.service_fee)

    @property
    def vat_text(self) -> str:
        return display_money(self.vat)

    @property
    def total_text(self) -> str:
        return total_money(self.total)


def _as_moment(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValidationError(f"{field} must be a date and time.", field=field)
    return value


def measure(pickup_dt: datetime, return_dt: datetime) -> Duration:
    """Length of a rental. Raises when the return is not after the pickup."""
    pickup = _as_moment(pickup_dt, "pickup_dt")
    drop_off = _as_moment(return_dt, "return_dt")
    if drop_off <= pickup:
        raise ValidationError(
            "The return date and time must be after the pickup.",
            field="return_dt",
        )

    # Round up to the next whole hour: 12h 05m is 13 hours of hire.
    seconds = Decimal((drop_off - pickup).total_seconds())
    hours = (seconds / Decimal(3600)).quantize(Decimal("1"), rounding="ROUND_CEILING")
    billable = hours - GRACE_HOURS
    billed_days = max(MIN_CHARGEABLE_DAYS, ceil(billable / Decimal(24)))
    whole_days = int(hours // 24)
    return Duration(
        hours=hours,
        days=whole_days,
        extra_hours=int(hours - whole_days * 24),
        billed_days=billed_days,
    )


def rental_days(pickup_dt: datetime, return_dt: datetime) -> int:
    """Billed days for one rental. The number the daily rate multiplies."""
    return measure(pickup_dt, return_dt).billed_days


def quote(daily_rate, pickup_dt: datetime, return_dt: datetime) -> Quote:
    """Price one rental end to end. The only money function the UI calls."""
    duration = measure(pickup_dt, return_dt)
    rate = money(daily_rate, field="daily_rate")
    subtotal = money(rate * duration.billed_days, field="subtotal")
    net = money(subtotal + INSURANCE_FEE + SERVICE_FEE, field="net")
    vat = money(net * VAT_RATE, field="vat")
    return Quote(
        daily_rate=rate,
        duration=duration,
        subtotal=subtotal,
        insurance=INSURANCE_FEE,
        service_fee=SERVICE_FEE,
        vat=vat,
        total=money(net + vat, field="total"),
    )


# ---------- display ----------
def display_money(amount) -> str:
    """'₱4,800' -- thousand separators, cents only when there are any."""
    value = money(amount, field="amount")
    text = f"{value:,.2f}"
    if text.endswith(".00"):
        text = text[:-3]
    return f"{PESO_SIGN}{text}"


def total_money(amount) -> str:
    """'₱18,110.40' -- the grand total always shows its cents."""
    return pesos(money(amount, field="amount"))


def zero_text() -> str:
    return display_money(ZERO)
