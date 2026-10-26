from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QEventLoop,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.staff import theme
from app.staff.feedback import describe, kind_colour

#: How long a toast stays up.
TOAST_MS = 3200

#: Toast travel distance.
TOAST_SLIDE_PX = 14


class Toast(QFrame):
    def __init__(self, parent: QWidget, message: str, kind: str = "ok") -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._label = QLabel(message, self)
        self._label.setObjectName("toastText")
        self._label.setProperty("kind", kind)
        border = kind_colour(kind)
        self.setStyleSheet(
            f"QFrame#toast {{ border-left: 3px solid {border}; "
            f"border-top-right-radius: 6px; border-bottom-right-radius: 6px; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 14, 9)
        layout.addWidget(self._label)

        self._fade: QPropertyAnimation | None = None
        self._dismiss_fade: QPropertyAnimation | None = None
        #: Bumps on every retarget so a stale fade-out cannot hide/delete
        #: a toast that has since been reused for a newer message.
        self._gen = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(TOAST_MS)
        self._timer.timeout.connect(self.dismiss)

    def show_for(self, duration_ms: int | None = None) -> None:
        self.adjustSize()
        self.move(self._origin())
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(140)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.start()
        self._timer.start(TOAST_MS if duration_ms is None else duration_ms)

    def _origin(self) -> QPoint:
        parent = self.parentWidget()
        if parent is None:
            return QPoint(0, 0)
        margin = theme_margin()
        return QPoint(
            parent.width() - self.width() - margin,
            parent.height() - self.height() - margin,
        )

    def retarget(self, message: str, kind: str = "ok", duration_ms: int | None = None) -> None:
        """Replace text/kind in place and restart the timer.

        Used by toast() so a second error updates the visible toast
        instead of stacking a new widget over it.
        """
        self._gen += 1
        self._timer.stop()
        if self._fade is not None:
            self._fade.stop()
        if self._dismiss_fade is not None:
            self._dismiss_fade.stop()
            self._dismiss_fade = None
        self._label.setText(message)
        self._label.setProperty("kind", kind)
        border = kind_colour(kind)
        self.setStyleSheet(
            f"QFrame#toast {{ border-left: 3px solid {border}; "
            f"border-top-right-radius: 6px; border-bottom-right-radius: 6px; }}"
        )
        self.style().unpolish(self._label)
        self.style().polish(self._label)
        self.show_for(duration_ms)

    def dismiss(self) -> None:
        if not self.isVisible():
            return
        self._timer.stop()
        gen = self._gen
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(180)
        fade.setStartValue(self.windowOpacity())
        fade.setEndValue(0.0)
        fade.finished.connect(lambda: self._finish_dismiss(gen))
        self._dismiss_fade = fade
        fade.start()

    def _finish_dismiss(self, gen: int) -> None:
        if gen != self._gen:
            return
        self._dismiss_fade = None
        self.hide()
        self.deleteLater()


def theme_margin() -> int:
    return 20


def toast(parent: QWidget, message: str, kind: str = "ok",
          duration_ms: int | None = None) -> Toast:
    # One live toast per window: a second error updates the visible one
    # (text + kind + timer restart) instead of stacking a new widget on
    # the same bottom-right anchor. Call-sites in base/shell/pages all
    # resolve to the same window even when parented to different pages.
    if duration_ms is None:
        try:
            from app.staff import prefs as _prefs
            duration_ms = int(_prefs.get("behavior/toast_ms"))
        except Exception:
            duration_ms = None
def _visible_overlay(host: QWidget):
    """A live frosted overlay covering `host`, if any.

    Duck-typed (no imports): both overlay classes are window-children with
    show/hide_overlay methods. Matched by shape so neither overlay module
    becomes an import of this one.
    """
    try:
        for child in host.findChildren(QWidget):
            if (child.isVisible()
                    and not isinstance(child, Toast)
                    and hasattr(child, "show_overlay")
                    and hasattr(child, "hide_overlay")
                    and child.parentWidget() is host):
                return child
    except (AttributeError, RuntimeError):
        pass
    return None


def toast(parent: QWidget, message: str, kind: str = "ok",
          duration_ms: int | None = None) -> Toast:
    # One live toast per window: a second error updates the visible one
    # (text + kind + timer restart) instead of stacking a new widget on
    # the same bottom-right anchor. Call-sites in base/shell/pages all
    # resolve to the same window even when parented to different pages.
    if duration_ms is None:
        try:
            from app.staff import prefs as _prefs
            duration_ms = int(_prefs.get("behavior/toast_ms"))
        except Exception:
            duration_ms = None
    host = parent.window() or parent
    overlay = _visible_overlay(host)
    if overlay is not None:
        # The frosted glass covers the whole window: a page-parented toast
        # paints underneath it and errors look like nothing happened, so
        # new toasts go into the overlay itself (above backdrop and card).
        # A stale page-level toast is left to dismiss on its own, never
        # reused here, or its old text would surface over the modal.
        for existing in active_toasts(overlay):
            existing.retarget(message, kind, duration_ms)
            return existing
        widget = Toast(overlay, message, kind)
        widget.show_for(duration_ms)
        return widget
    for existing in active_toasts(host):
        existing.retarget(message, kind, duration_ms)
        return existing
    widget = Toast(parent, message, kind)
    widget.show_for(duration_ms)
    return widget


# -- blocking dialogs ------------------------------------------------------


def blocking_error(parent: QWidget, error: BaseException, title: str = "Cannot continue") -> str:
    message, _field, _kind = describe(error)
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()
    return message


def confirm(parent: QWidget, question: str, *, detail: str = "", destructive: bool = False,
            pref: str | None = None) -> bool:
    """Blocking Yes/Cancel, honouring the Settings confirm toggles.

    `pref` is one of behavior/confirm_cancel, behavior/confirm_retire,
    behavior/confirm_deactivate. When the pref exists and is off, returns
    True without showing anything.
    """
    if pref:
        try:
            from app.staff import prefs as _prefs
            if not bool(_prefs.get(pref)):
                return True
        except Exception:
            pass
    box = QMessageBox(parent)
    box.setIcon(
        QMessageBox.Icon.Warning if destructive else QMessageBox.Icon.Question
    )
    box.setWindowTitle("Please confirm")
    box.setText(question)
    if detail:
        box.setInformativeText(detail)
    box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
    )
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    if destructive:
        box.button(QMessageBox.StandardButton.Yes).setObjectName("dangerButton")
    return box.exec() == QMessageBox.StandardButton.Yes


