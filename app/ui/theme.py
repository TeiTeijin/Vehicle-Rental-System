from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QRegularExpression,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QVariantAnimation,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPixmap,
    QFontDatabase,
    QRegularExpressionValidator,

)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QApplication,
    QCheckBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QLineEdit,

)

from app.database import SessionLocal
from app.services import image_cache, media_service
from app.services.vehicle_service import (
    ShowcaseVehicle,
    showcase_vehicle_rows,
    to_showcase,
)

ICONS_DIR = Path(__file__).with_name("icons")

UI_DIR = Path(__file__).parent
QSS_PATH = UI_DIR / "sign.qss"
FONTS_DIR = UI_DIR / "fonts"

# Design tokens, referenced in styles.qss as @name
TOKENS = {
    # base
    "bg_dark": "#111111",
    "bg_cream": "#f1ede4",
    "gold": "#B48A4A",
    "text_on_dark": "#FFFFFF",
    "text_on_dark_muted": "rgba(255, 255, 255, 168)",
    "font_family": '"Inter", "Segoe UI", sans-serif',
    # sign-in card
    "card_bg": "#faf8f3",
    "card_border": "#e4ded1",
    "input_border": "#d6cfc0",
    "text_dark": "#1a1a1a",
    "text_muted": "#6b665c",
    "btn_hover": "#2b2b2b",
    "btn_disabled": "#8f8a7f",
    # password-strength feedback — vivid so met/missed is obvious
    "success": "#00C853",
    "danger": "#D50000",
}



# ---------- setup ----------
def apply_theme(app: QApplication) -> None:
    """Load fonts and the stylesheet. Call once at startup."""
    for font_file in FONTS_DIR.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(font_file))

    qss = QSS_PATH.read_text(encoding="utf-8")
    for name in sorted(TOKENS, key=len, reverse=True):  # longest first
        qss = qss.replace(f"@{name}", TOKENS[name])
    app.setStyleSheet(qss)


# ---------- helpers ----------
def make_label(text: str, object_name: str, spacing: float = 0, wrap: bool = False) -> QLabel:
    """Labels must keep their natural height.

    A QLabel defaults to a vertical size policy that lets it GROW. Inside the
    fixed-height card that spare room was handed to every label, stretching a
    20px "Email address" caption to ~98px and blowing the vertical rhythm
    apart.

    Minimum means "sit at your size hint, but never clip": the label stays
    compact instead of absorbing slack, and still grows if the text really
    does wrap onto another line on a narrow window. Fixed would risk cutting
    the text off, so Minimum is the safer of the two.

    Long paragraphs must be created with wrap=True (hero subtitle, card
    subtitle, terms text). Explicit \n newlines work either way.
    """
    label = QLabel(text)
    label.setObjectName(object_name)
    if wrap:
        label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
    if spacing:
        font = label.font()
        font.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
        label.setFont(font)
    return label


class RequirementLabel(QLabel):
    """One password rule with an animated green/red state.

    The text style (size etc.) comes from sign.qss via objectName ``reqItem``,
    but the COLOUR is driven by the QPalette so QVariantAnimation can fade the
    text between muted -> green (met) and muted -> red (missed) as you type.
    No setStyleSheet() is needed; the palette colour is picked up automatically.
    """

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setObjectName("reqItem")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self._neutral = QColor(TOKENS["text_muted"])
        self._ok = QColor(TOKENS["success"])
        self._err = QColor(TOKENS["danger"])
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(300)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_color)
        self._set_color(self._neutral)

    def _set_color(self, color: QColor) -> None:
        palette = self.palette()
        palette.setColor(QPalette.WindowText, color)
        palette.setColor(QPalette.Text, color)
        self.setPalette(palette)

    def _on_color(self, color) -> None:
        self._set_color(QColor(color))

    def set_state(self, state: str) -> None:
        """Animate to ``ok`` (green), ``not`` (red) or ``neutral`` (muted)."""
        target = {"ok": self._ok, "not": self._err}.get(state, self._neutral)
        self._anim.stop()
        self._anim.setStartValue(self.palette().color(QPalette.Text))
        self._anim.setEndValue(target)
        self._anim.start()


