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

#: Roles the staff application will accept; `customer` is refused.
STAFF_ROLES = ("staff", "admin")
ADMIN_ROLES = ("admin",)

DEFAULT_DEMO_FILE = "demo.db"


class AccessDenied(Exception):
    pass


class NotSignedIn(AccessDenied):
    pass


@dataclass(frozen=True)
class StaffUser:
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
    LIVE = "live"
    DEMO = "demo"

    @property
    def is_demo(self) -> bool:
        return self is DatabaseTarget.DEMO


@dataclass(frozen=True)
class DatabaseSelection:
    url: str
    target: DatabaseTarget
    #: On-screen database label; never contains credentials.
    label: str

    @property
    def is_demo(self) -> bool:
        return self.target.is_demo


def select_database(
    *,
    demo: bool = False,
    demo_file: str | Path | None = None,
) -> DatabaseSelection:
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

    # Used verbatim: re-parsing would mangle a SQLite path.
    return DatabaseSelection(
        url=DATABASE_URL,
        target=DatabaseTarget.LIVE,
        label=f"Live - {_database_name(DATABASE_URL)}",
    )


def _database_name(url: str) -> str:
    try:
        return make_url(url).database or "database"
    except Exception:
        return "database"


class StaffContext:
    def __init__(self, selection: DatabaseSelection) -> None:
        self.selection = selection
        self._user: StaffUser | None = None
        # Aiven drops idle connections well inside the recycle window.
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
        self._user = user if isinstance(user, StaffUser) else StaffUser.from_row(user)

    def sign_out(self) -> None:
        self._user = None

    def require_staff(self) -> StaffUser:
        user = self.user
        if user.role not in STAFF_ROLES:
            raise AccessDenied(
                f"'{user.email}' is a {user.role} account. Staff access only."
            )
        return user

    def require_admin(self) -> StaffUser:
        user = self.require_staff()
        if user.role not in ADMIN_ROLES:
            raise AccessDenied(
                "That area is for administrators. Ask an admin if you need it."
            )
        return user

    def dispose(self) -> None:
        self.engine.dispose()
