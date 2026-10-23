"""The customer's account information page.

This is what the top-bar avatar's **Account info** action opens. It is a
read-only view of the data the branch already holds: identity, contact
details, emergency contact and licence, plus the counts that tie the account
to its bookings and receipts. The Personal / Bookings / Payments strip mirrors
the reference profile page; Bookings and Payments hand control back to the two
standing tabs instead of drawing a second copy of them here.
"""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.database import SessionLocal
from app.services import profile_service
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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self._identity: Identity | None = None
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
        right.addWidget(self._build_emergency())
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
        col.addWidget(section_title("Personal information"))
        col.addWidget(divider())

        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setHorizontalSpacing(32)
        grid.setVerticalSpacing(18)
        grid.addWidget(self._field("First name", "first_name"), 0, 0)
        grid.addWidget(self._field("Last name", "last_name"), 0, 1)
        grid.addWidget(self._field("Date of birth", "birthdate"), 1, 0)
        grid.addWidget(self._field("Nationality", "nationality"), 1, 1)
        grid.addWidget(self._field("Email address", "email"), 2, 0)
        grid.addWidget(self._field("Mobile number", "phone"), 2, 1)
        grid.addWidget(
            self._field("Home address", "address", wrap=True), 3, 0
        )
        grid.addWidget(self._field("Account type", "account_type"), 3, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        col.addLayout(grid)
        return frame

    def _build_emergency(self) -> QFrame:
        frame, col = theme.panel(
            "profilePanel", spacing=12, margins=(22, 20, 22, 22)
        )
        col.addWidget(make_label("Emergency contact", "profileCardTitle"))
        col.addWidget(divider())
        col.addWidget(overline("Name and contact", spacing=1.0))
        self.emergency_value = make_label("", "profileValue", wrap=True)
        self.emergency_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        col.addWidget(self.emergency_value)
        self.emergency_hint = hint_label("", tone="muted")
        col.addWidget(self.emergency_hint)
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
                "your rentals. To change your name, birthdate, emergency "
                "contact or licence details, ask the branch counter; those "
                "records are checked there.",
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
            self._show_signed_out()
            self._fail("That account no longer exists.")
            return

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
        self._set("nationality", MISSING, missing=True)
        self._set("email", profile.email or MISSING, missing=not profile.email)
        self._set(
            "phone",
            _phone_text(profile.phone),
            missing=not profile.phone,
        )
        self._set("address", profile.address or MISSING, missing=not profile.address)
        self._set("account_type", "Customer")

        if profile.emergency_contact:
            self.emergency_value.setText(profile.emergency_contact)
            self.emergency_hint.setText(
                "Keep this person reachable while you have a vehicle out."
            )
            self._tone(self.emergency_value, "normal")
        else:
            self.emergency_value.setText(MISSING)
            self.emergency_hint.setText(
                "Add an emergency contact at the branch counter."
            )
            self._tone(self.emergency_value, "muted")

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

        if profile.license_expiry is None:
            self._set_chip(self.license_status, "Unknown", "muted")
        elif profile.license_expiry < date.today():
            self._set_chip(self.license_status, "Expired", "red")
        else:
            self._set_chip(self.license_status, "Valid", "green")

    def _show_signed_out(self) -> None:
        self.name_label.setText("Your account")
        self.email_label.setText("Sign in from the top bar to see your account.")
        self.meta_label.setText("")
        self._set_chip(self.status, "Signed out", "muted")
        for key in (
            "first_name",
            "last_name",
            "birthdate",
            "nationality",
            "email",
            "phone",
            "address",
            "account_type",
            "license_number",
            "license_class",
            "license_expiry",
        ):
            self._set(key, MISSING)
        self.emergency_value.setText(MISSING)
        self.emergency_hint.setText(
            "Sign in to see the emergency contact on file."
        )
        self._tone(self.emergency_value, "muted")
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


__all__ = ["PROFILE_TABS", "ProfilePage"]