# ---------- hero screen pieces ----------
def build_hero_panel(
    eyebrow: str = "SIGN IN",
    title: str = "Your next drive\nis waiting.",
    subtitle: str = (
        "Premium rides, effortless booking. Manage your vehicle "
        "reservations in one secure place.\n"
        "Verified vehicles • 100% Secure payments • Dedicated customer support"
    ),
) -> QWidget:
    """Dark left panel. Sign-in uses the defaults; sign-up passes its own copy."""
    panel = QWidget()
    panel.setObjectName("heroPanel")

    col = QVBoxLayout(panel)
    col.setContentsMargins(72, 0, 48, 0)
    col.setSpacing(12)

    col.addStretch(1)
    col.addWidget(make_label(eyebrow, "eyebrow", spacing=2))
    # The 54pt hero heading is line-broken with \n; keeping wordWrap on for
    # it preserves the login screen exactly (without it the heading's natural
    # single-line width would force the whole window wider).
    col.addWidget(make_label(title, "heroTitle", wrap=True))
    col.addSpacing(24)
    col.addWidget(make_label(subtitle, "heroSubtitle", wrap=True))
    col.addStretch(2)
    return panel


def _field(caption: str, placeholder: str, password: bool = False) -> tuple[QLabel, QLineEdit]:
    label = make_label(caption, "fieldLabel")
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    if password:
        edit.setEchoMode(QLineEdit.Password)
    return label, edit


def _link(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setObjectName("link")
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFlat(True)
    return btn


# =========================================================================
#  FIELD VALIDATION  (shared by the sign-in and sign-up screens)
# =========================================================================
def is_valid_email(text: str) -> bool:
    """True when the address has a local part, an "@" and a domain part."""
    value = text.strip()
    if value.count("@") != 1 or any(ch.isspace() for ch in value):
        return False
    local, domain = value.split("@")
    return bool(local) and bool(domain)


# Only digits plus the separators people actually type (+ - parentheses,
# spaces). Everything else is rejected before it reaches the field.
PHONE_ALLOWED = "+-() "


def is_valid_phone(text: str) -> bool:
    """True when the number is digits only (separators are ignored)."""
    value = text.strip()
    if not value or any(ch.isdigit() is False and ch not in PHONE_ALLOWED
                        for ch in value):
        return False
    digits = "".join(ch for ch in value if ch.isdigit())
    return 7 <= len(digits) <= 15


def phone_input_validator(parent: Optional[QLineEdit] = None) -> QRegularExpressionValidator:
    """Blocks letters in the phone box so only digits/`+ - ( )`/spaces get in."""
    return QRegularExpressionValidator(
        QRegularExpression(f"[0-9{re.escape(PHONE_ALLOWED)}]*"), parent
    )


def _set_field_state(edit: QLineEdit, state: str) -> None:
    """Re-apply the QSS border for a state change ('ok' / 'error' / '')."""
    if edit.property("state") == state:
        return
    edit.setProperty("state", state)
    edit.style().unpolish(edit)
    edit.style().polish(edit)
    edit.update()


def _field_hint() -> QLabel:
    """Small red message under a field; hidden until the input is wrong."""
    hint = QLabel()
    hint.setObjectName("fieldHint")
    hint.setWordWrap(True)
    hint.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
    hint.hide()
    return hint


def _check_field(
    edit: QLineEdit,
    hint: QLabel,
    is_valid,
    message: str,
) -> bool:
    """Colour a required field by validity and explain what went wrong.

    Empty -> red border (it is required) with no message, filled but wrong ->
    red border + ``message``, valid -> green border. Returns True when valid.
    """
    text = edit.text().strip()
    if not text:
        _set_field_state(edit, "error")
        hint.clear()
        hint.hide()
        return False
    if is_valid(text):
        _set_field_state(edit, "ok")
        hint.clear()
        hint.hide()
        return True
    _set_field_state(edit, "error")
    hint.setText(message)
    hint.show()
    return False


def _build_card(
    card_width: int = 660,
    card_height: Optional[int] = 779,
    margins: Optional[tuple[int, int, int, int]] = None,
    spacing: int = 8,
) -> tuple[QFrame, QVBoxLayout]:
    """Shared card shell for both the sign-in and sign-up screens.

    Keeping this in one place guarantees the two panels look identical —
    edit the size, margins or spacing here and both screens update.

    card_height=None leaves the card free to size to its content (used by
    the taller sign-up form).
    """
    card = QFrame()
    card.setObjectName("card")
    card.setFixedWidth(card_width)
    if card_height is not None:
        card.setFixedHeight(card_height)
    else:
        card.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)

    shadow = QGraphicsDropShadowEffect(card)  # QSS has no box-shadow
    shadow.setBlurRadius(40)
    shadow.setOffset(0, 8)
    shadow.setColor(QColor(0, 0, 0, 30))
    card.setGraphicsEffect(shadow)

    col = QVBoxLayout(card)
    col.setContentsMargins(*(margins or (32, 32, 32, 28)))
    col.setSpacing(spacing)
    return card, col


