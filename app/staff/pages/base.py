from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.staff import icons, theme
from app.staff.cards import IconButton
from app.staff.context import AccessDenied
from app.staff.feedback import describe
from app.staff.metrics import (
    AVATAR,
    HEADER_H,
    PAGE_MARGIN_X,
    PAGE_MARGIN_Y,
    PAGE_PANEL_PAD,
    SIDEBAR_GAP,
)


class Avatar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(theme.OBJ_AVATAR)
        self.setFixedSize(AVATAR, AVATAR)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.SURFACE))
        # Centre+radii, not the rect overload: `width() / 2` is a float.
        radius = self.width() / 2
        centre = QPointF(self.width() / 2, self.height() / 2)
        painter.drawEllipse(centre, radius, radius)
        glyph = icons.pixmap("user", theme.TEXT, 18)
        painter.drawPixmap(
            (self.width() - glyph.width()) // 2,
            (self.height() - glyph.height()) // 2,
            glyph,
        )
        painter.end()


class StaffPage(QWidget):
    admin_only = False
    #: Draw the whole page on one white rounded sheet.
    PANEL = False
    #: Draw the header row (title, subtitle, overflow button, avatar).
    HEADER = True

    def __init__(self, shell, title: str, subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("page")
        self.shell = shell
        self.context = shell.context

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("pageTitle")
        title_font = QFont("Inter")
        title_font.setPixelSize(32)
        title_font.setWeight(QFont.Weight.Normal)
        self.title_label.setFont(title_font)
        self.title_label.setStyleSheet(
            f"color: {theme.TEXT}; background: transparent;"
        )

        self.subtitle_label = QLabel(subtitle, self)
        self.subtitle_label.setObjectName("pageSubtitle")
        self.subtitle_label.setVisible(bool(subtitle))

        self.overflow_button = IconButton(
            "dots",
            colour=theme.MUTED,
            size=36,
            icon_size=18,
            background=theme.SURFACE,
            tooltip="More",
            parent=self,
        )
        self.overflow_button.setObjectName(theme.OBJ_PAGE_OVERFLOW)
        # Built lazily: a page with no actions must not own a QMenu at all.
        self.overflow_menu: QMenu | None = None
        self._overflow_specs: list[tuple[str, Callable[[], None]]] = []
        self.overflow_button.clicked.connect(self._show_overflow_menu)
        self.avatar = Avatar(self)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(2)
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        header.addLayout(titles)
        header.addStretch(1)
        header.addWidget(self.overflow_button, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignVCenter)

        # Fixed height so a late font load cannot move the body.
        self._header = QWidget(self)
        self._header.setObjectName(theme.OBJ_PAGE_HEADER)
        self._header.setFixedHeight(HEADER_H)
        header_host = QHBoxLayout(self._header)
        header_host.setContentsMargins(0, 0, 0, 0)
        header_host.setSpacing(0)
        header_host.addLayout(header)

        self._content = QWidget(self)
        self._content.setObjectName("pageContent")
        self.body = QVBoxLayout(self._content)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(14)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Vertical only: horizontal scrolling would hide a fit failure.
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.viewport().setObjectName("pageViewport")
        self._scroll.setWidget(self._content)

        self._pinned = QWidget(self)
        self._pinned.setObjectName("pagePinned")
        self._pinned_layout = QHBoxLayout(self._pinned)
        self._pinned_layout.setContentsMargins(0, 0, 0, 0)
        self._pinned_layout.setSpacing(10)
        self._pinned.setFixedHeight(0)
        self._pinned.hide()

        layout = QVBoxLayout(self)
        if self.PANEL:
            # 8+8 split with the shell gap: sidebar-right (SIDEBAR_GAP) +
            # page-left (SIDEBAR_GAP) = SIDEBAR_MARGIN, visually equal to
            # the window-to-sidebar margin on the far left.
            self.panel = QFrame(self)
            self.panel.setObjectName(theme.OBJ_PAGE_PANEL)
            inner = QVBoxLayout(self.panel)
            inner.setContentsMargins(
                PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD
            )
            inner.setSpacing(14)
            if self.HEADER:
                inner.addWidget(self._header)
            inner.addWidget(self._pinned)
            inner.addWidget(self._scroll, 1)
            layout.setContentsMargins(
                SIDEBAR_GAP,
                0,
                SIDEBAR_GAP,
                0,
            )
            layout.setSpacing(0)
            layout.addWidget(self.panel, 1)
        else:
            self.panel = None
            layout.setContentsMargins(
                PAGE_MARGIN_X, PAGE_MARGIN_Y, PAGE_MARGIN_X, PAGE_MARGIN_Y
            )
            layout.setSpacing(14)
            if self.HEADER:
                layout.addWidget(self._header)
            layout.addWidget(self._pinned)
            layout.addWidget(self._scroll, 1)

        if not self.HEADER:
            # Kept for its labels (reload() names the page), just not shown.
            self._header.hide()

        self._loaded = False
        # Default empty: the page shows it only after `_sync_overflow` finds
        # actions, which subclasses may only have built after this returns.
        self.overflow_button.hide()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # Resolved on show, never inside `__init__`: a subclass may not have
        # finished setting up the attributes its actions read yet.
        self._sync_overflow()
        super().showEvent(event)

    def overflow_actions(self) -> list[tuple[str, Callable[[], None]]]:
        """Header-menu actions for this page; empty by default.

        A page with nothing useful to put here gets no More button at all --
        a control that opens an empty menu is worse than no control.
        """
        return []

    def _ensure_overflow_menu(self) -> QMenu:
        if self.overflow_menu is None:
            menu = QMenu(self)
            menu.setStyleSheet(
                f"QMenu {{ background: {theme.PAPER}; border: 1px solid "
                f"{theme.BORDER}; border-radius: 8px; padding: 4px; }}"
                f"QMenu::item {{ padding: 7px 22px; color: {theme.TEXT}; }}"
                f"QMenu::item:selected {{ background: {theme.SURFACE}; }}"
            )
            self.overflow_menu = menu
        return self.overflow_menu

    def _sync_overflow(self) -> None:
        self._overflow_specs = list(self.overflow_actions())
        if not self._overflow_specs:
            if self.overflow_menu is not None:
                self.overflow_menu.clear()
            self.overflow_button.hide()
            return

        menu = self._ensure_overflow_menu()
        menu.clear()
        for label, _callback in self._overflow_specs:
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, action_label=label: self._run_overflow_action(
                    action_label
                )
            )
        self.overflow_button.show()

    def _run_overflow_action(self, label: str) -> None:
        # Resolve the callback on click, not when the menu was built, so a
        # page that swaps a handler keeps working.
        for current_label, callback in self.overflow_actions():
            if current_label == label:
                callback()
                return

    def _show_overflow_menu(self) -> None:
        # Rebuild on open so an action added after construction still appears.
        self._sync_overflow()
        if not self._overflow_specs or self.overflow_menu is None:
            return
        anchor = self.overflow_button.mapToGlobal(
            QPoint(0, self.overflow_button.height())
        )
        self.overflow_menu.popup(anchor)

    @property
    def scroll_area(self) -> QScrollArea:
        return self._scroll

    @property
    def pinned_bar(self) -> QWidget:
        return self._pinned

    def show_pinned_bar(self, height: int) -> None:
        self._pinned.setFixedHeight(height)
        self._pinned.setVisible(height > 0)

    def refresh(self) -> None:
        if self.admin_only:
            self.context.require_admin()
        else:
            self.context.require_staff()
        self._loaded = True

    def reload(self) -> None:
        try:
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            message, _, _ = describe(exc)
            from app.staff.widgets import toast

            toast(self.window(), f"{self.title_label.text()}: {message}", "error")
