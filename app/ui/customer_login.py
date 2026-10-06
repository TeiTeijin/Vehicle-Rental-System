from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.ui.theme import (
    apply_theme,
    build_form_panel,
    build_hero_panel,
    build_split_layout,
)


class Login(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Login")
        self.resize(1280, 800)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        form = build_form_panel()
        build_split_layout(root, build_hero_panel(), form)

        # Keeps a strong reference so the sign-up window is not garbage
        # collected while it is open.
        self.signup_window = None

        # "Sign Up" link at the bottom of the card -> open the sign-up screen
        form.signup.clicked.connect(self._open_signup)

    def _open_signup(self) -> None:
        from app.ui.signup import Signup

        self.signup_window = Signup(on_back=self.show)
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