# =========================================================================
#  SIGN-IN SCREEN  (app/ui/customer_login.py)
# =========================================================================
def build_form_panel() -> QWidget:
    panel = QWidget()
    panel.setObjectName("formPanel")

    # ----- the card -----
    card, col = _build_card()

    col.addWidget(make_label("Sign In", "cardTitle"))
    col.addWidget(make_label("Use the email linked to your bookings.", "cardSubtitle", wrap=True))
    col.addSpacing(16)

    # The card is a fixed 779px tall but the content only fills ~500px of it.
    # This stretch soaks up the gap under the header so the fields stay in the
    # upper half instead of drifting into the middle of the card.
    col.addStretch(1)

    email_lbl, panel.email = _field("Email address", "you@email.com")
    pass_lbl, panel.password = _field("Password", "Password", password=True)
    col.addWidget(email_lbl)
    col.addWidget(panel.email)
    panel.email_hint = _field_hint()
    col.addWidget(panel.email_hint)
    col.addSpacing(8)
    col.addWidget(pass_lbl)
    col.addWidget(panel.password)

    options = QHBoxLayout()
    panel.remember = QCheckBox("Remember this device")
    panel.forgot = _link("Forgot password?")
    options.addWidget(panel.remember)
    options.addStretch(1)
    options.addWidget(panel.forgot)
    col.addSpacing(4)
    col.addLayout(options)

    panel.signin_btn = QPushButton("→  SIGN IN")
    panel.signin_btn.setObjectName("primaryButton")
    panel.signin_btn.setCursor(Qt.PointingHandCursor)
    col.addSpacing(12)
    col.addWidget(panel.signin_btn, alignment=Qt.AlignLeft)

    # ----- live validation: red/green borders + disabled button -----
    def refresh_validation() -> None:
        # Email must contain "@" (with text on both sides of it).
        email_ok = _check_field(
            panel.email,
            panel.email_hint,
            is_valid_email,
            'Enter a valid email address - it must contain "@".',
        )
        # Password is simply required here (no strength rules on sign-in).
        password_ok = bool(panel.password.text())
        _set_field_state(panel.password, "error" if not password_ok else "")

        panel.signin_btn.setEnabled(email_ok and password_ok)

    panel.refresh_validation = refresh_validation
    panel.email.textChanged.connect(refresh_validation)
    panel.password.textChanged.connect(refresh_validation)
    refresh_validation()

    # Second stretch: pushes the "Sign In" button and the divider down to the
    # lower half of the card, leaving the field block optically centred.
    col.addStretch(1)

    divider = QFrame()
    divider.setObjectName("divider")
    divider.setFixedHeight(1)
    col.addSpacing(16)
    col.addWidget(divider)
    col.addSpacing(8)

    signup = QHBoxLayout()
    signup.addStretch(1)
    signup.addWidget(make_label("New customer? Create an account", "hint"))
    panel.signup = _link("Sign Up")
    signup.addWidget(panel.signup)
    signup.addStretch(1)
    col.addLayout(signup)

    # ----- center the card in the right panel -----
    outer = QVBoxLayout(panel)
    outer.setContentsMargins(24, 24, 24, 24)
    outer.addStretch(1)
    outer.addWidget(card, alignment=Qt.AlignHCenter)
    outer.addSpacing(24)
    help_row = QHBoxLayout()
    help_row.addStretch(1)
    help_row.addWidget(make_label("Need help?", "hint"))
    panel.support = _link("Contact rental support")
    help_row.addWidget(panel.support)
    help_row.addStretch(1)
    outer.addLayout(help_row)
    outer.addStretch(1)
    return panel


# =========================================================================
#  SIGN-UP SCREEN  (app/ui/signup.py)
#  -------------------------------------------------------------------------
#  EDIT BELOW THIS LINE to change the sign-up screen.
#  It uses the same card, fonts, colours and spacing as the sign-in screen
#  (both come from _build_card() above and from sign.qss), so anything you
#  change here stays visually consistent with the login page.
#  Every widget is stored as an attribute on the returned panel so
#  signup.py can read it later, e.g. panel.first_name / panel.create_btn /
#  panel.signin_link / panel.support_link.
# =========================================================================
def _field_row(
    scroll: QScrollArea,
    left_caption: str,
    left_placeholder: str,
    left_attr: str,
    right_caption: str,
    right_placeholder: str,
    right_attr: str,
) -> QHBoxLayout:
    """Two equal-width fields side by side, each with its small bold caption."""
    row = QHBoxLayout()
    row.setSpacing(14)

    left_col = QVBoxLayout()
    left_col.setSpacing(6)
    left_lbl, left_edit = _field(left_caption, left_placeholder)
    setattr(scroll, left_attr, left_edit)
    left_col.addWidget(left_lbl)
    left_col.addWidget(left_edit)

    right_col = QVBoxLayout()
    right_col.setSpacing(6)
    right_lbl, right_edit = _field(right_caption, right_placeholder)
    setattr(scroll, right_attr, right_edit)
    right_col.addWidget(right_lbl)
    right_col.addWidget(right_edit)

    row.addLayout(left_col, 1)
    row.addLayout(right_col, 1)
    return row


