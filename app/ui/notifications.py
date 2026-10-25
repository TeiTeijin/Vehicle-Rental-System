"""The customer's notification bell popup.

The bell lives in the top bar (app/ui/theme.py); this module owns only the
panel it opens. Items are booking-derived (app/services/notification_service.py)
and clicking one takes the customer to the Bookings tab, where the full card
lives.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
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

from app.services import notification_service
from app.ui import theme
from app.ui.theme import TOKENS, make_label

#: Icons and colours per notification kind. The label circles get their tint
#: from QSS; these are the glyph colours.
_KIND_ICONS = {
    notification_service.KIND_PICKUP: ("calendar", TOKENS["amber"]),
    notification_service.KIND_SUCCESS: ("check", TOKENS["green"]),
    notification_service.KIND_PENDING: ("clock", TOKENS["amber"]),
    notification_service.KIND_COMPLETED: ("receipt", TOKENS["text_muted"]),
    notification_service.KIND_ONGOING: ("car", TOKENS["text_dark"]),
}


class NotificationItem(QFrame):
    """One clickable update: title, vehicle/reference, and a short meta line."""

    clicked = Signal()

    def __init__(
        self,
        notification: notification_service.Notification,
        *,
        unread: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.notification = notification
        self.setObjectName("notificationItem")
        self.setProperty("unread", bool(unread))
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Open your bookings")

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 10, 12, 10)
        row.setSpacing(11)

        icon_name, colour = _KIND_ICONS.get(
            notification.kind, ("info", TOKENS["text_muted"])
        )
        glyph_holder = QLabel()
        glyph_holder.setObjectName("notificationIcon")
        glyph_holder.setProperty("kind", notification.kind)
        glyph_holder.setAlignment(Qt.AlignCenter)
        glyph_holder.setFixedSize(34, 34)
        glyph_holder.setPixmap(
            theme.icon_from(icon_name, colour, 17).pixmap(17, 17)
        )
        glyph_holder.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        row.addWidget(glyph_holder, 0, Qt.AlignTop)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        title = make_label(notification.title, "notificationItemTitle")
        detail = make_label(notification.detail, "notificationItemBody", wrap=True)
        meta = make_label(notification.meta, "notificationItemMeta", wrap=True)
        for label in (title, detail, meta):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text.addWidget(title)
        text.addWidget(detail)
        text.addWidget(meta)
        row.addLayout(text, 1)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
                event.accept()
                return
        super().mouseReleaseEvent(event)


class NotificationPopup(QFrame):
    """A small popup anchored under the bell.

    It is a ``Qt.Popup`` window, so clicking anywhere outside closes it without
    a modal backdrop -- the same lightweight feel as the account menu.
    """

    dismissed = Signal()
    bookings_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None,
        notifications: Iterable[notification_service.Notification],
        *,
        unread_keys: set[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("notificationPopup")
        self.setWindowFlags(
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFixedWidth(392)

        self._dismissed = False
        self.items: list[NotificationItem] = []
        items = list(notifications)
        unread_keys = set(unread_keys or ())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(0)

        self.panel = QFrame()
        self.panel.setObjectName("notificationPanel")
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 7)
        shadow.setColor(QColor(17, 17, 17, 38))
        self.panel.setGraphicsEffect(shadow)
        outer.addWidget(self.panel)

        column = QVBoxLayout(self.panel)
        column.setContentsMargins(16, 15, 16, 14)
        column.setSpacing(10)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)

        heading = QVBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(1)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title_row.addWidget(make_label("Notifications", "notificationTitle"))
        if unread_keys:
            badge = make_label(
                f"{len(unread_keys)} new",
                "notificationCount",
                spacing=0.4,
            )
            badge.setAlignment(Qt.AlignCenter)
            title_row.addWidget(badge, 0, Qt.AlignVCenter)
        title_row.addStretch(1)
        heading.addLayout(title_row)
        heading.addWidget(
            make_label(
                "Booking confirmations and pickup reminders",
                "notificationSubtitle",
            )
        )
        head.addLayout(heading, 1)

        close = QToolButton()
        close.setObjectName("notificationClose")
        close.setCursor(Qt.PointingHandCursor)
        close.setIcon(theme.icon_from("close", TOKENS["text_muted"], 14))
        close.setIconSize(QSize(14, 14))
        close.setFixedSize(28, 28)
        close.clicked.connect(self.close)
        head.addWidget(close, 0, Qt.AlignTop)
        column.addLayout(head)

        divider = QFrame()
        divider.setObjectName("notificationDivider")
        divider.setFixedHeight(1)
        column.addWidget(divider)

        if items:
            for note in items:
                item = NotificationItem(
                    note,
                    unread=note.key in unread_keys,
                )
                item.clicked.connect(self._open_bookings)
                self.items.append(item)
                column.addWidget(item)
        else:
            column.addWidget(self._empty_state())

        self.adjustSize()

    # ---------- empty state ----------
    def _empty_state(self) -> QWidget:
        holder = QWidget()
        holder.setObjectName("notificationEmpty")
        col = QVBoxLayout(holder)
        col.setContentsMargins(14, 22, 14, 24)
        col.setSpacing(6)
        col.setAlignment(Qt.AlignCenter)

        glyph = QLabel()
        glyph.setObjectName("notificationEmptyIcon")
        glyph.setAlignment(Qt.AlignCenter)
        glyph.setFixedSize(44, 44)
        glyph.setPixmap(
            theme.icon_from("check", TOKENS["green"], 21).pixmap(21, 21)
        )
        col.addWidget(glyph, 0, Qt.AlignHCenter)
        title = make_label("You're all caught up", "notificationEmptyTitle")
        title.setAlignment(Qt.AlignCenter)
        col.addWidget(title)
        body = make_label(
            "Booking confirmations and pickup reminders will appear here.",
            "notificationEmptyBody",
            wrap=True,
        )
        body.setAlignment(Qt.AlignCenter)
        col.addWidget(body)
        return holder

    # ---------- placement / dismissal ----------
    def show_under(self, anchor: QWidget) -> None:
        """Open below ``anchor``, flipped above if the screen runs out."""
        self.adjustSize()
        origin = anchor.mapToGlobal(QPoint(0, 0))
        screen = anchor.screen() or QGuiApplication.primaryScreen()
        available = (
            screen.availableGeometry()
            if screen is not None
            else self.rect()
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

    def _open_bookings(self) -> None:
        self.close()
        self.bookings_requested.emit()

    def hideEvent(self, event: QEvent) -> None:
        super().hideEvent(event)
        if not self._dismissed:
            self._dismissed = True
            self.dismissed.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)


__all__ = ["NotificationItem", "NotificationPopup"]
