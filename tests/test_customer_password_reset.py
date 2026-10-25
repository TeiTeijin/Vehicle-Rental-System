"""Forgot-password service rules and the two-step reset dialog."""

from __future__ import annotations

import os
from contextlib import nullcontext

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from app.services import auth_service
from app.services.errors import ValidationError
from app.ui import forgot_password as reset_ui
from app.ui import theme
from app.utils.security import verify_password


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous_style = app.styleSheet()
    theme.apply_theme(app)
    yield app
    app.processEvents()
    app.setStyleSheet(previous_style)
    app.processEvents()


def test_reset_verifies_the_account_and_writes_a_new_password(session, customer):
    with pytest.raises(auth_service.PasswordResetError):
        auth_service.password_reset_account(session, "nobody@example.com")

    user = auth_service.reset_customer_password(
        session, customer.email, "New-pass1!"
    )
    assert user.user_id == customer.user_id
    assert verify_password("New-pass1!", user.password_hash)
    assert verify_password("correct horse battery staple", user.password_hash) is False

    signed_in = auth_service.sign_in_customer(
        session, customer.email, "New-pass1!"
    )
    assert signed_in.user_id == customer.user_id


def test_reset_rejects_a_weak_or_reused_password(session, customer):
    with pytest.raises(ValidationError) as excinfo:
        auth_service.reset_customer_password(session, customer.email, "short")
    assert excinfo.value.field == "password"

    with pytest.raises(ValidationError):
        auth_service.reset_customer_password(
            session,
            customer.email,
            "correct horse battery staple",
        )


def test_mask_email_keeps_the_domain():
    assert reset_ui.mask_email("demo@rentwheels.local") == (
        "d\u2022\u2022\u2022\u2022@rentwheels.local"
    )


def test_reset_dialog_verifies_then_shows_success(
    qapp, monkeypatch, session, customer
):
    monkeypatch.setattr(
        reset_ui, "SessionLocal", lambda: nullcontext(session)
    )
    parent = QWidget()
    dialog = reset_ui.ForgotPasswordDialog(parent, email=customer.email)
    try:
        dialog._verify_account()
        assert dialog.account_email == customer.email
        assert dialog.stack.currentIndex() == 1
        assert "Reset link verified for" in dialog.verified_label.text()
        assert customer.email not in dialog.verified_label.text()

        dialog.new_password.setText("New-pass1!")
        dialog.confirm_password.setText("New-pass1!")
        dialog._refresh_password_state()
        assert dialog.reset_button.isEnabled() is True

        dialog._reset_password()
        assert dialog.reset_completed is True
        assert dialog.stack.currentIndex() == 2
        assert verify_password("New-pass1!", customer.password_hash)
    finally:
        dialog.deleteLater()
        parent.deleteLater()
