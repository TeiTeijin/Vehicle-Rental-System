"""Counter-side customer identity: find, never duplicate.

The clerk verifies the physical licence card, so `license_number` is the
identity key: government-issued, unique per person, already UNIQUE in the
database. Creating is only reachable through a failed lookup, so repeat
renters resolve to their existing row instead of piling up duplicates.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Users
from app.services.errors import ConflictError, ValidationError
from app.utils.security import hash_password

#: Fixed password set on counter-created accounts. The counter never logs
#: customers in (it verifies the physical licence); the customer uses this
#: once in the customer app and changes it there.
STORE_TEMP_PASSWORD = "rentwheels-temp"

#: Minimum legal renting age. Tunable in one place; eligibility enforces it.
MIN_RENTING_AGE = 21

#: Licence-expiry warning window (matches the Customers tab bucket).
EXPIRING_SOON_DAYS = 30

#: Shape of the licence numbers this app stores: a letter followed by nine
#: digits, shown as D03-1234-567 (the format the demo/seed rows already use,
#: and the 12 characters USERS.license_number allows).
LICENCE_EXAMPLE = "D03-1234-567"
_LICENCE_COMPACT_RE = re.compile(r"^[A-Z]\d{9}$")


def canonical_licence(raw: str | None) -> str | None:
    """Return the canonical licence number, or None when it is not one.

    Spaces and dashes are ignored, so ``d03 1234 567`` and ``D031234567``
    both come back as ``D03-1234-567``. The strict shape is deliberate: the
    licence box must not accept a random word.
    """
    compact = "".join(
        ch for ch in (raw or "").upper() if ch not in " -"
    )
    if not _LICENCE_COMPACT_RE.match(compact):
        return None
    return f"{compact[:3]}-{compact[3:7]}-{compact[7:]}"


def normalize_licence(raw: str | None) -> str:
    """Compare licences ignoring case, spaces and dashes (`D03-1234-567`
    matches `d031234567`). Stored values keep their original shape."""
    return "".join(
        ch for ch in (raw or "").upper() if ch.isalnum()
    )


def customers(session: Session) -> list[Users]:
    return list(
        session.query(Users)
        .filter(Users.role == "customer")
        .order_by(Users.full_name)
        .all()
    )


def find_by_licence(
    session: Session, licence_number: str, *, any_role: bool = False
) -> Users | None:
    """Exact licence match (normalized). The table is branch-scale, so the
    normalization compares in Python over customer rows.

    ``any_role=True`` also inspects staff rows: the UNIQUE column is global,
    so customer sign-up must not collide with a staff member's licence.
    """
    wanted = normalize_licence(licence_number)
    if not wanted:
        return None
    rows = session.query(Users).all() if any_role else customers(session)
    for customer in rows:
        if normalize_licence(customer.license_number) == wanted:
            return customer
    return None


def search_customers(session: Session, query: str, *, limit: int = 8) -> list[Users]:
    """Name/phone/email fragment search, for the left-licence-at-home case.
    Still resolves to existing rows -- never creates."""
    fragment = f"%{(query or '').strip()}%"
    if fragment == "%%":
        return []
    return list(
        session.query(Users)
        .filter(
            Users.role == "customer",
            or_(
                Users.full_name.ilike(fragment),
                Users.phone.ilike(fragment),
                Users.email.ilike(fragment),
            ),
        )
        .order_by(Users.full_name)
        .limit(limit)
        .all()
    )


def create_customer(
    session: Session,
    *,
    full_name: str,
    email: str,
    phone: str | None,
    address: str,
    license_number: str,
    license_expiry: date,
    birthdate: date | None = None,
    emergency_contact: str | None = None,
    source: str | None = None,
) -> Users:
    """Counter quick-add. Raises friendly errors; the DB unique constraints
    on email/licence are the final double-create backstop."""
    name = (full_name or "").strip()
    mail = (email or "").strip().lower()
    licence = (license_number or "").strip()
    if not name:
        raise ValidationError("Customer name is required.", field="full_name")
    import re as _re

    if not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", mail):
        raise ValidationError("Enter a valid email address.", field="email")
    if not (address or "").strip():
        raise ValidationError("Customer address is required.", field="address")
    if not licence:
        raise ValidationError(
            "No licence, no rental: the licence number is required.",
            field="license_number",
        )
    if license_expiry is None:
        raise ValidationError(
            "Licence expiry is required.", field="license_expiry"
        )
    if find_by_licence(session, licence) is not None:
        raise ConflictError(
            "That licence already has an account: search for it instead of "
            "creating a duplicate."
        )
    if session.query(Users).filter_by(email=mail).one_or_none() is not None:
        raise ConflictError(
            "That email is already registered to another customer."
        )

    customer = Users(
        full_name=name,
        email=mail,
        phone=(phone or "").strip() or None,
        password_hash=hash_password(STORE_TEMP_PASSWORD),
        address=address.strip(),
        license_number=licence,
        license_expiry=license_expiry,
        role="customer",
        birthdate=birthdate,
        emergency_contact=(emergency_contact or "").strip() or None,
        source=(source or "walk_in"),
        is_active=True,
        created_at=datetime.now(),
    )
    session.add(customer)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(
            "That licence or email was just registered: search for the "
            "customer instead of creating a duplicate."
        ) from exc
    return customer


def update_contact(
    session: Session,
    customer: Users,
    *,
    phone: str | None = None,
    address: str | None = None,
) -> Users:
    """Refresh a returning customer's details without a new row."""
    if phone is not None:
        customer.phone = phone.strip() or None
    if address is not None:
        if not address.strip():
            raise ValidationError(
                "Customer address cannot be blanked.", field="address"
            )
        customer.address = address.strip()
    session.flush()
    return customer


