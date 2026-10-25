"""The customer's account information page.

This is what the top-bar avatar's **Account info** action opens. Identity,
contact details, guarantor and licence sit on one page, together with the
counts that tie the account to its bookings and receipts. The customer can
edit their own name, contact details, address and guarantor; licence details
stay with the branch's pickup verification. The Personal / Bookings / Payments
strip mirrors the reference profile page; Bookings and Payments hand control
back to the two standing tabs instead of drawing a second copy of them here.
"""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import QPixmap, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.database import SessionLocal
from app.services import profile_service
from app.services.errors import ServiceError, ValidationError
from app.ui import theme
from app.ui.session import Identity
from app.ui.theme import (
    divider,
    hint_label,
    make_label,
    overline,
    section_title,
    status_chip,
)

#: The strip under the profile header. Personal is this page; the other two
#: are the standing tabs, so the router owns where they land.
PROFILE_TABS: tuple[tuple[str, str], ...] = (
    ("personal", "Personal"),
    ("bookings", "Bookings"),
    ("payments", "Payments"),
)

MISSING = "Not provided"


def _date_text(value: date | None) -> str:
    if value is None:
        return MISSING
    return f"{value:%B} {value.day}, {value.year}"


def _expiry_text(value: date | None) -> str:
    if value is None:
        return MISSING
    return f"{value.month:02d} / {value.day:02d} / {value.year}"


def _month_year(value: datetime | None) -> str:
    if value is None:
        return "Member since not recorded"
    return f"Member since {value:%B %Y}"


