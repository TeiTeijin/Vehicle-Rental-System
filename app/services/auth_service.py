from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Users
from app.utils.security import verify_password


class AuthError(Exception):
    pass


class InvalidCredentials(AuthError):
    pass


DUMMY_HASH = "$2b$12$izSvmM0zX.Kx3tsRF.uwIep3aJoUFsgXQKCB/tgDEYzXuknE8bO7S"
INVALID_CREDENTIALS_MESSAGE = "Incorrect email or password."


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
