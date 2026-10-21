from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.database import SessionLocal
from app.services import auth_service, location_service
from app.services.errors import ServiceError
from app.ui.session import Identity
from app.ui.theme import (
    apply_theme,
    build_signup_form_panel,
    build_signup_hero_panel,
    build_split_layout,
)

#: Step-1 fields; errors on these slide back to the account-details page.
_STEP_ONE_FIELDS = {"full_name", "email", "phone", "password"}


class Signup(QMainWindow):
    """Create-account screen. Mirrors the sign-in window in customer_login.py.

    The card has two pages: account details, then address and licence. The
    first page's button slides to the second; only the second page submits.

    Parameters
    ----------
    on_back:
        Called when this window is closed and it was opened from the sign-in
        screen (so sign-in can un-hide itself). When None the window was run
        standalone, so the "Sign In" link opens a fresh sign-in window and
        the X button just quits as usual.
    on_success:
        Called with the new customer's `Identity` (app/ui/session.py) once the
        account exists. The booking gate passes it in, so a new customer
        carries straight on to the vehicle they were booking.
    """

    def __init__(
        self,
        on_back: Optional[Callable[[], None]] = None,
        on_success: Optional[Callable[[object], None]] = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Create Account")
        # Resizable / maximizable as normal. The right-panel card never
        # changes size when the window grows: build_signup_form_panel uses a
        # non-stretching scroll area whose widget keeps its natural size and
        # stays centred (see theme.py), so all the fields stay fixed on a
        # maximize. No minimum is forced here -- the scroll area's own
        # minimum size already prevents the form from being squished.
        self.resize(1280, 980)
        self._on_back = on_back
        self._on_success = on_success
        self._login_window = None  # keeps a standalone sign-in window alive
        self._app_window = None  # keeps a standalone customer app alive

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        # The province/municipality lists come from the live PSGC tables. A
        # database hiccup must not stop the window from opening: the form
        # shows the reason under the province box and stays unsubmittable.
        province_options = []
        location_error = ""
        try:
            with SessionLocal() as session:
                province_options = location_service.province_options(session)
        except Exception as exc:  # noqa: BLE001 - the database is remote
            location_error = f"Could not load the province list: {exc}"

        self.form = build_signup_form_panel(
            province_options=province_options,
            cities_for=self._cities_for,
            location_error=location_error,
        )
        build_split_layout(root, build_signup_hero_panel(), self.form)

        # Step 1: the card only validates; this window runs the slide.
        self.form.create_btn.clicked.connect(self._next_step)
        self.form.back_link.clicked.connect(self._back_to_details)
        self.form.pager.settled.connect(self._step_settled)

        # Step 2: this is the button that actually creates the account.
        self.form.finish_btn.clicked.connect(self._create_account)

        # "Sign in" link at the bottom of the card -> back to sign-in
        self.form.signin_link.clicked.connect(self._go_back)

        # Animate the password requirement rows as you type.
        self.form.password.textChanged.connect(self.form.update_password_state)

    # ----- step navigation -------------------------------------------------
    def _next_step(self) -> None:
        self.form.error.hide()
        self.form.step2_error.hide()
        self.form.pager.slide_to(1)

    def _back_to_details(self) -> None:
        self.form.step2_error.hide()
        self.form.pager.slide_to(0)

    def _step_settled(self, index: int) -> None:
        self.form.verticalScrollBar().setValue(0)
        if index == 1:
            self.form.province.setFocus()
        else:
            self.form.first_name.setFocus()

    def _cities_for(self, province_key: str):
        """Province change -> its cities/municipalities, on demand."""
        with SessionLocal() as session:
            return location_service.municipalities(session, province_key)

    # ----- creating the account -------------------------------------------
    def _create_account(self) -> None:
        """Register the customer, then hand them back to whoever asked."""
        self.form.refresh_address_validation()
        if not self.form.address_ready():
            self._show_step_two_error(
                "Choose your province and city/municipality, then complete "
                "the street address and licence number."
            )
            return

        full_name = " ".join(
            part
            for part in (
                self.form.first_name.text().strip(),
                self.form.last_name.text().strip(),
            )
            if part
        )
        email = self.form.email.text().strip()
        phone = self.form.mobile.text().strip()
        password = self.form.password.text()
        address = self.form.address_values()
        self.form.error.hide()
        self.form.step2_error.hide()

        try:
            with SessionLocal() as session:
                user = auth_service.register_customer(
                    session,
                    full_name=full_name,
                    email=email,
                    phone=phone,
                    password=password,
                    street_address=address["street_address"],
                    province_key=address["province_key"],
                    city_municipality_code=address["city_municipality_code"],
                    license_number=address["license_number"],
                )
                identity = Identity.of(user)
                session.commit()
        except ServiceError as exc:
            self._show_service_error(exc)
            return
        except Exception as exc:  # noqa: BLE001 - the database is remote
            self._show_service_error(exc)
            return

        if self._on_success is not None:
            # The gate is still open and now has its customer: closing must
            # not reopen the sign-in window underneath it.
            self._on_back = None
            self.close()
            self._on_success(identity)
            return

        # Standalone sign-up (or sign-up opened from a standalone sign-in):
        # there is nobody to hand the identity to, so continue into the app
        # the same way customer_login does.
        self._open_app(identity)

    def _open_app(self, identity) -> None:
        from app.ui.main_window import MainWindow

        self._app_window = MainWindow(identity=identity)
        self._app_window.show()
        self.hide()

    def _show_service_error(self, exc: Exception) -> None:
        """Put a service error on the page that owns the offending field."""
        message = str(exc)
        field = getattr(exc, "field", None)
        if field in _STEP_ONE_FIELDS or "email" in message.lower():
            self._show_error(message)
            self.form.pager.slide_to(0)
            return
        if field in (
            "street_address",
            "province_code",
            "city_municipality_code",
            "license_number",
        ) or "licence" in message.lower():
            self._show_step_two_error(message)
            return
        # Unknown failure (database/network): show it where the user is.
        if self.form.pager.current_index() == 1:
            self._show_step_two_error(message)
        else:
            self._show_error(message)

    def _show_error(self, message: str) -> None:
        self.form.error.setText(message)
        self.form.error.show()

    def _show_step_two_error(self, message: str) -> None:
        self.form.step2_error.setText(message)
        self.form.step2_error.show()

    # ----- navigation back to sign-in -----------------------------------
    def _go_back(self) -> None:
        if self._on_back is not None:
            self.close()  # closeEvent hands control back to sign-in
        else:
            self._open_signin_window()

    def _open_signin_window(self) -> None:
        from app.ui.customer_login import Login

        self._login_window = Login()
        self._login_window.show()
        self.close()

    def suppress_back(self) -> None:
        """Used by sign-in when it is shutting down: closing this window
        must not try to bring sign-in back."""
        self._on_back = None

    def closeEvent(self, event) -> None:
        # The X button and the "Sign In" link both land here, so closing the
        # window always returns to sign-in instead of leaving nothing open.
        super().closeEvent(event)
        on_back, self._on_back = self._on_back, None
        if on_back is not None:
            on_back()


def main() -> None:
    app = QApplication(sys.argv)
    apply_theme(app)
    window = Signup()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