def normalize_phone(raw: str | None) -> str:
    """Compare phone numbers ignoring spaces, dashes, plus signs and an
    optional country trunk (`+63 912-345-6789` matches `09123456789`)."""
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if digits.startswith("63") and len(digits) > 10:
        digits = "0" + digits[2:]
    return digits


def find_duplicates(
    session: Session, *, phone: str | None = None, license_number: str | None = None,
    exclude_id: int | None = None,
) -> list[Users]:
    """Rows matching an existing phone or licence: the walk-in double-entry
    warning. Never includes `exclude_id` (the row being edited)."""
    hits: dict[int, Users] = {}
    if phone and normalize_phone(phone):
        wanted = normalize_phone(phone)
        for customer in customers(session):
            if customer.user_id == exclude_id:
                continue
            if customer.phone and normalize_phone(customer.phone) == wanted:
                hits[customer.user_id] = customer
    if license_number and normalize_licence(license_number):
        match = find_by_licence(session, license_number)
        if match is not None and match.user_id != exclude_id:
            hits[match.user_id] = match
    return list(hits.values())


def update_customer(session: Session, customer: Users, **changes) -> Users:
    """Edit every staff-maintained field. Licence/email clashes are refused
    like in create (uniques are the backstop); birthdate/age validated."""
    if "email" in changes and changes["email"] is not None:
        mail = changes["email"].strip().lower()
        import re as _re

        if not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", mail):
            raise ValidationError("Enter a valid email address.", field="email")
        clash = (
            session.query(Users)
            .filter(Users.email == mail, Users.user_id != customer.user_id)
            .one_or_none()
        )
        if clash is not None:
            raise ConflictError("That email belongs to another customer.")
        changes["email"] = mail
    if "license_number" in changes and changes["license_number"] is not None:
        licence = changes["license_number"].strip()
        if not licence:
            raise ValidationError("Licence number cannot be blanked.", field="license_number")
        match = find_by_licence(session, licence)
        if match is not None and match.user_id != customer.user_id:
            raise ConflictError("That licence belongs to another customer.")
        changes["license_number"] = licence
    if "full_name" in changes and changes["full_name"] is not None:
        if not changes["full_name"].strip():
            raise ValidationError("Customer name is required.", field="full_name")
        changes["full_name"] = changes["full_name"].strip()
    if "birthdate" in changes and changes["birthdate"] is not None:
        if changes["birthdate"] > date.today():
            raise ValidationError("Birthdate cannot be in the future.", field="birthdate")
    for field in ("phone", "address", "emergency_contact"):
        if field in changes and isinstance(changes[field], str):
            changes[field] = changes[field].strip() or None
    for field, value in changes.items():
        if not hasattr(customer, field):
            raise ValidationError(f"Unknown customer field: {field}", field=field)
        setattr(customer, field, value)
    session.flush()
    return customer