def _phone_text(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return MISSING
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 11 and digits.startswith("09"):
        return f"{digits[:4]} {digits[4:7]} {digits[7:]}"
    return raw


def _count_text(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


class ProfilePage(QWidget):
    """The account information page. `set_user` re-reads the signed-in row."""

    #: "bookings" / "payments" -- the two standing tabs the strip links to.
    tab_requested = Signal(str)
    #: Emitted after a successful edit so the top bar can refresh its name.
    profile_updated = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self._identity: Identity | None = None
        self._profile: profile_service.CustomerProfile | None = None
        self._values: dict[str, QLabel] = {}

        self.error = hint_label("", tone="danger")
        self.error.hide()

        self.header = self._build_header()
        self.tabs = self._build_tabs()

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(18)
        left.addWidget(self._build_personal())
        left.addStretch(1)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(18)
        right.addWidget(self._build_guarantor())
        right.addWidget(self._build_licence())
        right.addWidget(self._build_privacy())
        right.addStretch(1)

        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(18)
        columns.addLayout(left, 2)
        columns.addLayout(right, 1)

        body = QVBoxLayout()
        body.setContentsMargins(40, 28, 40, 32)
        body.setSpacing(18)
        body.addWidget(self.header)
        body.addWidget(self.tabs)
        body.addWidget(self.error)
        body.addLayout(columns)
        body.addStretch(1)

        holder = QWidget()
        holder.setObjectName("pageBody")
        holder.setLayout(body)

        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(holder)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    # ---------- construction ----------
    def _build_header(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("profileHeader")
        row = QHBoxLayout(frame)
        row.setContentsMargins(26, 22, 26, 22)
        row.setSpacing(20)

        self.avatar = QLabel()
        self.avatar.setObjectName("profileAvatar")
        self.avatar.setAlignment(Qt.AlignCenter)
        self.avatar.setFixedSize(78, 78)
        avatar_path = theme.ICONS_DIR / "account.png"
        pixmap = QPixmap(str(avatar_path))
        if not pixmap.isNull():
            self.avatar.setPixmap(
                pixmap.scaled(
                    68,
                    68,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
        row.addWidget(self.avatar, 0, Qt.AlignTop)

        text = QVBoxLayout()
        text.setContentsMargins(0, 1, 0, 1)
        text.setSpacing(4)
        text.addWidget(overline("Customer profile account", spacing=1.2))

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(12)
        self.name_label = make_label("", "profileName")
        self.status = status_chip("Customer", "muted")
        name_row.addWidget(self.name_label, 0, Qt.AlignVCenter)
        name_row.addWidget(self.status, 0, Qt.AlignVCenter)
        name_row.addStretch(1)
        text.addLayout(name_row)

        self.email_label = make_label("", "profileEmail")
        self.email_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text.addWidget(self.email_label)

        self.meta_label = make_label("", "profileMeta")
        text.addWidget(self.meta_label)
        text.addStretch(1)

        row.addLayout(text, 1)
        return frame

    def _build_tabs(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("profileTabRow")
        row = QHBoxLayout(frame)
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(26)

        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        self.tab_buttons: dict[str, QPushButton] = {}
        for key, caption in PROFILE_TABS:
            button = QPushButton(caption)
            button.setObjectName("profileTab")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(
                lambda _checked=False, k=key: self._on_tab(k)
            )
            self._tab_group.addButton(button)
            self.tab_buttons[key] = button
            row.addWidget(button)
        row.addStretch(1)
        self.tab_buttons["personal"].setChecked(True)
        return frame

    def _field(self, caption: str, key: str, *, wrap: bool = False) -> QWidget:
        holder = QWidget()
        holder.setObjectName("profileField")
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        col.addWidget(overline(caption, spacing=1.0))
        value = make_label("", "profileValue", wrap=wrap)
        value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        col.addWidget(value)
        self._values[key] = value
        return holder

    def _build_personal(self) -> QFrame:
        frame, col = theme.panel(
            "profilePanel", spacing=14, margins=(26, 22, 26, 24)
        )
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(section_title("Personal information"))
        head.addStretch(1)
        self.edit_button = theme.text_button("Edit details")
        self.edit_button.setObjectName("textButton")
        self.edit_button.setToolTip("Edit your personal and guarantor details")
        self.edit_button.clicked.connect(self._edit_profile)
        head.addWidget(self.edit_button, 0, Qt.AlignVCenter)
        col.addLayout(head)
        col.addWidget(divider())

        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setHorizontalSpacing(32)
        grid.setVerticalSpacing(18)
        grid.addWidget(self._field("First name", "first_name"), 0, 0)
        grid.addWidget(self._field("Last name", "last_name"), 0, 1)
        grid.addWidget(self._field("Email address", "email"), 1, 0)
        grid.addWidget(self._field("Mobile number", "phone"), 1, 1)
        grid.addWidget(self._field("Date of birth", "birthdate"), 2, 0)
        grid.addWidget(self._field("Account type", "account_type"), 2, 1)
        grid.addWidget(
            self._field("Home address", "address", wrap=True), 3, 0, 1, 2
        )
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        col.addLayout(grid)
        return frame

    def _build_guarantor(self) -> QFrame:
        frame, col = theme.panel(
            "profilePanel", spacing=12, margins=(22, 20, 22, 22)
        )
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(make_label("Guarantor", "profileCardTitle"))
        head.addStretch(1)
        edit = theme.text_button("Edit")
        edit.setObjectName("textButton")
        edit.clicked.connect(self._edit_profile)
        head.addWidget(edit, 0, Qt.AlignVCenter)
        col.addLayout(head)
        col.addWidget(divider())

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(12)
        grid.addWidget(self._field("Name", "guarantor_name", wrap=True), 0, 0)
        grid.addWidget(self._field("Mobile number", "guarantor_mobile"), 0, 1)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        col.addLayout(grid)

        self.guarantor_name_value = self._values["guarantor_name"]
        self.guarantor_mobile_value = self._values["guarantor_mobile"]
        self.guarantor_hint = hint_label("", tone="muted")
        col.addWidget(self.guarantor_hint)

        # Kept as aliases so older screens/tests that still use the previous
        # emergency-contact attribute names keep reading the same labels.
        self.emergency_value = self.guarantor_name_value
        self.emergency_hint = self.guarantor_hint
        return frame

    def _build_licence(self) -> QFrame:
        frame, col = theme.panel(
            "profilePanel", spacing=12, margins=(22, 20, 22, 22)
        )
        col.addWidget(make_label("Licence information", "profileCardTitle"))
        col.addWidget(divider())

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(14)
        grid.addWidget(self._field("Licence number", "license_number"), 0, 0)
        grid.addWidget(self._field("Licence class", "license_class"), 1, 0)
        grid.addWidget(self._field("Expiry date", "license_expiry"), 0, 1)

        status_holder = QWidget()
        status_col = QVBoxLayout(status_holder)
        status_col.setContentsMargins(0, 0, 0, 0)
        status_col.setSpacing(3)
        status_col.addWidget(overline("Status", spacing=1.0))
        self.license_status = status_chip("Unknown", "muted")
        status_col.addWidget(self.license_status, 0, Qt.AlignLeft)
        grid.addWidget(status_holder, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        col.addLayout(grid)

        self.license_hint = hint_label(
            "The branch checks the physical licence when you pick the vehicle up.",
            tone="muted",
        )
        col.addWidget(self.license_hint)
        return frame

    def _build_privacy(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("profileNote")
        col = QVBoxLayout(frame)
        col.setContentsMargins(22, 18, 22, 20)
        col.setSpacing(8)
        col.addWidget(overline("Private by default", spacing=1.2))
        col.addWidget(
            hint_label(
                "Your account information is kept private and used only for "
                "your rentals. Use Edit details to update your contact "
                "information or guarantor. Licence details are recorded and "
                "checked by the branch when you pick a vehicle up.",
                tone="muted",
            )
        )
        return frame

    # ---------- state ----------
    def set_user(self, identity: Identity | None) -> None:
        """Whose account this is. A guest can never land on this page."""
        self._identity = identity
        self.reload()

    def _on_tab(self, key: str) -> None:
        if key == "personal":
            self.tab_buttons["personal"].setChecked(True)
            return
        if key in {"bookings", "payments"}:
            self.tab_requested.emit(key)

    def _select_personal(self) -> None:
        self.tab_buttons["personal"].setChecked(True)

    def _edit_profile(self) -> None:
        """Open the personal-information editor over the whole window."""
        if self._identity is None or self._profile is None:
            self._fail("Sign in to edit your account information.")
            return
        dialog = ProfileEditDialog(self.window(), self._profile)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.saved_identity is None:
            return
        self._identity = dialog.saved_identity
        self.profile_updated.emit(dialog.saved_identity)
        self.reload()

    # ---------- data ----------
    def reload(self) -> None:
        self.error.hide()
        self._select_personal()

        if self._identity is None:
            self._show_signed_out()
            return

        profile: profile_service.CustomerProfile | None
        try:
            with SessionLocal() as session:
                profile = profile_service.profile_for(
                    session, self._identity.user_id
                )
        except Exception as exc:  # noqa: BLE001 - the database is remote
            self._fail(f"Could not load your account information: {exc}")
            return

        if profile is None:
            self._profile = None
            self._show_signed_out()
            self._fail("That account no longer exists.")
            return

        self._profile = profile
        self.name_label.setText(profile.full_name or "Customer")
        self.email_label.setText(profile.email or MISSING)
        self.meta_label.setText(self._meta_text(profile))
        self._set_chip(self.status, profile.status_label, profile.status_tone)

        self._set(
            "first_name",
            profile.first_name or MISSING,
            missing=not profile.first_name,
        )
        self._set(
            "last_name",
            profile.last_name or MISSING,
            missing=not profile.last_name,
        )
        self._set(
            "birthdate",
            _date_text(profile.birthdate),
            missing=profile.birthdate is None,
        )
        self._set("email", profile.email or MISSING, missing=not profile.email)
        self._set(
            "phone",
            _phone_text(profile.phone),
            missing=not profile.phone,
        )
        self._set("address", profile.address or MISSING, missing=not profile.address)
        self._set("account_type", "Customer")

        if profile.has_guarantor:
            name = profile.guarantor_name or profile.emergency_contact or MISSING
            self.guarantor_name_value.setText(name)
            self.guarantor_mobile_value.setText(
                _phone_text(profile.guarantor_mobile)
            )
            self._tone(
                self.guarantor_name_value,
                "normal" if profile.guarantor_name else "muted",
            )
            self._tone(
                self.guarantor_mobile_value,
                "normal" if profile.guarantor_mobile else "muted",
            )
            self.guarantor_hint.setText(
                "We contact your guarantor only if we can't reach you while "
                "a vehicle is out."
            )
        elif profile.emergency_contact:
            # Rows created before the guarantor columns only have the old
            # single-line contact. Show it rather than hiding known data.
            self.guarantor_name_value.setText(profile.emergency_contact)
            self.guarantor_mobile_value.setText(MISSING)
            self._tone(self.guarantor_name_value, "normal")
            self._tone(self.guarantor_mobile_value, "muted")
            self.guarantor_hint.setText(
                "This is the contact on file. Use Edit details to add a "
                "guarantor's first name, last name and mobile number."
            )
        else:
            self.guarantor_name_value.setText(MISSING)
            self.guarantor_mobile_value.setText(MISSING)
            self._tone(self.guarantor_name_value, "muted")
            self._tone(self.guarantor_mobile_value, "muted")
            self.guarantor_hint.setText(
                "Add a guarantor from Edit details so we know who to call "
                "if we can't reach you."
            )

        self._set(
            "license_number",
            profile.license_number or MISSING,
            missing=not profile.license_number,
        )
        self._set("license_class", MISSING, missing=True)
        self._set(
            "license_expiry",
            _expiry_text(profile.license_expiry),
            missing=profile.license_expiry is None,
        )

        if not profile.license_number:
            self._set_chip(self.license_status, "Pending", "amber")
            self.license_hint.setText(
                "Online sign-up no longer asks for a licence number. The "
                "branch records it when you first pick a vehicle up."
            )
        elif profile.license_expiry is None:
            self._set_chip(self.license_status, "Unknown", "muted")
            self.license_hint.setText(
                "The branch checks the physical licence when you pick the "
                "vehicle up."
            )
        elif profile.license_expiry < date.today():
            self._set_chip(self.license_status, "Expired", "red")
            self.license_hint.setText(
                "Your licence expiry has passed. Ask the branch to update it "
                "before your next rental."
            )
        else:
            self._set_chip(self.license_status, "Valid", "green")
            self.license_hint.setText(
                "The branch checks the physical licence when you pick the "
                "vehicle up."
            )

    def _show_signed_out(self) -> None:
        self._profile = None
        self.name_label.setText("Your account")
        self.email_label.setText("Sign in from the top bar to see your account.")
        self.meta_label.setText("")
        self._set_chip(self.status, "Signed out", "muted")
        for key in (
            "first_name",
            "last_name",
            "birthdate",
            "email",
            "phone",
            "address",
            "account_type",
            "license_number",
            "license_class",
            "license_expiry",
        ):
            self._set(key, MISSING)
        self.guarantor_name_value.setText(MISSING)
        self.guarantor_mobile_value.setText(MISSING)
        self.guarantor_hint.setText(
            "Sign in to see the guarantor on file."
        )
        self._tone(self.guarantor_name_value, "muted")
        self._tone(self.guarantor_mobile_value, "muted")
        self._set_chip(self.license_status, "Unknown", "muted")

    def _meta_text(self, profile: profile_service.CustomerProfile) -> str:
        joined = "  \u00b7  ".join(
            (
                _month_year(profile.created_at),
                _count_text(profile.booking_count, "booking"),
                _count_text(profile.payment_count, "payment"),
            )
        )
        return joined

    def _set(self, key: str, text: str, *, missing: bool = False) -> None:
        label = self._values.get(key)
        if label is None:
            return
        label.setText(text)
        self._tone(
            label,
            "muted" if missing or text == MISSING else "normal",
        )

    @staticmethod
    def _tone(label: QLabel, tone: str) -> None:
        label.setProperty("tone", tone)
        label.style().unpolish(label)
        label.style().polish(label)

    @staticmethod
    def _set_chip(label: QLabel, text: str, tone: str) -> None:
        label.setText(text.upper())
        label.setProperty("tone", tone)
        label.style().unpolish(label)
        label.style().polish(label)

    def _fail(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()


class ProfileEditDialog(theme.OverlayDialog):
    """Edit the details a customer is allowed to maintain themselves.

    The licence is deliberately absent: it is verified by the branch. The
    dialog writes through ``profile_service.update_profile`` and only reports
    success after the session commits.
    """

    def __init__(
        self,
        parent: QWidget,
        profile: profile_service.CustomerProfile,
    ) -> None:
        super().__init__(parent, object_name="overlayCard", card_width=650)
        self.profile = profile
        self.saved_identity: Identity | None = None
        self.fields: dict[str, QLineEdit] = {}

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(make_label("Edit personal information", "overlayTitle"), 1)
        head.addWidget(self.close_button(), 0, Qt.AlignTop)
        self.column.addLayout(head)
        self.column.addWidget(
            make_label(
                "Keep your contact details current. Your guarantor is the "
                "person we call if we can't reach you.",
                "overlaySubtitle",
                wrap=True,
            )
        )

        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 4, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(10)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1)
        row = 0
        self.grid.addWidget(overline("Your details", spacing=1.0), row, 0, 1, 2)
        row += 1
        self._add_field("first_name", "First name", "Juan", row, 0)
        self._add_field("last_name", "Last name", "Dela Cruz", row, 1)
        row += 1
        self._add_field("email", "Email address", "you@email.com", row, 0)
        self._add_field("phone", "Mobile number", "09XX XXX XXXX", row, 1)
        row += 1
        self._add_field("birthdate", "Date of birth", "YYYY-MM-DD", row, 0)
        self._add_field("address", "Home address", "House / unit, street, city", row, 1)
        row += 1
        self.grid.addWidget(overline("Guarantor", spacing=1.0), row, 0, 1, 2)
        row += 1
        self._add_field(
            "guarantor_first_name", "Guarantor first name", "First name", row, 0
        )
        self._add_field(
            "guarantor_last_name", "Guarantor last name", "Last name", row, 1
        )
        row += 1
        self._add_field(
            "guarantor_mobile", "Guarantor mobile number", "09XX XXX XXXX", row, 0
        )
        self.column.addLayout(self.grid)

        self.fields["phone"].setMaxLength(11)
        self.fields["phone"].setValidator(
            QRegularExpressionValidator(
                QRegularExpression("09[0-9]*"), self.fields["phone"]
            )
        )
        self.fields["guarantor_mobile"].setMaxLength(11)
        self.fields["guarantor_mobile"].setValidator(
            QRegularExpressionValidator(
                QRegularExpression("09[0-9]*"), self.fields["guarantor_mobile"]
            )
        )
        self.fields["birthdate"].setMaxLength(10)

        self.error = hint_label("", tone="danger")
        self.error.hide()
        self.column.addWidget(self.error)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 4, 0, 0)
        actions.setSpacing(12)
        self.save_button = theme.primary_button("SAVE CHANGES")
        self.save_button.clicked.connect(self._save)
        cancel = theme.text_button("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(self.save_button)
        actions.addWidget(cancel, 0, Qt.AlignVCenter)
        actions.addStretch(1)
        self.column.addLayout(actions)

        self._prefill(profile)

    def _add_field(
        self,
        key: str,
        caption: str,
        placeholder: str,
        row: int,
        column: int,
    ) -> QLineEdit:
        holder = theme.field(caption, placeholder)
        edit = holder.edit
        self.fields[key] = edit
        self.grid.addWidget(holder, row, column)
        return edit

    def _prefill(self, profile: profile_service.CustomerProfile) -> None:
        self.fields["first_name"].setText(profile.first_name)
        self.fields["last_name"].setText(profile.last_name)
        self.fields["email"].setText(profile.email or "")
        self.fields["phone"].setText(profile.phone or "")
        self.fields["birthdate"].setText(
            profile.birthdate.isoformat() if profile.birthdate else ""
        )
        self.fields["address"].setText(profile.address or "")
        self.fields["guarantor_first_name"].setText(
            profile.guarantor_first_name or ""
        )
        self.fields["guarantor_last_name"].setText(
            profile.guarantor_last_name or ""
        )
        self.fields["guarantor_mobile"].setText(profile.guarantor_mobile or "")

        # A legacy row only has "Name - mobile" in emergency_contact. Split
        # it so opening the editor does not silently discard the old contact.
        if (
            not profile.has_guarantor
            and profile.emergency_contact
            and " - " in profile.emergency_contact
        ):
            name, mobile = profile.emergency_contact.split(" - ", 1)
            parts = name.split(maxsplit=1)
            self.fields["guarantor_first_name"].setText(parts[0])
            if len(parts) > 1:
                self.fields["guarantor_last_name"].setText(parts[1])
            self.fields["guarantor_mobile"].setText(mobile.strip())

    def _save(self) -> None:
        self.error.hide()
        try:
            birthdate = self._birthdate()
            with SessionLocal() as session:
                user = profile_service.update_profile(
                    session,
                    self.profile.user_id,
                    first_name=self.fields["first_name"].text(),
                    last_name=self.fields["last_name"].text(),
                    email=self.fields["email"].text(),
                    phone=self.fields["phone"].text(),
                    birthdate=birthdate,
                    address=self.fields["address"].text(),
                    guarantor_first_name=self.fields[
                        "guarantor_first_name"
                    ].text(),
                    guarantor_last_name=self.fields[
                        "guarantor_last_name"
                    ].text(),
                    guarantor_mobile=self.fields["guarantor_mobile"].text(),
                )
                session.commit()
                self.saved_identity = Identity.of(user)
        except ServiceError as exc:
            self._show_error(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - the database is remote
            self._show_error(f"Could not save your details: {exc}")
            return
        self.accept()

    def _birthdate(self) -> date | None:
        text = self.fields["birthdate"].text().strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValidationError(
                "Enter your date of birth as YYYY-MM-DD.", field="birthdate"
            ) from exc

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()


__all__ = ["PROFILE_TABS", "ProfilePage", "ProfileEditDialog"]
