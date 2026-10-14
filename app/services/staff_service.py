"""Staff/admin accounts: create, password changes, activate/deactivate.

`Users` carries every NOT NULL column the table demands (address, licence
fields), so staff rows fill those with stable placeholders — staff never
rent, they only work the counter. The licence columns stay unique, hence
the `STAFF-` prefix per user.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Users
from app.services.errors import ConflictError, StateError, ValidationError
from app.utils.security import hash_password, verify_password

STAFF_ROLES = ("staff", "admin")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 8


def _require_email(email: str) -> str:
    mail = (email or "").strip().lower()
    if not EMAIL_RE.match(mail):
        raise ValidationError("Enter a valid email address.", field="email")
    return mail


def _staff_licence(email: str, user_id: int | None = None) -> str:
    base = re.sub(r"[^A-Z0-9]", "", email.upper())[:8] or "STAFF"
    suffix = f"{user_id or abs(hash(email)) % 10_000_000:07d}"
    return f"ST{suffix}"[:12]


def list_staff(session: Session) -> list[Users]:
    return list(session.execute(
        select(Users).where(Users.role.in_(STAFF_ROLES)).order_by(
            Users.created_at.desc(), Users.user_id.desc())
    ).scalars().all())


def active_admin_count(session: Session) -> int:
    return session.execute(
        select(func.count(Users.user_id)).where(
            Users.role == "admin", Users.is_active.is_(True))
    ).scalar_one()


def has_any_user(session: Session) -> bool:
    return session.execute(
        select(func.count(Users.user_id))).scalar_one() > 0


def create_staff(session: Session, *, full_name: str, email: str,
                 role: str, temp_password: str,
                 created_by: int | None = None) -> Users:
    name = (full_name or "").strip()
    if not name:
        raise ValidationError("Staff name is required.", field="full_name")
    mail = _require_email(email)
    if role not in STAFF_ROLES:
        raise ValidationError("Role must be staff or admin.", field="role")
    if not (temp_password or "") or len(temp_password) < MIN_PASSWORD_LEN:
        raise ValidationError(
            f"Temporary password must be at least {MIN_PASSWORD_LEN} characters.",
            field="temp_password")
    if session.execute(select(Users).where(
            or_(Users.email == mail))).scalar_one_or_none() is not None:
        raise ConflictError("That email is already registered.")
    row = Users(
        full_name=name,
        email=mail,
        password_hash=hash_password(temp_password),
        address="Staff",
        license_number=_staff_licence(mail),
        license_expiry=date(2099, 1, 1),
        role=role,
        is_active=True,
        must_change_password=True,
        created_at=datetime.now(),
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        # Licence clash (same prefix): retry once with the fresh id space.
        row.license_number = _staff_licence(mail + str(datetime.now().timestamp()))
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc2:
            session.rollback()
            raise ConflictError(
                "That email or licence was just registered.") from exc2
        return row
    # Ensure uniqueness even when two staff share an email prefix.
    if session.execute(select(Users).where(
            Users.license_number == row.license_number,
            Users.user_id != row.user_id)).scalar_one_or_none() is not None:
        row.license_number = f"ST{row.user_id:07d}"[:12]
        session.flush()
    return row


def ensure_first_admin(session: Session, *, full_name: str, email: str,
                       password: str) -> Users:
    if has_any_user(session):
        raise StateError("Users already exist; first-run setup is closed.")
    if not (password or "") or len(password) < MIN_PASSWORD_LEN:
        raise ValidationError(
            f"Password must be at least {MIN_PASSWORD_LEN} characters.",
            field="password")
    name = (full_name or "").strip()
    if not name:
        raise ValidationError("Name is required.", field="full_name")
    mail = _require_email(email)
    row = Users(
        full_name=name, email=mail, password_hash=hash_password(password),
        address="Staff", license_number=_staff_licence(mail),
        license_expiry=date(2099, 1, 1), role="admin",
        is_active=True, must_change_password=False,
        created_at=datetime.now(),
    )
    session.add(row)
    session.flush()
    return row


def change_password(session: Session, user: Users, *,
                    current_password: str, new_password: str) -> Users:
    if not verify_password(current_password or "", user.password_hash):
        raise ValidationError(
            "Current password is incorrect.", field="current_password")
    if not (new_password or "") or len(new_password) < MIN_PASSWORD_LEN:
        raise ValidationError(
            f"New password must be at least {MIN_PASSWORD_LEN} characters.",
            field="new_password")
    if verify_password(new_password, user.password_hash):
        raise ValidationError(
            "New password must be different.", field="new_password")
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    session.flush()
    return user


def force_change_password(session: Session, user: Users, *,
                          new_password: str) -> Users:
    """First-login temp-password replacement (no current password needed)."""
    if not (new_password or "") or len(new_password) < MIN_PASSWORD_LEN:
        raise ValidationError(
            f"Password must be at least {MIN_PASSWORD_LEN} characters.",
            field="new_password")
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    session.flush()
    return user


def reset_password(session: Session, user: Users, *,
                   temp_password: str) -> Users:
    if user.role not in STAFF_ROLES:
        raise ValidationError("Only staff accounts reset here.", field="role")
    if not (temp_password or "") or len(temp_password) < MIN_PASSWORD_LEN:
        raise ValidationError(
            f"Temporary password must be at least {MIN_PASSWORD_LEN} characters.",
            field="temp_password")
    user.password_hash = hash_password(temp_password)
    user.must_change_password = True
    session.flush()
    return user


def set_active(session: Session, user: Users, active: bool) -> Users:
    if user.role not in STAFF_ROLES:
        raise ValidationError("Only staff accounts deactivate here.", field="role")
    if not active and user.role == "admin" and user.is_active:
        remaining = session.execute(select(func.count(Users.user_id)).where(
            Users.role == "admin", Users.is_active.is_(True),
            Users.user_id != user.user_id)).scalar_one()
        if remaining == 0:
            raise StateError(
                "Cannot deactivate the last active admin.")
    user.is_active = active
    session.flush()
    return user
