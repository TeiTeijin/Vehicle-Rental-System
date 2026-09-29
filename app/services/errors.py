"""Errors the services raise, and how the UI should react to each.

Staff errors are not interchangeable. A wrong plate number is a typo the
counter clerk fixes in a second; a database that went away mid-check-out is a
half-applied transaction someone has to look at. This module gives the screens
a stable set of exception types to catch so each one can be routed to the
right feedback channel -- inline field error, toast, or blocking modal --
without the services having to know anything about Qt.

The rule the staff UI follows:
    ValidationError -> inline, on the named field, non-blocking
    ConflictError / StateError -> modal, the action was refused
    DataUnavailable -> modal + offer to retry, the screen is now untrustworthy
    everything else -> modal, and the transaction was rolled back

Messages are written for the person at the counter, not for a log file. Stack
traces and driver text go to the console; these strings go on screen.
"""


class ServiceError(Exception):
    """Base for every expected failure in the service layer."""

    #: Short label for logs, not for staff.
    kind = "service error"


class ValidationError(ServiceError):
    """A field value is unusable. Attaches to a form field when there is one."""

    kind = "validation"

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


class NotFoundError(ServiceError):
    """A referenced record does not exist."""

    kind = "not found"


class ConflictError(ServiceError):
    """The action would collide with existing data -- a duplicate plate, a
    licence already on file, a vehicle booked for those dates."""

    kind = "conflict"


class StateError(ServiceError):
    """The record is not in a state that allows this action -- checking out a
    booking that was never checked in, taking a payment against a cancelled
    booking, moving a vehicle from a status it cannot leave."""

    kind = "invalid state"


class PermissionDenied(ServiceError):
    """The signed-in user's role does not allow this action."""

    kind = "permission denied"


class DataUnavailable(ServiceError):
    """The database could not be reached or read.

    Distinct from the others because the screen it happened on can no longer be
    trusted: it must show an error state with Retry rather than an empty table,
    since "no data" and "could not load data" look identical otherwise.
    """

    kind = "data unavailable"

    def __init__(self, message: str = "Could not reach the database.") -> None:
        super().__init__(message)
        self.message = message
