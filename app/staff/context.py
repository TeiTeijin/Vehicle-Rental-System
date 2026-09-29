"""Runtime context for the staff application.

Two responsibilities, and they are deliberately in one place:

  * **Which database.** The staff app builds its own engine instead of using
    the module-level ``app.database.engine``. That engine is bound to
    ``DATABASE_URL`` the moment ``app.models`` is imported, so in ``--demo``
    mode a stray ``from app.database import get_session`` would silently talk
    to the live Aiven instance. Owning the engine here means there is exactly
    one object to audit, and a test can assert the live one is never touched.

  * **Who is signed in.** ``user`` and ``role`` live on the context rather than
    on the window, so a page cannot accidentally read them from somewhere
    else, and :meth:`StaffContext.require_role` gives the role gate a single
    implementation.

Sessions are short-lived per operation. A desktop app that holds one session
open for hours accumulates stale identity-map state and keeps a connection
checked out against a MySQL server that will have dropped it.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import BASE_DIR, DATABASE_URL
from app.models import Users

#: Roles the staff application will accept. A `customer` row is a valid row in
#: the table and is *not* a staff member; refusing it is the whole point of
#: the gate.
STAFF_ROLES = ("staff", "admin")
ADMIN_ROLES = ("admin",)

DEFAULT_DEMO_FILE = "demo.db"


class AccessDenied(Exception):
    """Raised when a signed-in user asks for something their role excludes."""


class NotSignedIn(AccessDenied):
    """Raised when a page asks for the current user before login."""


@dataclass(frozen=True)
class StaffUser:
    """The signed-in identity, as a plain value.

    Deliberately *not* a `Users` row. `auth_service.sign_in` returns an ORM
    instance bound to the session that loaded it, and the session is closed
    before the shell ever sees the result -- so keeping the row would make
    every later `user.role` raise `DetachedInstanceError`. Copying the four
    fields that matter sidesteps that entirely and means the signed-in
    identity cannot be mutated by later database work.
    """

    user_id: int
    full_name: str
    email: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role in ADMIN_ROLES

    @classmethod
    def from_row(cls, row: Users) -> "StaffUser":
        return cls(
            user_id=row.user_id,
            full_name=row.full_name or "",
            email=row.email or "",
            role=row.role or "",
        )


class DatabaseTarget(Enum):
    """Which database this process is pointed at.

    Carried as an enum rather than a bool so the demo banner cannot be
    rendered by a truthiness check that happens to be inverted.
    """

    LIVE = "live"
    DEMO = "demo"

    @property
    def is_demo(self) -> bool:
        return self is DatabaseTarget.DEMO


@dataclass(frozen=True)
class DatabaseSelection:
    """A resolved database URL, plus enough context to label it on screen."""

    url: str
    target: DatabaseTarget
    #: A human-readable name for the banner and the window subtitle. Never
    #: contains credentials.
    label: str

    @property
    def is_demo(self) -> bool:
        return self.target.is_demo


def select_database(
    *,
    demo: bool = False,
    demo_file: str | Path | None = None,
) -> DatabaseSelection:
    """Decide which database to open, without opening it yet.

    ``demo`` is opt-in and explicit. The default is the live ``DATABASE_URL``,
    because the staff app is a tool for the branch and a demo that silently
    took priority would mean someone reconciling against fiction.
    """
    if demo:
        path = Path(demo_file) if demo_file else BASE_DIR / DEFAULT_DEMO_FILE
        if not path.is_absolute():
            path = BASE_DIR / path
        if not path.exists():
            raise FileNotFoundError(
                f"No demo database at {path}.\n"
                "  Create one first:\n"
                "      python -m scripts.seed_demo_data --database demo.db"
            )
        return DatabaseSelection(
            url=f"sqlite:///{path}",
            target=DatabaseTarget.DEMO,
            label=f"Demo data - {path.name}",
        )

    # `app.config.DATABASE_URL` is already normalised. Re-running the builder
    # on its own output would be a no-op for MySQL but would mangle a SQLite
    # path, so it is used verbatim.
    return DatabaseSelection(
        url=DATABASE_URL,
        target=DatabaseTarget.LIVE,
        label=f"Live - {_database_name(DATABASE_URL)}",
    )


def _database_name(url: str) -> str:
    """The database name from a URL, for the on-screen label.

    The username and password are deliberately dropped. This string ends up in
    the window subtitle and is the sort of thing that ends up in a screenshot.
    """
    try:
        return make_url(url).database or "database"
    except Exception:
        return "database"


class StaffContext:
    """Holds the engine, the signed-in user, and the role gate."""

    def __init__(self, selection: DatabaseSelection) -> None:
        self.selection = selection
        self._user: StaffUser | None = None
        # `pool_pre_ping` matters on the live MySQL instance: Aiven drops idle
        # connections well inside the 30-minute recycle window.
        self.engine: Engine = create_engine(
            selection.url,
            future=True,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        self._sessions = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )

    # -- sessions ---------------------------------------------------------

    @contextmanager
    def session(self):
        """One unit of work.

        Commits on a clean exit and rolls back on any exception, so a handler
        that raises halfway through a payment cannot leave a partial write
        behind.
        """
        session: Session = self._sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def reading(self):
        """A read-only unit of work. Rolls back rather than committing, which
        makes it obvious at a glance that nothing here is meant to write."""
        session: Session = self._sessions()
        try:
            yield session
        finally:
            session.rollback()
            session.close()

    # -- identity ---------------------------------------------------------

    @property
    def user(self) -> StaffUser:
        if self._user is None:
            raise NotSignedIn("Nobody is signed in.")
        return self._user

    @property
    def is_signed_in(self) -> bool:
        return self._user is not None

    @property
    def role(self) -> str:
        return self._user.role if self._user is not None else ""

    @property
    def is_admin(self) -> bool:
        return self._user is not None and self._user.is_admin

    @property
    def display_name(self) -> str:
        if self._user is None:
            return ""
        return self._user.full_name or self._user.email

    def sign_in(self, user) -> None:
        """Record the signed-in user after the role gate has passed.

        Accepts either a `Users` row or an existing `StaffUser`, because
        `auth_service.sign_in` returns the former and it is already detached by
        the time it arrives.
        """
        self._user = user if isinstance(user, StaffUser) else StaffUser.from_row(user)

    def sign_out(self) -> None:
        self._user = None

    def require_staff(self) -> StaffUser:
        """The identity every staff page assumes.

        Raises rather than returning ``None`` so that a page reached by a bad
        route shows a permission error instead of rendering an empty table and
        looking like there is no data.
        """
        user = self.user
        if user.role not in STAFF_ROLES:
            raise AccessDenied(
                f"'{user.email}' is a {user.role} account. Staff access only."
            )
        return user

    def require_admin(self) -> StaffUser:
        """For the admin-only parts of the dashboard."""
        user = self.require_staff()
        if user.role not in ADMIN_ROLES:
            raise AccessDenied(
                "That area is for administrators. Ask an admin if you need it."
            )
        return user

    def dispose(self) -> None:
        self.engine.dispose()
