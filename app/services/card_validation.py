"""Card checks and the mock payment gateway for the customer checkout.

Nothing in this module stores a card: :class:`CardDetails` keeps the brand and
the last four digits, and that is all that ever reaches the database. The full
number and the CVV live in the widget until the customer presses Pay, then they
are dropped.

The gateway is a mock with one deliberate failure path -- a card whose number
ends ``0002`` is declined, the same test convention real processors use -- so
the failure branch is reachable without a network.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.services.errors import ValidationError

#: Longest number we accept (19 digits covers every issuer scheme).
MAX_DIGITS = 19

#: A card that ends with this is always declined: the mock failure path.
DECLINE_SUFFIX = "0002"

#: How far ahead an expiry may be before we call it a typo.
MAX_EXPIRY_YEARS = 20

PESO = "\u20b1"


@dataclass(frozen=True)
class CardDetails:
    """What we are allowed to keep from a card form."""

    brand: str
    last4: str
    holder: str

    @property
    def masked(self) -> str:
        return f"\u2022\u2022\u2022\u2022 \u2022\u2022\u2022\u2022 \u2022\u2022\u2022\u2022 {self.last4}"


@dataclass(frozen=True)
class ChargeResult:
    """The mock gateway's answer."""

    ok: bool
    message: str
    reference: str = ""


# ---------- text helpers ----------
def digits(text: str) -> str:
    return "".join(ch for ch in (text or "") if ch.isdigit())


def format_number(text: str) -> str:
    """Digits re-grouped in fours: '4111111111111111' -> '4111 1111 1111 1111'."""
    grouped = digits(text)[:MAX_DIGITS]
    return " ".join(grouped[i : i + 4] for i in range(0, len(grouped), 4))


def format_expiry(text: str) -> str:
    """Keeps the field reading 'MM / YY' while the customer types."""
    value = digits(text)[:4]
    if len(value) <= 2:
        return value
    return f"{value[:2]} / {value[2:]}"


def format_cvv(text: str) -> str:
    return digits(text)[:4]


# ---------- card checks ----------
def luhn_ok(number: str) -> bool:
    """The Luhn checksum every real card number satisfies."""
    value = digits(number)
    if len(value) < 12:
        return False
    total = 0
    for index, char in enumerate(reversed(value)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def brand_of(number: str) -> str | None:
    """'Visa' / 'Mastercard', or None when the prefix is not one we take."""
    value = digits(number)
    if value.startswith("4"):
        return "Visa"
    if value[:2] in {"51", "52", "53", "54", "55"}:
        return "Mastercard"
    if value[:4].isdigit() and 2221 <= int(value[:4]) <= 2720:
        return "Mastercard"
    return None


def parse_expiry(text: str) -> tuple[int, int]:
    """'08 / 29' -> (8, 2029). Raises when the shape is wrong."""
    value = digits(text)
    if len(value) != 4:
        raise ValidationError(
            "Enter the expiry as MM / YY.", field="expiry"
        )
    month, year = int(value[:2]), int(value[2:])
    if not 1 <= month <= 12:
        raise ValidationError(
            "That month does not exist: use 01 to 12.", field="expiry"
        )
    return month, 2000 + year


def expiry_ok(month: int, year: int, today: date | None = None) -> bool:
    """True while the card is valid: its month has not ended yet."""
    now = today or date.today()
    if year > now.year + MAX_EXPIRY_YEARS:
        return False
    return (year, month) >= (now.year, now.month)


def cvv_ok(cvv: str) -> bool:
    value = digits(cvv)
    return 3 <= len(value) <= 4


def validate_card(
    holder: str,
    number: str,
    expiry: str,
    cvv: str,
    *,
    today: date | None = None,
) -> CardDetails:
    """Check a whole card form and return only what may be kept."""
    name = (holder or "").strip()
    if not name:
        raise ValidationError("Enter the name on the card.", field="cardholder")

    value = digits(number)
    if len(value) < 12:
        raise ValidationError(
            "That card number looks too short.", field="card_number"
        )
    brand = brand_of(value)
    if brand is None:
        raise ValidationError(
            "Only Visa and Mastercard are accepted.", field="card_number"
        )
    if not luhn_ok(value):
        raise ValidationError(
            "That card number is not valid. Check the digits.",
            field="card_number",
        )

    month, year = parse_expiry(expiry)
    if not expiry_ok(month, year, today):
        raise ValidationError(
            "That card has expired. Use a valid card.", field="expiry"
        )

    if not cvv_ok(cvv):
        raise ValidationError(
            "The CVV is the 3 or 4 digit code on the back.", field="cvv"
        )

    return CardDetails(brand=brand, last4=value[-4:], holder=name)


# ---------- mock gateway ----------
def _reference(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4).upper()}"


def charge_card(details: CardDetails, amount: Decimal) -> ChargeResult:
    """Simulated authorisation. `0002` cards decline so the branch exists."""
    if details.last4 == DECLINE_SUFFIX[-4:]:
        return ChargeResult(
            ok=False,
            message=(
                "Your bank declined the payment. Please try another card "
                "(test cards ending 0002 are always declined)."
            ),
        )
    return ChargeResult(
        ok=True,
        message=f"Charged {PESO}{amount:,.2f} to {details.brand} {details.masked}.",
        reference=_reference("CARD"),
    )


def charge_gcash(amount: Decimal) -> ChargeResult:
    """Simulated GCash confirmation: the QR was scanned and paid."""
    return ChargeResult(
        ok=True,
        message=f"GCash payment of {PESO}{amount:,.2f} confirmed.",
        reference=_reference("GCASH"),
    )


def pay_at_pickup(amount: Decimal) -> ChargeResult:
    """Cash bookings are not charged now; the branch collects at handover."""
    return ChargeResult(
        ok=True,
        message=f"Nothing to pay yet: {PESO}{amount:,.2f} is due at pickup.",
        reference="",
    )


def collect(
    method: str, amount: Decimal, details: CardDetails | None = None
) -> ChargeResult:
    """Run the mock payment for one method. Never raises; callers read `ok`."""
    if method == "card":
        if details is None:
            return ChargeResult(ok=False, message="Card details are missing.")
        return charge_card(details, amount)
    if method == "gcash":
        return charge_gcash(amount)
    return pay_at_pickup(amount)


def today() -> date:
    return datetime.now().date()
