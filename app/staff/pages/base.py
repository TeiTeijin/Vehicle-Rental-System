"""What every staff page has in common: a header row, and a scrolling body.

The header is the brief's top row -- page title at the leading edge, a "..." and
a circular avatar at the trailing edge -- and it sits *outside* the scroll area.
That is deliberate: the title and the identity are how a member of staff knows
which screen they are on and who is signed in, and neither is worth scrolling out
of reach on a window that is allowed to be 640px tall.

The body is inside a `QScrollArea` because the window is resizable and the
content is not. The dashboard's narrowest layout stacks five cards whose minimum
heights come to about 1500px, which no permitted window height can show; the
table pages are shorter but still overflow on a small screen. Without a scroll
area the answer is that the bottom of the page is simply cut off, and a member
of staff looking for the log-out button or the last row of a table would be
looking for something that is not there.

`setWidgetResizable(True)` is what makes the stretch arguments pages already pass
to `self.body.addWidget(..., 1)` still mean "fill the leftover height": a
resizable scroll area stretches its content to the viewport when the content is
shorter than the viewport, and only scrolls when it is taller.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
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
)


class Avatar(QWidget):
    """The 36px circle in the page header: a SURFACE fill and a thin user glyph.

    Painted rather than assembled from a `QLabel` and a stylesheet, because the
    brief's version is a filled circle with a centred icon and a QLabel only
    offers a rounded-rectangle border-radius -- which clips the glyph's corners
    differently at 36px than at 40px.
    """

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
    """A page. Subclasses build into `self.body` and override `refresh`."""

    admin_only = False
    #: Draw the whole page on one white rounded sheet.
    PANEL = False

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

        layout = QVBoxLayout(self)
        if self.PANEL:
            # Padding comes out of the page margin, keeping grid width unchanged.
            self.panel = QFrame(self)
            self.panel.setObjectName(theme.OBJ_PAGE_PANEL)
            inner = QVBoxLayout(self.panel)
            inner.setContentsMargins(
                PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD
            )
            inner.setSpacing(14)
            inner.addWidget(self._header)
            inner.addWidget(self._scroll, 1)
            layout.setContentsMargins(
                PAGE_MARGIN_X - PAGE_PANEL_PAD,
                0,
                PAGE_MARGIN_X - PAGE_PANEL_PAD,
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
            layout.addWidget(self._header)
            layout.addWidget(self._scroll, 1)

        self._loaded = False

    @property
    def scroll_area(self) -> QScrollArea:
        """The page's scroll area. Exposed for the layout tests."""
        return self._scroll

    def refresh(self) -> None:
        """Reload the page's data. Enforces the role, then marks it loaded."""
        if self.admin_only:
            self.context.require_admin()
        else:
            self.context.require_staff()
        self._loaded = True

    def reload(self) -> None:
        """`refresh` with the failure reported as a toast rather than raised.

        Every page's refresh path is a set of database reads that can fail on a
        dropped connection, and the table screens already render the failure
        themselves. This is the fallback for the ones that do not, so a failure
        names the page it happened on.
        """
        try:
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            message, _, _ = describe(exc)
            from app.staff.widgets import toast

            toast(self.window(), f"{self.title_label.text()}: {message}", "error")
