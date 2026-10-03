"""The left-hand navigation column.

A fixed 210px panel floating 16px from the window edge, rather than a bar across
the top. The floating part matters: on the cream page a bar pinned to the top
edge and a panel inset from it read as two different objects, and the brief asks
for the panel. The 16px inset is what makes it one.

**The active state is two things at once**, and they are painted rather than left
to the stylesheet because a stylesheet cannot do both:

* a 3px bar down the leading edge, and
* the label going bold.

There is deliberately no pill behind the current row: the panel is white and the
row is marked by the bar and the weight alone, so a merely hovered row cannot be
mistaken for the page you are on. The bar is a filled rounded rect rather than a
`border-left`, because a border's rounded ends get clipped and leave a mark with
soft corners instead of a clean indicator running the full height of the row.

Nav items carry a `page_key` dynamic property. Every row is `autoExclusive`, so
the active set is the button's own checked state rather than a "clear the others"
loop, and two items can never both look active at once.
"""

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
    NAV_ACTIVE_BAR,
    NAV_GAP,
    NAV_ICON,
    NAV_ROW_H,
    SIDEBAR_PAD,
    SIDEBAR_RADIUS,
    SIDEBAR_W,
)


class NavItem(QAbstractButton):
    """One row: the active bar, the icon, the label.

    A `QAbstractButton` rather than a `QFrame` with a `mousePressEvent`, because
    a hand-rolled row is not reachable from the keyboard and the old navbar's
    `QPushButton` rows were. `QAbstractButton` gives the checkable state, the
    Space/Enter handling and the focus ring for nothing, and the paint is
    overridden anyway -- so the only thing left to hand-roll is the drawing.

    Checkable rather than merely clickable so "which page am I on" is the
    button's own state instead of a flag beside it that two widgets can disagree
    about.
    """

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
        """Do not let a stub row become checked.

        Qt's hook for "this button does not toggle", and the right place for it:
        the row is `autoExclusive`, and an auto-exclusive button that is the
        only checked one in its group refuses to uncheck itself. So calling
        `setChecked(False)` after the click -- which is what an ordinary
        `clicked` handler would do -- silently does nothing, and the sidebar
        goes on claiming to be on a page that does not exist.
        """
        if self._page_enabled:
            super().nextCheckState()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            radius = float(SIDEBAR_RADIUS) * 0.42
            active = self.isChecked()
            hovered = self._hovered or self.hasFocus()

            if hovered:
                painter.setBrush(QColor(theme.SURFACE))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(self.rect(), radius, radius)

            if active:
                painter.setBrush(QColor(theme.INK))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(
                    QRectF(4.0, 4.0, float(NAV_ACTIVE_BAR), self.height() - 8.0),
                    1.5,
                    1.5,
                )

            icon_colour = theme.INK
            text_colour = theme.INK

            glyph = icons.pixmap(self._icon_name, icon_colour, NAV_ICON)
            painter.drawPixmap(16, (self.height() - glyph.height()) // 2, glyph)

            font = QFont("Inter")
            font.setPixelSize(13)
            font.setWeight(
                QFont.Weight.DemiBold if active else QFont.Weight.Medium
            )
            painter.setFont(font)
            painter.setPen(QColor(text_colour))
            painter.drawText(
                QRectF(
                    16 + NAV_ICON + 12,
                    0,
                    self.width() - NAV_ICON - 40,
                    self.height(),
                ),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._label_text,
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
    """The navigation panel: wordmark, grouped items, and log out at the foot.

    The item list lives in a `QScrollArea` because the two groups plus the
    wordmark add up to about 720px, which is more than the 640px the window can
    be dragged down to. Without it the log-out button is the part that goes
    missing, and the item that goes missing is the one nobody can reach a
    workaround for.
    """

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
        """(Re)build the item list.

        `sections` is a list of `(group label, items)`, each item being
        `(key, label, icon name, enabled)`.

        A whole new list widget per session rather than draining the old one, for
        the reason the navbar used to be rebuilt: the visible set depends on the
        role, and a row left over from the last sign-in is a way to reach a page
        that should not be reachable. Swapping the widget also disposes of every
        previous row at once, so there is no path where one of them is removed
        from the layout but still a live child.
        """
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
        """A row was pressed. Placeholders say so instead of navigating."""
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
        """Every item's label, in order. The tests read the nav through this."""
        return [row.label for row in self._items.values()]

    def item(self, key: str) -> NavItem | None:
        return self._items.get(key)
