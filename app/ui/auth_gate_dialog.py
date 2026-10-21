"""Screen 2: the sign-in gate shown when a guest tries to book.

The customer can browse and price a vehicle without an account; the moment
they ask to book, this modal asks who they are, because a booking belongs to
somebody. Signing in here returns to exactly the step the customer was on --
the router re-opens the booking flow with the same vehicle (see
app/ui/main_window.py).

It is the same card look as the standalone sign-in screen (app/ui/
customer_login.py), only inside a dimmed overlay instead of a whole window:
the modal has to feel like a step of the booking, not a detour away from it.

The three ways out, all of them used by the router:

    AuthGateDialog.exec() == Accepted   -> `identity` holds the customer
    Create account                      -> `signup_requested` (router opens
                                           app/ui/signup.py and comes back)
    Close / Esc                         -> rejected; the router stays put
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget

from app.database import SessionLocal
from app.services import auth_service
from app.services.auth_service import InvalidCredentials
from app.services.errors import DataUnavailable
from app.ui import theme
from app.ui.session import Identity
from app.ui.theme import field, hint_label, make_label, primary_button, text_button


class AuthGateDialog(theme.OverlayDialog):
    """Modal sign-in. `identity` is set only when the sign-in succeeded."""

    signup_requested = Signal()

    def __init__(
        self,
        parent: QWidget,
        *,
        vehicle_name: str = "",
        reason: str = "",
        title: str = "Sign in to book",
    ) -> None:
        super().__init__(parent, object_name="overlayCard", card_width=440)
        self.identity: Identity | None = None

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(make_label(title, "overlayTitle"), 1)
        head.addWidget(self.close_button(), 0, Qt.AlignTop)

        self.column.addLayout(head)
        self.column.addWidget(
            make_label(
                reason
                or (
                    f"Your booking for {vehicle_name} is saved to your account, "
                    "so the counter can find you at pickup."
                    if vehicle_name
                    else "Your bookings live in your account, so the counter "
                    "can find you at pickup."
                ),
                "overlaySubtitle",
                wrap=True,
            )
        )
        self.column.addSpacing(8)

        email_holder = field("Email address", "you@email.com")
        self.email = email_holder.edit
        self.column.addWidget(email_holder)

        password_holder = field("Password", "Password", password=True)
        self.password = password_holder.edit
        self.password.returnPressed.connect(self.submit)
        self.column.addWidget(password_holder)

        self.error = hint_label("", tone="danger")
        self.error.hide()
        self.column.addWidget(self.error)

        self.submit_button = primary_button("SIGN IN")
        self.submit_button.clicked.connect(self.submit)
        self.column.addWidget(self.submit_button)

        self.column.addSpacing(4)
        links = QHBoxLayout()
        links.setContentsMargins(0, 0, 0, 0)
        links.addWidget(make_label("New customer?", "overlayNote"))
        signup = text_button("Create an account")
        signup.clicked.connect(self.signup_requested)
        links.addWidget(signup)
        links.addStretch(1)
        support = text_button("Need help?")
        support.clicked.connect(self._help)
        links.addWidget(support)
        self.column.addLayout(links)

        self.email.setFocus()

    # ---------- signing in ----------
    def submit(self) -> None:
        email = self.email.text().strip()
        password = self.password.text()
        if not email or not password:
            self._fail("Enter your email address and password.")
            return

        self.submit_button.setEnabled(False)
        try:
            with SessionLocal() as session:
                user = auth_service.sign_in(session, email, password)
                identity = Identity.of(user)
        except InvalidCredentials as exc:
            self._fail(str(exc))
        except DataUnavailable as exc:
            self._fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - the database is remote
            self._fail(f"Could not sign you in: {exc}")
        else:
            self.identity = identity
            self.accept()
        finally:
            self.submit_button.setEnabled(True)

    def _fail(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()
        self.password.selectAll()
        self.password.setFocus()

    def signed_in(self, identity: Identity) -> None:
        """The sign-up window finished: carry this customer back to the caller.

        Connected to Signup's `on_success`, so a brand-new account behaves
        exactly like a sign-in from this dialog.
        """
        self.identity = identity
        self.accept()

    def _help(self) -> None:
        self.error.setText(
            "Support can reset your password: support@rentwheels.local, "
            "or use the counter's help desk during branch hours."
        )
        self.error.setProperty("tone", "muted")
        self.error.style().unpolish(self.error)
        self.error.style().polish(self.error)
        self.error.show()


__all__ = ["AuthGateDialog"]