def confirm_modal(
    parent: QWidget,
    question: str,
    *,
    detail: str = "",
    destructive: bool = False,
    confirm_text: str = "Yes",
    pref: str | None = None,
) -> bool:
    """Blocking confirmation on the app's warm frosted overlay.

    Same Yes/No semantics as :func:`confirm`, but the question is a 540px
    white sheet with the standard modal header, footer, and frosted glass
    instead of an OS message box. Escape and an outside click count as No.
    ``pref`` honours the same Settings confirmation toggles as
    :func:`confirm`.
    """
    if pref:
        try:
            from app.staff import prefs as _prefs

            if not bool(_prefs.get(pref)):
                return True
        except Exception:
            pass
    from app.staff.overlay import FrostedOverlay
    from app.staff.ui_kit import ConfirmPanel

    window = parent.window() if parent is not None else None
    host = window if window is not None else parent
    overlay = FrostedOverlay(host, card_width=540)
    panel = ConfirmPanel(
        question,
        detail,
        destructive=destructive,
        confirm_text=confirm_text,
    )
    overlay.set_content(panel)

    accepted = {"value": False}
    loop = QEventLoop()

    def _accept() -> None:
        accepted["value"] = True
        loop.quit()

    panel.confirmed.connect(_accept)
    panel.cancelled.connect(loop.quit)
    overlay.closed.connect(loop.quit)
    overlay.show_overlay()
    panel.keep_button.setFocus()
    loop.exec()
    overlay.hide_overlay()
    overlay.deleteLater()
    return accepted["value"]


class InfoDialog(QDialog):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)

        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(20, 18, 20, 16)
        self.body.setSpacing(12)

        self.heading = QLabel(title, self)
        self.heading.setObjectName("pageTitle")
        self.body.addWidget(self.heading)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName(
            "primaryButton"
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setObjectName(
            "secondaryButton"
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.body.addWidget(self.buttons)

    def error_label(self) -> QLabel:
        label = QLabel("", self)
        label.setObjectName("fieldError")
        label.setWordWrap(True)
        label.setVisible(False)
        self.body.insertWidget(self.body.count() - 1, label)
        return label


# -- app-wide toast host ---------------------------------------------------


def active_toasts(parent: QWidget) -> list[Toast]:
    return [c for c in parent.findChildren(Toast) if c.isVisible()]


# -- modal dim backdrop ------------------------------------------------------


class DimOverlay(QWidget):
    """Dark glass over a window while a modal dialog is up.

    Covers `window` edge to edge and tracks its resizes (event filter), so a
    popup reads as floating above a dimmed app. Non-modal itself: the dialog
    is a separate top-level window and stays on top.
    """

    def __init__(self, window: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent or window)
        self._window = window
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("background: rgba(23, 23, 23, 110); border: none;")
        self.hide()
        window.installEventFilter(self)

    def show_for(self) -> None:
        self.setGeometry(self._window.rect())
        self.raise_()
        self.show()

    def hide(self) -> None:  # noqa: N802 - Qt naming
        self._window.removeEventFilter(self)
        super().hide()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        from PySide6.QtCore import QEvent

        # Same teardown hardening as VehicleDetailsOverlay.eventFilter: never
        # let a straggler event during destruction raise out of the loop.
        try:
            is_window_resize = (
                watched is self._window and event.type() == QEvent.Type.Resize
            )
        except (AttributeError, RuntimeError):
            return False
        if is_window_resize:
            try:
                self.setGeometry(self._window.rect())
            except (AttributeError, RuntimeError):
                pass
        try:
            return super().eventFilter(watched, event)
        except RuntimeError:
            return False
