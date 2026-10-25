from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.database import SessionLocal
from app.services import auth_service
from app.services.auth_service import AuthError
from app.ui.session import Identity
from app.ui.theme import (
    apply_theme,
    build_form_panel,
    build_hero_panel,
    build_split_layout,
)


class Login(QMainWindow):
    """The standalone sign-in window.

    `on_success` is how this window hands the customer back to whoever asked
    for a sign-in -- the booking gate passes it in (app/ui/auth_gate_dialog.py)
    so the customer keeps the vehicle they were booking. Without it the window
    signs the customer into the app itself.
    """

    def __init__(self, on_success=None) -> None:
        super().__init__()
        self.setWindowTitle("Login")
        self.resize(1280, 800)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        form = build_form_panel()
        build_split_layout(root, build_hero_panel(), form)
        self.form = form
        self._on_success = on_success
        self._app_window = None

        # The card only validates; this window decides what Sign In does.
        form.submit_handler = self._attempt_sign_in

        # Keeps a strong reference so the sign-up window is not garbage
        # collected while it is open.
        self.signup_window = None

        # "Sign Up" link at the bottom of the card -> open the sign-up screen
        form.signup.clicked.connect(self._open_signup)
        form.forgot.clicked.connect(self._open_forgot_password)
        self.forgot_dialog = None

    # ----- signing in -----------------------------------------------------
    def _attempt_sign_in(self) -> None:
        email = self.form.email.text().strip()
        password = self.form.password.text()
        try:
            with SessionLocal() as session:
                user = auth_service.sign_in_customer(session, email, password)
                identity = Identity.of(user)
        except AuthError as exc:
            self._show_error(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - the database is remote
            self._show_error(f"Could not sign you in: {exc}")
            return

        self.form.error.hide()
        if self._on_success is not None:
            self._on_success(identity)
            return
        self._open_app(identity)

    def _show_error(self, message: str) -> None:
        self.form.error.setText(message)
        self.form.error.setProperty("tone", "danger")
        self.form.error.style().unpolish(self.form.error)
        self.form.error.style().polish(self.form.error)
        self.form.error.show()

    def _open_app(self, identity: Identity) -> None:
        """Standalone run: sign in straight into the customer app."""
        from app.ui.main_window import MainWindow

        window = MainWindow(identity=identity)
        self._app_window = window
        window.show()
        self.hide()

    def _open_forgot_password(self) -> None:
        from PySide6.QtWidgets import QDialog

        from app.ui.forgot_password import ForgotPasswordDialog

        dialog = ForgotPasswordDialog(
            self, email=self.form.email.text().strip()
        )
        self.forgot_dialog = dialog
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.reset_completed:
            self.form.email.setText(dialog.account_email)
            self.form.password.clear()
            self._show_success(
                "Password updated. Sign in with your new password."
            )
            self.form.password.setFocus()
        self.forgot_dialog = None

    def _show_success(self, message: str) -> None:
        label = self.form.error
        label.setText(message)
        label.setProperty("tone", "green")
        label.style().unpolish(label)
        label.style().polish(label)
        label.show()

    def _open_signup(self) -> None:
        from app.ui.signup import Signup

        self.signup_window = Signup(
            on_back=self.show, on_success=self._on_success
        )
        # showNormal() + raise_()/activateWindow() make sure the new window
        # opens on top and focused instead of minimized or lost behind other
        # windows when this one hides.
        self.signup_window.showNormal()
        self.signup_window.raise_()
        self.signup_window.activateWindow()
        self.hide()  # hide, don't close, so we can come back to it

    def closeEvent(self, event) -> None:
        # Closing sign-in while the sign-up screen is open: tell signup not to
        # call back, otherwise it would re-open this window as it shuts down.
        if self.signup_window is not None:
            self.signup_window.suppress_back()
            self.signup_window.close()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    apply_theme(app)
    window = Login()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()