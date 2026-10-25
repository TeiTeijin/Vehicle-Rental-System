"""Forgot-password and reset flow for the customer app.

There is no mail transport in this project, so the reset link is simulated:
the account email is verified immediately and the next page says the link was
verified for a masked address. The password is then written through
``auth_service.reset_customer_password``, which applies the same rules as
sign-up.

The dialog is an overlay so it can be opened from either sign-in surface --
the standalone window (app/ui/customer_login.py) or the booking gate
(app/ui/auth_gate_dialog.py).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from app.database import SessionLocal
from app.services import auth_service
from app.services.auth_service import AuthError
from app.services.errors import ServiceError
from app.ui import theme
from app.ui.theme import (
    RequirementLabel,
    hint_label,
    icon_from,
    make_label,
    primary_button,
    text_button,
)


def mask_email(email: str) -> str:
    """'demo@rentwheels.local' -> 'd••••@rentwheels.local'."""
    local, separator, domain = (email or "").strip().partition("@")
    if not separator:
        return email or ""
    dot = "\u2022"
    visible = local[:1] or dot
    return f"{visible}{dot * max(4, len(local) - 1)}@{domain}"


class ForgotPasswordDialog(theme.OverlayDialog):
    """Two-step reset: verify the account, then choose a new password."""

    def __init__(self, parent: QWidget, *, email: str = "") -> None:
        super().__init__(parent, object_name="overlayCard", card_width=500)
        self.account_email = ""
        self.reset_completed = False

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addStretch(1)
        top.addWidget(self.close_button(), 0, Qt.AlignTop)
        self.column.addLayout(top)

        self.stack = QStackedLayout()
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.stack.addWidget(self._request_page(email))
        self.stack.addWidget(self._reset_page())
        self.stack.addWidget(self._success_page())
        self.column.addLayout(self.stack)
        self.stack.setCurrentIndex(0)

    # ---------- page 1: account email ----------
    def _request_page(self, email: str) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)

        col.addWidget(make_label("Forgot password?", "overlayTitle"))
        col.addWidget(
            make_label(
                "Enter the email address linked to your bookings. We'll "
                "verify it before you choose a new password.",
                "overlaySubtitle",
                wrap=True,
            )
        )
        col.addSpacing(6)

        holder = theme.field("Account email", "you@email.com")
        self.email = holder.edit
        self.email.setText(email)
        self.email.returnPressed.connect(self._verify_account)
        col.addWidget(holder)

        self.error = hint_label("", tone="danger")
        self.error.hide()
        col.addWidget(self.error)

        self.continue_button = primary_button("CONTINUE")
        self.continue_button.clicked.connect(self._verify_account)
        col.addWidget(self.continue_button)

        back = text_button("Back to sign in")
        back.clicked.connect(self.reject)
        col.addWidget(back, 0, Qt.AlignHCenter)
        col.addStretch(1)
        return page

    # ---------- page 2: new password ----------
    def _reset_page(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)

        col.addWidget(make_label("Set a new password", "overlayTitle"))
        self.verified_label = hint_label("", tone="green")
        self.verified_label.setWordWrap(True)
        col.addWidget(self.verified_label)
        col.addSpacing(4)

        holder = theme.field("New password", "New password", password=True)
        self.new_password = holder.edit
        self.new_password.textChanged.connect(self._refresh_password_state)
        col.addWidget(holder)

        symbols = "!@#$%^&*()_+-=[]{};:'\"|,.<>/?`~"
        rules = [
            ("8+ characters", lambda text: len(text) >= 8),
            ("- 1 Uppercase letter", lambda text: any(c.isupper() for c in text)),
            ("- 1 Symbol", lambda text: any(c in symbols for c in text)),
            ("- 1 Number", lambda text: any(c.isdigit() for c in text)),
        ]
        self.req_items: list[tuple[RequirementLabel, object]] = []
        for caption, check in rules:
            item = RequirementLabel(caption)
            self.req_items.append((item, check))
            col.addWidget(item)
        col.addSpacing(8)

        confirm = theme.field("Confirm password", "Confirm password", password=True)
        self.confirm_password = confirm.edit
        self.confirm_password.textChanged.connect(self._refresh_password_state)
        self.confirm_password.returnPressed.connect(self._reset_password)
        col.addWidget(confirm)

        self.match_hint = hint_label("", tone="danger")
        self.match_hint.hide()
        col.addWidget(self.match_hint)

        self.reset_error = hint_label("", tone="danger")
        self.reset_error.hide()
        col.addWidget(self.reset_error)

        self.reset_button = primary_button("RESET PASSWORD")
        self.reset_button.setEnabled(False)
        self.reset_button.clicked.connect(self._reset_password)
        col.addWidget(self.reset_button)

        back = text_button("Use a different email")
        back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        col.addWidget(back, 0, Qt.AlignHCenter)
        col.addStretch(1)
        return page

    # ---------- page 3: success ----------
    def _success_page(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)

        tick = QLabel()
        tick.setPixmap(icon_from("check", theme.TOKENS["green"], 34).pixmap(34, 34))
        tick.setFixedSize(34, 34)
        col.addWidget(tick, 0, Qt.AlignLeft)
        col.addWidget(make_label("Password updated", "overlayTitle"))
        col.addWidget(
            make_label(
                "Your new password is ready. Use it the next time you sign in.",
                "overlaySubtitle",
                wrap=True,
            )
        )

        banner, banner_col = theme.panel(
            "successBanner", spacing=4, margins=(16, 12, 16, 12)
        )
        banner_col.addWidget(make_label("PASSWORD RESET COMPLETE", "successTitle"))
        self.success_email = hint_label("", tone="green")
        self.success_email.setWordWrap(True)
        banner_col.addWidget(self.success_email)
        col.addWidget(banner)

        done = primary_button("BACK TO SIGN IN")
        done.clicked.connect(self.accept)
        col.addWidget(done)
        col.addStretch(1)
        return page

    # ---------- actions ----------
    def _verify_account(self) -> None:
        self.error.hide()
        email = self.email.text().strip()
        try:
            with SessionLocal() as session:
                user = auth_service.password_reset_account(session, email)
                self.account_email = user.email
        except (AuthError, ServiceError) as exc:
            self._show_error(self.error, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - the database is remote
            self._show_error(self.error, f"Could not verify that email: {exc}")
            return

        self.verified_label.setText(
            f"Reset link verified for {mask_email(self.account_email)}."
        )
        self.new_password.clear()
        self.confirm_password.clear()
        self._refresh_password_state("")
        self.stack.setCurrentIndex(1)
        self.new_password.setFocus()

    def _refresh_password_state(self, _text: str = "") -> None:
        password = self.new_password.text()
        for item, check in self.req_items:
            item.set_state(
                "neutral" if not password else ("ok" if check(password) else "not")
            )
        confirm = self.confirm_password.text()
        mismatch = bool(confirm) and confirm != password
        self.match_hint.setVisible(mismatch)
        if mismatch:
            self.match_hint.setText("The passwords do not match yet.")
        self.reset_button.setEnabled(
            bool(password)
            and all(check(password) for _item, check in self.req_items)
            and confirm == password
        )

    def _reset_password(self) -> None:
        if not self.reset_button.isEnabled():
            return
        self.reset_error.hide()
        try:
            with SessionLocal() as session:
                auth_service.reset_customer_password(
                    session,
                    self.account_email,
                    self.new_password.text(),
                )
                session.commit()
        except (AuthError, ServiceError) as exc:
            self._show_error(self.reset_error, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - the database is remote
            self._show_error(self.reset_error, f"Could not reset the password: {exc}")
            return

        self.reset_completed = True
        self.success_email.setText(
            f"Signed-in account: {mask_email(self.account_email)}"
        )
        self.stack.setCurrentIndex(2)

    @staticmethod
    def _show_error(label: QLabel, message: str) -> None:
        label.setText(message)
        label.show()


__all__ = ["ForgotPasswordDialog", "mask_email"]
