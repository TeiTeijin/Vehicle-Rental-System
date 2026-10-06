class ServiceError(Exception):
    #: Short label for logs, not for staff.
    kind = "service error"


class ValidationError(ServiceError):
    kind = "validation"

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


class NotFoundError(ServiceError):
    kind = "not found"


class ConflictError(ServiceError):
    kind = "conflict"


class StateError(ServiceError):
    kind = "invalid state"


class PermissionDenied(ServiceError):
    kind = "permission denied"


class DataUnavailable(ServiceError):
    kind = "data unavailable"

    def __init__(self, message: str = "Could not reach the database.") -> None:
        super().__init__(message)
        self.message = message
