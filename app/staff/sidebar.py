from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.staff import branding, icons, theme
from app.staff.metrics import (
    NAV_GAP,
    NAV_ICON,
    NAV_ROW_H,
    SIDEBAR_PAD,
    SIDEBAR_RADIUS,
    SIDEBAR_W,
)
from app.staff.section_row import paint_active_row


class NavItem(QAbstractButton):
    def __init__(
        self,
        key: str,
        label: str,
        icon_name: str,
        *,
        parent: QWidget | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(parent)
        self._key = key
        self._label_text = label
        self._icon_name = icon_name
        self._page_enabled = enabled
        self._hovered = False

        self.setObjectName(theme.OBJ_SIDEBAR_ITEM)
        self.setProperty("page_key", key)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setFixedHeight(NAV_ROW_H)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if enabled:
            self.setToolTip(label)
        else:
            self.setToolTip(f"{label} — not wired up in this build")

    @property
    def key(self) -> str:
        return self._key

    @property
    def label(self) -> str:
        return self._label_text

    @property
    def page_enabled(self) -> bool:
        return self._page_enabled

    def set_active(self, active: bool) -> None:
        if self.isChecked() != active:
            self.setChecked(active)
            self.update()

    def is_active(self) -> bool:
        return self.isChecked()

    def nextCheckState(self) -> None:  # noqa: N802
        if self._page_enabled:
            super().nextCheckState()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            glyph = icons.pixmap(self._icon_name, theme.INK, NAV_ICON)
            paint_active_row(
                painter,
                self.rect(),
                active=self.isChecked(),
                hovered=self._hovered or self.hasFocus(),
                label=self._label_text,
                label_x=16,
                glyph=glyph,
            )
        finally:
            painter.end()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)


class Sidebar(QFrame):
    page_requested = Signal(str)
    sign_out_requested = Signal()
    #: (label, reason) for a placeholder row that was pressed.
    stub_requested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(theme.OBJ_SIDEBAR)
        self.setFixedWidth(SIDEBAR_W)
        self._items: dict[str, NavItem] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(
            SIDEBAR_PAD, SIDEBAR_PAD, SIDEBAR_PAD, SIDEBAR_PAD
        )
        root.setSpacing(0)

        wordmark = QLabel(self)
        wordmark.setObjectName(theme.OBJ_SIDEBAR_WORDMARK)
        wordmark.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        logo = branding.wordmark_pixmap(SIDEBAR_W - 2 * SIDEBAR_PAD)
        if logo.isNull():
            wordmark.setText("rentwheels")
        else:
            wordmark.setPixmap(logo)
        root.addWidget(wordmark)
        root.addSpacing(14)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.viewport().setObjectName("sidebarViewport")
        self._scroll.setStyleSheet(
            "QScrollArea, #sidebarViewport { background: transparent; }"
        )
        self._list = QWidget()
        self._list.setObjectName("sidebarList")
        self._list_layout = QVBoxLayout(self._list)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(NAV_GAP)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(self._list)
        root.addWidget(self._scroll, 1)

        self.sign_out = NavItem("sign_out", "Log out", "logout", parent=self)
        self.sign_out.clicked.connect(self.sign_out_requested.emit)
        root.addSpacing(10)
        root.addWidget(self.sign_out)

    def build(
        self, sections: list[tuple[str, list[tuple[str, str, str, bool]]]]
    ) -> None:
        self._items.clear()

        self._list = QWidget()
        self._list.setObjectName("sidebarList")
        self._list_layout = QVBoxLayout(self._list)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(NAV_GAP)
        self._scroll.setWidget(self._list)

        for group_label, items in sections:
            if not items:
                continue
            header = QLabel(group_label, self._list)
            header.setObjectName(theme.OBJ_SIDEBAR_GROUP)
            self._list_layout.addWidget(header)
            self._list_layout.addSpacing(2)
            for key, label, icon_name, enabled in items:
                row = NavItem(
                    key, label, icon_name, parent=self._list, enabled=enabled
                )
                row.setAutoExclusive(True)
                if not enabled:
                    row.clicked.connect(
                        lambda _checked=False, _key=key: self._on_row(_key)
                    )
                else:
                    row.clicked.connect(
                        lambda _checked=False, _key=key: self.page_requested.emit(_key)
                    )
                self._items[key] = row
                self._list_layout.addWidget(row)
            self._list_layout.addSpacing(NAV_GAP)
        self._list_layout.addStretch(1)

    def _on_row(self, key: str) -> None:
        row = self._items.get(key)
        if row is not None and not row.page_enabled:
            row.setChecked(False)
            self.stub_requested.emit(
                row.label, "not wired up in this build"
            )
            return
        self.page_requested.emit(key)

    def set_current(self, key: str | None) -> None:
        for row_key, row in self._items.items():
            row.set_active(row_key == key)

    def item_labels(self) -> list[str]:
        return [row.label for row in self._items.values()]

    def item(self, key: str) -> NavItem | None:
        return self._items.get(key)
