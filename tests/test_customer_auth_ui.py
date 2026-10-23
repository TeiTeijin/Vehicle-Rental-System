"""Customer sign-in/sign-up UI details the eye can otherwise miss."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit

from app.ui import theme


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous_style = app.styleSheet()
    theme.apply_theme(app)
    yield app
    # Do not leak the customer stylesheet into the staff-UI test modules.
    app.processEvents()
    app.setStyleSheet(previous_style)
    app.processEvents()


@pytest.fixture
def login(qapp):
    panel = theme.build_form_panel()
    panel.resize(panel.sizeHint())
    panel.show()
    QTest.qWait(80)
    yield panel
    panel.hide()
    panel.deleteLater()


@pytest.fixture
def signup(qapp):
    form = theme.build_signup_form_panel(province_options=[], cities_for=None)
    form.resize(653, 980)
    form.show()
    QTest.qWait(80)
    yield form
    form.hide()
    form.deleteLater()


def _global_pos(widget) -> QPoint:
    return widget.mapToGlobal(QPoint(0, 0))


def test_login_starts_neutral_and_email_hint_is_live(login):
    assert login.email.property("state") == ""
    assert login.password.property("state") == ""

    login.email.setText("abc")
    QTest.qWait(30)

    assert login.email.property("state") == "error"
    assert login.email_hint.isVisible()
    assert "@" in login.email_hint.text()


def test_signup_starts_neutral_and_email_hint_does_not_move_fields(signup):
    fields = (
        signup.first_name,
        signup.last_name,
        signup.email,
        signup.mobile,
        signup.password,
        signup.confirm_password,
    )
    assert [field.property("state") for field in fields] == [""] * len(fields)

    positions_before = {
        field: _global_pos(field)
        for field in (signup.first_name, signup.last_name, signup.email,
                      signup.mobile, signup.password, signup.confirm_password)
    }

    signup.email.setText("abc")
    QTest.qWait(30)

    assert signup.first_name.property("state") == ""
    assert signup.email.property("state") == "error"
    assert signup.email_hint.isVisible()
    assert "@" in signup.email_hint.text()
    for field, position in positions_before.items():
        assert _global_pos(field) == position


def test_mobile_accepts_only_eleven_digits_starting_09(signup):
    edit = signup.mobile
    edit.setFocus()
    assert edit.placeholderText() == "09XX XXX XXXX"
    assert edit.maxLength() == 11

    QTest.keyClicks(edit, "123")
    assert edit.text() == ""

    QTest.keyClicks(edit, "09171234567")
    assert edit.text() == "09171234567"

    QTest.keyClicks(edit, "8")
    assert edit.text() == "09171234567"


@pytest.mark.parametrize("field_name", ["password", "confirm_password"])
def test_signup_password_eyes_reveal_and_remask(signup, field_name):
    edit = getattr(signup, field_name)
    assert edit.echoMode() == QLineEdit.Password

    edit.password_action.trigger()
    assert edit.echoMode() == QLineEdit.Normal

    edit.password_action.trigger()
    assert edit.echoMode() == QLineEdit.Password


def test_login_password_eye_reveals_and_remasks(login):
    assert login.password.echoMode() == QLineEdit.Password

    login.password.password_action.trigger()
    assert login.password.echoMode() == QLineEdit.Normal

    login.password.password_action.trigger()
    assert login.password.echoMode() == QLineEdit.Password


def test_navbar_shows_logo_and_uses_account_avatar(qapp):
    navbar = theme.NavBar()
    navbar.show()
    QTest.qWait(30)

    logo = navbar.findChild(QLabel, "navLogo")
    assert logo is not None
    assert not logo.pixmap().isNull()

    navbar.set_user("Maria")
    assert navbar.auth_button.text() == ""
    assert not navbar.auth_button.icon().isNull()
    assert "Maria" in navbar.auth_button.toolTip()

    navbar.set_user(None)
    assert navbar.auth_button.text() == theme.GUEST_LABEL
    assert navbar.auth_button.icon().isNull()

    navbar.hide()
    navbar.deleteLater()
