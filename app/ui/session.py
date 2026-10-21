"""Who the customer app is signed in as.

The signed-in row never leaves the services: the UI keeps a small, plain
`Identity` (see `auth_service.sign_in` -> `Identity.of`), so a closed database
session can never leave a screen holding a half-dead ORM object.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime


@dataclass(frozen=True)
class Identity:
    """A customer, reduced to what the screens draw."""

    user_id: int
    full_name: str
    email: str

    @classmethod
    def of(cls, user) -> "Identity":
        return cls(
            user_id=user.user_id,
            full_name=(user.full_name or "").strip(),
            email=(user.email or "").strip(),
        )

    @property
    def short_name(self) -> str:
        """'Demo' -- the first word, for the chip in the top bar."""
        return self.full_name.split(" ")[0] if self.full_name else self.email


class CustomerSession:
    """The signed-in customer for this run of the app, or nobody.

    Deliberately tiny: whether anyone is signed in is a UI fact (which chip
    the top bar shows, whether booking asks for a sign-in first), not a
    database state. Both the modal and the top bar read the same object.
    """

    def __init__(self) -> None:
        self._identity: Identity | None = None

    @property
    def is_signed_in(self) -> bool:
        return self._identity is not None

    @property
    def user(self) -> Identity | None:
        return self._identity

    @property
    def user_id(self) -> int | None:
        return self._identity.user_id if self._identity else None

    @property
    def display_name(self) -> str:
        return self._identity.short_name if self._identity else ""

    def sign_in(self, identity: Identity) -> None:
        self._identity = identity

    def sign_out(self) -> None:
        self._identity = None


@dataclass
class BookingDraft:
    """One in-flight booking, from the vehicle page to the confirmation.

    Screens 3 and 4 read and write this same object, which is what makes the
    dates, times and branches survive BACK from checkout: nothing is rebuilt
    from the widgets, and nothing is written to the database until the
    customer confirms.
    """

    vehicle_id: int
    pickup_dt: datetime | None = None
    return_dt: datetime | None = None
    #: None == "return to the pickup branch".
    pickup_branch_id: int | None = None
    return_branch_id: int | None = None
    payment_method: str | None = None
    card_last4: str | None = None
    #: Filled in once the booking exists; the router shows it in the receipt.
    reference_code: str | None = None

    @property
    def ready(self) -> bool:
        return self.pickup_dt is not None and self.return_dt is not None

    def copy(self) -> "BookingDraft":
        return replace(self)


__all__ = ["BookingDraft", "CustomerSession", "Identity"]
