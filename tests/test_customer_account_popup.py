"""The rounded account popup opened by the signed-in avatar."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from app.ui import theme
from app.ui.account_popup import AccountAction, AccountPopup


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous_style = app.styleSheet()
    theme.apply_theme(app)
    yield app
    app.processEvents()
    app.setStyleSheet(previous_style)
    app.processEvents()


def test_account_popup_has_styled_actions_and_emits(qapp):
    popup = AccountPopup(
        None,
        name="Demo Customer",
        email="demo@rentwheels.local",
    )
    try:
        assert popup.findChild(QLabel, "accountName").text() == "Demo Customer"
        assert (
            popup.findChild(QLabel, "accountEmail").text()
            == "demo@rentwheels.local"
        )

        actions = popup.findChildren(AccountAction)
        titles = [
            action.findChild(QLabel, "accountActionTitle").text()
            for action in actions
        ]
        assert titles == ["Personal information", "Log out"]
        assert actions[0].property("danger") is False
        assert actions[1].property("danger") is True

        seen: list[str] = []
        popup.profile_requested.connect(lambda: seen.append("profile"))
        actions[0].clicked.emit()
        assert seen == ["profile"]
    finally:
        popup.close()


def test_logout_action_emits_logout_request(qapp):
    popup = AccountPopup(
        None,
        name="Demo Customer",
        email="demo@rentwheels.local",
    )
    try:
        seen: list[str] = []
        popup.logout_requested.connect(lambda: seen.append("logout"))
        popup.findChildren(AccountAction)[1].clicked.emit()
        assert seen == ["logout"]
    finally:
        popup.close()
