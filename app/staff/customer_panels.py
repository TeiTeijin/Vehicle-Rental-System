"""Customer panels: warm frosted-overlay sheets, never OS windows.

Same warm modal recipe as the counter rental wizard: a white rounded sheet on
the frosted overlay, a quiet eyebrow, a large title, two columns, and a sticky
footer whose primary action is the only dark button.

- CustomerFormPanel: the ONE customer form (Add/Edit here, inline create in
  the rental wizard). Duplicate phone/licence warns live.
- CustomerDetailsPanel: profile + licence docs, current/next + history,
  payments + incidents, staff notes, stats.
- SettlePanel: booking picker (oldest pre-selected), amount with cascade
  preview, one handover split into per-booking Payment rows.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.models import Booking, Users
from app.services import customer_service, payment_service
from app.staff.cards import Caption, Card
from app.staff.feedback import describe
from app.staff.rental_ui import StatusChip
from app.utils.money import ZERO, money, pesos

DASH = "—"

#: The customer sheet is the same 900px paper as the counter wizard.
CUSTOMER_MODAL_WIDTH = 900

#: Settle has two dense columns (booking picker + payment form), so it keeps
#: the reference's proportions with a little more room.
SETTLE_MODAL_WIDTH = 980

INK = "#171717"
MUTED = "#7D7265"
SOFT = "#8B7F70"
BORDER = "#E0D6C9"
CREAM = "#F1E8D8"
AMBER = "#F7E3C3"
RED = "#9B2C1F"

SOURCE_LABELS = {"walk_in": "Walk-in", "online": "Online"}
METHOD_LABELS = {"cash": "Cash", "gcash": "GCash", "card": "Card"}


# -- small presentation helpers ---------------------------------------------


def _label(
    text: str,
    *,
    parent: QWidget | None = None,
    size: int = 13,
    weight: QFont.Weight = QFont.Weight.Normal,
    colour: str = INK,
) -> QLabel:
    label = QLabel(text, parent)
    font = QFont("Inter")
    font.setPixelSize(size)
    font.setWeight(weight)
    label.setFont(font)
    label.setStyleSheet(f"color: {colour}; background: transparent;")
    return label


def _field_label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("customerFieldLabel")
    return label


def _section_label(text: str, parent: QWidget) -> QLabel:
    label = _label(
        text.upper(),
        parent=parent,
        size=11,
        weight=QFont.Weight.DemiBold,
        colour=MUTED,
    )
    label.setObjectName("customerSectionLabel")
    return label


def _initials(name: str) -> str:
    parts = [part for part in (name or "").split() if part]
    if not parts:
        return "NC"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _friendly(error: BaseException) -> str:
    message, _field, _kind = describe(error)
    return message


def _thumb(path: str | None, width: int = 160, height: int = 100) -> QPixmap | None:
    if not path:
        return None
    image = QImage(path)
    if image.isNull():
        return None
    return QPixmap.fromImage(
        image.scaled(
            width, height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    )


def _openable_doc(value: str | None) -> str | None:
    """Resolve a stored document path, or None when it is missing/unsafe.

    Details-panel display must never raise; a bad row simply shows `—`.
    """
    if not value:
        return None
    try:
        return str(customer_service.resolve_doc_path(value))
    except Exception:  # noqa: BLE001 - a broken row must not break the panel
        return None


def _date_text(value: date | None, *, fmt: str = "%d %b %Y") -> str:
    return value.strftime(fmt) if value else DASH


def _init_edit(edit: QLineEdit, placeholder: str = "") -> QLineEdit:
    edit.setObjectName("customerInput")
    if placeholder:
        edit.setPlaceholderText(placeholder)
    return edit


def _choice_height(button: QPushButton) -> QPushButton:
    button.setMinimumHeight(40)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


class _DocBox(QPushButton):
    """Dashed licence-photo box: title over a muted action line."""

    def __init__(self, title: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("docBox")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(88)
        self._title_text = title
        self._complete = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(3)
        self.preview_label = QLabel(self)
        self.preview_label.setObjectName("docBoxPreview")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setFixedHeight(46)
        self.preview_label.setVisible(False)
        layout.addWidget(self.preview_label)
        self.title_label = _label(
            title, parent=self, size=13, weight=QFont.Weight.DemiBold
        )
        self.title_label.setObjectName("docBoxTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_label = _label(
            "Upload or take photo", parent=self, size=11, colour=SOFT
        )
        self.subtitle_label.setObjectName("docBoxSubtitle")
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for label in (self.title_label, self.subtitle_label):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addStretch(1)
        layout.addWidget(self.title_label)
        layout.addWidget(self.subtitle_label)
        layout.addStretch(1)

    @property
    def title_text(self) -> str:
        return self._title_text

    def set_complete(self, complete: bool, *, action: str = "Replace photo") -> None:
        self._complete = bool(complete)
        self.subtitle_label.setText(action if complete else "Upload or take photo")

    def set_preview(self, pixmap: QPixmap | None) -> None:
        if pixmap is None or pixmap.isNull():
            self.preview_label.clear()
            self.preview_label.setVisible(False)
            return
        self.preview_label.setPixmap(
            pixmap.scaled(
                120,
                46,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.preview_label.setVisible(True)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        # Kept for old callers: the visible title is a label, not the button.
        self._title_text = text
        self.title_label.setText(text)


class _FactRow(QWidget):
    def __init__(
        self, key: str, value: str, *, parent: QWidget, value_wrap: bool = False
    ) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.key_label = _label(key, parent=self, size=12, colour=MUTED)
        self.key_label.setObjectName("customerFactKey")
        self.key_label.setFixedWidth(92)
        self.value_label = _label(
            value, parent=self, size=12, weight=QFont.Weight.DemiBold
        )
        self.value_label.setObjectName("customerFactValue")
        self.value_label.setWordWrap(value_wrap)
        row.addWidget(self.key_label, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.value_label, 1)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class _ModalSheet(QWidget):
    """Transparent host around a white 900px rounded customer sheet."""

    MODAL_WIDTH = CUSTOMER_MODAL_WIDTH

    def __init__(
        self, parent: QWidget | None = None, *, inline: bool = False
    ) -> None:
        super().__init__(parent)
        self.setObjectName("customerModal")
        self._inline = inline
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addStretch(1)

        self.card = Card(self, radius=18)
        self.card.setObjectName("customerModalCard")
        if inline:
            # The rental wizard embeds this form at ~550px; let it fill the
            # step instead of forcing the standalone 900px sheet.
            self.card.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
            outer.addWidget(self.card)
        else:
            self.card.setFixedWidth(self.MODAL_WIDTH)
            self.card.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred
            )
            outer.addWidget(self.card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)

        self.header_host = QWidget(self.card)
        self.header_host.setObjectName("customerModalHeader")
        self.header_layout = QVBoxLayout(self.header_host)
        self.header_layout.setContentsMargins(28, 20, 28, 0)
        self.header_layout.setSpacing(6)
        self.card.layout().addWidget(self.header_host)

        self.content_host = QWidget(self.card)
        self.content_host.setObjectName("customerModalBody")
        self.body = QVBoxLayout(self.content_host)
        self.body.setContentsMargins(28, 18, 28, 18)
        self.body.setSpacing(14)
        self._body = self.body
        self.card.layout().addWidget(self.content_host, 1)

        self.footer = QFrame(self.card)
        self.footer.setObjectName("customerModalFooter")
        self.footer_layout = QHBoxLayout(self.footer)
        self.footer_layout.setContentsMargins(28, 11, 28, 13)
        self.footer_layout.setSpacing(10)
        self.footer_layout.addStretch(1)
        self.footer.setVisible(False)
        self.card.layout().addWidget(self.footer)

        self.error_label = QLabel("", self.content_host)
        self.error_label.setObjectName("fieldError")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        self._error = self.error_label

    # -- header ---------------------------------------------------------

    def _head(self, eyebrow: str, title: str, subtitle: str = "") -> None:
        self.eyebrow_label = _label(
            eyebrow.upper(),
            parent=self.header_host,
            size=11,
            weight=QFont.Weight.DemiBold,
            colour="#6B5E4F",
        )
        self.eyebrow_label.setObjectName("customerEyebrow")
        self.eyebrow_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.eyebrow_label.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self.header_layout.addWidget(
            self.eyebrow_label, 0, Qt.AlignmentFlag.AlignLeft
        )

        self.title_label = _label(
            title,
            parent=self.header_host,
            size=28,
            weight=QFont.Weight.Bold,
        )
        self.title_label.setObjectName("customerModalTitle")
        self.title_label.setWordWrap(True)
        self.header_layout.addWidget(self.title_label)

        self.subtitle_label = None
        if subtitle:
            self.subtitle_label = _label(
                subtitle,
                parent=self.header_host,
                size=13,
                colour=SOFT,
            )
            self.subtitle_label.setObjectName("customerModalSubtitle")
            self.subtitle_label.setWordWrap(True)
            self.header_layout.addWidget(self.subtitle_label)

        self._divider(self.header_layout)

    def _divider(self, layout: QVBoxLayout) -> QFrame:
        rule = QFrame(self.header_host)
        rule.setObjectName("customerModalDivider")
        rule.setFixedHeight(1)
        layout.addWidget(rule)
        return rule

    # -- footer ---------------------------------------------------------

    def _buttons(
        self, primary_text: str, *, back_text: str = "Back"
    ) -> tuple[QPushButton, QPushButton]:
        while self.footer_layout.count():
            item = self.footer_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.footer_layout.addStretch(1)
        back = QPushButton(back_text, self.footer)
        back.setObjectName("modalSecondary")
        back.setMinimumHeight(40)
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        primary = QPushButton(primary_text, self.footer)
        primary.setObjectName("modalPrimary")
        primary.setMinimumHeight(40)
        primary.setCursor(Qt.CursorShape.PointingHandCursor)
        self.footer_layout.addWidget(back)
        self.footer_layout.addWidget(primary)
        self.footer.setVisible(True)
        return back, primary

    def add_error(self) -> QLabel:
        self.body.addWidget(self.error_label)
        return self.error_label


# -- add / edit --------------------------------------------------------------


class CustomerFormPanel(_ModalSheet):
    """The single customer form. `preset_licence` for wizard inline-create."""

    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(
        self, context, user_id: int | None = None,
        *, preset_licence: str = "", parent=None, inline: bool = False,
    ) -> None:
        super().__init__(parent, inline=inline)
        self._context = context
        self._user_id = user_id
        self._edit = user_id is not None
        self._head(
            "Edit customer" if self._edit else "New customer",
            "Identity, licence and how to reach them",
            "Update contact details or replace the licence photos."
            if self._edit
            else "Required: name, contact number, licence number and expiry.",
        )
        if inline:
            font = self.title_label.font()
            font.setPixelSize(22)
            self.title_label.setFont(font)

        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(28)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        right_host = QWidget(self.content_host)
        right_host.setObjectName("customerPreviewColumn")
        right_host.setFixedWidth(276)
        right = QVBoxLayout(right_host)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(12)
        columns.addLayout(left, 1)
        columns.addWidget(right_host, 0)
        if inline:
            # The embedded wizard column is too narrow for the preview rail;
            # the form itself is the point there, so the summary stays out.
            right_host.setVisible(False)
        self.body.addLayout(columns)

        # -- identity ---------------------------------------------------
        left.addWidget(_section_label("Identity", self.content_host))
        identity = QGridLayout()
        identity.setContentsMargins(0, 0, 0, 0)
        identity.setHorizontalSpacing(16)
        identity.setVerticalSpacing(10)
        self.name_edit = _init_edit(
            QLineEdit(self.content_host), "Name as on the licence"
        )
        self.name_edit.setMaxLength(customer_service.MAX_TEXT_LENGTH)
        self.birth_edit = QDateEdit(self.content_host)
        self.birth_edit.setObjectName("customerInput")
        self.birth_edit.setCalendarPopup(True)
        self.birth_edit.setDisplayFormat("dd MMM yyyy")
        self.birth_edit.setMinimumDate(QDate(1900, 1, 1))
        self.birth_edit.setSpecialValueText("Not on file")
        self.birth_edit.setDate(self.birth_edit.minimumDate())
        self.birth_edit.setMaximumDate(QDate.currentDate())
        identity.addWidget(
            self._field("Full name", self.name_edit), 0, 0
        )
        identity.addWidget(
            self._field("Birthdate", self.birth_edit), 0, 1
        )
        identity.setColumnStretch(0, 1)
        identity.setColumnStretch(1, 1)
        left.addLayout(identity)

        # -- contact ----------------------------------------------------
        left.addWidget(_section_label("Contact", self.content_host))
        contact = QGridLayout()
        contact.setContentsMargins(0, 0, 0, 0)
        contact.setHorizontalSpacing(16)
        contact.setVerticalSpacing(10)
        self.phone_edit = _init_edit(
            QLineEdit(self.content_host), "09XX XXX XXXX"
        )
        self.phone_edit.setMaxLength(customer_service.MAX_TEXT_LENGTH)
        self.email_edit = _init_edit(
            QLineEdit(self.content_host), "For the receipt and app login"
        )
        self.email_edit.setMaxLength(customer_service.MAX_TEXT_LENGTH)
        self.address_edit = _init_edit(QLineEdit(self.content_host))
        self.address_edit.setMaxLength(customer_service.MAX_TEXT_LENGTH)
        self.emergency_edit = _init_edit(
            QLineEdit(self.content_host), "Name and number"
        )
        self.emergency_edit.setMaxLength(customer_service.MAX_TEXT_LENGTH)
        contact.addWidget(
            self._field("Contact number", self.phone_edit), 0, 0
        )
        contact.addWidget(self._field("Email", self.email_edit), 0, 1)
        contact.addWidget(
            self._field("Address", self.address_edit), 1, 0, 1, 2
        )
        contact.addWidget(
            self._field(
                "Emergency contact", self.emergency_edit, optional=True
            ),
            2, 0, 1, 2,
        )
        contact.setColumnStretch(0, 1)
        contact.setColumnStretch(1, 1)
        left.addLayout(contact)

        # -- licence ----------------------------------------------------
        left.addWidget(_section_label("Licence", self.content_host))
        licence = QGridLayout()
        licence.setContentsMargins(0, 0, 0, 0)
        licence.setHorizontalSpacing(16)
        licence.setVerticalSpacing(10)
        self.licence_edit = _init_edit(
            QLineEdit(self.content_host), "N01-23-456789"
        )
        self.licence_edit.setMaxLength(customer_service.MAX_LICENCE_LENGTH)
        self.licence_edit.setText(preset_licence)
        self.expiry_edit = QDateEdit(self.content_host)
        self.expiry_edit.setObjectName("customerInput")
        self.expiry_edit.setCalendarPopup(True)
        self.expiry_edit.setDisplayFormat("dd MMM yyyy")
        self.expiry_edit.setDate(QDate.currentDate().addYears(3))
        licence.addWidget(
            self._field("Licence number", self.licence_edit), 0, 0
        )
        licence.addWidget(
            self._field("Licence expiry", self.expiry_edit), 0, 1
        )
        licence.setColumnStretch(0, 1)
        licence.setColumnStretch(1, 1)
        left.addLayout(licence)

        doc_row = QHBoxLayout()
        doc_row.setContentsMargins(0, 0, 0, 0)
        doc_row.setSpacing(12)
        self.front_button = _DocBox("Licence front", self.content_host)
        self.front_button.clicked.connect(lambda: self._pick_doc("front"))
        self.back_button = _DocBox("Licence back", self.content_host)
        self.back_button.clicked.connect(lambda: self._pick_doc("back"))
        doc_row.addWidget(self.front_button, 1)
        doc_row.addWidget(self.back_button, 1)
        left.addLayout(doc_row)
        self._doc_paths: dict[str, str] = {}

        # -- source -----------------------------------------------------
        left.addWidget(_section_label("Source", self.content_host))
        self.source_combo = QComboBox(self.content_host)
        self.source_combo.addItems(["walk_in", "online"])
        self.source_combo.setVisible(False)
        self.source_combo.setObjectName("customerSourceCombo")
        self.source_group = QButtonGroup(self)
        self.source_group.setExclusive(True)
        self.source_chips: dict[str, QPushButton] = {}
        source_row = QHBoxLayout()
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(12)
        for key, text in (("walk_in", "Walk-in"), ("online", "Online")):
            chip = QPushButton(text, self.content_host)
            chip.setObjectName("sourceChip")
            chip.setCheckable(True)
            chip.setMinimumHeight(40)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            chip.clicked.connect(
                lambda _checked=False, chosen=key: self._choose_source(chosen)
            )
            self.source_group.addButton(chip)
            self.source_chips[key] = chip
            source_row.addWidget(chip)
        left.addLayout(source_row)
        left.addWidget(self.source_combo)

        self._dup_label = Caption("", parent=self.content_host)
        self._dup_label.setObjectName("customerDuplicateWarning")
        self._dup_label.setWordWrap(True)
        left.addWidget(self._dup_label)
        left.addWidget(self.error_label)
        left.addStretch(1)

        # -- right-hand preview ----------------------------------------
        self.preview_panel = QFrame(self.content_host)
        self.preview_panel.setObjectName("customerPreview")
        preview = QVBoxLayout(self.preview_panel)
        preview.setContentsMargins(18, 18, 18, 18)
        preview.setSpacing(10)
        self.preview_avatar = _label(
            "NC", parent=self.preview_panel, size=18, weight=QFont.Weight.Bold
        )
        self.preview_avatar.setObjectName("customerAvatar")
        self.preview_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_avatar.setFixedSize(56, 56)
        preview.addWidget(
            self.preview_avatar, 0, Qt.AlignmentFlag.AlignLeft
        )
        self.preview_name = _label(
            "New customer",
            parent=self.preview_panel,
            size=18,
            weight=QFont.Weight.Bold,
        )
        self.preview_name.setObjectName("customerPreviewName")
        self.preview_name.setWordWrap(True)
        preview.addWidget(self.preview_name)
        self._preview_rule = QFrame(self.preview_panel)
        self._preview_rule.setObjectName("customerPreviewRule")
        self._preview_rule.setFixedHeight(1)
        preview.addWidget(self._preview_rule)
        self.preview_phone = _FactRow(
            "Phone", "Not set", parent=self.preview_panel
        )
        self.preview_licence = _FactRow(
            "Licence", "Not set", parent=self.preview_panel
        )
        self.preview_expiry = _FactRow(
            "Expires", _date_text(date.today()), parent=self.preview_panel
        )
        self.preview_source = _FactRow(
            "Source", "Walk-in", parent=self.preview_panel
        )
        for row in (
            self.preview_phone, self.preview_licence,
            self.preview_expiry, self.preview_source,
        ):
            row.setObjectName("customerPreviewRow")
            preview.addWidget(row)
        right.addWidget(self.preview_panel)
        right.addWidget(
            self._notice(
                "A valid licence is required. No licence, no rental.",
                tone="amber",
            )
        )
        right.addStretch(1)

        back, go = self._buttons(
            "Create & continue"
            if user_id is None and preset_licence
            else "Save customer"
        )
        back.clicked.connect(self.cancelled.emit)
        go.clicked.connect(self._on_go)
        self._go = go

        self._duplicate_timer = QTimer(self)
        self._duplicate_timer.setSingleShot(True)
        self._duplicate_timer.setInterval(250)
        self._duplicate_timer.timeout.connect(self._check_duplicates)
        for widget in (self.phone_edit, self.licence_edit):
            widget.textChanged.connect(
                lambda _t: self._duplicate_timer.start()
            )

        for widget in (self.name_edit, self.phone_edit, self.licence_edit):
            widget.textChanged.connect(lambda _t: self._refresh_preview())
        self.expiry_edit.dateChanged.connect(lambda _d: self._refresh_preview())
        self.source_combo.currentIndexChanged.connect(
            lambda _i: self._sync_source_chips()
        )
        self.source_combo.currentIndexChanged.connect(
            lambda _i: self._refresh_preview()
        )
        self._choose_source("walk_in", update_combo=False)
        self._refresh_preview()

        if user_id is not None:
            self._prefill()
        self._refresh_preview()

    # -- small builders -------------------------------------------------

    @staticmethod
    def _field(
        label: str, widget: QWidget, *, optional: bool = False
    ) -> QWidget:
        host = QWidget(widget.parentWidget())
        host.setObjectName("customerField")
        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(5)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(5)
        head.addWidget(_field_label(label, host))
        if optional:
            suffix = _label("· optional", parent=host, size=12, colour=SOFT)
            suffix.setObjectName("customerFieldOptional")
            head.addWidget(suffix)
        head.addStretch(1)
        column.addLayout(head)
        column.addWidget(widget)
        return host

    @staticmethod
    def _notice(text: str, *, tone: str) -> QFrame:
        box = QFrame()
        box.setObjectName("customerLicenceNotice")
        box.setProperty("tone", tone)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(16, 12, 16, 12)
        label = _label(text, parent=box, size=12, colour=INK)
        label.setObjectName("customerNoticeText")
        label.setWordWrap(True)
        lay.addWidget(label, 1)
        return box

    # -- source chips ---------------------------------------------------

    def _choose_source(self, key: str, *, update_combo: bool = True) -> None:
        if update_combo:
            index = self.source_combo.findText(key)
            if index >= 0 and self.source_combo.currentIndex() != index:
                self.source_combo.setCurrentIndex(index)
        self._sync_source_chips()
        self._refresh_preview()

    def _sync_source_chips(self) -> None:
        key = self.source_combo.currentText()
        for source, chip in self.source_chips.items():
            blocked = chip.blockSignals(True)
            try:
                chip.setChecked(source == key)
            finally:
                chip.blockSignals(blocked)

    # -- data -----------------------------------------------------------

    def _prefill(self) -> None:
        with self._context.reading() as session:
            customer = session.get(Users, self._user_id)
            if customer is None:
                return
            snap = {
                "full_name": customer.full_name,
                "email": customer.email,
                "phone": customer.phone,
                "address": customer.address,
                "birthdate": customer.birthdate,
                "emergency_contact": customer.emergency_contact,
                "license_number": customer.license_number,
                "license_expiry": customer.license_expiry,
                "source": customer.source or "walk_in",
                "front": customer.licence_front_path,
                "back": customer.licence_back_path,
            }
        self.name_edit.setText(snap["full_name"] or "")
        self.email_edit.setText(snap["email"] or "")
        self.phone_edit.setText(snap["phone"] or "")
        self.address_edit.setText(snap["address"] or "")
        if snap["birthdate"] is not None:
            birth = snap["birthdate"]
            self.birth_edit.setDate(QDate(birth.year, birth.month, birth.day))
        else:
            self.birth_edit.setDate(self.birth_edit.minimumDate())
        self.emergency_edit.setText(snap["emergency_contact"] or "")
        self.licence_edit.setText(snap["license_number"] or "")
        if snap["license_expiry"] is not None:
            expiry = snap["license_expiry"]
            self.expiry_edit.setDate(QDate(expiry.year, expiry.month, expiry.day))
        self.source_combo.setCurrentText(snap["source"])
        if snap["front"]:
            self.front_button.set_complete(True)
        if snap["back"]:
            self.back_button.set_complete(True)
        self._sync_source_chips()
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        name = self.name_edit.text().strip()
        self.preview_avatar.setText(_initials(name))
        self.preview_name.setText(name or "New customer")
        self.preview_phone.set_value(self.phone_edit.text().strip() or "Not set")
        self.preview_licence.set_value(
            self.licence_edit.text().strip() or "Not set"
        )
        self.preview_expiry.set_value(
            self.expiry_edit.date().toString("dd MMM yyyy")
        )
        self.preview_source.set_value(
            SOURCE_LABELS.get(self.source_combo.currentText(), "Walk-in")
        )

    def _pick_doc(self, side: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, f"Licence {side}", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if path:
            self._doc_paths[side] = path
            button = self.front_button if side == "front" else self.back_button
            button.set_complete(True)

    def _check_duplicates(self) -> None:
        with self._context.reading() as session:
            hits = customer_service.find_duplicates(
                session,
                phone=self.phone_edit.text(),
                license_number=self.licence_edit.text(),
                exclude_id=self._user_id,
            )
            names = [c.full_name for c in hits]
        if names:
            self._dup_label.setText(
                f"Possible duplicate: {', '.join(names)}. Select them instead of creating a new record."
            )
        else:
            self._dup_label.setText("")

    def _fail(self, message: str) -> None:
        self._error.setText(message)
        self._error.setVisible(True)

    def _on_go(self) -> None:
        import re as _re

        if not self.name_edit.text().strip():
            self._fail("Customer name is required.")
            return
        if not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", self.email_edit.text().strip()):
            self._fail("Enter a valid email address.")
            return
        phone = self.phone_edit.text().strip()
        if phone:
            digits = _re.sub(r"\D", "", phone)
            if len(digits) < 7 or len(digits) > 15:
                self._fail("Contact number must be 7–15 digits.")
                return
        if not self.address_edit.text().strip():
            self._fail("Customer address is required.")
            return
        if not self.licence_edit.text().strip():
            self._fail("No licence, no rental: the licence number is required.")
            return
        if (
            self.birth_edit.date() != self.birth_edit.minimumDate()
            and self.birth_edit.date().toPython() > date.today()
        ):
            self._fail("Birthdate cannot be in the future.")
            return
        self._error.setVisible(False)
        birthdate = (
            None
            if self.birth_edit.date() == self.birth_edit.minimumDate()
            else self.birth_edit.date().toPython()
        )
        self.submitted.emit(
            {
                "full_name": self.name_edit.text().strip(),
                "email": self.email_edit.text().strip(),
                "phone": self.phone_edit.text().strip() or None,
                "address": self.address_edit.text().strip(),
                "birthdate": birthdate,
                "emergency_contact": self.emergency_edit.text().strip() or None,
                "license_number": self.licence_edit.text().strip(),
                "license_expiry": self.expiry_edit.date().toPython(),
                "source": self.source_combo.currentText(),
                "front_src": self._doc_paths.get("front"),
                "back_src": self._doc_paths.get("back"),
            }
        )


# -- settle ------------------------------------------------------------------


class _BookingChoice(QPushButton):
    """Checkable booking row with its own #ref / owes / coverage layout."""

    def __init__(
        self, booking_id: int, owed, parent: QWidget
    ) -> None:
        super().__init__(parent)
        self.booking_id = booking_id
        self.owed = owed
        self.setObjectName("settleBooking")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(66)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)
        self.check_label = _label("", parent=self, size=12, weight=QFont.Weight.Bold)
        self.check_label.setObjectName("settleCheck")
        self.check_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.check_label.setFixedSize(22, 22)
        layout.addWidget(self.check_label, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        self.ref_label = _label(
            f"#{booking_id}", parent=self, size=14, weight=QFont.Weight.Bold
        )
        self.ref_label.setObjectName("settleRef")
        self.owed_label = _label(
            f"Owes {pesos(owed)}", parent=self, size=11, colour=MUTED
        )
        self.owed_label.setObjectName("settleOwed")
        text.addWidget(self.ref_label)
        text.addWidget(self.owed_label)
        layout.addLayout(text, 1)
        self.coverage_label = _label(
            "Not covered", parent=self, size=11, colour=MUTED
        )
        self.coverage_label.setObjectName("settleCoverage")
        self.coverage_label.setProperty("state", "none")
        layout.addWidget(
            self.coverage_label, 0, Qt.AlignmentFlag.AlignVCenter
        )
        for child in (self.check_label, self.ref_label, self.owed_label, self.coverage_label):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.toggled.connect(self._sync_tick)

    def _sync_tick(self) -> None:
        checked = self.isChecked()
        self.check_label.setText("✓" if checked else "")
        self.check_label.setStyleSheet(
            "background: {0}; border: 1px solid {0}; border-radius: 6px;"
            "color: {1}; font-size: 12px; font-weight: 700;".format(
                "#171717" if checked else "transparent",
                "#FFFFFF" if checked else "transparent",
            )
        )

    def set_coverage(self, text: str, state: str) -> None:
        self.coverage_label.setText(text)
        self.coverage_label.setProperty("state", state)
        background, colour = {
            "full": ("#DCEEDC", "#3F6D4E"),
            "partial": ("#F7E3C3", "#8A6523"),
            "none": ("transparent", "#7D7265"),
        }.get(state, ("transparent", "#7D7265"))
        self.coverage_label.setStyleSheet(
            f"background: {background}; color: {colour};"
            "border-radius: 9px; padding: 3px 9px;"
        )
        self.coverage_label.style().unpolish(self.coverage_label)
        self.coverage_label.style().polish(self.coverage_label)


class SettlePanel(_ModalSheet):
    """Pick the booking, type the handover: remainder cascades oldest-first."""

    submitted = Signal(dict)
    cancelled = Signal()
    MODAL_WIDTH = SETTLE_MODAL_WIDTH

    def __init__(self, context, customer_id: int, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        self._customer_id = customer_id
        with self._context.reading() as session:
            customer = session.get(Users, customer_id)
            self._customer_name = customer.full_name if customer else DASH
            self._unpaid = (
                customer_service.unpaid_bookings(session, customer)
                if customer is not None
                else []
            )
        self._head(
            "Settle balance",
            "One handover, split across bookings.",
            f"{self._customer_name} · Customer #{customer_id}",
        )
        self._total_owed = sum((owed for _, owed in self._unpaid), ZERO)

        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(28)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)
        right_host = QWidget(self.content_host)
        right_host.setObjectName("settleColumn")
        right_host.setFixedWidth(360)
        right = QVBoxLayout(right_host)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(10)
        columns.addLayout(left, 1)
        columns.addWidget(right_host, 0)
        self.body.addLayout(columns)

        left.addWidget(
            _label(
                "Bookings with a balance",
                parent=self.content_host,
                size=16,
                weight=QFont.Weight.Bold,
            )
        )
        left.addWidget(
            Caption(
                "The amount is applied from the top down.",
                parent=self.content_host,
                colour=MUTED,
                size=12,
            )
        )

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons_map: dict[int, _BookingChoice] = {}
        if not self._unpaid:
            left.addWidget(Caption("Nothing owed.", parent=self.content_host))
        else:
            for booking_id, owed in self._unpaid:
                button = _BookingChoice(booking_id, owed, self.content_host)
                self._group.addButton(button)
                left.addWidget(button)
                self._buttons_map[booking_id] = button
            first = self._unpaid[0][0]
            self._buttons_map[first].setChecked(True)
            for button in self._buttons_map.values():
                button.clicked.connect(self._refresh_preview)
            rule = QFrame(self.content_host)
            rule.setObjectName("customerModalDivider")
            rule.setFixedHeight(1)
            left.addWidget(rule)
            total_row = QHBoxLayout()
            total_row.setContentsMargins(0, 2, 0, 0)
            total_row.addWidget(
                _label(
                    "Total owed here",
                    parent=self.content_host,
                    size=13,
                    weight=QFont.Weight.DemiBold,
                )
            )
            total_row.addStretch(1)
            self.total_label = _label(
                pesos(self._total_owed),
                parent=self.content_host,
                size=14,
                weight=QFont.Weight.Bold,
            )
            self.total_label.setObjectName("settleTotal")
            total_row.addWidget(self.total_label)
            left.addLayout(total_row)
        left.addStretch(1)
        left.addWidget(self.error_label)

        # -- amount ----------------------------------------------------
        right.addWidget(_field_label("Amount (PHP)", self.content_host))
        amount_box = QFrame(self.content_host)
        amount_box.setObjectName("settleAmountBox")
        amount_row = QHBoxLayout(amount_box)
        amount_row.setContentsMargins(14, 0, 12, 0)
        amount_row.setSpacing(6)
        prefix = _label("₱", parent=amount_box, size=18, weight=QFont.Weight.Bold)
        prefix.setObjectName("settleAmountPrefix")
        amount_row.addWidget(prefix)
        self.amount_edit = QLineEdit(amount_box)
        self.amount_edit.setObjectName("settleAmountInput")
        self.amount_edit.setMinimumHeight(48)
        amount_row.addWidget(self.amount_edit, 1)
        right.addWidget(amount_box)

        self.pay_all_button = QPushButton(
            f"Pay all · {pesos(self._total_owed)}", self.content_host
        )
        self.pay_all_button.setObjectName("settlePayAll")
        self.pay_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pay_all_button.clicked.connect(self._pay_all)
        right.addWidget(
            self.pay_all_button, 0, Qt.AlignmentFlag.AlignLeft
        )

        right.addSpacing(4)
        right.addWidget(_field_label("Method", self.content_host))
        self.method_combo = QComboBox(self.content_host)
        self.method_combo.addItems(list(payment_service.METHODS))
        self.method_combo.setVisible(False)
        self._method_group = QButtonGroup(self)
        self._method_group.setExclusive(True)
        self.method_chips: dict[str, QPushButton] = {}
        method_row = QHBoxLayout()
        method_row.setContentsMargins(0, 0, 0, 0)
        method_row.setSpacing(10)
        # Reference order: Cash, GCash, Card. The hidden combo keeps the
        # service's own order for existing callers.
        method_order = [m for m in ("cash", "gcash", "card") if m in payment_service.METHODS]
        for method in method_order + [
            m for m in payment_service.METHODS if m not in method_order
        ]:
            chip = QPushButton(
                METHOD_LABELS.get(method, method.title()), self.content_host
            )
            chip.setObjectName("settleMethod")
            chip.setCheckable(True)
            chip.setMinimumHeight(58)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            chip.clicked.connect(
                lambda _checked=False, chosen=method: self._choose_method(chosen)
            )
            self._method_group.addButton(chip)
            self.method_chips[method] = chip
            method_row.addWidget(chip)
        right.addLayout(method_row)
        right.addWidget(self.method_combo)

        right.addSpacing(2)
        self.ref_label = _field_label("Reference no.", self.content_host)
        right.addWidget(self.ref_label)
        self.ref_hint = _label(
            "Not needed for cash", parent=self.content_host, size=11, colour=SOFT
        )
        self.ref_hint.setObjectName("settleRefHint")
        right.addWidget(self.ref_hint)
        self.ref_edit = QLineEdit(self.content_host)
        self.ref_edit.setObjectName("customerInput")
        self.ref_edit.setPlaceholderText("Enter reference number")
        right.addWidget(self.ref_edit)

        self.summary = QFrame(self.content_host)
        self.summary.setObjectName("settleSummary")
        summary = QVBoxLayout(self.summary)
        summary.setContentsMargins(14, 10, 14, 10)
        summary.setSpacing(6)
        self.picked_row = self._summary_row(summary, "#—", "—")
        self.still_row = self._summary_row(
            summary, "Still owing across these", pesos(self._total_owed)
        )
        rule = QFrame(self.summary)
        rule.setObjectName("settleSummaryRule")
        rule.setFixedHeight(1)
        summary.addWidget(rule)
        self.recording_row = self._summary_row(
            summary, "Recording now", "—", strong=True
        )
        right.addWidget(self.summary)
        self.preview = Caption("", parent=self.content_host, colour=MUTED, size=11)
        self.preview.setWordWrap(True)
        right.addWidget(self.preview)
        right.addStretch(1)

        self._choose_method(
            self.method_combo.currentText() or "cash", update_combo=False
        )
        self.amount_edit.textChanged.connect(lambda _t: self._refresh_preview())
        self.method_combo.currentIndexChanged.connect(
            lambda _i: self._sync_method_chips()
        )
        self.method_combo.currentIndexChanged.connect(
            lambda _i: self._refresh_method_hint()
        )
        self._refresh_method_hint()

        back, go = self._buttons("Record payment")
        back.clicked.connect(self.cancelled.emit)
        go.clicked.connect(self._on_go)
        self._go = go
        self._refresh_preview()

    # -- builders -------------------------------------------------------

    @staticmethod
    def _summary_row(
        layout: QVBoxLayout,
        label: str,
        value: str,
        *,
        strong: bool = False,
    ) -> tuple[QLabel, QLabel]:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        key = _label(label, size=12, colour=MUTED if not strong else INK)
        key.setWordWrap(True)
        val = _label(
            value,
            size=13 if strong else 12,
            weight=QFont.Weight.Bold,
            colour=INK,
        )
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(key, 1)
        row.addWidget(val)
        layout.addLayout(row)
        return key, val

    # -- method ---------------------------------------------------------

    def _choose_method(self, method: str, *, update_combo: bool = True) -> None:
        if update_combo:
            index = self.method_combo.findText(method)
            if index >= 0 and self.method_combo.currentIndex() != index:
                self.method_combo.setCurrentIndex(index)
        self._sync_method_chips()
        self._refresh_method_hint()

    def _sync_method_chips(self) -> None:
        current = self.method_combo.currentText()
        for method, chip in self.method_chips.items():
            blocked = chip.blockSignals(True)
            try:
                chip.setChecked(method == current)
            finally:
                chip.blockSignals(blocked)

    def _refresh_method_hint(self) -> None:
        gcash = self.method_combo.currentText() == "gcash"
        self.ref_hint.setText(
            "Required for GCash" if gcash else "Not needed for cash"
            if self.method_combo.currentText() == "cash"
            else "Optional reference"
        )
        self.ref_edit.setPlaceholderText(
            "GCash reference" if gcash else "Enter reference number"
        )

    def _pay_all(self) -> None:
        if self._total_owed > ZERO:
            self.amount_edit.setText(f"{self._total_owed:,.2f}")

    # -- data -----------------------------------------------------------

    def _picked(self) -> int | None:
        for booking_id, button in getattr(self, "_buttons_map", {}).items():
            if button.isChecked():
                return booking_id
        return self._unpaid[0][0] if self._unpaid else None

    def _refresh_preview(self) -> None:
        picked = self._picked()
        if picked is None:
            self.amount_edit.setText("")
            self.preview.setText("")
            return
        owed_here = next(owed for bid, owed in self._unpaid if bid == picked)
        if not self.amount_edit.text().strip():
            self.amount_edit.setText(f"{owed_here:,.2f}")
        raw = self.amount_edit.text()
        try:
            legs = customer_service.allocate_settlement(
                self._unpaid, picked, raw or "0"
            )
        except Exception as exc:  # noqa: BLE001 - advisory preview only
            self.preview.setText(_friendly(exc))
            for button in self._buttons_map.values():
                button.set_coverage("Not covered", "none")
            self._go.setText("Record payment")
            return
        self.preview.setText(
            " + ".join(f"#{bid} {owed:,.2f}" for bid, owed in legs)
        )
        covered = {bid: owed for bid, owed in legs}
        for booking_id, button in self._buttons_map.items():
            if booking_id not in covered:
                button.set_coverage("Not covered", "none")
                continue
            take = covered[booking_id]
            if take >= button.owed:
                button.set_coverage(
                    f"{pesos(take)} · paid in full", "full"
                )
            else:
                button.set_coverage(f"{pesos(take)} covered", "partial")
        amount = money(raw or "0")
        first_id, first_take = legs[0]
        self.picked_row[0].setText(f"#{first_id}")
        self.picked_row[1].setText(pesos(first_take))
        still = max(ZERO, self._total_owed - amount)
        self.still_row[1].setText(pesos(still))
        self.recording_row[1].setText(pesos(amount))
        self._go.setText(f"Record payment · {pesos(amount)}")

    def _on_go(self) -> None:
        picked = self._picked()
        if picked is None:
            self._error.setText("Nothing owed.")
            self._error.setVisible(True)
            return
        if self.method_combo.currentText() == "gcash" and not self.ref_edit.text().strip():
            self._error.setText("A GCash reference number is required.")
            self._error.setVisible(True)
            return
        try:
            legs = customer_service.allocate_settlement(
                self._unpaid, picked, self.amount_edit.text()
            )
        except Exception as exc:  # noqa: BLE001
            self._error.setText(_friendly(exc))
            self._error.setVisible(True)
            return
        self._error.setVisible(False)
        self.submitted.emit(
            {
                "legs": [(bid, str(owed)) for bid, owed in legs],
                "method": self.method_combo.currentText(),
                "reference_no": self.ref_edit.text().strip(),
            }
        )


# -- profile -----------------------------------------------------------------


class _ActivityTile(QFrame):
    def __init__(
        self,
        title: str,
        detail: str,
        *,
        tone: str = "plain",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("customerActivityTile")
        self.setProperty("tone", tone)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 9, 16, 10)
        lay.setSpacing(2)
        head = _label(title, parent=self, size=12, weight=QFont.Weight.DemiBold)
        head.setObjectName("customerActivityTitle")
        self.detail_label = _label(detail, parent=self, size=12, colour=INK)
        self.detail_label.setObjectName("customerActivityDetail")
        self.detail_label.setWordWrap(True)
        lay.addWidget(head)
        lay.addWidget(self.detail_label)


class _MetricTile(QFrame):
    def __init__(
        self, label: str, value: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("customerMetricTile")
        self.setMinimumHeight(66)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(1)
        self.label = _label(label, parent=self, size=11, colour=MUTED)
        self.label.setObjectName("customerMetricLabel")
        self.value_label = _label(
            value, parent=self, size=18, weight=QFont.Weight.Bold
        )
        self.value_label.setObjectName("customerMetricValue")
        lay.addWidget(self.label)
        lay.addWidget(self.value_label)


class CustomerDetailsPanel(_ModalSheet):
    closed = Signal()
    edit_requested = Signal(int)
    rental_requested = Signal(int)

    def __init__(self, context, user_id: int, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        self._user_id = user_id
        self._load()

    # -- data -----------------------------------------------------------

    def _read(self) -> dict | None:
        with self._context.reading() as session:
            customer = session.get(Users, self._user_id)
            if customer is None or customer.role != "customer":
                return None
            bookings = (
                session.query(Booking)
                .filter(Booking.user_id == customer.user_id)
                .order_by(Booking.start_date.desc())
                .all()
            )
            booking_rows = [
                {
                    "ref": b.booking_id,
                    "vehicle": f"{b.vehicle.plate_number} {b.vehicle.make} {b.vehicle.model}" if b.vehicle else DASH,
                    "category": b.vehicle.category.category_name if b.vehicle and b.vehicle.category else DASH,
                    "start": b.start_date,
                    "end": b.end_date,
                    "status": b.status,
                    "total": b.total_cost,
                    "paid": payment_service.booking_balance(session, b).amount_paid,
                    "owed": payment_service.booking_balance(session, b).balance,
                }
                for b in bookings
            ]
            payments = []
            for b in bookings:
                for p in b.payments:
                    payments.append(
                        {
                            "ref": b.booking_id,
                            "amount": p.amount,
                            "method": p.method,
                            "kind": p.kind,
                            "status": p.status,
                            "when": p.paid_at,
                        }
                    )
            penalties = []
            for b in bookings:
                for penalty in b.penalties:
                    inspections = "; ".join(
                        f"{r.inspection_type} {r.mileage_reading:,}km"
                        + (f" ({r.damage_notes})" if r.damage_notes else "")
                        for r in b.inspections
                    ) or "no inspection"
                    penalties.append(
                        {
                            "ref": b.booking_id,
                            "type": penalty.penalty_type,
                            "amount": penalty.amount,
                            "description": penalty.description,
                            "inspections": inspections,
                        }
                    )
            notes = [
                {
                    "note": n.note,
                    "author_id": n.author_id,
                    "when": n.created_at,
                }
                for n in customer_service.list_notes(session, customer)
            ]
            return {
                "name": customer.full_name,
                "email": customer.email,
                "phone": customer.phone or DASH,
                "address": customer.address,
                "birthdate": customer.birthdate,
                "emergency": customer.emergency_contact or DASH,
                "licence_number": customer.license_number,
                "licence_expiry": customer.license_expiry,
                "source": (customer.source or "walk_in").replace("_", " ").title(),
                "is_active": customer.is_active,
                "flag_reason": (customer.flag_reason or "").strip() or None,
                "front": customer.licence_front_path,
                "back": customer.licence_back_path,
                "bookings": booking_rows,
                "payments": payments,
                "penalties": penalties,
                "notes": notes,
            }

    # -- layout ---------------------------------------------------------

    def _load(self) -> None:
        data = self._read()
        if data is None:
            self._head("Customer", "Customer profile")
            self.body.addWidget(
                Caption("That customer no longer exists.", parent=self.content_host)
            )
            return
        self._data = data
        self._build_header(data)
        self._build_activity_strip(data)
        self._build_body(data)
        self._build_footer()

    def _build_header(self, data: dict) -> None:
        name = data["name"]
        self.eyebrow_label = _label(
            f"Customer #{self._user_id}",
            parent=self.header_host,
            size=11,
            weight=QFont.Weight.DemiBold,
            colour="#6B5E4F",
        )
        self.eyebrow_label.setObjectName("customerEyebrow")
        self.eyebrow_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.eyebrow_label.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self.header_layout.addWidget(
            self.eyebrow_label, 0, Qt.AlignmentFlag.AlignLeft
        )

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        self.avatar_label = _label(
            _initials(name),
            parent=self.header_host,
            size=18,
            weight=QFont.Weight.Bold,
        )
        self.avatar_label.setObjectName("customerAvatarLarge")
        self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_label.setFixedSize(58, 58)
        row.addWidget(self.avatar_label, 0, Qt.AlignmentFlag.AlignVCenter)

        identity = QVBoxLayout()
        identity.setContentsMargins(0, 0, 0, 0)
        identity.setSpacing(3)
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        self.name_label = _label(
            name,
            parent=self.header_host,
            size=24,
            weight=QFont.Weight.Bold,
        )
        self.name_label.setObjectName("customerProfileName")
        name_row.addWidget(self.name_label)
        self.tier_chip = StatusChip(
            self._tier(data), tone="neutral", parent=self.header_host
        )
        name_row.addWidget(self.tier_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        if not data["is_active"]:
            name_row.addWidget(
                StatusChip("Deactivated", tone="danger", parent=self.header_host),
                0,
                Qt.AlignmentFlag.AlignVCenter,
            )
        if data["flag_reason"]:
            name_row.addWidget(
                StatusChip("Flagged", tone="warn", parent=self.header_host),
                0,
                Qt.AlignmentFlag.AlignVCenter,
            )
        name_row.addStretch(1)
        identity.addLayout(name_row)
        meta = _label(
            data["source"],
            parent=self.header_host,
            size=12,
            colour=SOFT,
        )
        meta.setObjectName("customerProfileMeta")
        identity.addWidget(meta)
        row.addLayout(identity, 1)

        contact = QVBoxLayout()
        contact.setContentsMargins(0, 0, 0, 0)
        contact.setSpacing(6)
        self.contact_label = _label(
            f"{data['phone']} · {data['email']}",
            parent=self.header_host,
            size=12,
            colour=MUTED,
        )
        self.contact_label.setObjectName("customerProfileContact")
        self.contact_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        contact.addWidget(self.contact_label)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self.copy_phone = QPushButton("Copy phone", self.header_host)
        self.copy_phone.setObjectName("modalSecondary")
        self.copy_phone.clicked.connect(
            lambda: QApplication.clipboard().setText(
                data["phone"] if data["phone"] != DASH else ""
            )
        )
        self.copy_email = QPushButton("Copy email", self.header_host)
        self.copy_email.setObjectName("modalSecondary")
        self.copy_email.clicked.connect(
            lambda: QApplication.clipboard().setText(data["email"])
        )
        self.mailto = QPushButton("Email", self.header_host)
        self.mailto.setObjectName("modalSecondary")
        self.mailto.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(f"mailto:{data['email']}"))
        )
        for button in (self.copy_phone, self.copy_email, self.mailto):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(34)
            actions.addWidget(button)
        contact.addLayout(actions)
        row.addLayout(contact, 0)
        self.header_layout.addLayout(row)
        self._divider(self.header_layout)

    def _tier(self, data: dict) -> str:
        rentals = sum(
            1 for booking in data["bookings"] if booking["status"] == "completed"
        )
        return "Frequent" if rentals >= 10 else "Regular"

    def _build_activity_strip(self, data: dict) -> None:
        actives = [
            booking
            for booking in data["bookings"]
            if booking["status"] in ("pending", "confirmed", "ongoing")
        ]
        strip = QFrame(self.card)
        strip.setObjectName("customerActivityStrip")
        lay = QHBoxLayout(strip)
        lay.setContentsMargins(28, 12, 28, 12)
        lay.setSpacing(16)
        if actives:
            current = actives[0]
            out_detail = (
                f"#{current['ref']} · {current['vehicle']} · "
                f"{current['start']:%d %b} → {current['end']:%d %b}"
            )
        else:
            out_detail = "No vehicle out."
        out = _ActivityTile(
            "Out now", out_detail, tone="amber", parent=strip
        )
        if len(actives) > 1:
            nxt = actives[1]
            next_detail = (
                f"#{nxt['ref']} · {nxt['vehicle']} · "
                f"{nxt['start']:%d %b} → {nxt['end']:%d %b}"
            )
        else:
            next_detail = "No upcoming booking."
        upcoming = _ActivityTile(
            "Next", next_detail, tone="plain", parent=strip
        )
        lay.addWidget(out, 1)
        lay.addWidget(upcoming, 1)
        self.card.layout().insertWidget(1, strip)

    def _build_body(self, data: dict) -> None:
        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(26)
        left_host = QWidget(self.content_host)
        left_host.setObjectName("customerProfileColumn")
        left_host.setFixedWidth(300)
        left = QVBoxLayout(left_host)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(12)
        columns.addWidget(left_host, 0)
        columns.addLayout(right, 1)
        self.body.addLayout(columns)

        # -- summary ---------------------------------------------------
        left.addWidget(_section_label("Summary", self.content_host))
        tiles = QGridLayout()
        tiles.setContentsMargins(0, 0, 0, 0)
        tiles.setHorizontalSpacing(8)
        tiles.setVerticalSpacing(8)
        done = [
            booking
            for booking in data["bookings"]
            if booking["status"] == "completed"
        ]
        spent = sum(
            (booking["paid"] for booking in data["bookings"]), ZERO
        )
        lengths = [
            (booking["end"] - booking["start"]).days for booking in done
        ]
        avg = sum(lengths) / len(lengths) if lengths else 0
        categories: dict[str, int] = {}
        for booking in done:
            categories[booking["category"]] = (
                categories.get(booking["category"], 0) + 1
            )
        top = max(categories, key=categories.get) if categories else DASH
        metrics = (
            ("Rentals", str(len(done))),
            ("Spent", pesos(spent)),
            ("Average", f"{avg:.1f} days"),
            ("Mostly", top),
        )
        for index, (label, value) in enumerate(metrics):
            tiles.addWidget(
                _MetricTile(label, value, self.content_host),
                index // 2,
                index % 2,
            )
        tiles.setColumnStretch(0, 1)
        tiles.setColumnStretch(1, 1)
        left.addLayout(tiles)

        # -- profile ---------------------------------------------------
        left.addWidget(_section_label("Profile", self.content_host))
        left.addWidget(
            self._facts(
                (
                    ("Address", data["address"], True),
                    (
                        "Birthdate",
                        f"{data['birthdate']:%d %b %Y}"
                        if data["birthdate"]
                        else DASH,
                        False,
                    ),
                    ("Emergency", data["emergency"], True),
                    ("Source", data["source"], False),
                )
            )
        )

        # -- licence ---------------------------------------------------
        left.addWidget(_section_label("Licence", self.content_host))
        licence_tone = self._licence_tone(data["licence_expiry"])
        left.addWidget(
            self._facts(
                (
                    ("Number", data["licence_number"] or DASH, False),
                    (
                        "Valid to",
                        _date_text(data["licence_expiry"]),
                        False,
                    ),
                )
            )
        )
        self.licence_chip = StatusChip(
            self._licence_text(data["licence_expiry"]),
            tone=licence_tone,
            parent=self.content_host,
        )
        left.addWidget(
            self.licence_chip, 0, Qt.AlignmentFlag.AlignLeft
        )
        docs = QHBoxLayout()
        docs.setContentsMargins(0, 2, 0, 0)
        docs.setSpacing(10)
        shown_docs = False
        for label, stored in (
            ("Licence front", data["front"]),
            ("Licence back", data["back"]),
        ):
            path = _openable_doc(stored)
            box = _DocBox(label, self.content_host)
            if path:
                thumb = _thumb(path)
                shown_docs = True
                box.set_complete(True, action="Photo on file · open")
                box.set_preview(thumb)
                box.clicked.connect(
                    lambda _checked=False, _p=path: QDesktopServices.openUrl(
                        QUrl.fromLocalFile(_p)
                    )
                )
            else:
                box.subtitle_label.setText("No photo")
                box.setEnabled(False)
            docs.addWidget(box, 1)
        left.addLayout(docs)
        if not shown_docs:
            left.addWidget(
                Caption(
                    "No licence photos on file.",
                    parent=self.content_host,
                    colour=MUTED,
                    size=11,
                )
            )

        # -- notes -----------------------------------------------------
        left.addWidget(_section_label("Staff notes", self.content_host))
        self._notes_host = QVBoxLayout()
        self._notes_host.setContentsMargins(0, 0, 0, 0)
        self._notes_host.setSpacing(6)
        notes_box = QWidget(self.content_host)
        notes_box.setObjectName("customerNotesHost")
        notes_box.setLayout(self._notes_host)
        left.addWidget(notes_box)
        for note in data["notes"]:
            when = (
                note["when"].strftime("%d %b %Y %H:%M")
                if note["when"]
                else DASH
            )
            author = (
                f"staff #{note['author_id']}" if note["author_id"] else "staff"
            )
            self._notes_host.addWidget(
                Caption(
                    f"{note['note']} ({author}, {when})",
                    parent=notes_box,
                    colour=MUTED,
                    size=11,
                )
            )
        note_row = QHBoxLayout()
        note_row.setContentsMargins(0, 0, 0, 0)
        note_row.setSpacing(8)
        self.note_edit = QLineEdit(self.content_host)
        self.note_edit.setObjectName("customerInput")
        self.note_edit.setMaxLength(customer_service.MAX_NOTE_LENGTH)
        self.note_edit.setPlaceholderText("Add a note…")
        self.note_edit.returnPressed.connect(self._on_add_note)
        note_add = QPushButton("Add", self.content_host)
        note_add.setObjectName("modalSecondary")
        note_add.setMinimumHeight(40)
        note_add.clicked.connect(self._on_add_note)
        note_row.addWidget(self.note_edit, 1)
        note_row.addWidget(note_add)
        left.addLayout(note_row)
        self._error = self.error_label
        left.addWidget(self.error_label)
        left.addStretch(1)

        # -- balance + tabs -------------------------------------------
        owed_total = sum(
            (booking["owed"] for booking in data["bookings"]), ZERO
        )
        owed_count = sum(
            1 for booking in data["bookings"] if booking["owed"] > ZERO
        )
        self.owed_banner = QFrame(self.content_host)
        self.owed_banner.setObjectName("customerOwedBanner")
        self.owed_banner.setProperty(
            "tone", "owed" if owed_total > ZERO else "clear"
        )
        owed_row = QHBoxLayout(self.owed_banner)
        owed_row.setContentsMargins(16, 12, 16, 12)
        owed_row.setSpacing(10)
        self.owed_label = _label(
            f"Owes across {owed_count} rental(s)"
            if owed_total > ZERO
            else "No balance due",
            parent=self.owed_banner,
            size=13,
            weight=QFont.Weight.DemiBold,
            colour=RED if owed_total > ZERO else "#3F6D4E",
        )
        self.owed_label.setObjectName("customerOwedLabel")
        self.owed_amount = _label(
            pesos(owed_total),
            parent=self.owed_banner,
            size=22,
            weight=QFont.Weight.Bold,
            colour=RED if owed_total > ZERO else "#3F6D4E",
        )
        self.owed_amount.setObjectName("customerOwedAmount")
        self.owed_amount.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        owed_row.addWidget(self.owed_label, 1)
        owed_row.addWidget(self.owed_amount)
        right.addWidget(self.owed_banner)

        self.tabs = QTabWidget(self.content_host)
        self.tabs.setObjectName("customerTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(
            self._bookings_tab(data["bookings"]), "Bookings"
        )
        self.tabs.addTab(
            self._payments_tab(data["payments"]), "Payments"
        )
        incidents = len(data["penalties"])
        self.tabs.addTab(
            self._incidents_tab(data["penalties"]),
            f"Incidents ({incidents})" if incidents else "Incidents",
        )
        right.addWidget(self.tabs, 1)

    def _facts(self, rows) -> QWidget:
        host = QWidget(self.content_host)
        host.setObjectName("customerFacts")
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        for key, value, wrap in rows:
            lay.addWidget(
                _FactRow(key, value, parent=host, value_wrap=wrap)
            )
        return host

    @staticmethod
    def _licence_tone(expiry: date | None) -> str:
        if expiry is None:
            return "neutral"
        today = date.today()
        if expiry < today:
            return "danger"
        if (expiry - today).days <= 30:
            return "warn"
        return "ok"

    @staticmethod
    def _licence_text(expiry: date | None) -> str:
        if expiry is None:
            return "No expiry on file"
        if expiry < date.today():
            return f"Expired {expiry:%d %b %Y}"
        return f"Valid to {expiry:%d %b %Y}"

    def _table(
        self,
        headers: tuple[str, ...],
        widths: tuple[int, ...],
        *,
        height: int,
    ) -> QTableWidget:
        table = QTableWidget(0, len(headers), self.content_host)
        table.setObjectName("customerBookingsTable")
        table.setHorizontalHeaderLabels(list(headers))
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(42)
        table.setShowGrid(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setAlternatingRowColors(False)
        table.setMinimumHeight(height)
        table.setMaximumHeight(height)
        header = table.horizontalHeader()
        header.setHighlightSections(False)
        for index, width in enumerate(widths):
            if width <= 0:
                header.setSectionResizeMode(
                    index, QHeaderView.ResizeMode.Stretch
                )
            else:
                table.setColumnWidth(index, width)
                header.setSectionResizeMode(
                    index, QHeaderView.ResizeMode.Fixed
                )
        return table

    def _bookings_tab(self, bookings: list[dict]) -> QWidget:
        host = QWidget(self.content_host)
        host.setObjectName("customerTabPage")
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(8)
        history = [
            booking
            for booking in bookings
            if booking["status"] in ("completed", "cancelled")
        ][:5]
        if not history:
            lay.addWidget(
                Caption(
                    "No past rentals.",
                    parent=host,
                    colour=MUTED,
                    size=12,
                )
            )
            lay.addStretch(1)
            return host
        table = self._table(
            ("Ref", "Vehicle", "Dates", "Status"),
            (64, 0, 150, 96),
            height=42 * len(history) + 38,
        )
        table.setRowCount(len(history))
        for row, booking in enumerate(history):
            table.setItem(row, 0, QTableWidgetItem(f"#{booking['ref']}"))
            table.setItem(row, 1, QTableWidgetItem(booking["vehicle"]))
            table.setItem(
                row,
                2,
                QTableWidgetItem(
                    f"{booking['start']:%d %b} → {booking['end']:%d %b %Y}"
                ),
            )
            table.setCellWidget(
                row,
                3,
                self._status_cell(booking["status"]),
            )
        lay.addWidget(table)
        lay.addStretch(1)
        return host

    def _payments_tab(self, payments: list[dict]) -> QWidget:
        host = QWidget(self.content_host)
        host.setObjectName("customerTabPage")
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(8)
        rows = payments[:10]
        if not rows:
            lay.addWidget(
                Caption(
                    "No payments recorded.",
                    parent=host,
                    colour=MUTED,
                    size=12,
                )
            )
            lay.addStretch(1)
            return host
        table = self._table(
            ("Ref", "Amount", "Method", "Kind / status", "When"),
            (58, 92, 76, 0, 104),
            height=42 * len(rows) + 38,
        )
        table.setRowCount(len(rows))
        for row, payment in enumerate(rows):
            when = (
                payment["when"].strftime("%d %b %Y")
                if payment["when"]
                else "pending"
            )
            table.setItem(row, 0, QTableWidgetItem(f"#{payment['ref']}"))
            table.setItem(
                row, 1, QTableWidgetItem(f"{payment['amount']:,.2f}")
            )
            table.setItem(
                row, 2, QTableWidgetItem(str(payment["method"]).title())
            )
            table.setItem(
                row,
                3,
                QTableWidgetItem(
                    f"{payment['kind']} / {payment['status']}"
                ),
            )
            table.setItem(row, 4, QTableWidgetItem(when))
        lay.addWidget(table)
        lay.addStretch(1)
        return host

    def _incidents_tab(self, penalties: list[dict]) -> QWidget:
        host = QWidget(self.content_host)
        host.setObjectName("customerTabPage")
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(8)
        if not penalties:
            lay.addWidget(
                Caption(
                    "No penalties or incidents.",
                    parent=host,
                    colour=MUTED,
                    size=12,
                )
            )
            lay.addStretch(1)
            return host
        for penalty in penalties:
            row = QFrame(host)
            row.setObjectName("customerIncidentRow")
            row_lay = QVBoxLayout(row)
            row_lay.setContentsMargins(12, 8, 12, 8)
            row_lay.setSpacing(2)
            title = QHBoxLayout()
            title.setContentsMargins(0, 0, 0, 0)
            title.addWidget(
                _label(
                    f"#{penalty['ref']} · {penalty['type'].replace('_', ' ').title()}",
                    parent=row,
                    size=12,
                    weight=QFont.Weight.DemiBold,
                )
            )
            title.addStretch(1)
            title.addWidget(
                _label(
                    pesos(penalty["amount"]),
                    parent=row,
                    size=12,
                    weight=QFont.Weight.Bold,
                )
            )
            row_lay.addLayout(title)
            row_lay.addWidget(
                Caption(
                    f"{penalty['description']} · {penalty['inspections']}",
                    parent=row,
                    colour=MUTED,
                    size=11,
                )
            )
            lay.addWidget(row)
        lay.addStretch(1)
        return host

    @staticmethod
    def _status_cell(status: str) -> QWidget:
        tone = {
            "completed": "ok",
            "ongoing": "warn",
            "confirmed": "ok",
            "pending": "warn",
            "cancelled": "neutral",
        }.get(status, "neutral")
        host = QWidget()
        host.setObjectName("customerStatusCell")
        lay = QHBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(
            StatusChip(str(status).replace("_", " ").title(), tone=tone),
            0,
            Qt.AlignmentFlag.AlignCenter,
        )
        return host

    def _build_footer(self) -> None:
        self.footer_layout.addStretch(1)
        self.edit_button = QPushButton("Edit", self.footer)
        self.edit_button.setObjectName("modalSecondary")
        self.edit_button.setMinimumHeight(40)
        self.edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_button.clicked.connect(
            lambda: self.edit_requested.emit(self._user_id)
        )
        self.close_button = QPushButton("Close", self.footer)
        self.close_button.setObjectName("modalSecondary")
        self.close_button.setMinimumHeight(40)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self.closed.emit)
        self.rental_button = QPushButton("New rental", self.footer)
        self.rental_button.setObjectName("modalPrimary")
        self.rental_button.setMinimumHeight(40)
        self.rental_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rental_button.clicked.connect(
            lambda: self.rental_requested.emit(self._user_id)
        )
        self.footer_layout.addWidget(self.edit_button)
        self.footer_layout.addWidget(self.close_button)
        self.footer_layout.addWidget(self.rental_button)
        self.footer.setVisible(True)

    def _on_add_note(self) -> None:
        text = self.note_edit.text().strip()
        if not text:
            self._error.setText("Note cannot be empty.")
            self._error.setVisible(True)
            return
        try:
            with self._context.session() as session:
                customer = session.get(Users, self._user_id)
                customer_service.add_note(
                    session, customer, text,
                    author_id=self._context.user.user_id,
                )
        except Exception as exc:  # noqa: BLE001
            self._error.setText(_friendly(exc))
            self._error.setVisible(True)
            return
        self.note_edit.clear()
        self._error.setVisible(False)
        self._notes_host.addWidget(
            Caption(
                f"{text} (staff #{self._context.user.user_id})",
                parent=self.content_host,
                colour=MUTED,
                size=11,
            )
        )
