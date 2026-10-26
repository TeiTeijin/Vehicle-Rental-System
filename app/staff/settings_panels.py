"""Small overlay forms for the Settings users group.

The Add staff, Reset password, and Accent color sheets follow the same warm
recipe as the booking modals: a white card on the frosted overlay, a cream
eyebrow, warm inputs, and a sticky footer. FirstRunPanel stays a plain dialog
panel because the shell owns that QDialog.
"""

from __future__ import annotations

import secrets

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.staff.metrics import TOOL_ROW_H
from app.staff.ui_kit import (
    BookingModal,
    ChipGroup,
    FormField,
    NoticeBox,
)

_TEMP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def _temp_password(length: int = 12) -> str:
    return "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(length))


def _inline_button(text: str, parent: QWidget) -> QPushButton:
    button = QPushButton(text, parent)
    button.setObjectName("settingsInline")
    button.setFixedHeight(TOOL_ROW_H)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def _field_label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("settingsFieldLabel")
    return label


class _TempPasswordRow(QWidget):
    """Warm temp-password input with the reference's Show/Generate pair."""

    def __init__(self, edit: QLineEdit, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsFieldHost")
        self.edit = edit
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(edit, 1)
        self.show_button = _inline_button("Show", self)
        self.generate_button = _inline_button("Generate", self)
        row.addWidget(self.show_button, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.generate_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.show_button.clicked.connect(self.toggle_show)
        self.generate_button.clicked.connect(self.generate)

    def toggle_show(self) -> None:
        hidden = self.edit.echoMode() == QLineEdit.EchoMode.Password
        self.edit.setEchoMode(
            QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password
        )
        self.show_button.setText("Hide" if hidden else "Show")

    def generate(self) -> None:
        self.edit.setText(_temp_password())
        if self.edit.echoMode() == QLineEdit.EchoMode.Password:
            self.toggle_show()


class _WarmModal(BookingModal):
    """Booking-modal frame with the Settings signals and error alias."""

    submitted = Signal(dict)
    cancelled = Signal()
    MODAL_WIDTH = 540

    def __init__(
        self,
        eyebrow: str,
        title: str,
        subtitle: str = "",
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(eyebrow, title, subtitle, parent=parent)
        self.setProperty("variant", "settings")
        self.card.setFixedWidth(self.MODAL_WIDTH)
        #: Kept for the old panel contract (``panel.error``).
        self.error = self.error_label
        self.cancel_button = self.add_back("Cancel")
        self.cancel_button.clicked.connect(self.cancelled.emit)

    def _show_error(self, message: str) -> None:
        self.set_error(message)

    def _clear_error(self) -> None:
        self.set_error("")


class StaffFormPanel(_WarmModal):
    MODAL_WIDTH = 900

    def __init__(self, parent=None) -> None:
        super().__init__(
            "Add staff",
            "Create a staff account",
            "They set their own password the first time they sign in.",
            parent=parent,
        )
        self.name = QLineEdit(self.content_host)
        self.name.setObjectName("warmInput")
        self.name.setPlaceholderText("e.g. Maria Santos")
        self.email = QLineEdit(self.content_host)
        self.email.setObjectName("warmInput")
        self.email.setPlaceholderText("name@example.com")
        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(18)
        columns.addWidget(FormField("Full name", self.name, parent=self.content_host), 1)
        columns.addWidget(FormField("Email", self.email, parent=self.content_host), 1)
        self.body.addLayout(columns)

        # Kept as a QComboBox so SettingsPage and any callers still read the
        # submitted role from the same attribute; the visible control is the
        # reference's Staff/Admin chip pair.
        self.role = QComboBox(self.content_host)
        self.role.setObjectName("warmInput")
        self.role.addItems(["staff", "admin"])
        self.role.hide()
        self.role_chips = ChipGroup(
            [("staff", "Staff"), ("admin", "Admin")], parent=self.content_host
        )
        self.role.currentTextChanged.connect(self.role_chips.select)
        self.role_chips.changed.connect(self.role.setCurrentText)
        self.body.addWidget(FormField("Role", self.role_chips, parent=self.content_host))

        self.temp = QLineEdit(self.content_host)
        self.temp.setObjectName("warmInput")
        self.temp.setEchoMode(QLineEdit.EchoMode.Password)
        self.temp.setPlaceholderText("At least 8 characters")
        self.temp_row = _TempPasswordRow(self.temp, self.content_host)
        self.temp_show = self.temp_row.show_button
        self.generate = self.temp_row.generate_button
        self.body.addWidget(
            FormField("Temp password", self.temp_row, parent=self.content_host)
        )
        self.body.addWidget(
            NoticeBox(
                "Share the temp password in person. "
                "It stops working once it is changed.",
                tone="green",
                parent=self.content_host,
            )
        )
        self.body.addWidget(self.error_label)
        self.primary_button = self.add_primary("Add staff")
        self.primary_button.clicked.connect(self._on_save)

    def _on_save(self) -> None:
        if not self.name.text().strip() or not self.email.text().strip() \
                or not self.temp.text():
            self._show_error("Name, email, and temp password are required.")
            return
        self._clear_error()
        self.submitted.emit({"full_name": self.name.text().strip(),
                             "email": self.email.text().strip(),
                             "role": self.role.currentText(),
                             "temp_password": self.temp.text()})


class ResetPwPanel(_WarmModal):
    MODAL_WIDTH = 540

    def __init__(self, email: str, parent=None) -> None:
        super().__init__(
            "Reset password",
            "Reset password",
            f"{email} must change this temporary password on next login.",
            parent=parent,
        )
        self.email = email
        self.email_label = QLabel(email, self.content_host)
        self.email_label.setObjectName("settingsEmailValue")
        self.body.addWidget(
            FormField("Account", self.email_label, parent=self.content_host)
        )

        self.temp = QLineEdit(self.content_host)
        self.temp.setObjectName("warmInput")
        self.temp.setEchoMode(QLineEdit.EchoMode.Password)
        self.temp.setPlaceholderText("At least 8 characters")
        self.temp_row = _TempPasswordRow(self.temp, self.content_host)
        self.temp_show = self.temp_row.show_button
        self.generate = self.temp_row.generate_button
        self.temp_show.setText("Show")
        self.body.addWidget(
            FormField("Temp password", self.temp_row, parent=self.content_host)
        )
        self.body.addWidget(
            NoticeBox(
                "Share the temp password in person. "
                "It stops working once it is changed.",
                tone="green",
                parent=self.content_host,
            )
        )
        self.body.addWidget(self.error_label)
        self.primary_button = self.add_primary("Reset")
        self.primary_button.clicked.connect(self._on_save)

    def _on_save(self) -> None:
        value = self.temp.text()
        if not value:
            self._show_error("Enter a temporary password.")
            return
        if len(value) < 8:
            self._show_error("Temporary password must be at least 8 characters.")
            return
        self._clear_error()
        self.submitted.emit({"temp_password": value})


#: Sampled from the Accent color reference.
ACCENT_PRESETS = (
    "#D97757",
    "#C2410C",
    "#B45309",
    "#4D7C0F",
    "#0F766E",
    "#2563EB",
    "#7C3AED",
    "#BE185D",
)


def _preset_style(colour: str, selected: bool) -> str:
    border = "#171717" if selected else "transparent"
    return (
        f"QPushButton {{ background: {colour}; border: 2px solid {border};"
        f" border-radius: 17px; }}"
    )


def _contrast_ratio(colour: QColor, other: QColor) -> float:
    def _luminance(value: QColor) -> float:
        channels = []
        for raw in (value.redF(), value.greenF(), value.blueF()):
            channels.append(
                raw / 12.92 if raw <= 0.03928 else ((raw + 0.055) / 1.055) ** 2.4
            )
        return (
            0.2126 * channels[0]
            + 0.7152 * channels[1]
            + 0.0722 * channels[2]
        )

    high, low = sorted(
        (_luminance(colour), _luminance(other)), reverse=True
    )
    return (high + 0.05) / (low + 0.05)


class AccentPanel(_WarmModal):
    """Warm accent picker: presets, hue strip, hex, and a live preview."""

    MODAL_WIDTH = 540

    def __init__(self, current: str = "#D97757", parent=None) -> None:
        super().__init__(
            "Accent color",
            "Pick an accent",
            "Used for highlights on this PC only.",
            parent=parent,
        )
        self._current = "#D97757"
        colour = QColor(current)
        if colour.isValid():
            self._current = colour.name()

        presets = QWidget(self.content_host)
        presets.setObjectName("settingsFieldHost")
        preset_column = QVBoxLayout(presets)
        preset_column.setContentsMargins(0, 0, 0, 0)
        preset_column.setSpacing(8)
        preset_column.addWidget(_field_label("Presets", presets))
        preset_row = QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.setSpacing(10)
        self.preset_buttons: dict[str, QPushButton] = {}
        for colour_hex in ACCENT_PRESETS:
            button = QPushButton(presets)
            button.setFixedSize(34, 34)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(_preset_style(colour_hex, False))
            button.clicked.connect(
                lambda _checked=False, chosen=colour_hex: self.select_colour(chosen)
            )
            preset_row.addWidget(button)
            self.preset_buttons[colour_hex] = button
        preset_row.addStretch(1)
        preset_column.addLayout(preset_row)
        self.body.addWidget(presets)

        custom = QWidget(self.content_host)
        custom.setObjectName("settingsFieldHost")
        custom_column = QVBoxLayout(custom)
        custom_column.setContentsMargins(0, 0, 0, 0)
        custom_column.setSpacing(8)
        custom_column.addWidget(_field_label("Custom", custom))
        self.slider = QSlider(Qt.Orientation.Horizontal, custom)
        self.slider.setObjectName("accentSlider")
        self.slider.setRange(0, 359)
        self.slider.setFixedHeight(30)
        self.slider.valueChanged.connect(self._on_hue)
        custom_column.addWidget(self.slider)
        self.body.addWidget(custom)

        self.hex_edit = QLineEdit(self.content_host)
        self.hex_edit.setObjectName("warmInput")
        self.hex_edit.setMaxLength(9)
        self.hex_edit.setPlaceholderText("#D97757")
        self.hex_edit.editingFinished.connect(self._apply_hex)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(18)
        row.addWidget(FormField("Hex", self.hex_edit, parent=self.content_host), 1)

        preview_host = QWidget(self.content_host)
        preview_host.setObjectName("settingsFieldHost")
        preview_column = QVBoxLayout(preview_host)
        preview_column.setContentsMargins(0, 0, 0, 0)
        preview_column.setSpacing(6)
        preview_column.addWidget(_field_label("Preview", preview_host))
        preview_row = QHBoxLayout()
        preview_row.setContentsMargins(0, 0, 0, 0)
        preview_row.setSpacing(10)
        self.preview = QLabel("Record payment", preview_host)
        self.preview.setObjectName("settingsAccentPreview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedHeight(40)
        self.contrast_label = QLabel("", preview_host)
        self.contrast_label.setObjectName("settingsContrast")
        preview_row.addWidget(self.preview, 0)
        preview_row.addWidget(self.contrast_label, 1)
        preview_column.addLayout(preview_row)
        row.addWidget(preview_host, 1)
        self.body.addLayout(row)
        self.body.addWidget(self.error_label)

        self.primary_button = self.add_primary("Apply accent")
        self.primary_button.clicked.connect(self._on_save)
        self.select_colour(self._current)

    def select_colour(self, value: str, *, sync_hex: bool = True) -> None:
        colour = QColor(value)
        if not colour.isValid():
            return
        self._current = colour.name()
        if sync_hex:
            self.hex_edit.setText(self._current)
        hue = QColor(self._current).hsvHue()
        blocked = self.slider.blockSignals(True)
        try:
            self.slider.setValue(hue if hue >= 0 else 0)
        finally:
            self.slider.blockSignals(blocked)
        for preset, button in self.preset_buttons.items():
            button.setStyleSheet(
                _preset_style(preset, preset.lower() == self._current.lower())
            )
        self.preview.setStyleSheet(
            f"QLabel#settingsAccentPreview {{ background: {self._current};"
            " color: #FFFFFF; border-radius: 10px; padding: 0 16px;"
            " font-size: 13px; font-weight: 600; }}"
        )
        ratio = _contrast_ratio(QColor(self._current), QColor("#FFFFFF"))
        self.contrast_label.setText(f"Contrast {ratio:.1f}:1")

    def _on_hue(self, hue: int) -> None:
        self.select_colour(QColor.fromHsv(hue, 160, 220).name())

    def _apply_hex(self) -> None:
        colour = QColor(self.hex_edit.text().strip())
        if not colour.isValid():
            self._show_error("Enter a hex colour like #D97757.")
            return
        self._clear_error()
        self.select_colour(colour.name())

    def _on_save(self) -> None:
        colour = QColor(self.hex_edit.text().strip())
        if not colour.isValid():
            self._show_error("Enter a hex colour like #D97757.")
            return
        self._clear_error()
        self.submitted.emit({"accent": colour.name()})


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
