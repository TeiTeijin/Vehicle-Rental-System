"""The staff window: sidebar, routing, and the sign-in gate.

Structure is a ``QStackedWidget`` with login at index 0 and the pages after
it, so signing in and out is a page switch rather than a teardown. The chrome
around the stack -- sidebar, demo banner -- is rebuilt per session, and the
page widgets themselves are discarded on sign-out: a page that outlived the
session would still hold the previous user's loaded rows.

The sidebar is rebuilt from :data:`default_pages` on every sign-in and sign-out
rather than being created once, because the visible set depends on the role and
a stale row is a way to reach a page you should not be able to see. Each page
also re-checks its own permission on activation, so even a constructed-
but-hidden page cannot be shown to the wrong role.

The window is resizable at every stage: the sign-in form opens at its own
smaller size and the dashboard grows to :data:`metrics.WINDOW_MIN_W` by
:data:`metrics.WINDOW_MIN_H`, but nothing is pinned to a fixed size, so the
window can always be dragged. The pages decide for themselves how to reflow at
the width they are given, and the page base scrolls when the height runs out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.staff import metrics, theme
from app.staff.context import AccessDenied, StaffContext
from app.staff.login import LOGIN_SIZE, LoginView
from app.staff.sidebar import Sidebar
from app.staff.widgets import blocking_error, confirm, toast

#: Dashboard re-read interval; overridable so tests need not wait.
DASHBOARD_REFRESH_MS = 30_000


#: Sidebar groups, in order.
NAV_SECTIONS: tuple[str, ...] = ("General", "Tools")


@dataclass(frozen=True)
class PageSpec:
    """One sidebar entry.

    `admin_only` hides the row for staff; the page then re-checks the role on
    activation, so the check exists in two places on purpose.

    `icon` names a glyph in `app.staff.icons`. It defaults rather than being
    required because a `PageSpec` is also what a test builds to exercise
    routing, and a test page should not have to pick a glyph; shipping a page
    with the wrong glyph is a one-line fix, shipping one with none is a row of
    leading-edge whitespace.
    """

    key: str
    title: str
    factory: Callable[["StaffShell"], QWidget]
    icon: str = "dashboard"
    admin_only: bool = False
    #: Pages that hold live figures refresh themselves on the dashboard timer.
    auto_refresh: bool = False
    #: Which sidebar group the row sits in.
    section: str = NAV_SECTIONS[0]


#: Brief's rows with no screen behind them: key -> (label, icon, group).
STUB_SECTIONS: dict[str, tuple[str, str, str]] = {
    "reports": ("Reports", "reports", "General"),
    "billing": ("Billing", "billing", "Tools"),
    "maintenance": ("Maintenance", "maintenance", "Tools"),
    "settings": ("Settings", "settings", "Tools"),
}


class StaffShell(QMainWindow):
    """The staff application window."""

    def __init__(self, context: StaffContext, pages: list[PageSpec] | None = None) -> None:
        super().__init__()
        self.context = context
        self.pages = list(pages) if pages is not None else default_pages()
        self._current_key: str | None = None

        self.setWindowTitle("rentwheels")
        #: Size the window opens at; re-applied on sign-in.
        self._windowed_size = (metrics.REFERENCE_W, metrics.REFERENCE_H)
        self.resize(*LOGIN_SIZE)
        self.setMinimumSize(*LOGIN_SIZE)
        self.setStyleSheet(theme.load_stylesheet())

        self.stack = QStackedWidget(self)

        self._chrome = QWidget(self)
        self._chrome.setObjectName("page")
        self._chrome_layout = QVBoxLayout(self._chrome)
        self._chrome_layout.setContentsMargins(0, 0, 0, 0)
        self._chrome_layout.setSpacing(0)

        self._body = QWidget(self._chrome)
        self._body_layout = QHBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(0)
        self._body_layout.addWidget(self.stack, 1)

        self._chrome_layout.addWidget(self._body, 1)
        self.setCentralWidget(self._chrome)

        #: Rebuilt per session so no wrong-role row survives.
        self._sidebar: Sidebar | None = None
        self._banner: QFrame | None = None

        self.login_view = LoginView(context, self._on_signed_in, self)
        self.stack.addWidget(self.login_view)

        #: Filled lazily by `_page_for`.
        self._page_widgets: dict[str, QWidget] = {}

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(DASHBOARD_REFRESH_MS)
        self._refresh_timer.timeout.connect(self._on_refresh_tick)

        if context.selection.is_demo:
            self._title = "rentwheels  -  DEMO DATA"
        else:
            self._title = f"rentwheels  -  {context.selection.label}"
        self.setWindowTitle(self._title)

    # -- chrome -----------------------------------------------------------

    def _build_sidebar(self) -> Sidebar:
        """A fresh sidebar for the current role.

        Rebuilt rather than reused so a row that is not allowed for this role
        cannot be left over from the last session.
        """
        sidebar = Sidebar(parent=self)
        sidebar.page_requested.connect(self.show_page)
        sidebar.sign_out_requested.connect(self.sign_out)
        sidebar.stub_requested.connect(self._on_stub_requested)
        sidebar.build(self._sidebar_sections())
        return sidebar

    def _sidebar_sections(self) -> list[tuple[str, list[tuple[str, str, str, bool]]]]:
        """`[(group label, [(key, label, icon, navigable)])]`, in order.

        Real pages carry `enabled=True`. The brief lists Billing, Maintenance and
        Settings as destinations and this build has no screen for any of them,
        so they appear as rows that report themselves on press rather than
        navigating to nothing. Dropping them would have been the alternative and
        it would make the sidebar look like a different application.

        Groups come from `NAV_SECTIONS` and both lists are walked in order, so
        the sidebar reads top-to-bottom in declaration order rather than in
        whichever order a dict happened to hash.

        Admin-only pages are omitted entirely for staff, which is why the sidebar
        is rebuilt per session rather than mutated in place.
        """
        rows: dict[str, list[tuple[str, str, str, bool]]] = {
            group: [] for group in NAV_SECTIONS
        }
        unknown: set[str] = set()

        for spec in self.pages:
            if spec.admin_only and not self.context.is_admin:
                continue
            group = rows.get(spec.section)
            if group is None:
                unknown.add(spec.section)
                continue
            group.append((spec.key, spec.title, spec.icon, True))

        for key, (label, icon, section) in STUB_SECTIONS.items():
            group = rows.get(section)
            if group is None:
                unknown.add(section)
                continue
            group.append((key, label, icon, False))

        if unknown:
            raise ValueError(
                f"sidebar has no group named {sorted(unknown)}; "
                f"known groups are {list(NAV_SECTIONS)}"
            )

        return [(group, rows[group]) for group in NAV_SECTIONS if rows[group]]

    def _on_stub_requested(self, label: str, reason: str) -> None:
        toast(self, f"{label} is {reason}.", "error")

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
        if self._sidebar is not None:
            self._sidebar.set_current(self._current_key)

    @property
    def current_page_key(self) -> str | None:
        return self._current_key

    # -- session ----------------------------------------------------------

    def _on_signed_in(self, user) -> None:
        """Build the pages and go to the first one."""
        self.setMinimumSize(metrics.WINDOW_MIN_W, metrics.WINDOW_MIN_H)
        self.resize(*self._windowed_size)
        self._clear_chrome()
        if self.context.selection.is_demo:
            self._banner = self._build_demo_banner()
            self._chrome_layout.insertWidget(0, self._banner)
        self._sidebar = self._build_sidebar()
        self._body_layout.setContentsMargins(
            metrics.SIDEBAR_MARGIN,
            metrics.SIDEBAR_MARGIN,
            0,
            metrics.SIDEBAR_MARGIN,
        )
        self._body_layout.setSpacing(metrics.SIDEBAR_MARGIN)
        self._body_layout.insertWidget(0, self._sidebar)

        self._refresh_timer.start()
        first = self._visible_pages()
        if first:
            self.show_page(first[0].key)
        toast(self, f"Signed in as {self.context.display_name}.")

    def _clear_chrome(self) -> None:
        """Remove and release the sidebar and banner.

        ``setParent(None)`` *without* ``deleteLater``. Doing both is a double
        free: reparenting hands the C++ widget to Python, and the deferred
        delete is then posted for an object Python may already have destroyed
        once the reference below is dropped. Orphan the widget and drop the
        reference -- the last reference going frees it exactly once -- and the
        old chrome cannot be reachable from the window in the meantime.
        """
        if self._banner is not None:
            self._chrome_layout.removeWidget(self._banner)
            self._banner.setParent(None)
        if self._sidebar is not None:
            self._body_layout.removeWidget(self._sidebar)
            self._sidebar.setParent(None)
        self._banner = None
        self._sidebar = None
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(0)

    def sign_out(self) -> None:
        if not confirm(
            self,
            "Sign out of RentWheels Staff?",
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
        self.setMinimumSize(*LOGIN_SIZE)
        self.resize(*LOGIN_SIZE)
        self.login_view.password.clear()
        self.login_view.error.setVisible(False)

    def _discard_pages(self) -> None:
        """Take every page out of the stack and let it go.

        Clearing `_page_widgets` on its own is not enough: the widgets stay in
        the `QStackedWidget` as hidden children, still holding the previous
        user's loaded rows. The next sign-in builds fresh pages, so the old
        ones would sit in the stack for the life of the process -- reachable,
        and holding data the person who just signed out had access to.

        ``setParent(None)``, not ``deleteLater``: the widget is orphaned from
        the stack immediately and freed when the last Python reference drops,
        so it is destroyed once. The two together hand the object to Python and
        post a deferred delete for it as well, which can free it twice.
        """
        for key, widget in self._page_widgets.items():
            self.stack.removeWidget(widget)
            widget.setParent(None)
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
        PageSpec(
            "dashboard",
            "Dashboard",
            build_dashboard_page,
            icon="dashboard",
            auto_refresh=True,
        ),
        PageSpec(
            "today",
            "New Rental",
            build_today_page,
            icon="pos",
            auto_refresh=True,
        ),
        PageSpec("bookings", "Bookings", build_bookings_page, icon="bookings"),
        PageSpec("fleet", "Fleet", build_fleet_page, icon="vehicle"),
        PageSpec("customers", "Customers", build_customers_page, icon="customers"),
        PageSpec(
            "payments",
            "Payments",
            build_payments_page,
            icon="card",
            auto_refresh=True,
        ),
        PageSpec(
            "inspections", "Inspections", build_inspections_page, icon="check"
        ),
    ]


def sidebar_page_keys(pages: list[PageSpec] | None = None) -> list[str]:
    """The keys the sidebar shows for a staff member, in sidebar order.

    Admin-only pages are excluded, matching what `Sidebar` is given at sign-in.
    The keys that are *not* in this list are the stub rows, which exist in the
    sidebar but navigate nowhere.
    """
    specs = default_pages() if pages is None else pages
    return [s.key for s in specs if not s.admin_only]
