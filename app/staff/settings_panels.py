"""Small overlay forms for the Settings users group."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.staff.metrics import TOOL_ROW_H


class _Base(QWidget):
    submitted = Signal(dict)
    cancelled = Signal()

    def _buttons(self, save_text: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        save = QPushButton(save_text, self)
        save.setObjectName("primaryButton")
        save.setFixedHeight(TOOL_ROW_H)
        save.clicked.connect(self._on_save)
        cancel = QPushButton("Cancel", self)
        cancel.setObjectName("secondaryButton")
        cancel.setFixedHeight(TOOL_ROW_H)
        cancel.clicked.connect(self.cancelled.emit)
        row.addWidget(save)
        row.addWidget(cancel)
        row.addStretch(1)
        return row

    def _error(self) -> QLabel:
        label = QLabel("", self)
        label.setObjectName("fieldError")
        label.setWordWrap(True)
        label.setVisible(False)
        return label


class StaffFormPanel(_Base):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("wizardStep")
        layout = QVBoxLayout(self)
        title = QLabel("Add staff account", self)
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        form = QFormLayout()
        self.name = QLineEdit(self)
        self.email = QLineEdit(self)
        self.email.setPlaceholderText("name@example.com")
        self.role = QComboBox(self)
        self.role.addItems(["staff", "admin"])
        self.temp = QLineEdit(self)
        self.temp.setEchoMode(QLineEdit.EchoMode.Password)
        self.temp.setPlaceholderText("min 8 chars, must change on login")
        for widget, label in ((self.name, "Full name"), (self.email, "Email"),
                              (self.role, "Role"), (self.temp, "Temp password")):
            widget.setFixedHeight(TOOL_ROW_H)
            form.addRow(label, widget)
        layout.addLayout(form)
        self.error = self._error()
        layout.addWidget(self.error)
        layout.addLayout(self._buttons("Add account"))

    def _on_save(self) -> None:
        if not self.name.text().strip() or not self.email.text().strip() \
                or not self.temp.text():
            self.error.setText("Name, email, and temp password are required.")
            self.error.setVisible(True)
            return
        self.error.setVisible(False)
        self.submitted.emit({"full_name": self.name.text().strip(),
                             "email": self.email.text().strip(),
                             "role": self.role.currentText(),
                             "temp_password": self.temp.text()})


class ResetPwPanel(_Base):
    def __init__(self, email: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("wizardStep")
        layout = QVBoxLayout(self)
        title = QLabel(f"Reset password — {email}", self)
        title.setObjectName("pageTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        form = QFormLayout()
        self.temp = QLineEdit(self)
        self.temp.setEchoMode(QLineEdit.EchoMode.Password)
        self.temp.setFixedHeight(TOOL_ROW_H)
        self.temp.setPlaceholderText("min 8 chars")
        form.addRow("Temp password", self.temp)
        layout.addLayout(form)
        self.error = self._error()
        layout.addWidget(self.error)
        layout.addLayout(self._buttons("Reset"))

    def _on_save(self) -> None:
        if not self.temp.text():
            self.error.setText("Enter a temporary password.")
            self.error.setVisible(True)
            return
        self.error.setVisible(False)
        self.submitted.emit({"temp_password": self.temp.text()})


class FirstRunPanel(_Base):
    """Create the first admin when the database has no users at all."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("wizardStep")
        layout = QVBoxLayout(self)
        title = QLabel("Create the first admin", self)
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        note = QLabel("No users exist yet. This account becomes the branch admin.",
                      self)
        note.setObjectName("pageSubtitle")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.name = QLineEdit(self)
        self.email = QLineEdit(self)
        self.pw = QLineEdit(self)
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        for widget, label in ((self.name, "Full name"), (self.email, "Email"),
                              (self.pw, "Password (min 8)")):
            widget.setFixedHeight(TOOL_ROW_H)
            form.addRow(label, widget)
        layout.addLayout(form)
        self.error = self._error()
        layout.addWidget(self.error)
        layout.addLayout(self._buttons("Create admin"))

    def _on_save(self) -> None:
        if not self.name.text().strip() or not self.email.text().strip() \
                or not self.pw.text():
            self.error.setText("All fields are required.")
            self.error.setVisible(True)
            return
        self.error.setVisible(False)
        self.submitted.emit({"full_name": self.name.text().strip(),
                             "email": self.email.text().strip(),
                             "password": self.pw.text()})
