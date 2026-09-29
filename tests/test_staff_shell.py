"""Tests for the staff shell: database selection, the role gate, and routing.

These run against a real `QApplication` on the `offscreen` platform plugin.
There is no `pytest-qt` in this project, so the fixture below is hand-rolled;
it is deliberately minimal -- Qt widgets need an application to exist before
they are constructed, and every test that builds a window needs one.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine

# Set before any Qt widget is built. `conftest.py` has already set
# DATABASE_URL by the time this module is imported, so the live Aiven instance
# cannot be reached from here.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.database import Base  # noqa: E402
from app.staff.context import (  # noqa: E402
    AccessDenied,
    DatabaseSelection,
    DatabaseTarget,
    NotSignedIn,
    StaffContext,
    StaffUser,
    select_database,
)
from app.staff.shell import PageSpec, StaffShell  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def context(tmp_path):
    """A context over a private on-disk SQLite file.

    On disk rather than ``sqlite://`` in memory because `create_engine` on a
    memory URL gives each connection its own database, and `StaffContext`
    opens a new session per read and per write.
    """
    path = tmp_path / "staff_test.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    engine.dispose()
    ctx = StaffContext(
        DatabaseSelection(
            url=f"sqlite:///{path}",
            target=DatabaseTarget.LIVE,
            label="Test",
        )
    )
    yield ctx
    ctx.dispose()


def sign_in(context, *, role: str, email: str | None = None) -> StaffUser:
    user = StaffUser(
        user_id=1, full_name=f"Test {role}", email=email or f"{role}@example.com", role=role
    )
    context.sign_in(user)
    return user


# --------------------------------------------------------------------------
# Database selection
# --------------------------------------------------------------------------


class TestDatabaseSelection:
    def test_demo_is_opt_in(self, monkeypatch):
        """The staff app is a branch tool. A demo that silently won the
        argument would mean someone reconciling against fiction."""
        monkeypatch.setattr(
            "app.staff.context.DATABASE_URL", "mysql://u:p@db.example.com/rentdesk"
        )
        live = select_database()
        assert live.target is DatabaseTarget.LIVE
        assert live.is_demo is False

    def test_demo_points_at_the_named_file(self, tmp_path, monkeypatch):
        from app.config import BASE_DIR

        path = tmp_path / "scratch.db"
        path.write_bytes(b"")
        monkeypatch.setattr("app.staff.context.BASE_DIR", tmp_path)
        picked = select_database(demo=True, demo_file="scratch.db")
        assert picked.target is DatabaseTarget.DEMO
        assert picked.url == f"sqlite:///{path}"

    def test_missing_demo_file_says_how_to_make_one(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.staff.context.BASE_DIR", tmp_path)
        with pytest.raises(FileNotFoundError) as caught:
            select_database(demo=True)
        # The message is the whole point: a bare "not found" leaves the next
        # step to guesswork.
        assert "seed_demo_data" in str(caught.value)

    def test_the_label_never_carries_credentials(self, monkeypatch):
        monkeypatch.setattr(
            "app.staff.context.DATABASE_URL",
            "mysql://admin:hunter2@db.example.com/rentdesk",
        )
        label = select_database().label
        assert "hunter2" not in label
        assert "admin" not in label
        assert "rentdesk" in label


# --------------------------------------------------------------------------
# The role gate
# --------------------------------------------------------------------------


class TestRoleGate:
    def test_nobody_signed_in_raises_rather_than_returning_none(self, context):
        with pytest.raises(NotSignedIn):
            context.user
        assert context.is_signed_in is False

    def test_a_customer_row_is_refused(self, context):
        sign_in(context, role="customer")
        with pytest.raises(AccessDenied) as caught:
            context.require_staff()
        assert "customer account" in str(caught.value)

    def test_staff_may_use_the_counter_pages(self, context):
        sign_in(context, role="staff")
        assert context.is_admin is False
        assert context.require_staff().role == "staff"

    def test_staff_may_not_use_the_admin_areas(self, context):
        sign_in(context, role="staff")
        with pytest.raises(AccessDenied) as caught:
            context.require_admin()
        assert "administrators" in str(caught.value)

    def test_admin_may_use_both(self, context):
        sign_in(context, role="admin")
        assert context.is_admin is True
        assert context.require_admin().role == "admin"

    def test_the_identity_survives_the_session_that_loaded_it(self, context):
        """`sign_in` returns an ORM row from a session that is then closed.

        Keeping that row would make every later `user.role` raise
        DetachedInstanceError, so the context copies the fields it needs.
        """
        from datetime import date, datetime

        from app.models import Users
        from app.utils.security import hash_password

        with context.session() as session:
            row = Users(
                full_name="Copy Me",
                email="copy@example.com",
                password_hash=hash_password("x"),
                address="1 Test Street",
                license_number="LIC99999999",
                license_expiry=date(2030, 1, 1),
                created_at=datetime(2026, 1, 1, 9, 0),
                role="admin",
            )
            session.add(row)
            session.flush()
            identity = StaffUser.from_row(row)
            detached_id = row.user_id

        # The session is closed here; reading `row` again would raise.
        context.sign_in(identity)
        assert context.user.user_id == detached_id
        assert context.user.role == "admin"
        assert context.user.email == "copy@example.com"


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------


class TestSessionOwnership:
    @staticmethod
    def _row(**overrides):
        """A row that satisfies every NOT NULL column on `USERS`.

        Spelled out rather than borrowed from `conftest`'s factories, which
        write through the suite's own `session` fixture -- this test needs the
        row to go through `StaffContext.session()` instead.
        """
        from datetime import date, datetime

        from app.models import Users

        fields = {
            "full_name": "Test User",
            "email": "test@example.com",
            "password_hash": "not-a-real-hash",
            "address": "1 Test Street",
            "license_number": "LIC00000001",
            "license_expiry": date(2030, 1, 1),
            "created_at": datetime(2026, 1, 1, 9, 0),
            "role": "staff",
        }
        fields.update(overrides)
        return Users(**fields)

    def test_a_write_is_committed(self, context):
        from app.models import Users

        with context.session() as session:
            session.add(self._row(email="written@example.com"))
        with context.reading() as session:
            assert session.query(Users).count() == 1

    def test_a_failing_write_leaves_nothing_behind(self, context):
        from app.models import Users

        with pytest.raises(RuntimeError):
            with context.session() as session:
                session.add(self._row(email="doomed@example.com"))
                raise RuntimeError("halfway through")
        with context.reading() as session:
            assert session.query(Users).count() == 0

    def test_reading_never_commits(self, context):
        from app.models import Users

        with context.reading() as session:
            session.add(self._row(email="ghost@example.com"))
        with context.reading() as session:
            assert session.query(Users).count() == 0


# --------------------------------------------------------------------------
# The shell
# --------------------------------------------------------------------------


def _stub_page(title: str = "Stub"):
    """A page that records its refreshes, for routing tests."""

    def factory(shell):
        from PySide6.QtWidgets import QLabel

        page = QLabel(title)
        page.refreshes = 0

        def refresh():
            page.refreshes += 1

        page.refresh = refresh
        return page

    return factory


class TestShellRouting:
    @pytest.fixture
    def shell(self, qapp, context):
        pages = [
            PageSpec("one", "One", _stub_page("One"), auto_refresh=True),
            PageSpec("two", "Two", _stub_page("Two")),
            PageSpec(
                "secret",
                "Secret",
                _stub_page("Secret"),
                admin_only=True,
            ),
        ]
        window = StaffShell(context, pages)
        yield window
        window.deleteLater()
        qapp.processEvents()

    def test_login_is_the_first_screen(self, shell):
        assert shell.stack.currentWidget() is shell.login_view
        assert shell._page_widgets == {}

    def test_signing_in_builds_the_chrome_and_lands_on_the_first_page(self, shell, qapp):
        sign_in(shell.context, role="admin")
        shell._on_signed_in(shell.context.user)
        qapp.processEvents()
        assert shell._navbar is not None
        assert shell.stack.currentWidget() is not shell.login_view
        assert shell.current_page_key == "one"

    def test_a_page_is_built_once_and_refreshed_on_revisit(self, shell, qapp):
        sign_in(shell.context, role="admin")
        shell._on_signed_in(shell.context.user)
        shell.show_page("two")
        first = shell._page_widgets["two"]
        shell.show_page("one")
        shell.show_page("two")
        assert shell._page_widgets["two"] is first
        # Once at construction-time show_page, once per later visit.
        assert first.refreshes == 2

    def test_an_unknown_key_is_refused(self, shell):
        sign_in(shell.context, role="admin")
        shell._on_signed_in(shell.context.user)
        assert shell.show_page("nowhere") is False

    def test_staff_cannot_open_an_admin_page(self, shell, qapp, monkeypatch):
        import app.staff.shell as shell_mod

        monkeypatch.setattr(shell_mod, "toast", lambda *a, **k: None)
        sign_in(shell.context, role="staff")
        shell._on_signed_in(shell.context.user)
        assert shell.show_page("secret") is False
        # Not merely hidden: never constructed, so it holds no data at all.
        assert "secret" not in shell._page_widgets

    def test_staff_never_see_the_admin_button(self, shell, qapp):
        sign_in(shell.context, role="staff")
        shell._on_signed_in(shell.context.user)
        labels = [b.text() for b in shell._nav_group.buttons()]
        assert "Secret" not in labels
        assert "One" in labels

    def test_admin_see_every_button(self, shell, qapp):
        sign_in(shell.context, role="admin")
        shell._on_signed_in(shell.context.user)
        labels = [b.text() for b in shell._nav_group.buttons()]
        assert {"One", "Two", "Secret"} <= set(labels)

    def test_the_navbar_is_rebuilt_per_session(self, shell, qapp):
        """A button left over from the last sign-in is a way to reach a page
        the current role should not see."""
        sign_in(shell.context, role="admin")
        shell._on_signed_in(shell.context.user)
        first_navbar = shell._navbar
        shell.context.sign_out()
        sign_in(shell.context, role="staff")
        shell._on_signed_in(shell.context.user)
        assert shell._navbar is not first_navbar
        assert first_navbar.parent() is None

    def test_sign_out_discards_the_pages(self, shell, qapp, monkeypatch):
        import app.staff.shell as shell_mod

        monkeypatch.setattr(shell_mod, "confirm", lambda *a, **k: True)
        sign_in(shell.context, role="admin")
        shell._on_signed_in(shell.context.user)
        shell.show_page("two")
        page = shell._page_widgets["two"]

        shell.sign_out()
        qapp.processEvents()

        assert shell.context.is_signed_in is False
        assert shell._page_widgets == {}
        assert page.parent() is None
        # The stack is back to the login screen alone. Pages left in there
        # would still hold the previous user's rows.
        assert shell.stack.count() == 1
        assert shell.stack.currentWidget() is shell.login_view

    def test_the_title_still_says_which_database_after_signing_out(self, shell, qapp, monkeypatch):
        import app.staff.shell as shell_mod

        monkeypatch.setattr(shell_mod, "confirm", lambda *a, **k: True)
        sign_in(shell.context, role="admin")
        shell._on_signed_in(shell.context.user)
        shell.sign_out()
        assert shell.windowTitle() == shell._title

    def test_a_cancelled_sign_out_does_nothing(self, shell, qapp, monkeypatch):
        import app.staff.shell as shell_mod

        monkeypatch.setattr(shell_mod, "confirm", lambda *a, **k: False)
        sign_in(shell.context, role="staff")
        shell._on_signed_in(shell.context.user)
        shell.sign_out()
        assert shell.context.is_signed_in is True

    def test_the_timer_only_refreshes_the_visible_opted_in_page(self, shell, qapp):
        sign_in(shell.context, role="admin")
        shell._on_signed_in(shell.context.user)
        shell.show_page("two")  # no auto_refresh

        shell.show_page("one")
        before = shell._page_widgets["one"].refreshes
        shell._on_refresh_tick()
        assert shell._page_widgets["one"].refreshes == before + 1

        shell.show_page("two")
        before = shell._page_widgets["two"].refreshes
        shell._on_refresh_tick()
        assert shell._page_widgets["two"].refreshes == before

    def test_the_timer_is_inert_before_login(self, shell):
        shell._on_refresh_tick()  # must not raise

    def test_a_demo_selection_banners_and_says_so_in_the_title(self, qapp, tmp_path):
        path = tmp_path / "demo.db"
        path.write_bytes(b"")
        ctx = StaffContext(
            DatabaseSelection(
                url=f"sqlite:///{path}", target=DatabaseTarget.DEMO, label="Demo data"
            )
        )
        window = StaffShell(ctx, [PageSpec("one", "One", _stub_page("One"))])
        window._on_signed_in(StaffUser(1, "T", "t@example.com", "staff"))
        qapp.processEvents()
        assert "DEMO DATA" in window.windowTitle()
        assert window._banner is not None
        window.deleteLater()
        ctx.dispose()

    def test_a_live_selection_is_not_bannered(self, shell, qapp):
        assert shell._banner is None
        sign_in(shell.context, role="staff")
        shell._on_signed_in(shell.context.user)
        assert shell._banner is None
