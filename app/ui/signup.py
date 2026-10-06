from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.ui.theme import (
    apply_theme,
    build_signup_form_panel,
    build_signup_hero_panel,
    build_split_layout,
)


class Signup(QMainWindow):
    """Create-account screen. Mirrors the sign-in window in customer_login.py.

    Parameters
    ----------
    on_back:
        Called when this window is closed and it was opened from the sign-in
        screen (so sign-in can un-hide itself). When None the window was run
        standalone, so the "Sign In" link opens a fresh sign-in window and
        the X button just quits as usual.
    """

    def __init__(self, on_back: Optional[Callable[[], None]] = None) -> None:
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
        self._login_window = None  # keeps a standalone sign-in window alive

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        self.form = build_signup_form_panel()
        build_split_layout(root, build_signup_hero_panel(), self.form)

        # "Sign in" link at the bottom of the card -> back to sign-in
        self.form.signin_link.clicked.connect(self._go_back)

        # Animate the password requirement rows as you type.
        self.form.password.textChanged.connect(self.form.update_password_state)

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