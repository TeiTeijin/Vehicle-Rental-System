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
    pass


def to_decimal(value: object, *, field: str = "amount") -> Decimal:
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
    return to_decimal(value, field=field).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def pesos(value: object, *, field: str = "amount") -> str:
    return f"{PESO_SIGN}{money(value, field=field):,.2f}"


def pesos_ascii(value: object, *, field: str = "amount") -> str:
    return f"{PESO_SIGN_ASCII}{money(value, field=field):,.2f}"


def compact_pesos(value: object, *, field: str = "amount") -> str:
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
    total = ZERO
    for value in values:
        total += to_decimal(value, field=field)
    return total.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