def build_signup_form_panel() -> QScrollArea:
    """Right-hand cream panel holding the sign-up card.

    The whole panel lives inside a QScrollArea so the form stays usable on
    short screens. The card itself is 560px wide and sized to its content
    (card_height=None in _build_card) instead of locked to 779px.
    """
    scroll = QScrollArea()
    scroll.setObjectName("formPanel")          # keeps the cream background
    scroll.setFrameShape(QFrame.NoFrame)
    # False: the holder keeps its natural (content) size instead of being
    # stretched to fill the viewport, so the card can never change size when
    # the window is minimized / maximized. AlignCenter keeps it optically
    # centred whenever there is spare space.
    scroll.setWidgetResizable(False)
    scroll.setAlignment(Qt.AlignCenter)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    card, col = _build_card(
        card_width=560,
        card_height=None,                      # size to content, don't lock
        margins=(32, 30, 32, 26),              # tight vertical padding
        spacing=6,
    )

    # ----- header -----
    col.addWidget(make_label("Create your account", "cardTitle"))
    col.addWidget(make_label(
        "Required fields are used only to process reservations and verify pickup.",
        "cardSubtitle", wrap=True,
    ))
    col.addSpacing(14)

    # ----- first name | last name -----
    col.addLayout(_field_row(
        scroll, "First name", "First name", "first_name",
        "Last name", "Last name", "last_name",
    ))
    col.addSpacing(14)

    # ----- email address -----
    email_lbl, scroll.email = _field("Email address", "you@email.com")
    col.addWidget(email_lbl)
    col.addWidget(scroll.email)
    scroll.email_hint = _field_hint()
    col.addWidget(scroll.email_hint)
    col.addSpacing(14)

    # ----- mobile number -----
    mobile_lbl, scroll.mobile = _field("Mobile number", "+63 9XX XXX XXXX")
    # Letters can never be typed in: only digits and "+ - ( )" / spaces.
    scroll.mobile.setValidator(phone_input_validator(scroll.mobile))
    col.addWidget(mobile_lbl)
    col.addWidget(scroll.mobile)
    scroll.mobile_hint = _field_hint()
    col.addWidget(scroll.mobile_hint)
    col.addSpacing(14)

    # ----- password + requirements hint -----
    pass_lbl, scroll.password = _field("Password", "Password", password=True)
    col.addWidget(pass_lbl)
    col.addWidget(scroll.password)
    col.addSpacing(8)

    # One RequirementLabel per rule, each animating green (met) / red (missed).
    symbols = "!@#$%^&*()_+-=[]{};:'\"|,.<>/?`~"
    rules = [
        ("8+ characters", lambda t: len(t) >= 8),
        ("- 1 Uppercase letter", lambda t: any(c.isupper() for c in t)),
        ("- 1 Symbol", lambda t: any(c in symbols for c in t)),
        ("- 1 Number", lambda t: any(c.isdigit() for c in t)),
    ]
    scroll.req_items = []
    for caption, check in rules:
        item = RequirementLabel(caption)
        scroll.req_items.append((item, check))
        col.addWidget(item)
    col.addSpacing(14)

    def update_password_state(text: str) -> None:
        for item, check in scroll.req_items:
            item.set_state("neutral" if not text else ("ok" if check(text) else "not"))

    scroll.update_password_state = update_password_state

    # ----- confirm password -----
    confirm_lbl, scroll.confirm_password = _field("Confirm password", "Confirm password", password=True)
    col.addWidget(confirm_lbl)
    col.addWidget(scroll.confirm_password)
    col.addSpacing(10)

    # ----- terms -----
    col.addWidget(make_label(
        "By continuing, you agree to the rental terms and privacy notice. "
        "Marketing consent is optional.",
        "note", wrap=True,
    ))
    col.addSpacing(14)

    # ----- create button -----
    scroll.create_btn = QPushButton("→  CREATE ACCOUNT")
    scroll.create_btn.setObjectName("primaryButton")
    scroll.create_btn.setCursor(Qt.PointingHandCursor)
    col.addWidget(scroll.create_btn, alignment=Qt.AlignLeft)
    col.addSpacing(14)

    # ----- live validation: red highlights + disabled button -----
    REQUIRED = (scroll.first_name, scroll.last_name, scroll.email,
                scroll.mobile, scroll.password, scroll.confirm_password)

    def refresh_validation() -> None:
        # Any other required field that is still empty gets a red border.
        for edit in REQUIRED:
            if edit is scroll.email or edit is scroll.mobile \
                    or edit is scroll.confirm_password \
                    or edit is scroll.password:
                continue
            _set_field_state(edit, "error" if not edit.text().strip() else "")

        # Email: must contain "@" with text on both sides of it.
        email_ok = _check_field(
            scroll.email,
            scroll.email_hint,
            is_valid_email,
            'Enter a valid email address - it must contain "@".',
        )

        # Mobile: numbers only (the validator keeps letters out, this checks
        # that at least 7 digits were actually entered).
        phone_ok = _check_field(
            scroll.mobile,
            scroll.mobile_hint,
            is_valid_phone,
            "Enter a valid mobile number using digits only.",
        )

        # Password: every rule in the requirement rows must be met
        # (8+ chars, uppercase, symbol, number) or the field stays red.
        password_text = scroll.password.text()
        password_ok = all(check(password_text) for _item, check in scroll.req_items)
        _set_field_state(scroll.password, "ok" if password_ok else "error")

        # Confirm password: green when it matches, red when empty / different.
        confirm = scroll.confirm_password
        confirm_text = confirm.text()
        matched = bool(confirm_text) and confirm_text == password_text
        _set_field_state(confirm, "ok" if matched else "error")

        # Disable the button until every field is filled in, the email and
        # phone number are well formed, the password meets all four rules
        # and the two passwords match.
        filled = all(edit.text().strip() for edit in REQUIRED)
        scroll.create_btn.setEnabled(
            bool(filled) and matched and email_ok and phone_ok and password_ok
        )

    scroll.refresh_validation = refresh_validation
    for attr in ("first_name", "last_name", "email", "mobile", "password", "confirm_password"):
        edit = getattr(scroll, attr)
        edit.textChanged.connect(lambda _t, e=edit: refresh_validation())
    refresh_validation()

    # ----- divider + "already have an account" -----
    divider = QFrame()
    divider.setObjectName("divider")
    divider.setFixedHeight(1)
    col.addWidget(divider)
    col.addSpacing(8)

    signin = QHBoxLayout()
    signin.addStretch(1)
    signin.addWidget(make_label("Already have an account?", "hint"))
    scroll.signin_link = _link("Sign in")
    signin.addWidget(scroll.signin_link)
    signin.addStretch(1)
    col.addLayout(signin)

    # ----- card centred inside the scroll area -----
    holder = QWidget()
    holder.setObjectName("formHolder")         # transparent; only does layout
    outer = QVBoxLayout(holder)
    outer.setContentsMargins(24, 24, 24, 24)
    outer.addStretch(1)
    outer.addWidget(card, alignment=Qt.AlignHCenter)
    outer.addSpacing(24)
    help_row = QHBoxLayout()
    help_row.addStretch(1)
    help_row.addWidget(make_label("Need help?", "hint"))
    scroll.support_link = _link("Contact rental support")
    help_row.addWidget(scroll.support_link)
    help_row.addStretch(1)
    outer.addLayout(help_row)
    outer.addStretch(1)

    scroll.setWidget(holder)

    # The right panel must never get smaller than its content, otherwise the
    # form would shrink/clip on minimize. Min size = content size.
    scroll.setMinimumSize(holder.sizeHint())
    return scroll


def build_signup_hero_panel() -> QWidget:
    """Left-hand dark panel for the sign-up screen (shares build_hero_panel)."""
    return build_hero_panel(
        eyebrow="CREATE ACCOUNT",
        title="Made for the road\nahead.",
        subtitle="Create your account. Enter your details; they remain "
        "visible only to authorized rental staff.",
    )


# =========================================================================
#  SHARED - used by every screen, leave as is
# =========================================================================
def build_split_layout(root: QWidget, left: QWidget, right: QWidget) -> None:
    """49/51 split that resizes with the window."""
    layout = QHBoxLayout(root)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(left, 49)
    layout.addWidget(right, 51)
