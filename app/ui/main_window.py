"""The customer app: one window, a top bar, and every screen behind it.

Run it with:

    python -m app.ui.main_window

The window is the only piece of the flow that knows the order of things:

    Vehicles (dashboard)  ->  Vehicle details (screen 1)
        ->  sign-in gate (screen 2, only for a guest)
        ->  Dates & time (screen 3)  ->  Payment (screen 4)
        ->  Bookings (the new booking, on the Upcoming pill)
        ->  Payments (the receipts that belong to the account)

Screens 3 and 4 share one `BookingDraft` (app/ui/session.py): the router keeps
it while the customer walks between them, which is what makes BACK from the
checkout land on the same dates, times and branches.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.database import SessionLocal
from app.models import Vehicle
from app.services import notification_service
from app.ui import theme
from app.ui.account_popup import AccountPopup
from app.ui.auth_gate_dialog import AuthGateDialog
from app.ui.booking_checkout import BookingCheckoutPage
from app.ui.booking_schedule import BookingSchedulePage
from app.ui.bookings import BookingsPage
from app.ui.notifications import NotificationPopup
from app.ui.payments import PaymentsPage
from app.ui.profile import ProfilePage
from app.ui.session import BookingDraft, CustomerSession, Identity
from app.ui.vehicle_details import VehicleDetailsPage
from app.ui.home import HomePage

#: Tabs that belong to an account: a guest meets the sign-in gate first.
MEMBER_TABS = frozenset({"bookings", "payments"})

#: What the gate says when a guest taps one of those tabs.
MEMBER_REASONS = {
    "bookings": "Your bookings and pickup details live in your account.",
    "payments": "Your receipts and payment history live in your account.",
}


class MainWindow(QMainWindow):
    """The router. Owns the session, the top bar and the page stack."""

    def __init__(self, identity: Identity | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("RentWheels")
        self.resize(1440, 940)

        self.session = CustomerSession()
        if identity is not None:
            self.session.sign_in(identity)

        self._pages: dict[str, QWidget] = {}
        self._signup_window = None
        self._active_tab = "vehicles"
        self._notifications: list[notification_service.Notification] = []
        self._read_notification_keys: set[str] = set()
        self._notification_popup: NotificationPopup | None = None
        self._account_popup: AccountPopup | None = None

        root = QWidget()
        root.setObjectName("root")
        column = QVBoxLayout(root)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        self.navbar = theme.NavBar()
        self.navbar.tab_selected.connect(self.show_tab)
        self.navbar.auth_clicked.connect(self._on_auth_clicked)
        self.navbar.notifications_clicked.connect(self._on_notifications_clicked)
        self.navbar.search_changed.connect(self._on_search)
        column.addWidget(self.navbar)

        self.stack = QStackedWidget()
        column.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        # ----- the standing tabs -----
        self.vehicles_page = HomePage()
        self.vehicles_page.vehicle_selected.connect(self.open_vehicle)
        self.vehicles_page.book_requested.connect(self.start_booking)
        self.bookings_page = BookingsPage()
        self.bookings_page.vehicle_selected.connect(self.open_vehicle)
        self.payments_page = PaymentsPage()
        self.payments_page.vehicle_selected.connect(self.open_vehicle)
        self.profile_page = ProfilePage()
        self.profile_page.tab_requested.connect(self.show_tab)
        self.profile_page.profile_updated.connect(self._profile_updated)
        self._register("vehicles", self.vehicles_page)
        self._register("bookings", self.bookings_page)
        self._register("payments", self.payments_page)
        self._register("profile", self.profile_page)

        self.bookings_page.set_user(self.session.user)
        self.payments_page.set_user(self.session.user)
        self.profile_page.set_user(self.session.user)
        self._sync_chrome()
        self._refresh_notifications()
        self.show_tab("vehicles")

    # ---------- the stack ----------
    def _register(self, key: str, page: QWidget) -> None:
        self._pages[key] = page
        self.stack.addWidget(page)

    def _show_page(self, key: str) -> None:
        page = self._pages.get(key)
        if page is not None:
            self.stack.setCurrentWidget(page)

    def show_tab(self, key: str) -> None:
        """A top bar tab was clicked (or the router sent the customer there)."""
        if key in MEMBER_TABS and not self.session.is_signed_in:
            if not self._prompt_sign_in(reason=MEMBER_REASONS[key]):
                # Closed the gate: stay where we were, tab strip included.
                self.navbar.set_active(self._active_tab)
                return
        if key == "bookings":
            self.bookings_page.set_user(self.session.user)
            self._refresh_notifications()
            self._show_page("bookings")
        elif key == "payments":
            self.payments_page.set_user(self.session.user)
            self._refresh_notifications()
            self._show_page("payments")
        else:
            self._show_page("vehicles")
        self._active_tab = key
        self.navbar.set_active(key)

    def show_profile(self) -> None:
        """Open the account information page from the avatar menu."""
        if not self.session.is_signed_in:
            if not self._prompt_sign_in(
                reason="Sign in to see the details saved to your account."
            ):
                self.navbar.set_active(self._active_tab)
                return
        self.profile_page.set_user(self.session.user)
        self._show_page("profile")
        self._active_tab = "profile"
        # The profile strip owns Personal / Bookings / Payments; no top-bar
        # tab is the current screen, so none of them should stay underlined.
        self.navbar.clear_active()

    # ---------- screen 1 ----------
    def open_vehicle(self, vehicle_id: int) -> None:
        page = VehicleDetailsPage(vehicle_id)
        page.back_requested.connect(lambda: self.show_tab("vehicles"))
        page.book_requested.connect(self.start_booking)
        self._swap("details", page)
        self._active_tab = "vehicles"
        self.navbar.set_active("vehicles")

    # ---------- screen 2: only a guest sees it ----------
    def start_booking(self, vehicle_id: int) -> None:
        if not self.session.is_signed_in and not self._ask_sign_in(vehicle_id):
            return  # closed the modal: stay on the vehicle page
        self._open_schedule(BookingDraft(vehicle_id=vehicle_id))

    def _ask_sign_in(self, vehicle_id: int) -> bool:
        return self._prompt_sign_in(
            vehicle_name=self._vehicle_name(vehicle_id), title="Sign in to book"
        )

    def _prompt_sign_in(
        self,
        *,
        vehicle_name: str = "",
        reason: str = "",
        title: str = "Sign in to continue",
    ) -> bool:
        """The gate's three ways out; True only when a customer signed in."""
        dialog = AuthGateDialog(
            self, vehicle_name=vehicle_name, reason=reason, title=title
        )
        dialog.signup_requested.connect(lambda: self._open_signup(dialog))
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if accepted and dialog.identity is not None:
            self._sign_in(dialog.identity)
        return accepted and dialog.identity is not None

    def _open_signup(self, gate: AuthGateDialog) -> None:
        """'Create an account' on the gate. A new customer = a signed-in one."""
        from app.ui.signup import Signup

        window = Signup(on_success=gate.signed_in)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._signup_window = window
        window.showNormal()
        window.raise_()
        window.activateWindow()

    def _vehicle_name(self, vehicle_id: int) -> str:
        try:
            with SessionLocal() as session:
                vehicle = session.get(Vehicle, vehicle_id)
                if vehicle is None:
                    return ""
                return f"{vehicle.make} {vehicle.model}".strip()
        except Exception:  # noqa: BLE001 - the modal can live without the name
            return ""

    # ---------- screen 3 ----------
    def _open_schedule(self, draft: BookingDraft) -> None:
        page = BookingSchedulePage(draft)
        page.back_requested.connect(lambda: self.open_vehicle(draft.vehicle_id))
        page.continue_requested.connect(self._open_checkout)
        self._swap("schedule", page)

    # ---------- screen 4 ----------
    def _open_checkout(self, draft: BookingDraft) -> None:
        page = BookingCheckoutPage(draft, user_id=self.session.user_id)
        page.back_requested.connect(lambda: self._open_schedule(draft))
        page.booked.connect(self._on_booked)
        self._swap("checkout", page)

    def _on_booked(self, draft: BookingDraft) -> None:
        """The receipt was dismissed: show it in the list it now belongs to."""
        self.bookings_page.set_user(self.session.user)
        self.bookings_page.focus_bucket("upcoming")
        # The payment tab sits right after Bookings and is now up to date; the
        # customer lands on Bookings, then can step across to the receipt.
        self.payments_page.set_user(self.session.user)
        self.profile_page.set_user(self.session.user)
        self._refresh_notifications()
        self._show_page("bookings")
        self._active_tab = "bookings"
        self.navbar.set_active("bookings")

    def _swap(self, key: str, page: QWidget) -> None:
        """Replace one of the stepped screens (details / schedule / checkout)."""
        old = self._pages.get(key)
        if old is not None:
            self.stack.removeWidget(old)
            old.deleteLater()
        self._register(key, page)
        self._show_page(key)

    # ---------- the auth corner ----------
    def _sign_in(self, identity: Identity) -> None:
        self.session.sign_in(identity)
        self._read_notification_keys.clear()
        self._sync_chrome()
        self.bookings_page.set_user(identity)
        self.payments_page.set_user(identity)
        self.profile_page.set_user(identity)
        self._refresh_notifications()

    def _sign_out(self) -> None:
        self._close_account_popup()
        self._close_notification_popup()
        self.session.sign_out()
        self._read_notification_keys.clear()
        self._notifications = []
        self._sync_chrome()
        self.navbar.set_notification_state(False, 0)
        self.bookings_page.set_user(None)
        self.payments_page.set_user(None)
        self.profile_page.set_user(None)
        self.show_tab("vehicles")

    def _profile_updated(self, identity: Identity) -> None:
        """A customer edited their own details: refresh the account chip."""
        self.session.sign_in(identity)
        self._sync_chrome()
        self.bookings_page.set_user(identity)
        self.payments_page.set_user(identity)

    def _sync_chrome(self) -> None:
        self.navbar.set_user(self.session.display_name or None)

    # ---------- notifications ----------
    def _refresh_notifications(self) -> None:
        """Re-read booking-derived updates; never let a query break the app."""
        if not self.session.is_signed_in:
            self._notifications = []
            self.navbar.set_notification_state(False, 0)
            return
        try:
            with SessionLocal() as session:
                self._notifications = notification_service.notifications_for(
                    session, self.session.user_id
                )
        except Exception:  # noqa: BLE001 - the database is remote
            self._notifications = []

        unread = [
            item
            for item in self._notifications
            if item.key not in self._read_notification_keys
        ]
        self.navbar.set_notification_state(bool(unread), len(unread))

    def _on_notifications_clicked(self) -> None:
        if not self.session.is_signed_in:
            return
        self._refresh_notifications()
        unread_keys = {
            item.key
            for item in self._notifications
            if item.key not in self._read_notification_keys
        }
        self._close_notification_popup()

        popup = NotificationPopup(
            self,
            self._notifications,
            unread_keys=unread_keys,
        )
        popup.bookings_requested.connect(self._show_bookings_from_notification)
        popup.dismissed.connect(self._forget_notification_popup)
        self._notification_popup = popup
        popup.show_under(self.navbar.notifications_button)

        # Opening the panel is the "read" action; the unread tint inside the
        # panel still shows which items were new when it opened.
        self._read_notification_keys.update(
            item.key for item in self._notifications
        )
        self.navbar.set_notification_state(False, 0)

    def _show_bookings_from_notification(self) -> None:
        """Open Bookings from a notification row."""
        self.show_tab("bookings")

    def _forget_notification_popup(self) -> None:
        self._notification_popup = None

    def _close_notification_popup(self) -> None:
        popup = self._notification_popup
        self._notification_popup = None
        if popup is not None:
            try:
                popup.close()
            except RuntimeError:  # already deleted by Qt
                pass

    def _on_auth_clicked(self) -> None:
        if not self.session.is_signed_in:
            self._prompt_sign_in()
            return

        self._close_account_popup()
        identity = self.session.user
        if identity is None:
            return
        popup = AccountPopup(
            self,
            name=identity.full_name,
            email=identity.email,
        )
        popup.profile_requested.connect(self.show_profile)
        popup.logout_requested.connect(self._sign_out)
        popup.destroyed.connect(
            lambda _obj=None, p=popup: self._forget_account_popup(p)
        )
        self._account_popup = popup
        popup.show_under(self.navbar.auth_button)

    def _forget_account_popup(self, popup: AccountPopup) -> None:
        if self._account_popup is popup:
            self._account_popup = None

    def _close_account_popup(self) -> None:
        popup = self._account_popup
        self._account_popup = None
        if popup is not None:
            try:
                popup.close()
            except RuntimeError:  # already deleted by Qt
                pass

    # ---------- search ----------
    def _on_search(self, text: str) -> None:
        self.vehicles_page.set_search(text)
        if text.strip() and self.stack.currentWidget() is not self.vehicles_page:
            self.show_tab("vehicles")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    theme.apply_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["MainWindow", "main"]
