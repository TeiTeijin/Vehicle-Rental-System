from __future__ import annotations

from app.services.errors import (
    ConflictError,
    NotFoundError,
    ServiceError,
    StateError,
    ValidationError,
)
from app.staff.context import AccessDenied, NotSignedIn
from app.staff.theme import DANGER, OK, WARN

#: Generic failure text when the cause is not ours to explain.
GENERIC_FAILURE = "Something went wrong loading this. Try again."
GENERIC_SAVE_FAILURE = "That could not be saved. Nothing was changed."


def describe(error: BaseException) -> tuple[str, str | None, str]:
    if isinstance(error, NotSignedIn):
        return "Sign in to continue.", None, "warn"

    if isinstance(error, AccessDenied):
        return _text_of(error), None, "warn"

    if isinstance(error, ValidationError):
        return error.message, error.field, "error"

    if isinstance(error, (ConflictError, StateError)):
        return _text_of(error), None, "warn"

    if isinstance(error, NotFoundError):
        return _text_of(error), None, "warn"

    if isinstance(error, ServiceError):
        return _text_of(error), None, "error"

    if _looks_like_a_connection_problem(error):
        return (
            "Lost the connection to the database. Check the network and try again.",
            None,
            "error",
        )

    return GENERIC_FAILURE, None, "error"


def _text_of(error: BaseException) -> str:
    message = getattr(error, "message", None)
    if isinstance(message, str) and message:
        return message
    return str(error) or GENERIC_FAILURE


def _looks_like_a_connection_problem(error: BaseException) -> bool:
    names = {type(e).__name__ for e in _chain(error)}
    return bool(
        names
        & {
            "OperationalError",
            "InterfaceError",
            "DBAPIError",
            "ConnectionError",
            "TimeoutError",
            "DisconnectionError",
        }
    )


def _chain(error: BaseException):
    seen, current = set(), error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def kind_colour(kind: str) -> str:
    return {"ok": OK, "warn": WARN, "error": DANGER}.get(kind, WARN)


# -- inline field errors ---------------------------------------------------


def show_field_error(label, message: str | None) -> None:
    from PySide6.QtWidgets import QLabel

    if not isinstance(label, QLabel):
        return
    if message:
        label.setText(message)
        label.setObjectName("fieldError")
        label.setVisible(True)
    else:
        label.setText("")
        label.setVisible(False)


def clear_field_error(label) -> None:
    show_field_error(label, None)
