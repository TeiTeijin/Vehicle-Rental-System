"""The rounded account panel opened from the top-bar avatar.

The old ``QMenu`` was serviceable but looked like a native context menu next to
the rest of the customer app. This popup mirrors the notification panel: a
floating white card with a soft shadow, a signed-in header, and two large
rounded actions -- Personal information and Log out. Log out carries the
danger tone so it is visually distinct without turning the whole panel red.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.ui import theme
from app.ui.theme import TOKENS, make_label


class AccountAction(QFrame):
    """One clickable row in the account panel."""

    clicked = Signal()

    def __init__(
        self,
        icon_name: str,
        title: str,
        subtitle: str,
        *,
        danger: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("accountAction")
        self.setProperty("danger", bool(danger))
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(title)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 11, 12, 11)
        row.setSpacing(12)

        glyph = QLabel()
        glyph.setObjectName("accountActionIcon")
        glyph.setProperty("danger", bool(danger))
        glyph.setAlignment(Qt.AlignCenter)
        glyph.setFixedSize(36, 36)
        glyph.setPixmap(
            theme.icon_from(
                icon_name,
                TOKENS["danger"] if danger else TOKENS["text_dark"],
                18,
            ).pixmap(18, 18)
        )
        glyph.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(glyph, 0, Qt.AlignVCenter)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        title_label = make_label(title, "accountActionTitle")
        subtitle_label = make_label(subtitle, "accountActionSubtitle", wrap=True)
        for label in (title_label, subtitle_label):
            label.setProperty("danger", bool(danger))
            label.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
            )
        text.addWidget(title_label)
        text.addWidget(subtitle_label)
        row.addLayout(text, 1)

        arrow = QLabel()
        arrow.setObjectName("accountActionArrow")
        arrow.setPixmap(
            theme.icon_from(
                "forward",
                TOKENS["danger"] if danger else TOKENS["muted_soft"],
                14,
            ).pixmap(14, 14)
        )
        arrow.setFixedSize(14, 14)
        arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(arrow, 0, Qt.AlignVCenter)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
                event.accept()
                return
        super().mouseReleaseEvent(event)


class AccountPopup(QFrame):
    """A rounded popup anchored under the avatar button."""

    profile_requested = Signal()
    logout_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None,
        *,
        name: str,
        email: str,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("accountPopup")
        self.setWindowFlags(
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFixedWidth(354)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(0)

        self.panel = QFrame()
        self.panel.setObjectName("accountPanel")
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 7)
        shadow.setColor(QColor(17, 17, 17, 38))
        self.panel.setGraphicsEffect(shadow)
        outer.addWidget(self.panel)

        column = QVBoxLayout(self.panel)
        column.setContentsMargins(16, 15, 16, 16)
        column.setSpacing(10)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(11)

        avatar = QLabel()
        avatar.setObjectName("accountAvatar")
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setFixedSize(48, 48)
        avatar.setPixmap(
            theme.icon_from("account", TOKENS["text_dark"], 24).pixmap(24, 24)
        )
        head.addWidget(avatar, 0, Qt.AlignVCenter)

        who = QVBoxLayout()
        who.setContentsMargins(0, 0, 0, 0)
        who.setSpacing(2)
        who.addWidget(make_label(name or "Customer", "accountName"))
        who.addWidget(make_label(email or "Signed in", "accountEmail"))
        head.addLayout(who, 1)

        close = QToolButton()
        close.setObjectName("accountClose")
        close.setCursor(Qt.PointingHandCursor)
        close.setIcon(theme.icon_from("close", TOKENS["text_muted"], 14))
        close.setIconSize(QSize(14, 14))
        close.setFixedSize(28, 28)
        close.clicked.connect(self.close)
        head.addWidget(close, 0, Qt.AlignTop)
        column.addLayout(head)

        divider = QFrame()
        divider.setObjectName("accountDivider")
        divider.setFixedHeight(1)
        column.addWidget(divider)

        column.addWidget(
            make_label("ACCOUNT", "accountSection", spacing=1.4)
        )

        profile = AccountAction(
            "account",
            "Personal information",
            "View and update your details",
        )
        profile.clicked.connect(self._open_profile)
        column.addWidget(profile)

        logout = AccountAction(
            "logout",
            "Log out",
            "Sign out of this device",
            danger=True,
        )
        logout.clicked.connect(self._logout)
        column.addWidget(logout)

        self.adjustSize()

    def _open_profile(self) -> None:
        self.close()
        self.profile_requested.emit()

    def _logout(self) -> None:
        self.close()
        self.logout_requested.emit()

    def show_under(self, anchor: QWidget) -> None:
        """Open below ``anchor``, flipped above if the screen runs out."""
        self.adjustSize()
        origin = anchor.mapToGlobal(QPoint(0, 0))
        screen = anchor.screen() or QGuiApplication.primaryScreen()
        available = (
            screen.availableGeometry() if screen is not None else self.rect()
        )

        x = origin.x() + anchor.width() - self.width()
        y = origin.y() + anchor.height() + 10
        if y + self.height() > available.bottom() - 8:
            y = origin.y() - self.height() - 10
        x = max(
            available.left() + 8,
            min(x, available.right() - self.width() - 7),
        )
        y = max(
            available.top() + 8,
            min(y, available.bottom() - self.height() - 7),
        )

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)


__all__ = ["AccountAction", "AccountPopup"]
