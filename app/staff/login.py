"""The staff sign-in screen.

Sign-in is the role gate. ``auth_service.sign_in`` accepts any user in the
table, so this view is where a `customer` row is turned away -- the check is
here as well as in ``StaffContext.require_staff`` because refusing at the door
gives a clearer message than refusing on the first page.

Two details worth keeping:

  * The error is shown in one fixed place above the button, not as a popup, so
    the password field keeps focus and the user can just try again.
  * The email is lowercased on the way in, matching ``sign_in``, so an address
    typed with capitals still works instead of failing with "incorrect
    password" for a reason that is not about the password.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models import Users
from app.services.auth_service import AuthError, sign_in
from app.staff.context import StaffContext, StaffUser
from app.staff.theme import OBJ_ERROR


class LoginView(QWidget):
    """Email and password, then a callback with the authenticated user."""

    def __init__(
        self,
        context: StaffContext,
        on_signed_in,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("loginRoot")
        self.context = context
        self._on_signed_in = on_signed_in

        self.title = QLabel("RentDesk Staff", self)
        self.title.setObjectName("loginTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.subtitle = QLabel("Sign in to run the branch.", self)
        self.subtitle.setObjectName("loginSubtitle")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.email = QLineEdit(self)
        self.email.setPlaceholderText("Email address")
        self.email.setText("admin@rentdesk.local" if context.selection.is_demo else "")
        self.email.returnPressed.connect(self._focus_password)

        self.password = QLineEdit(self)
        self.password.setPlaceholderText("Password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.returnPressed.connect(self.attempt_sign_in)

        self.error = QLabel("", self)
        self.error.setObjectName(OBJ_ERROR)
        self.error.setWordWrap(True)
        self.error.setVisible(False)

        self.submit = QPushButton("Sign in", self)
        self.submit.setObjectName("primaryButton")
        self.submit.setDefault(True)
        self.submit.clicked.connect(self.attempt_sign_in)

        self.hint = QLabel("", self)
        self.hint.setObjectName("loginSubtitle")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setWordWrap(True)
        if context.selection.is_demo:
            # The demo credentials are printed by the seeder; repeating them
            # here removes the most likely first-run stumble.
            self.hint.setText(
                "Demo database. Sign in as admin@rentdesk.local\n"
                "with the password printed by the seeder."
            )

        card = QFrame(self)
        card.setStyleSheet(
            "QFrame { background: #FFFFFF; border: 1px solid #D8CCBB; border-radius: 8px; }"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 30, 32, 26)
        card_layout.setSpacing(10)
        card_layout.addWidget(self.title)
        card_layout.addWidget(self.subtitle)
        card_layout.addSpacing(14)
        card_layout.addWidget(self.email)
        card_layout.addWidget(self.password)
        card_layout.addWidget(self.error)
        card_layout.addWidget(self.submit)
        card_layout.addWidget(self.hint)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        self.email.setFocus()

    def _focus_password(self) -> None:
        self.password.setFocus()
        self.password.selectAll()

    def attempt_sign_in(self) -> Users | None:
        """Authenticate, then hand the user to the shell.

        Returns the user on success and ``None`` on any failure, having already
        shown the reason. A button handler returning ``None`` is easy to
        misread as a no-op, so callers that care should check
        :attr:`context.user`.
        """
        self.error.setVisible(False)
        email = self.email.text().strip().lower()
        password = self.password.text()

        if not email or not password:
            # Checked here so an empty field does not spend a bcrypt round.
            self._show_error("Enter your email address and password.")
            return None

        # The snapshot is taken *inside* the session. `sign_in` returns a
        # `Users` row, and the `rollback()` that ends a read session expires
        # every attribute on it, so the fields have to be copied out before the
        # context manager closes.
        try:
            with self.context.reading() as session:
                row = sign_in(session, email, password)
                identity = StaffUser.from_row(row)
        except AuthError as exc:
            self._show_error(str(exc))
            self.password.clear()
            self.password.setFocus()
            return None
        except Exception as exc:  # noqa: BLE001
            from app.staff.feedback import describe

            message, _field, _kind = describe(exc)
            self._show_error(message)
            return None

        user = identity
        if user.role not in ("staff", "admin"):
            # A valid account, wrong door. Deliberately not "no such user":
            # this branch is only reachable after a correct password, so
            # hiding it would tell a legitimate customer nothing useful while
            # helping nobody.
            self._show_error(
                f"{user.email} is a customer account. Staff sign-in only."
            )
            self.password.clear()
            return None

        self.context.sign_in(user)
        self._on_signed_in(user)
        return user

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(True)