def deactivate_customer(session: Session, customer: Users) -> Users:
    """Deactivate instead of delete: bookings keep their FK forever."""
    customer.is_active = False
    session.flush()
    return customer


def reactivate_customer(session: Session, customer: Users) -> Users:
    customer.is_active = True
    session.flush()
    return customer


def set_flag(
    session: Session, customer: Users, reason: str, *, staff_id: int | None = None
) -> Users:
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A flag needs a reason.", field="flag_reason")
    customer.flag_reason = reason
    customer.flagged_at = datetime.now()
    customer.flagged_by = staff_id
    session.flush()
    return customer


def clear_flag(session: Session, customer: Users) -> Users:
    customer.flag_reason = None
    customer.flagged_at = None
    customer.flagged_by = None
    session.flush()
    return customer


def add_note(
    session: Session, customer: Users, text: str, *, author_id: int | None = None
):
    from app.models import Customer_Note

    text = (text or "").strip()
    if not text:
        raise ValidationError("Note cannot be empty.", field="note")
    row = Customer_Note(
        customer_id=customer.user_id,
        author_id=author_id,
        note=text,
        created_at=datetime.now(),
    )
    session.add(row)
    session.flush()
    return row


def list_notes(session: Session, customer: Users) -> list:
    from app.models import Customer_Note

    return list(
        session.query(Customer_Note)
        .filter(Customer_Note.customer_id == customer.user_id)
        .order_by(Customer_Note.created_at.desc())
        .all()
    )


def age_on(birthdate: date | None, on_date: date) -> int | None:
    if birthdate is None:
        return None
    return on_date.year - birthdate.year - (
        (on_date.month, on_date.day) < (birthdate.month, birthdate.day)
    )


def outstanding_for(session: Session, customer: Users):
    """Total still owed across open bookings. Warnings, never a block: the
    fee-only path creates OUTSTANDING by design."""
    from app.services import payment_service

    from app.models import Booking

    total = payment_service.ZERO
    for booking in (
        session.query(Booking)
        .filter(
            Booking.user_id == customer.user_id,
            Booking.status.in_(("confirmed", "ongoing", "completed")),
        )
        .all()
    ):
        total += payment_service.booking_balance(session, booking).balance
    return payment_service.money(total, field="balance")


def unpaid_bookings(session: Session, customer: Users) -> list[tuple[int, object]]:
    """(booking_id, balance-due) oldest-first for the settle picker."""
    from app.services import payment_service

    from app.models import Booking

    rows = (
        session.query(Booking)
        .filter(
            Booking.user_id == customer.user_id,
            Booking.status.in_(("confirmed", "ongoing", "completed")),
        )
        .order_by(Booking.start_date.asc(), Booking.booking_id.asc())
        .all()
    )
    unpaid = []
    for booking in rows:
        due = payment_service.booking_balance(session, booking).balance
        if due > payment_service.ZERO:
            unpaid.append((booking.booking_id, due))
    return unpaid


