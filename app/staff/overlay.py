"""Generic frosted-glass overlay: snapshot-blur backdrop holding one card.

Same recipe as the check-details overlay, without any vehicle knowledge:
covers `window` edge to edge (never a new OS window), blocks input behind
the card, and closes on Esc or a left-click outside the card column.
Content swaps via `set_content()`; the backdrop renders once per opening
(the page behind never changes between swaps).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

#: Overlay card width; the frosted backdrop fills the whole window around it.
OVERLAY_CARD_W = 640

#: Frosted-glass tuning. Single constants so the look changes in one place.
BACKDROP_BLUR_RADIUS = 12
BACKDROP_DIM = 110
BACKDROP_DOWNSCALE = 4
BACKDROP_RERENDER_MS = 120


class FrostedOverlay(QWidget):
    """Window-embedded modal glass with swappable card content."""

    def __init__(
        self, window: QWidget, *, card_width: int = OVERLAY_CARD_W
    ) -> None:
        super().__init__(window)
        self._window = window
        self._card_width = int(card_width or OVERLAY_CARD_W)
        #: False while a wizard owns the overlay: Escape and outside clicks
        #: must not throw away a half-filled rental.
        self._dismissible = True
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Scoped dim: matches only the overlay itself, so no `background`
        # ever cascades onto descendants. The blurred snapshot carries its
        # own baked-in dim and covers this; this fill is the fallback when
        # the grab fails.
        self.setStyleSheet(
            "FrostedOverlay { background: rgba(23, 23, 23, 110);"
            " border: none; }"
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.backdrop = QLabel(self)
        self.backdrop.setObjectName("frostedBackdrop")
        self.backdrop.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._blur_timer = QTimer(self)
        self._blur_timer.setSingleShot(True)
        self._blur_timer.setInterval(BACKDROP_RERENDER_MS)
        self._blur_timer.timeout.connect(self._on_blur_timeout)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(0)

        middle = QHBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(0)
        middle.addStretch(1)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("frostedScroll")
        self.scroll.viewport().setObjectName("frostedViewport")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setStyleSheet(
            "QScrollArea#frostedScroll { background: transparent;"
            " border: none; }"
            " QScrollArea#frostedScroll QWidget#frostedViewport {"
            " background: transparent; border: none; }"
        )
        self.scroll.setFixedWidth(self._card_width)
        middle.addWidget(self.scroll)
        middle.addStretch(1)
        outer.addLayout(middle, 1)
        self.hide()

    def set_card_width(self, width: int) -> None:
        """Resize the content column; the backdrop follows on the next fit."""
        self._card_width = int(width or OVERLAY_CARD_W)
        self.scroll.setFixedWidth(self._card_width)
        if self.isVisible():
            self._fit()

    def set_dismissible(self, flag: bool) -> None:
        """Allow or refuse Escape/outside-click closing.

        Defaults to True. A wizard turns this off while it owns the overlay so
        a stray click cannot discard the form, then back on before hiding.
        """
        self._dismissible = bool(flag)

    # -- content ---------------------------------------------------------

    def set_content(self, widget: QWidget) -> QWidget:
        """Swap the card content, disposing the previous panel."""
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.scroll.setWidget(widget)
        self.scroll.verticalScrollBar().setValue(0)
        return widget

    # -- backdrop ----------------------------------------------------------

    def _render_backdrop(self) -> bool:
        try:
            window = self._window
            if window is None or window.size().isEmpty():
                return False
            src = window.grab()
            if src.isNull():
                return False
            dpr = src.devicePixelRatio()
            small = src.scaled(
                max(1, src.width() // BACKDROP_DOWNSCALE),
                max(1, src.height() // BACKDROP_DOWNSCALE),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            effect = QGraphicsBlurEffect()
            effect.setBlurRadius(BACKDROP_BLUR_RADIUS)
            scene = QGraphicsScene()
            item = QGraphicsPixmapItem(small)
            item.setGraphicsEffect(effect)
            scene.addItem(item)
            scene.setSceneRect(QRectF(small.rect()))
            blurred = QPixmap(small.size())
            blurred.setDevicePixelRatio(small.devicePixelRatio())
            blurred.fill(Qt.GlobalColor.transparent)
            painter = QPainter(blurred)
            try:
                scene.render(painter, QRectF(blurred.rect()), scene.sceneRect())
            finally:
                painter.end()
            target = self.size()
            if target.isEmpty():
                target = window.size()
            full = blurred.scaled(
                max(1, int(target.width() * dpr)),
                max(1, int(target.height() * dpr)),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            full.setDevicePixelRatio(dpr)
            painter = QPainter(full)
            try:
                painter.fillRect(full.rect(), QColor(23, 23, 23, BACKDROP_DIM))
            finally:
                painter.end()
            self.backdrop.setPixmap(full)
            return True
        except Exception:  # noqa: BLE001 - backdrop must never break modal
            return False

    def _render_soon(self) -> None:
        if self._blur_timer.isActive():
            self._blur_timer.stop()
        self._blur_timer.start()

    def _on_blur_timeout(self) -> None:
        if self.isVisible():
            self._render_backdrop()

    # -- show / hide -------------------------------------------------------

    def show_overlay(self) -> None:
        self._fit()
        self._render_backdrop()
        self._window.installEventFilter(self)
        self.raise_()
        self.backdrop.lower()
        self.show()
        self.setFocus()

    def hide_overlay(self) -> None:
        self._window.removeEventFilter(self)
        self._blur_timer.stop()
        self.backdrop.clear()
        self.hide()

    def _fit(self) -> None:
        self.setGeometry(self._window.rect())
        self.backdrop.setGeometry(self.rect())
        self.scroll.setMaximumHeight(max(320, self._window.height() - 96))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        try:
            is_window_resize = (
                watched is self._window and event.type() == QEvent.Type.Resize
            )
        except (AttributeError, RuntimeError):
            return False
        if is_window_resize:
            try:
                self._fit()
                if self.isVisible():
                    self._render_soon()
            except (AttributeError, RuntimeError):
                pass
        try:
            return super().eventFilter(watched, event)
        except RuntimeError:
            return False

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() == Qt.Key.Key_Escape and self._dismissible:
            self.hide_overlay()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # Outside-click-to-close only: presses inside the card column do
        # nothing at all; right-clicks never close.
        if (
            self._dismissible
            and event.button() == Qt.MouseButton.LeftButton
            and not self.scroll.geometry().contains(event.position().toPoint())
        ):
            self.hide_overlay()
        super().mousePressEvent(event)
