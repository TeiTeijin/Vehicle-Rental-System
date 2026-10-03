"""Money.

Every peso amount in this project moves as a Decimal. The database columns are
DECIMAL(10,2), the domain classes return Decimal, and float never appears in a
money path -- Decimal(str(x)) is used at the edges instead, because
Decimal(0.1 + 0.2) is not 0.3 and a rental desk that is off by a cent is a
rental desk nobody trusts.

Two rules the rest of the codebase relies on:

1. Convert with `to_decimal()`, never `Decimal(float)`. Decimal(0.1) is
   0.1000000000000000055511151231257827021181583404541015625, so a rate that
   arrived as a float has to go through str() first.
2. Quantise with `money()` at the boundary -- when writing to a DECIMAL column
   or rendering for a human -- and not in the middle of a calculation, or
   rounding happens twice and the totals stop adding up.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# DECIMAL(10,2): two decimal places, ten digits total.
CENT = Decimal("0.01")
MONEY_QUANTUM = CENT

ZERO = Decimal("0.00")

PESO_SIGN = "₱"

#: Spelled-out alternative to PESO_SIGN, for output that cannot carry the glyph.
PESO_SIGN_ASCII = "PHP "


class InvalidAmount(ValueError):
    """Raised when a value cannot be read as a peso amount."""


def to_decimal(value: object, *, field: str = "amount") -> Decimal:
    """Coerce a money-ish value to Decimal, safely.

    Accepts Decimal, int, str (including "1,250.00" and "₱1,250.00"), and
    float. Rejects None, empty strings, and anything unparseable, so a blank
    form field fails loudly here instead of silently becoming 0.00 somewhere
    downstream.
    """
    if value is None:
        raise InvalidAmount(f"{field} is required")

    if isinstance(value, Decimal):
        return value

    if isinstance(value, bool):
        # bool is an int subclass; True would quietly become 1.00.
        raise InvalidAmount(f"{field} must be a number, got a boolean")

    if isinstance(value, int):
        return Decimal(value)

    if isinstance(value, float):
        # str() first: Decimal(0.1) is the binary expansion of 0.1.
        return Decimal(str(value))

    if isinstance(value, str):
        cleaned = value.strip().replace(PESO_SIGN, "").replace(",", "").replace("₱", "")
        if not cleaned:
            raise InvalidAmount(f"{field} is required")
        try:
            return Decimal(cleaned)
        except InvalidOperation as exc:
            raise InvalidAmount(f"{field} is not a valid number: {value!r}") from exc

    raise InvalidAmount(f"{field} is not a valid number: {value!r}")


def money(value: object, *, field: str = "amount") -> Decimal:
    """Coerce and quantise to 2 places, half-up, for storage or display.

    ROUND_HALF_UP rather than Decimal's default ROUND_HALF_EVEN because
    "half-up" is what a person doing the arithmetic on a receipt expects.
    """
    return to_decimal(value, field=field).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def pesos(value: object, *, field: str = "amount") -> str:
    """Render for a human: "₱1,250.00". Quantised first, so the number a
    person reads is exactly the number that was stored."""
    return f"{PESO_SIGN}{money(value, field=field):,.2f}"


def pesos_ascii(value: object, *, field: str = "amount") -> str:
    """Render as "PHP 1,250.00", for anything drawn with a built-in PDF font.

    Same figure as `pesos`, spelled out. See PESO_SIGN_ASCII for why.
    """
    return f"{PESO_SIGN_ASCII}{money(value, field=field):,.2f}"


def compact_pesos(value: object, *, field: str = "amount") -> str:
    """Render large amounts short, for chart axes and tight tile labels.

    "₱18.5K", "₱1.2M". Below 10,000 the exact figure is shown, because at that
    size a staff member wants the real number.

    Rounds half-up explicitly: formatting a Decimal by default uses
    ROUND_HALF_EVEN, which would render ₱1,250,000 as "₱1.2M" and contradict
    the half-up that money() applies everywhere else.
    """
    amount = money(value, field=field)
    magnitude = abs(amount)
    if magnitude >= 1_000_000:
        scaled = (amount / Decimal("1000000")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        return f"{PESO_SIGN}{scaled}M"
    if magnitude >= 10_000:
        scaled = (amount / Decimal("1000")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        return f"{PESO_SIGN}{scaled}K"
    return pesos(amount, field=field)


def is_positive(value: object, *, field: str = "amount") -> bool:
    return to_decimal(value, field=field) > ZERO


def sum_money(values, *, field: str = "amount") -> Decimal:
    """Sum anything money-ish into a single quantised Decimal.

    `sum()` cannot be used on a generator of Decimals that start at int 0
    without care, and it never quantises; this does both.
    """
    total = ZERO
    for value in values:
        total += to_decimal(value, field=field)
    return total.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