def allocate_settlement(
    unpaid: list[tuple[int, object]], start_id: int, amount, *, field: str = "amount"
) -> list[tuple[int, object]]:
    """Split one cash handover across bookings: the picked booking first,
    then the rest oldest-first. Overpayment past the total owed is refused;
    partials leave the remainder owed. Each leg becomes its own Payment row
    so receipts match what was paid for."""
    from app.services import payment_service

    total_owed = sum((owed for _, owed in unpaid), payment_service.ZERO)
    value = payment_service.money(amount, field=field)
    if value <= payment_service.ZERO:
        from app.services.errors import ValidationError

        raise ValidationError("Settlement amount must be greater than zero.", field=field)
    if value > total_owed:
        from app.services.errors import ValidationError

        raise ValidationError(
            f"That is more than the {total_owed:,.2f} owed.", field=field
        )
    ordered = sorted(unpaid, key=lambda leg: (leg[0] != start_id, leg[0]))
    legs: list[tuple[int, object]] = []
    remaining = value
    for booking_id, owed in ordered:
        if remaining <= payment_service.ZERO:
            break
        take = min(owed, remaining)
        legs.append((booking_id, take))
        remaining -= take
    return legs


def store_licence_doc(licence_number: str, side: str, src_path: str) -> str:
    """Copy a licence photo into the docs dir. Keyed by normalised licence
    (stable before and after the row exists). Returns the stored path."""
    import shutil
    from pathlib import Path

    from app.config import BASE_DIR

    if side not in ("front", "back"):
        from app.services.errors import ValidationError

        raise ValidationError(f"Unknown document side: {side}.", field="side")
    src = Path(src_path)
    if not src.is_file():
        from app.services.errors import ValidationError

        raise ValidationError("Licence photo not found.", field=side)
    docs = Path(BASE_DIR) / "customer_docs"
    docs.mkdir(parents=True, exist_ok=True)
    dest = docs / f"{normalize_licence(licence_number)}_{side}{src.suffix.lower() or '.jpg'}"
    shutil.copyfile(src, dest)
    return str(dest)


def eligibility_for(session: Session, customer: Users, start_date: date, end_date: date) -> dict:
    """One verdict for the wizard and the details panel.

    Hard blocks (legal/insurance/data): inactive, missing birthdate, expired
    licence, or expiry before the return date. Flags and balances warn loudly
    with a required override note instead of blocking.
    """
    from app.services import payment_service

    reasons: list[str] = []
    warnings: list[str] = []
    if not customer.is_active:
        reasons.append("This customer is deactivated.")
    needs_dob = customer.birthdate is None
    if needs_dob:
        reasons.append("Birthdate is missing: add it to check the renting age.")
    else:
        years = age_on(customer.birthdate, start_date)
        if years is not None and years < MIN_RENTING_AGE:
            reasons.append(
                f"Under the minimum renting age of {MIN_RENTING_AGE}."
            )
    expiry = customer.license_expiry
    if expiry is None:
        reasons.append("No licence expiry on file.")
    elif expiry < start_date:
        reasons.append(f"Licence expired on {expiry:%d %b %Y}.")
    elif expiry < end_date:
        reasons.append(
            f"Licence expires on {expiry:%d %b %Y}, before the return date."
        )
    elif expiry <= start_date + timedelta(days=EXPIRING_SOON_DAYS):
        warnings.append(f"Licence expires soon ({expiry:%d %b %Y}).")
    flagged = bool((customer.flag_reason or "").strip())
    if flagged:
        warnings.append(f"Flagged: {customer.flag_reason.strip()}")
    owed = outstanding_for(session, customer)
    if owed > payment_service.ZERO:
        warnings.append(f"Outstanding balance of {owed:,.2f}.")
    return {
        "blocked": bool(reasons),
        "reasons": reasons,
        "warnings": warnings,
        "needs_dob": needs_dob,
        "flagged": flagged,
        "flag_reason": (customer.flag_reason or "").strip() or None,
        "owed": owed,
    }
