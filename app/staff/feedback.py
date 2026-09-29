"""How the staff app talks to the user when something goes wrong.

Four channels, each with a job, because "an exception" is not something a
person at a rental counter can act on:

  * :func:`describe` turns a typed service error into one sentence. The
    services already raise ``ValidationError``/``ConflictError``/
    ``StateError``/``NotFoundError`` with human text and sometimes a ``field``;
    this is the single place that decides what the user is shown.
  * :class:`Toast` confirms something happened without stealing focus, so a
    member of staff can keep working while a payment is recorded.
  * :func:`blocking_error` is for the cases where continuing would be wrong --
    a failed save the user believes succeeded.
  * :func:`show_field_error` / :func:`clear_field_error` put a message under
    the input that caused it, using the ``field`` the service supplied.

The rule that shapes all of it: a load that fails says so and offers a retry.
It never renders as an empty table, because an empty table reads as "no data"
and would quietly hide a broken database connection.
"""

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

#: What the user is told when the underlying failure is not ours to explain.
#: Deliberately generic: a stack trace or a driver message in a dialog is noise
#: that hides the one thing they needed to know.
GENERIC_FAILURE = "Something went wrong loading this. Try again."
GENERIC_SAVE_FAILURE = "That could not be saved. Nothing was changed."


def describe(error: BaseException) -> tuple[str, str | None, str]:
    """(message, field, kind) for any exception the staff app can raise.

    ``kind`` is one of ``ok`` / ``warn`` / ``error`` and picks the colour.

    The order matters: the more specific classes come first, because
    ``ValidationError`` and friends do not share a base with each other beyond
    ``ServiceError``, and falling through to the generic branch would throw
    away text that was already written for this purpose.
    """
    if isinstance(error, NotSignedIn):
        return "Sign in to continue.", None, "warn"

    if isinstance(error, AccessDenied):
        return _text_of(error), None, "warn"

    if isinstance(error, ValidationError):
        return error.message, error.field, "error"

    if isinstance(error, (ConflictError, StateError)):
        # These are refusals, not faults. "You can't do that" is information,
        # and colouring it as a failure would make a normal part of the domain
        # look like a bug.
        return _text_of(error), None, "warn"

    if isinstance(error, NotFoundError):
        return _text_of(error), None, "warn"

    if isinstance(error, ServiceError):
        return _text_of(error), None, "error"

    # Not ours. A dropped MySQL connection is the common case, and it is worth
    # naming because it is the one failure that is fixed by clicking Retry.
    if _looks_like_a_connection_problem(error):
        return (
            "Lost the connection to the database. Check the network and try again.",
            None,
            "error",
        )

    return GENERIC_FAILURE, None, "error"


def _text_of(error: BaseException) -> str:
    """The human message on a service error, however that class spells it.

    Only `ValidationError` sets `.message`; the other service errors are plain
    `Exception` subclasses whose text lives in `args[0]`. Reaching for
    `error.message` on a `ConflictError` therefore raises `AttributeError`
    *inside the handler*, which is the worst place for one: the original
    failure disappears and the user is left with a traceback and no message.
    """
    message = getattr(error, "message", None)
    if isinstance(message, str) and message:
        return message
    return str(error) or GENERIC_FAILURE


def _looks_like_a_connection_problem(error: BaseException) -> bool:
    """True for the driver errors that a retry can actually fix.

    Matched on class name rather than by importing driver-specific exception
    types, so the staff app keeps working whichever driver ``DATABASE_URL``
    selects and does not need PyMySQL to be installed to be testable.
    """
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
    """The exception and everything it was raised from."""
    seen, current = set(), error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def kind_colour(kind: str) -> str:
    return {"ok": OK, "warn": WARN, "error": DANGER}.get(kind, WARN)


# -- inline field errors ---------------------------------------------------


def show_field_error(label, message: str | None) -> None:
    """Put `message` under a field, or clear it when there is none.

    `label` is any QLabel; the error styling is applied by object name so the
    stylesheet owns the colour.
    """
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
