from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
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

    def dismiss(self) -> None:
        if not self.isVisible():
            return
        self._timer.stop()
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(180)
        fade.setStartValue(self.windowOpacity())
        fade.setEndValue(0.0)
        fade.finished.connect(self._finish_dismiss)
        fade.start()

    def _finish_dismiss(self) -> None:
        self.hide()
        self.deleteLater()


def theme_margin() -> int:
    return 20


def toast(parent: QWidget, message: str, kind: str = "ok") -> Toast:
    widget = Toast(parent, message, kind)
    widget.show_for()
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


def confirm(parent: QWidget, question: str, *, detail: str = "", destructive: bool = False) -> bool:
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
