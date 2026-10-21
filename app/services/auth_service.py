"""Customer sign-in and self-service signup.

The counter has its own path (app/services/customer_service.py, licence-first);
this module is what the customer app uses.
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Users
from app.services import customer_service, location_service
from app.services.errors import ConflictError, ValidationError
from app.utils.security import hash_password, verify_password


class AuthError(Exception):
    pass


class InvalidCredentials(AuthError):
    pass


DUMMY_HASH = "$2b$12$izSvmM0zX.Kx3tsRF.uwIep3aJoUFsgXQKCB/tgDEYzXuknE8bO7S"
INVALID_CREDENTIALS_MESSAGE = "Incorrect email or password."

#: Signup password floor, mirroring the four live rules on the sign-up card.
MIN_PASSWORD_LENGTH = 8
PASSWORD_SYMBOLS = "!@#$%^&*()_+-=[]{};:'\"|,.<>/?`~"

#: Licence expiry is still completed at the counter when the physical card
#: is checked; the online form only collects the licence number.
PLACEHOLDER_LICENCE_YEARS = 5

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_street_address(text: str) -> bool:
    """True for a street line that has both a number and a name.

    '123 Mabini St.' passes; 'Mabini' and '12345' do not. The upper bound
    keeps the composed USERS.address inside its VARCHAR(255).
    """
    value = " ".join((text or "").split())
    return (
        5 <= len(value) <= location_service.MAX_STREET_LENGTH
        and any(ch.isdigit() for ch in value)
        and any(ch.isalpha() for ch in value)
    )


def sign_in(session: Session, email: str, password: str) -> Users:
    normalized = (email or "").strip().lower()

    user = None
    if normalized:
        user = session.query(Users).filter_by(email=normalized).one_or_none()

    stored_hash = user.password_hash if user is not None else DUMMY_HASH
    password_ok = verify_password(password or "", stored_hash)

    if user is None or not password_ok:
        raise InvalidCredentials(INVALID_CREDENTIALS_MESSAGE)

    return user


def password_problems(password: str) -> list[str]:
    """Every rule the password still fails, in the sign-up card's order."""
    value = password or ""
    problems: list[str] = []
    if len(value) < MIN_PASSWORD_LENGTH:
        problems.append(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if not any(ch.isupper() for ch in value):
        problems.append("Add one uppercase letter.")
    if not any(ch.isdigit() for ch in value):
        problems.append("Add one number.")
    if not any(ch in PASSWORD_SYMBOLS for ch in value):
        problems.append("Add one symbol.")
    return problems


def register_customer(
    session: Session,
    *,
    full_name: str,
    email: str,
    phone: str | None,
    password: str,
    street_address: str,
    province_key: str,
    city_municipality_code: str,
    license_number: str,
) -> Users:
    """Create a customer account from the two-step sign-up form.

    The address pair must be an exact province + city/municipality from the
    PSGC tables and the licence must match the app's licence shape; both are
    re-checked here, not only in the form. The physical card and its expiry
    are still verified at the counter on the first rental (the expiry column
    keeps a stand-in until then). Raises ValidationError/ConflictError with a
    message fit to show the customer.
    """
    name = " ".join((full_name or "").split())
    mail = (email or "").strip().lower()

    if not name:
        raise ValidationError("Enter your name.", field="full_name")
    if not EMAIL_RE.match(mail):
        raise ValidationError("Enter a valid email address.", field="email")

    problems = password_problems(password)
    if problems:
        raise ValidationError(problems[0], field="password")

    if session.query(Users).filter_by(email=mail).one_or_none() is not None:
        raise ConflictError(
            "That email already has an account. Sign in instead."
        )

    street = " ".join((street_address or "").split())
    if not is_valid_street_address(street):
        raise ValidationError(
            "Enter your house/unit number and street name.",
            field="street_address",
        )

    location = location_service.resolve(
        session, province_key, city_municipality_code
    )

    licence = customer_service.canonical_licence(license_number)
    if licence is None:
        raise ValidationError(
            f"Enter a valid licence number ({customer_service.LICENCE_EXAMPLE}).",
            field="license_number",
        )
    if customer_service.find_by_licence(
        session, licence, any_role=True
    ) is not None:
        raise ConflictError(
            "That licence already has an account. Sign in instead."
        )

    customer = Users(
        full_name=name,
        email=mail,
        phone=_clean_phone(phone),
        password_hash=hash_password(password),
        address=location.address_line(street),
        region_code=location.region_code,
        province_code=location.province_code,
        city_municipality_code=location.city_code,
        street_address=street,
        license_number=licence,
        license_expiry=_placeholder_expiry(),
        role="customer",
        source="online",
        is_active=True,
        # USERS.created_at is NOT NULL with no database default, so the row
        # has to carry it (the counter's own customer_service does the same).
        created_at=datetime.now(),
    )
    session.add(customer)
    session.flush()
    return customer


def _clean_phone(phone: str | None) -> str | None:
    value = (phone or "").strip()
    return value or None


def _placeholder_expiry():
    from datetime import date

    today = date.today()
    return date(today.year + PLACEHOLDER_LICENCE_YEARS, 12, 31)
