"""Shared branch settings: one row per key in APP_SETTINGS.

Per-PC UI prefs (theme, sidebar, export folder) live in QSettings
(`app/staff/prefs.py`) and never touch the database. Everything here is
shared across counters, so a price change on one machine applies to all.

Reads always fall back to the current code constants, so a database that
has never run the migration behaves exactly as before. Writes validate
through the same `money()` / range checks the services use.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import App_Setting
from app.services.errors import ValidationError
from app.utils.money import money

#: key -> (default as stored string, kind). Kinds: money, int, decimal,
#: bool, text, choice. Choices carry their allowed values after a colon
#: in VALID_CHOICES.
DEFAULTS: dict[str, str] = {
    # -- pricing & policy --
    "scheduling_fee": "100.00",
    "deposit_motorcycle": "1500.00",
    "deposit_small": "2000.00",
    "deposit_medium": "2500.00",
    "deposit_suv": "3000.00",
    "deposit_van": "3500.00",
    "deposit_pickup": "4000.00",
    "deposit_truck": "5000.00",
    "deposit_default": "2500.00",
    "late_fee_per_day": "1000.00",
    "long_term_days": "7",
    "long_term_discount": "0.90",
    "moto_multiplier": "0.70",
    "sales_target": "6000000",
    # -- operations --
    "default_payment_method": "cash",
    "default_channel": "walk_in",
    "gcash_ref_required": "1",
    "allow_same_day": "1",
    "licence_expiring_days": "30",
    "maint_due_days": "14",
    "maint_due_km": "500",
    # -- receipts / reports --
    "receipt_branch_name": "RentWheels",
    "receipt_branch_address": "",
    "receipt_branch_phone": "",
    "receipt_fee_note": "1",
    "receipt_auto_open": "1",
}

MONEY_KEYS = {
    "scheduling_fee",
    "deposit_motorcycle", "deposit_small", "deposit_medium",
    "deposit_suv", "deposit_van", "deposit_pickup",
    "deposit_truck", "deposit_default",
    "late_fee_per_day", "sales_target",
}

INT_KEYS = {"long_term_days", "licence_expiring_days", "maint_due_days", "maint_due_km"}

DECIMAL_KEYS = {"long_term_discount", "moto_multiplier"}

BOOL_KEYS = {"gcash_ref_required", "allow_same_day", "receipt_fee_note", "receipt_auto_open"}

VALID_CHOICES: dict[str, tuple[str, ...]] = {
    "default_payment_method": ("cash", "card", "gcash"),
    "default_channel": ("walk_in", "online"),
}

DEPOSIT_KEYS = (
    "deposit_motorcycle", "deposit_small", "deposit_medium",
    "deposit_suv", "deposit_van", "deposit_pickup",
    "deposit_truck",
)


def _validate(key: str, raw: str) -> str:
    if key not in DEFAULTS:
        raise ValidationError(f"Unknown setting: {key}.", field=key)
    text = (raw if isinstance(raw, str) else str(raw)).strip()
    if key in MONEY_KEYS:
        value = money(text, field=key)
        if key != "sales_target" and value < Decimal("0.00"):
            raise ValidationError(f"{key} cannot be negative.", field=key)
        if key == "sales_target" and value <= Decimal("0.00"):
            raise ValidationError("Sales target must be greater than zero.", field="sales_target")
        return f"{value:.2f}"
    if key in INT_KEYS:
        try:
            value = int(text)
        except ValueError:
            raise ValidationError(f"{key} must be a whole number.", field=key) from None
        floors = {"long_term_days": 2, "licence_expiring_days": 0,
                  "maint_due_days": 0, "maint_due_km": 0}
        if value < floors[key]:
            raise ValidationError(
                f"{key} must be at least {floors[key]}.", field=key)
        if value > 100000:
            raise ValidationError(f"{key} is unreasonably large.", field=key)
        return str(value)
    if key in DECIMAL_KEYS:
        try:
            value = Decimal(text)
        except Exception:
            raise ValidationError(f"{key} must be a number.", field=key) from None
        if key == "long_term_discount" and not (Decimal("0.10") <= value <= Decimal("1.00")):
            raise ValidationError(
                "Long-term multiplier must be between 0.10 and 1.00.", field=key)
        if key == "moto_multiplier" and not (Decimal("0.10") <= value <= Decimal("1.00")):
            raise ValidationError(
                "Motorcycle multiplier must be between 0.10 and 1.00.", field=key)
        return str(value)
    if key in BOOL_KEYS:
        lowered = text.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return "1"
        if lowered in ("0", "false", "no", "off"):
            return "0"
        raise ValidationError(f"{key} must be on or off.", field=key)
    if key in VALID_CHOICES:
        if text not in VALID_CHOICES[key]:
            raise ValidationError(
                f"{key} must be one of {', '.join(VALID_CHOICES[key])}.", field=key)
        return text
    # free text (receipt header lines): cap length, allow empty.
    if len(text) > 255:
        raise ValidationError(f"{key} is too long (max 255).", field=key)
    return text


def get_raw(session: Session, key: str) -> str:
    """Stored string, or the default when no row exists yet."""
    if key not in DEFAULTS:
        raise ValidationError(f"Unknown setting: {key}.", field=key)
    try:
        row = session.execute(
            select(App_Setting).where(App_Setting.key == key)
        ).scalar_one_or_none()
    except Exception:
        # Table predates the migration (old demo.db): behave as defaults.
        return DEFAULTS[key]
    if row is None:
        return DEFAULTS[key]
    return row.value


def get_all(session: Session) -> dict[str, str]:
    out = dict(DEFAULTS)
    try:
        rows = session.execute(select(App_Setting)).scalars().all()
    except Exception:
        return out
    for row in rows:
        if row.key in out:
            out[row.key] = row.value
    return out


def set_value(session: Session, key: str, raw: object,
              *, updated_by: int | None = None) -> App_Setting:
    cleaned = _validate(key, raw if isinstance(raw, str) else str(raw))
    row = session.execute(
        select(App_Setting).where(App_Setting.key == key)
    ).scalar_one_or_none()
    if row is None:
        row = App_Setting(key=key, value=cleaned,
                          updated_by=updated_by, updated_at=datetime.now())
        session.add(row)
    else:
        row.value = cleaned
        row.updated_by = updated_by
        row.updated_at = datetime.now()
    session.flush()
    return row


def set_many(session: Session, values: dict[str, object],
             *, updated_by: int | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, raw in values.items():
        row = set_value(session, key, raw, updated_by=updated_by)
        out[key] = row.value
    return out


# -- typed readers (DB with fallback to code constants) ----------------------


def get_money(session: Session, key: str) -> Decimal:
    return money(get_raw(session, key), field=key)


def get_int(session: Session, key: str) -> int:
    return int(get_raw(session, key))


def get_decimal(session: Session, key: str) -> Decimal:
    return Decimal(get_raw(session, key))


def get_bool(session: Session, key: str) -> bool:
    return get_raw(session, key) == "1"


def scheduling_fee(session: Session | None = None) -> Decimal:
    if session is None:
        from app.services import booking_service
        return booking_service.SCHEDULING_FEE
    try:
        return get_money(session, "scheduling_fee")
    except Exception:
        from app.services import booking_service
        return booking_service.SCHEDULING_FEE


def late_fee_per_day(session: Session | None = None) -> Decimal:
    if session is None:
        from app.services import booking_service
        return booking_service.LATE_PENALTY_PER_DAY
    try:
        return get_money(session, "late_fee_per_day")
    except Exception:
        from app.services import booking_service
        return booking_service.LATE_PENALTY_PER_DAY


def deposit_for_class(session: Session | None, vehicle_class: str | None) -> Decimal:
    from app.services import booking_service
    if session is None:
        return booking_service.DEPOSIT_BY_CLASS.get(
            (vehicle_class or "").strip().lower(),
            booking_service.DEFAULT_DEPOSIT)
    try:
        key = f"deposit_{(vehicle_class or '').strip().lower()}"
        if key in DEFAULTS:
            return get_money(session, key)
        return get_money(session, "deposit_default")
    except Exception:
        return booking_service.DEPOSIT_BY_CLASS.get(
            (vehicle_class or "").strip().lower(),
            booking_service.DEFAULT_DEPOSIT)
