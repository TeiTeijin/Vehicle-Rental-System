"""Open the staff app on demo.db and sign in with the real login form.

This runs the genuine entry point -- real `StaffShell`, real `LoginView`, real
bcrypt authentication -- and then types the demo credentials into the real
fields and clicks the real button. Nothing is bypassed: `attempt_sign_in` does
the actual password check.

It exists only because there is no mouse or image tool in this session, so the
login cannot be typed by hand.

    python launch_demo.py
"""
from __future__ import annotations

import sys

from app.staff.context import StaffContext, select_database

EMAIL = "admin@rentwheels.local"
PASSWORD = "demo-password"


def main() -> int:
    selection = select_database(demo=True)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest

    from app.staff.shell import StaffShell
    from app.utils.fonts import load_fonts

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("RentWheels Staff")
    load_fonts()

    context = StaffContext(selection)
    shell = StaffShell(context)
    shell.show()

    def sign_in() -> None:
        view = shell.login_view
        if view is None:
            print("no login view on the shell", file=sys.stderr)
            return
        if not context.is_signed_in:
            view.email.setText(EMAIL)
            view.password.setText(PASSWORD)
            print(f"signing in as {EMAIL} ...", flush=True)
            view.attempt_sign_in()
        print(f"signed in: {context.user}", flush=True)

        # Land on New Rental, the page this work was about.
        print(f"opened new_rental: {shell.show_page('new_rental')}", flush=True)

    # One turn of the loop after show, so the login view is laid out and its
    # fields can take text.
    QTimer.singleShot(600, sign_in)

    try:
        return app.exec()
    finally:
        context.dispose()


if __name__ == "__main__":
    raise SystemExit(main())