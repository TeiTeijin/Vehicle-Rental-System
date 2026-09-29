"""The staff window: navbar, routing, and the sign-in gate.

Structure is a ``QStackedWidget`` with login at index 0 and the pages after
it, so signing in and out is a page switch rather than a teardown. The chrome
around the stack -- navbar, demo banner -- is rebuilt per session, and the page
widgets themselves are discarded on sign-out: a page that outlived the session
would still hold the previous user's loaded rows.

The navbar is rebuilt from :data:`PAGES` on every sign-in and sign-out rather
than being created once, because the visible set depends on the role and a
stale button is a way to reach a page you should not be able to see. Each
page also re-checks its own permission on activation, so even a constructed-
but-hidden page cannot be shown to the wrong role.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.staff import theme
from app.staff.context import AccessDenied, StaffContext
from app.staff.login import LoginView
from app.staff.widgets import blocking_error, confirm, toast

#: How often the dashboard re-reads the database. 30 seconds is short enough
#: that a check-in by a colleague shows up while you are still looking at the
#: screen, and long enough that it is not a visible source of flicker on a
#: shared machine. Overridable so a test does not have to wait for it.
DASHBOARD_REFRESH_MS = 30_000


@dataclass(frozen=True)
class PageSpec:
    """One navbar entry.

    `admin_only` hides the button for staff; the page then re-checks the role
    on activation, so the check exists in two places on purpose.
    """

    key: str
    title: str
    factory: Callable[["StaffShell"], QWidget]
    admin_only: bool = False
    #: Pages that hold live figures refresh themselves on the dashboard timer.
    auto_refresh: bool = False


class StaffShell(QMainWindow):
    """The staff application window."""

    def __init__(self, context: StaffContext, pages: list[PageSpec] | None = None) -> None:
        super().__init__()
        self.context = context
        self.pages = list(pages) if pages is not None else default_pages()
        self._current_key: str | None = None

        self.setWindowTitle("RentDesk Staff")
        self.resize(1180, 760)
        self.setStyleSheet(theme.load_stylesheet())

        self.stack = QStackedWidget(self)

        # The container is built once and the navbar and banner are inserted
        # into it on sign-in. Replacing the central widget each time would
        # mean reparenting `self.stack`, which is itself a child of the window
        # and cannot be moved while it is the central widget.
        self._chrome = QWidget(self)
        self._chrome.setObjectName("page")
        self._chrome_layout = QVBoxLayout(self._chrome)
        self._chrome_layout.setContentsMargins(0, 0, 0, 0)
        self._chrome_layout.setSpacing(0)
        self._chrome_layout.addWidget(self.stack, 1)
        self.setCentralWidget(self._chrome)

        #: Rebuilt per session, so a button for the wrong role can never be
        #: left over from the last sign-in.
        self._navbar: QFrame | None = None
        self._banner: QFrame | None = None

        self.login_view = LoginView(context, self._on_signed_in, self)
        self.stack.addWidget(self.login_view)

        #: Filled by `_build_navbar` on sign-in, so there is always somewhere to
        #: show an access error even when the navbar is not up.
        self._page_widgets: dict[str, QWidget] = {}

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(DASHBOARD_REFRESH_MS)
        self._refresh_timer.timeout.connect(self._on_refresh_tick)

        # Kept separately so sign-out can restore it. The title says which
        # database is open, and "RentDesk Staff" alone after a sign-out from
        # demo data would read as though the shell had switched to live.
        if context.selection.is_demo:
            self._title = "RentDesk Staff  -  DEMO DATA"
        else:
            self._title = f"RentDesk Staff  -  {context.selection.label}"
        self.setWindowTitle(self._title)

    # -- chrome -----------------------------------------------------------

    def _build_navbar(self) -> QFrame:
        """A fresh navbar for the current role.

        Rebuilt rather than reused so a button that is not allowed for this
        role cannot be left over from the last session.
        """
        navbar = QFrame(self)
        navbar.setObjectName(theme.OBJ_NAVBAR)
        navbar.setFixedHeight(58)

        layout = QHBoxLayout(navbar)
        layout.setContentsMargins(22, 0, 22, 0)
        layout.setSpacing(4)

        logo = QLabel("RentDesk", navbar)
        logo.setObjectName("logo")
        layout.addWidget(logo)

        self._nav_group = QButtonGroup(navbar)
        self._nav_group.setExclusive(True)

        for spec in self._visible_pages():
            button = QPushButton(spec.title, navbar)
            button.setObjectName(theme.OBJ_NAV_ITEM)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, key=spec.key: self.show_page(key))
            self._nav_group.addButton(button)
            layout.addWidget(button)
            button.setProperty("page_key", spec.key)

        layout.addStretch(1)

        self.nav_user = QLabel(self.context.display_name, navbar)
        self.nav_user.setObjectName("navUser")
        layout.addWidget(self.nav_user)

        if self.context.is_admin:
            role = QLabel("admin", navbar)
            role.setObjectName("navUser")
            layout.addWidget(role)

        sign_out = QPushButton("Sign out", navbar)
        sign_out.setObjectName("ghostButton")
        sign_out.clicked.connect(self.sign_out)
        layout.addWidget(sign_out)

        return navbar

    def _build_demo_banner(self) -> QFrame:
        banner = QFrame(self)
        banner.setObjectName(theme.OBJ_DEMO_BANNER)
        banner.setFixedHeight(28)
        text = QLabel(
            "  Demo data. Nothing here is real and nothing here is reconciled.  ",
            banner,
        )
        text.setObjectName("demoBannerText")
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(text)
        layout.addStretch(1)
        return banner

    # -- routing ----------------------------------------------------------

    def _visible_pages(self) -> list[PageSpec]:
        return [p for p in self.pages if p.admin_only is False or self.context.is_admin]

    def show_page(self, key: str) -> bool:
        """Switch to `key`. Returns False if the role does not allow it."""
        spec = next((p for p in self.pages if p.key == key), None)
        if spec is None:
            return False
        if spec.admin_only and not self.context.is_admin:
            toast(self, "That area is for administrators.", "error")
            return False

        widget = self._page_for(spec)
        try:
            refresh = getattr(widget, "refresh", None)
            if callable(refresh):
                refresh()
        except AccessDenied as exc:
            blocking_error(self, exc, title="Not available")
            return False
        except Exception as exc:  # noqa: BLE001
            blocking_error(self, exc, title="Could not open this page")
            return False

        self.stack.setCurrentWidget(widget)
        self._current_key = key
        self._sync_nav()
        return True

    def _page_for(self, spec: PageSpec) -> QWidget:
        widget = self._page_widgets.get(spec.key)
        if widget is None:
            widget = spec.factory(self)
            self._page_widgets[spec.key] = widget
            self.stack.addWidget(widget)
        return widget

    def _sync_nav(self) -> None:
        for button in self._nav_group.buttons():
            button.setChecked(button.property("page_key") == self._current_key)

    @property
    def current_page_key(self) -> str | None:
        return self._current_key

    # -- session ----------------------------------------------------------

    def _on_signed_in(self, user) -> None:
        """Build the pages and go to the first one."""
        self._clear_chrome()
        if self.context.selection.is_demo:
            self._banner = self._build_demo_banner()
            self._chrome_layout.insertWidget(0, self._banner)
        self._navbar = self._build_navbar()
        self._chrome_layout.insertWidget(self._chrome_layout.count() - 1, self._navbar)

        self._refresh_timer.start()
        first = self._visible_pages()
        if first:
            self.show_page(first[0].key)
        toast(self, f"Signed in as {self.context.display_name}.")

    def _clear_chrome(self) -> None:
        """Remove and destroy the navbar and banner.

        ``deleteLater`` rather than ``setParent(None)``: the old widgets would
        otherwise stay alive as hidden children of the container, each still
        holding a page's worth of stale buttons.
        """
        for widget in (self._banner, self._navbar):
            if widget is not None:
                self._chrome_layout.removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()
        self._banner = None
        self._navbar = None

    def sign_out(self) -> None:
        if not confirm(
            self,
            "Sign out of RentDesk Staff?",
            detail="Unsaved text in a form will be lost.",
        ):
            return

        self._refresh_timer.stop()
        self.context.sign_out()
        self._discard_pages()
        self._current_key = None
        self._clear_chrome()
        self.setWindowTitle(self._title)
        self.stack.setCurrentWidget(self.login_view)
        self.login_view.password.clear()
        self.login_view.error.setVisible(False)

    def _discard_pages(self) -> None:
        """Take every page out of the stack and let it go.

        Clearing `_page_widgets` on its own is not enough: the widgets stay in
        the `QStackedWidget` as hidden children, still holding the previous
        user's loaded rows. The next sign-in builds fresh pages, so the old
        ones would sit in the stack for the life of the process -- reachable,
        and holding data the person who just signed out had access to.
        """
        for key, widget in self._page_widgets.items():
            self.stack.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._page_widgets.clear()

    def _on_refresh_tick(self) -> None:
        """Re-read the current page's data.

        Only the visible page, and only if it opted in. Refreshing hidden pages
        would query the database seven times every 30 seconds for screens
        nobody is looking at.
        """
        if not self.context.is_signed_in or self._current_key is None:
            return
        spec = next((p for p in self.pages if p.key == self._current_key), None)
        if spec is None or not spec.auto_refresh:
            return
        widget = self._page_widgets.get(spec.key)
        refresh = getattr(widget, "refresh", None)
        if not callable(refresh):
            return
        try:
            refresh()
        except Exception:  # noqa: BLE001
            # A background refresh must never interrupt whatever the user is
            # doing. The page's own table shows the failure; a dialog here would
            # pop up unbidden every 30 seconds.
            pass

    # -- test seams --------------------------------------------------------

    def set_refresh_interval(self, ms: int) -> None:
        self._refresh_timer.setInterval(ms)


def default_pages() -> list[PageSpec]:
    """The shipped navigation.

    Imported lazily inside the factory lambdas so this module can be imported
    without pulling in every page's dependencies.
    """
    from app.staff.pages.bookings import build_bookings_page
    from app.staff.pages.customers import build_customers_page
    from app.staff.pages.dashboard import build_dashboard_page
    from app.staff.pages.fleet import build_fleet_page
    from app.staff.pages.inspections import build_inspections_page
    from app.staff.pages.payments import build_payments_page
    from app.staff.pages.today import build_today_page

    return [
        PageSpec("dashboard", "Dashboard", build_dashboard_page, auto_refresh=True),
        PageSpec("today", "Today", build_today_page, auto_refresh=True),
        PageSpec("bookings", "Bookings", build_bookings_page),
        PageSpec("fleet", "Fleet", build_fleet_page),
        PageSpec("customers", "Customers", build_customers_page),
        PageSpec("payments", "Payments", build_payments_page, auto_refresh=True),
        PageSpec("inspections", "Inspections", build_inspections_page),
    ]
