from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

import requests
from PySide6.QtCore import (
    QAbstractAnimation,
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
    QButtonGroup,
    QComboBox,
    QCompleter,
    QDialog,
    QGraphicsOpacityEffect,
    QGridLayout,
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
from app.services import image_cache, media_service, pricing
from app.services.auth_service import is_valid_mobile, is_valid_street_address

from app.services.location_service import MAX_STREET_LENGTH
from app.services.vehicle_service import (
    ShowcaseVehicle,
    showcase_vehicle_rows,
    to_showcase,
)

ICONS_DIR = Path(__file__).with_name("icons")

UI_DIR = Path(__file__).parent
QSS_PATH = UI_DIR / "sign.qss"
FONTS_DIR = UI_DIR / "fonts"

#: The stylesheet is text, but Qt's `image:` wants a file, so the one arrow
#: the customer screens need is written here on first run (the cache folder is
#: disposable: `python -m scripts.clear_cache` style cleanup just rebuilds it).
QSS_ASSET_DIR = QSS_PATH.parent.parent.parent / "cache" / "qss"

_CHEVRON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" '
    'viewBox="0 0 12 12"><path d="M2.5 4.5 L6 8 L9.5 4.5" fill="none" '
    'stroke="{colour}" stroke-width="1.6" stroke-linecap="round" '
    'stroke-linejoin="round"/></svg>\n'
)

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
    # customer screens (vehicles, booking dates, checkout) ----------------
    # Same cream/ink/gold family as above, with the neutrals the longer
    # pages need: white cards on cream, tan for anything selected.
    "surface": "#FFFFFF",
    "surface_soft": "#fbf9f4",
    "panel_tan": "#e9e0cd",
    "panel_tan_soft": "#f4efe3",
    "line": "#e4ded1",
    "line_strong": "#cfc4ac",
    "muted_soft": "#948e83",
    "green": "#1E7A46",
    "green_soft": "#e7f1e8",
    "amber": "#9a6d1c",
    "amber_soft": "#f7eedb",
    "danger_soft": "#fbecea",
    # The dashboard hero's huge vehicle name: the warm grey the showroom
    # reference used -- dark enough to read through the car's cutout edges.
    "hero_name": "#a39d92",
}



# ---------- setup ----------
def _chevron_url() -> str:
    """Write the combo boxes' down arrow once; return its path for the QSS."""
    target = QSS_ASSET_DIR / "chevron-down.svg"
    try:
        QSS_ASSET_DIR.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(
                _CHEVRON_SVG.format(colour=TOKENS["text_muted"]), encoding="utf-8"
            )
    except OSError:
        # A read-only checkout still runs; the combo just loses its arrow.
        return ""
    return target.as_posix()


def apply_theme(app: QApplication) -> None:
    """Load fonts and the stylesheet. Call once at startup."""
    for font_file in FONTS_DIR.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(font_file))

    qss = QSS_PATH.read_text(encoding="utf-8")
    qss = qss.replace("@chevron", _chevron_url())
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


def _password_toggle_icon(visible: bool) -> QIcon:
    """The eye that matches the field's current visibility."""
    filename = "eye_shown.png" if visible else "eye_hidden.png"
    return QIcon(str(ICONS_DIR / filename))


def _add_password_toggle(edit: QLineEdit) -> None:
    """Put a clickable eye inside a password field to reveal what is typed."""
    action = edit.addAction(
        _password_toggle_icon(False), QLineEdit.ActionPosition.TrailingPosition
    )
    action.setToolTip("Show password")

    def toggle(_checked: bool = False) -> None:
        visible = edit.echoMode() == QLineEdit.Normal
        edit.setEchoMode(QLineEdit.Password if visible else QLineEdit.Normal)
        action.setIcon(_password_toggle_icon(not visible))
        action.setToolTip("Show password" if visible else "Hide password")
        edit.setFocus()

    action.triggered.connect(toggle)
    edit.password_action = action  # type: ignore[attr-defined]


def _field(caption: str, placeholder: str, password: bool = False) -> tuple[QLabel, QLineEdit]:
    label = make_label(caption, "fieldLabel")
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    if password:
        edit.setEchoMode(QLineEdit.Password)
        _add_password_toggle(edit)
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


def _set_field_state(edit: QWidget, state: str) -> None:
    """Re-apply the QSS border for a state change ('ok' / 'error' / '')."""
    if edit.property("state") == state:
        return
    edit.setProperty("state", state)
    edit.style().unpolish(edit)
    edit.style().polish(edit)
    edit.update()


def _field_hint() -> QLabel:
    """Small red message under a field; hidden until the input is wrong.

    The line is reserved whether the hint is hidden or visible. Without that,
    the card's layout re-flows when an error appears and moves the fields
    around while the customer is still typing.
    """
    hint = QLabel()
    hint.setObjectName("fieldHint")
    hint.setWordWrap(True)
    hint.setFixedHeight(20)
    policy = hint.sizePolicy()
    policy.setRetainSizeWhenHidden(True)
    hint.setSizePolicy(policy)
    hint.hide()
    return hint


def _check_field(
    edit: QLineEdit,
    hint: QLabel,
    is_valid,
    message: str,
    *,
    empty_is_error: bool = True,
) -> bool:
    """Colour a required field by validity and explain what went wrong.

    Empty -> neutral before the field has been touched, otherwise a red
    border (it is required) with no message. Filled but wrong -> red border +
    ``message``. Valid -> green border. Returns True when valid.
    """
    text = edit.text().strip()
    if not text:
        _set_field_state(edit, "error" if empty_is_error else "")
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

    #: Where a failed sign-in says so. Hidden until the window fills it in.
    panel.error = make_label("", "hint", wrap=True)
    panel.error.setProperty("tone", "danger")
    panel.error.hide()

    #: The window decides what Sign In *does* (theme.py only draws the card).
    #: Left as None the button validates and does nothing -- the mock state
    #: the screen was built in.
    panel.submit_handler = None

    def _submit() -> None:
        panel.error.hide()
        if panel.submit_handler is not None:
            panel.submit_handler()

    panel.signin_btn.clicked.connect(_submit)

    col.addSpacing(12)
    col.addWidget(panel.error)
    col.addWidget(panel.signin_btn, alignment=Qt.AlignLeft)

    # ----- live validation: red/green borders + disabled button -----
    email_touched = False
    password_touched = False

    def refresh_validation() -> None:
        # Email must contain "@" (with text on both sides of it). The hint
        # follows every keystroke; an empty untouched field stays neutral.
        email_ok = _check_field(
            panel.email,
            panel.email_hint,
            is_valid_email,
            'Enter a valid email address - it must contain "@".',
            empty_is_error=email_touched,
        )
        # Password is simply required here (no strength rules on sign-in).
        password_ok = bool(panel.password.text())
        _set_field_state(
            panel.password,
            "error" if password_touched and not password_ok else "",
        )

        panel.signin_btn.setEnabled(email_ok and password_ok)

    def email_changed(_text: str) -> None:
        nonlocal email_touched
        email_touched = True
        refresh_validation()

    def password_changed(_text: str) -> None:
        nonlocal password_touched
        password_touched = True
        refresh_validation()

    panel.refresh_validation = refresh_validation
    panel.email.textChanged.connect(email_changed)
    panel.password.textChanged.connect(password_changed)
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
#: The sign-up card and its content width (32px padding on each side).
SIGNUP_CARD_WIDTH = 560
SIGNUP_PAGE_WIDTH = SIGNUP_CARD_WIDTH - 64


class SlidePager(QWidget):
    """Fixed-width pages that slide horizontally, used by the sign-up steps.

    Both pages are children of this widget, so the parent clips them while
    they move and the white card behind them never shifts. The pager is as
    tall as its tallest page, which keeps the card from jumping as the steps
    change; shorter pages simply leave space below their content.
    """

    #: The index that is now fully visible, after a slide (or an instant jump).
    settled = Signal(int)

    def __init__(self, page_width: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._page_width = page_width
        self._pages: list[QWidget] = []
        self._index = 0
        self._height = 0
        self._outgoing: Optional[QWidget] = None
        self._incoming: Optional[QWidget] = None
        self._direction = 1
        self.setFixedWidth(page_width)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(340)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._move)
        self._anim.finished.connect(self._land)

    def add_page(self, page: QWidget) -> None:
        page.setParent(self)
        page.setFixedWidth(self._page_width)
        page.adjustSize()
        page.hide()
        self._pages.append(page)
        if len(self._pages) == 1:
            page.show()
        self._height = max(
            self._height,
            max(p.sizeHint().height() for p in self._pages),
        )
        self.setFixedHeight(self._height)
        self._rest()

    def current_index(self) -> int:
        return self._index

    def slide_to(self, index: int, *, animate: bool = True) -> None:
        """Move to page `index`; animation is skipped if already there."""
        if index == self._index or not 0 <= index < len(self._pages):
            return
        if self._anim.state() == QAbstractAnimation.State.Running:
            return

        outgoing = self._pages[self._index]
        incoming = self._pages[index]
        self._direction = 1 if index > self._index else -1
        self._outgoing, self._incoming = outgoing, incoming
        incoming.setGeometry(
            self._direction * self.width(), 0, self.width(), self.height()
        )
        incoming.show()
        incoming.raise_()
        outgoing.show()

        if not animate:
            self._index = index
            self._outgoing = self._incoming = None
            self._rest()
            self.settled.emit(index)
            return

        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    # ---------- animation ----------
    def _move(self, value) -> None:
        if self._outgoing is None or self._incoming is None:
            return
        t = float(value)
        width = self.width()
        height = self.height()
        self._outgoing.setGeometry(
            int(-self._direction * width * t), 0, width, height
        )
        self._incoming.setGeometry(
            int(self._direction * width * (1.0 - t)), 0, width, height
        )

    def _land(self) -> None:
        if self._incoming is not None:
            self._index = self._pages.index(self._incoming)
        self._outgoing = self._incoming = None
        self._rest()
        self.settled.emit(self._index)

    def _rest(self) -> None:
        visible = self.width()
        for i, page in enumerate(self._pages):
            if i == self._index:
                page.setGeometry(0, 0, visible, self.height())
                page.show()
            else:
                page.setGeometry(visible, 0, visible, self.height())
                page.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._anim.state() == QAbstractAnimation.State.Running:
            self._anim.stop()
            self._land()
        else:
            self._rest()

    def sizeHint(self) -> QSize:
        return QSize(self._page_width, self._height)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()


def _search_combo(placeholder: str) -> QComboBox:
    """An editable combo: type to filter, but only list values count."""
    combo = QComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.NoInsert)
    combo.setMaxVisibleItems(12)
    combo.setMinimumHeight(43)
    combo.setCurrentIndex(-1)
    combo.lineEdit().setPlaceholderText(placeholder)
    completer = combo.completer()
    completer.setCompletionMode(QCompleter.PopupCompletion)
    completer.setFilterMode(Qt.MatchContains)
    completer.setCaseSensitivity(Qt.CaseInsensitive)
    return combo


def _new_signup_page() -> tuple[QWidget, QVBoxLayout]:
    page = QWidget()
    page.setObjectName("signupPage")
    col = QVBoxLayout(page)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(6)
    return page, col


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


def _build_signup_step_one(scroll: QScrollArea) -> QWidget:
    """Step 1: the account details (the original sign-up card)."""
    page, col = _new_signup_page()

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

    # ----- mobile number -----
    mobile_lbl, scroll.mobile = _field("Mobile number", "09XX XXX XXXX")
    # Exactly 11 digits, beginning with 09. The validator also blocks the
    # first digit from being anything other than 0 and the second other than 9.
    scroll.mobile.setMaxLength(11)
    scroll.mobile.setValidator(
        QRegularExpressionValidator(
            QRegularExpression(r"09[0-9]{0,9}"), scroll.mobile
        )
    )
    col.addWidget(mobile_lbl)
    col.addWidget(scroll.mobile)
    scroll.mobile_hint = _field_hint()
    col.addWidget(scroll.mobile_hint)

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

    #: Where a refused sign-up says why (duplicate email, weak password...).
    scroll.error = make_label("", "hint", wrap=True)
    scroll.error.setProperty("tone", "danger")
    scroll.error.hide()

    col.addWidget(scroll.error)
    col.addWidget(scroll.create_btn, alignment=Qt.AlignLeft)
    col.addSpacing(14)

    # ----- live validation: red highlights + disabled button -----
    REQUIRED = (scroll.first_name, scroll.last_name, scroll.email,
                scroll.mobile, scroll.password, scroll.confirm_password)

    #: Empty fields stay neutral until the customer has typed in them, so the
    #: first paint has no red outlines. Once touched, an empty field is an error.
    touched = {edit: False for edit in REQUIRED}

    def refresh_validation() -> None:
        # Names are simply required; empty + untouched stays neutral.
        for edit in (scroll.first_name, scroll.last_name):
            if edit.text().strip():
                _set_field_state(edit, "")
            else:
                _set_field_state(edit, "error" if touched[edit] else "")

        # Email: validated on every keystroke, including the moment the
        # address is missing its "@" (not only on the next edit or on blur).
        email_ok = _check_field(
            scroll.email,
            scroll.email_hint,
            is_valid_email,
            'Enter a valid email address - it must contain "@".',
            empty_is_error=touched[scroll.email],
        )

        # Mobile: exactly 11 digits, beginning with 09.
        phone_ok = _check_field(
            scroll.mobile,
            scroll.mobile_hint,
            is_valid_mobile,
            "Enter an 11-digit mobile number starting with 09.",
            empty_is_error=touched[scroll.mobile],
        )

        # Password: every rule in the requirement rows must be met
        # (8+ chars, uppercase, symbol, number). Empty untouched is neutral.
        password_text = scroll.password.text()
        password_ok = all(check(password_text) for _item, check in scroll.req_items)
        if password_text:
            _set_field_state(scroll.password, "ok" if password_ok else "error")
        else:
            _set_field_state(
                scroll.password, "error" if touched[scroll.password] else ""
            )

        # Confirm password: neutral until typed, then green only on a match.
        confirm = scroll.confirm_password
        confirm_text = confirm.text()
        matched = bool(confirm_text) and confirm_text == password_text
        if confirm_text:
            _set_field_state(confirm, "ok" if matched else "error")
        else:
            _set_field_state(confirm, "error" if touched[confirm] else "")

        # Disable the button until every field is filled in, the email and
        # phone number are well formed, the password meets all four rules
        # and the two passwords match.
        filled = all(edit.text().strip() for edit in REQUIRED)
        scroll.create_btn.setEnabled(
            bool(filled) and matched and email_ok and phone_ok and password_ok
        )

    def field_changed(edit: QLineEdit) -> None:
        touched[edit] = True
        refresh_validation()

    scroll.refresh_validation = refresh_validation
    for attr in ("first_name", "last_name", "email", "mobile", "password", "confirm_password"):
        edit = getattr(scroll, attr)
        edit.textChanged.connect(lambda _t, e=edit: field_changed(e))
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

    col.addStretch(1)
    return page


def _build_signup_step_two(
    scroll: QScrollArea,
    province_options: Iterable,
    cities_for: Optional[Callable[[str], Iterable]],
    location_error: str,
) -> QWidget:
    """Step 2: the PSGC address pair, street line and guarantor details."""
    page, col = _new_signup_page()

    top = QHBoxLayout()
    top.setContentsMargins(0, 0, 0, 0)
    top.addWidget(overline("Step 2 of 2"))
    top.addStretch(1)
    scroll.back_link = _link("← Back")
    top.addWidget(scroll.back_link)
    col.addLayout(top)
    col.addSpacing(8)

    col.addWidget(make_label("Address & guarantor", "cardTitle"))
    col.addWidget(make_label(
        "Where we'll reach you, and who we can call if we can't reach you.",
        "cardSubtitle", wrap=True,
    ))
    col.addSpacing(14)

    # ----- province -----
    col.addWidget(make_label("Province", "fieldLabel"))
    scroll.province = _search_combo("Search province")
    scroll.province_hint = _field_hint()
    col.addWidget(scroll.province)
    col.addWidget(scroll.province_hint)
    col.addSpacing(14)

    # ----- city / municipality -----
    col.addWidget(make_label("City / municipality", "fieldLabel"))
    scroll.municipality = _search_combo("Choose a province first")
    scroll.municipality.setEnabled(False)
    scroll.municipality_hint = _field_hint()
    col.addWidget(scroll.municipality)
    col.addWidget(scroll.municipality_hint)
    col.addSpacing(14)

    # ----- street line -----
    street_lbl, scroll.street = _field(
        "Street address", "House / unit no. and street name"
    )
    scroll.street.setMaxLength(MAX_STREET_LENGTH)
    scroll.street_hint = _field_hint()
    col.addWidget(street_lbl)
    col.addWidget(scroll.street)
    col.addWidget(scroll.street_hint)
    col.addSpacing(14)

    # ----- guarantor -----
    col.addWidget(overline("Guarantor"))
    col.addWidget(make_label(
        "Someone we can call if we can't reach you. They must be old enough "
        "to vouch for you and reachable on a Philippine mobile number.",
        "note", wrap=True,
    ))
    col.addSpacing(8)

    col.addLayout(_field_row(
        scroll,
        "Guarantor first name",
        "First name",
        "guarantor_first_name",
        "Guarantor last name",
        "Last name",
        "guarantor_last_name",
    ))
    col.addSpacing(14)

    guarantor_mobile_lbl, scroll.guarantor_mobile = _field(
        "Guarantor mobile number", "09XX XXX XXXX"
    )
    scroll.guarantor_mobile.setMaxLength(11)
    scroll.guarantor_mobile.setValidator(
        QRegularExpressionValidator(QRegularExpression("09[0-9]*"), scroll.guarantor_mobile)
    )
    scroll.guarantor_mobile_hint = _field_hint()
    col.addWidget(guarantor_mobile_lbl)
    col.addWidget(scroll.guarantor_mobile)
    col.addWidget(scroll.guarantor_mobile_hint)
    col.addSpacing(10)

    col.addWidget(make_label(
        "The counter verifies your physical driver's licence at pickup.",
        "note", wrap=True,
    ))
    col.addSpacing(14)

    scroll.step2_error = make_label("", "hint", wrap=True)
    scroll.step2_error.setProperty("tone", "danger")
    scroll.step2_error.hide()
    col.addWidget(scroll.step2_error)

    scroll.finish_btn = primary_button("→  COMPLETE SIGN-UP")
    col.addWidget(scroll.finish_btn, alignment=Qt.AlignLeft)
    col.addStretch(1)

    # ----- the "must pick from the list" rule -----
    scroll._cities_key = None

    def selected_key(combo: QComboBox) -> Optional[str]:
        text = combo.currentText().strip()
        if not text:
            return None
        index = combo.findText(text, Qt.MatchFixedString)
        return combo.itemData(index) if index >= 0 else None

    def load_cities(key: Optional[str]) -> None:
        if key == scroll._cities_key:
            return
        scroll._cities_key = key
        city = scroll.municipality
        city.blockSignals(True)
        city.clear()
        city.setCurrentIndex(-1)
        city.blockSignals(False)
        city.setEnabled(bool(key))
        city.lineEdit().setPlaceholderText(
            "Search city / municipality" if key else "Choose a province first"
        )
        _set_field_state(city, "")
        scroll.municipality_hint.hide()
        scroll.municipality_hint.clear()
        if not key or cities_for is None:
            return
        try:
            options = list(cities_for(key))
        except Exception as exc:  # noqa: BLE001 - the database is remote
            scroll.municipality_hint.setText(f"Could not load cities: {exc}")
            scroll.municipality_hint.show()
            return
        city.blockSignals(True)
        for option in options:
            city.addItem(option.name, option.code)
        city.setCurrentIndex(-1)
        city.blockSignals(False)

    def guarantor_ready() -> bool:
        return (
            bool(scroll.guarantor_first_name.text().strip())
            and bool(scroll.guarantor_last_name.text().strip())
            and is_valid_mobile(scroll.guarantor_mobile.text())
        )

    def address_ready() -> bool:
        return (
            bool(selected_key(scroll.province))
            and bool(selected_key(scroll.municipality))
            and is_valid_street_address(scroll.street.text())
            and guarantor_ready()
        )

    def update_finish() -> None:
        scroll.finish_btn.setEnabled(address_ready())

    def province_typed(_text: str = "") -> None:
        load_cities(selected_key(scroll.province))
        update_finish()

    def commit_province(*_args) -> None:
        province = scroll.province
        text = province.currentText().strip()
        if not text:
            _set_field_state(province, "")
            scroll.province_hint.hide()
            load_cities(None)
        elif selected_key(province):
            _set_field_state(province, "ok")
            scroll.province_hint.hide()
        else:
            province.blockSignals(True)
            province.setCurrentIndex(-1)
            province.blockSignals(False)
            _set_field_state(province, "error")
            scroll.province_hint.setText("Choose a province from the list.")
            scroll.province_hint.show()
            load_cities(None)
        update_finish()

    def commit_city(*_args) -> None:
        city = scroll.municipality
        text = city.currentText().strip()
        if not text:
            _set_field_state(city, "")
            scroll.municipality_hint.hide()
        elif selected_key(city):
            _set_field_state(city, "ok")
            scroll.municipality_hint.hide()
        else:
            _set_field_state(city, "error")
            scroll.municipality_hint.setText(
                "Choose a city or municipality from the list."
            )
            scroll.municipality_hint.show()
            city.blockSignals(True)
            city.setCurrentIndex(-1)
            city.blockSignals(False)
        update_finish()

    def street_changed(_text: str = "") -> None:
        text = scroll.street.text().strip()
        if not text:
            _set_field_state(scroll.street, "")
            scroll.street_hint.hide()
        elif is_valid_street_address(text):
            _set_field_state(scroll.street, "ok")
            scroll.street_hint.hide()
        else:
            _set_field_state(scroll.street, "error")
            scroll.street_hint.setText(
                "Include the house/unit number and the street name."
            )
            scroll.street_hint.show()
        update_finish()

    def guarantor_name_changed(_text: str = "") -> None:
        for edit in (scroll.guarantor_first_name, scroll.guarantor_last_name):
            _set_field_state(edit, "ok" if edit.text().strip() else "")
        update_finish()

    def guarantor_mobile_changed(_text: str = "") -> None:
        text = scroll.guarantor_mobile.text().strip()
        if not text:
            _set_field_state(scroll.guarantor_mobile, "")
            scroll.guarantor_mobile_hint.hide()
        elif is_valid_mobile(text):
            _set_field_state(scroll.guarantor_mobile, "ok")
            scroll.guarantor_mobile_hint.hide()
        else:
            _set_field_state(scroll.guarantor_mobile, "error")
            scroll.guarantor_mobile_hint.setText(
                "Use an 11-digit number starting with 09."
            )
            scroll.guarantor_mobile_hint.show()
        update_finish()

    scroll.province.editTextChanged.connect(province_typed)
    scroll.province.currentIndexChanged.connect(lambda _i: province_typed())
    scroll.province.activated.connect(commit_province)
    scroll.province.lineEdit().editingFinished.connect(commit_province)
    scroll.municipality.editTextChanged.connect(lambda _t: update_finish())
    scroll.municipality.currentIndexChanged.connect(lambda _i: update_finish())
    scroll.municipality.activated.connect(commit_city)
    scroll.municipality.lineEdit().editingFinished.connect(commit_city)
    scroll.street.textChanged.connect(street_changed)
    scroll.street.editingFinished.connect(street_changed)
    scroll.guarantor_first_name.textChanged.connect(guarantor_name_changed)
    scroll.guarantor_last_name.textChanged.connect(guarantor_name_changed)
    scroll.guarantor_mobile.textChanged.connect(guarantor_mobile_changed)

    scroll.province.blockSignals(True)
    for option in province_options:
        scroll.province.addItem(option.name, option.key)
    scroll.province.setCurrentIndex(-1)
    scroll.province.blockSignals(False)
    if location_error:
        scroll.province_hint.setText(location_error)
        scroll.province_hint.show()
    update_finish()

    def address_values() -> dict[str, str]:
        return {
            "province_key": selected_key(scroll.province) or "",
            "city_municipality_code": selected_key(scroll.municipality) or "",
            "street_address": " ".join(scroll.street.text().split()),
            "guarantor_first_name": " ".join(
                scroll.guarantor_first_name.text().split()
            ),
            "guarantor_last_name": " ".join(
                scroll.guarantor_last_name.text().split()
            ),
            "guarantor_mobile": "".join(
                scroll.guarantor_mobile.text().split()
            ),
        }

    scroll.refresh_address_validation = update_finish
    scroll.address_ready = address_ready
    scroll.address_values = address_values
    return page


def build_signup_form_panel(
    *,
    province_options: Optional[Iterable] = None,
    cities_for: Optional[Callable[[str], Iterable]] = None,
    location_error: str = "",
) -> QScrollArea:
    """Right-hand cream panel holding the two-step sign-up card.

    The panel lives inside a QScrollArea so the form stays usable on short
    screens. The card is 560px wide and sized to its content; the two steps
    sit in a SlidePager so switching steps is a horizontal slide.
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
        card_width=SIGNUP_CARD_WIDTH,
        card_height=None,                      # size to content, don't lock
        margins=(32, 30, 32, 26),              # tight vertical padding
        spacing=6,
    )

    pager = SlidePager(SIGNUP_PAGE_WIDTH)
    pager.add_page(_build_signup_step_one(scroll))
    pager.add_page(
        _build_signup_step_two(
            scroll,
            list(province_options or []),
            cities_for,
            location_error,
        )
    )
    col.addWidget(pager)
    scroll.pager = pager

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


# =========================================================================
#  CUSTOMER SCREENS  (app/ui/vehicles.py, vehicle_details.py, booking_*.py)
#  -------------------------------------------------------------------------
#  What the four customer screens share: the top bar, the small building
#  blocks (pills, spec tiles, value rows) and the display formatters.
#  Styling lives in sign.qss, keyed by objectName and dynamic properties;
#  nothing here calls setStyleSheet().
# =========================================================================

NAV_HEIGHT = 72

#: (key, caption) of the top bar's tabs, in order.
NAV_TABS: tuple[tuple[str, str], ...] = (
    ("vehicles", "Vehicles"),
    ("bookings", "Bookings"),
    ("payments", "Payments"),
)

#: How many vehicles one page of the browse grid holds.
GRID_PAGE_SIZE = 12

#: The booking flow's three steps, shown on screens 3 and 4.
BOOKING_STEPS: tuple[str, ...] = ("Vehicle", "Dates & time", "Payment")

#: Sentence the booking steps show when the customer is not signed in yet.
GUEST_LABEL = "SIGN IN"


# ---------- formatters ----------
def pesos_text(amount) -> str:
    """'₱4,294' -- cents only when the amount has any."""
    return pricing.display_money(amount)


def total_text(amount) -> str:
    """'₱18,110.40' -- totals always show their cents."""
    return pricing.total_money(amount)


def per_day_text(rate) -> str:
    """'₱4,294 / day'."""
    return f"{pricing.display_money(rate)} / day"


def short_day(value: date) -> str:
    """'25 Oct'."""
    return f"{value.day} {value:%b}"


def day_label(value: date) -> str:
    """'Sun, 25 Oct 2026'."""
    return f"{value:%a, %d %b %Y}"


def clock_text(value) -> str:
    """'9:00 AM' from a time or a datetime."""
    hour = value.hour % 12 or 12
    return f"{hour}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"


def moment_text(value: datetime) -> str:
    """'25 Oct 2026, 9:00 AM'."""
    return f"{value.day} {value:%b %Y}, {clock_text(value)}"


def window_text(start: datetime, end: datetime) -> str:
    """Two lines, the way the summary cards stack them."""
    return f"{moment_text(start)}\n{moment_text(end)}"


def days_text(count: int) -> str:
    return f"{count} rental day{'' if count == 1 else 's'}"


# ---------- small building blocks ----------
def overline(text: str, spacing: float = 1.5) -> QLabel:
    """The small uppercase caption above a value ('TOTAL', 'PICKUP')."""
    return make_label(text.upper(), "overline", spacing=spacing)


def section_title(text: str) -> QLabel:
    return make_label(text, "sectionTitle")


def hint_label(text: str = "", tone: str = "muted") -> QLabel:
    label = make_label(text, "hint", wrap=True)
    label.setProperty("tone", tone)
    return label


def panel(
    object_name: str = "panel",
    *,
    spacing: int = 14,
    margins: tuple[int, int, int, int] = (24, 22, 24, 22),
) -> tuple[QFrame, QVBoxLayout]:
    """A white card. Returns (frame, column) so callers add into the column."""
    frame = QFrame()
    frame.setObjectName(object_name)
    col = QVBoxLayout(frame)
    col.setContentsMargins(*margins)
    col.setSpacing(spacing)
    return frame, col


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    line.setFixedHeight(1)
    return line


def primary_button(text: str, *, icon_name: str | None = None) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("primaryButton")
    button.setCursor(Qt.PointingHandCursor)
    button.setMinimumHeight(44)
    if icon_name:
        button.setIcon(icon_from(icon_name, TOKENS["text_on_dark"], 16))
    return button


def ghost_button(text: str, *, icon_name: str | None = None) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("ghostButton")
    button.setCursor(Qt.PointingHandCursor)
    button.setMinimumHeight(44)
    if icon_name:
        button.setIcon(icon_from(icon_name, TOKENS["text_dark"], 16))
    return button


def text_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("textButton")
    button.setCursor(Qt.PointingHandCursor)
    button.setFlat(True)
    return button


def icon_from(name: str, colour: str, size: int = 18) -> QIcon:
    """Tiny shim so callers do not import app.ui.icons just for one icon."""
    from app.ui import icons  # local: keeps the icon module out of startup

    return icons.icon(name, colour, size)


def pill(text: str) -> QPushButton:
    """One filter pill. Checkable, so `pillGroup` can make them a radio set."""
    button = QPushButton(text)
    button.setObjectName("pill")
    button.setCheckable(True)
    button.setCursor(Qt.PointingHandCursor)
    button.setFocusPolicy(Qt.NoFocus)
    return button


def pill_group(pills: list[QPushButton]) -> QButtonGroup:
    """Make a list of pills exclusive and return the (kept) group."""
    group = QButtonGroup(pills[0].parentWidget() if pills else None)
    group.setExclusive(True)
    for index, button in enumerate(pills):
        group.addButton(button, index)
    return group


def spec_tile(icon_name: str, caption: str, value: str) -> QFrame:
    """One box of the 2x2 spec grid on the vehicle page."""
    tile = QFrame()
    tile.setObjectName("specTile")
    row = QHBoxLayout(tile)
    row.setContentsMargins(14, 12, 14, 12)
    row.setSpacing(12)

    glyph = QLabel()
    glyph.setObjectName("specIcon")
    glyph.setPixmap(icon_from(icon_name, TOKENS["text_dark"], 20).pixmap(20, 20))
    glyph.setFixedSize(34, 34)
    glyph.setAlignment(Qt.AlignCenter)
    row.addWidget(glyph, 0, Qt.AlignTop)

    text = QVBoxLayout()
    text.setContentsMargins(0, 0, 0, 0)
    text.setSpacing(2)
    text.addWidget(overline(caption, spacing=1.2))
    text.addWidget(make_label(value, "specValue"))
    row.addLayout(text, 1)
    return tile


def value_row(
    caption: str,
    value: str,
    *,
    icon_name: str | None = None,
    strong: bool = False,
) -> QWidget:
    """A caption on the left, a value on the right: the price breakdown."""
    holder = QWidget()
    holder.setObjectName("valueRow")
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(10)

    if icon_name:
        glyph = QLabel()
        glyph.setObjectName("specIcon")
        glyph.setPixmap(icon_from(icon_name, TOKENS["text_muted"], 16).pixmap(16, 16))
        glyph.setFixedSize(16, 16)
        row.addWidget(glyph, 0, Qt.AlignVCenter)

    label = make_label(caption, "valueCaption")
    label.setProperty("strong", strong)
    row.addWidget(label, 1)
    amount = make_label(value, "valueAmount")
    amount.setProperty("strong", strong)
    row.addWidget(amount, 0, Qt.AlignRight)
    return holder


def status_chip(text: str, tone: str = "muted") -> QLabel:
    """'CONFIRMED' -- tone drives the colour (see sign.qss)."""
    chip = make_label(text.upper(), "statusChip", spacing=1.2)
    chip.setProperty("tone", tone)
    chip.setAlignment(Qt.AlignCenter)
    return chip


def step_strip(labels: tuple[str, ...], current: int) -> QWidget:
    """'1 Vehicle -- 2 Dates & time -- 3 Payment' with the current step lit."""
    holder = QWidget()
    holder.setObjectName("stepStrip")
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(10)

    for index, caption in enumerate(labels, start=1):
        step = QWidget()
        step.setObjectName("stepItem")
        step.setProperty("state", "current" if index == current else ("done" if index < current else "todo"))
        line = QHBoxLayout(step)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)

        dot = QLabel(str(index))
        dot.setObjectName("stepDot")
        dot.setProperty("state", step.property("state"))
        dot.setAlignment(Qt.AlignCenter)
        dot.setFixedSize(22, 22)
        line.addWidget(dot)
        label = make_label(caption, "stepLabel")
        label.setProperty("state", step.property("state"))
        line.addWidget(label)

        row.addWidget(step)
        if index < len(labels):
            arrow = QLabel()
            arrow.setPixmap(icon_from("forward", TOKENS["muted_soft"], 14).pixmap(14, 14))
            arrow.setFixedSize(14, 14)
            row.addWidget(arrow, 0, Qt.AlignVCenter)
    row.addStretch(1)
    return holder


def field(caption: str, placeholder: str = "", *, password: bool = False) -> QWidget:
    """Caption above an input, the way the sign-in card stacks them."""
    holder = QWidget()
    holder.setObjectName("fieldHolder")
    col = QVBoxLayout(holder)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(6)
    label = make_label(caption, "fieldLabel")
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    if password:
        edit.setEchoMode(QLineEdit.Password)
        _add_password_toggle(edit)
    col.addWidget(label)
    col.addWidget(edit)
    holder.edit = edit  # type: ignore[attr-defined]
    holder.label = label  # type: ignore[attr-defined]
    return holder


# ---------- top bar ----------
class NavBar(QFrame):
    """The customer app's top bar: brand, tabs, search and the auth corner.

    Shared by every screen in the router (app/ui/main_window.py). The tabs are
    exclusive checkable buttons, so the active one is styled by QSS alone.
    """

    tab_selected = Signal(str)
    search_changed = Signal(str)
    auth_clicked = Signal()
    notifications_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("navbar")
        self.setFixedHeight(NAV_HEIGHT)

        self.search = QLineEdit()
        self.search.setObjectName("navSearch")
        self.search.setPlaceholderText("Search make, model or plate")
        self.search.setClearButtonEnabled(True)
        # Wide enough to read the placeholder: the tabs take the middle third
        # of the bar, so without a floor the field collapses to a stub.
        self.search.setMinimumWidth(300)
        self.search.setMaximumWidth(340)
        self.search.textChanged.connect(self.search_changed)

        # The bell is an account feature: hidden until a customer signs in.
        self._notifications_unread = False
        self._notifications_count = 0
        self.notifications_button = QPushButton()
        self.notifications_button.setObjectName("notifButton")
        self.notifications_button.setCursor(Qt.PointingHandCursor)
        self.notifications_button.setFocusPolicy(Qt.NoFocus)
        self.notifications_button.setFixedSize(40, 40)
        self.notifications_button.setIconSize(QSize(30, 30))
        self.notifications_button.setToolTip("Notifications")
        self.notifications_button.setAccessibleName("Notifications")
        self.notifications_button.clicked.connect(
            lambda _checked=False: self.notifications_clicked.emit()
        )
        self.notifications_button.hide()

        # Scale the shipped 512px art once; the loop only rotates a 22px copy.
        self._bell_pixmaps = {
            state: QPixmap(str(ICONS_DIR / name)).scaled(
                22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            for state, name in (
                (False, "notif.png"),
                (True, "notif_ring.png"),
            )
        }

        # One looping swing while there is something unread. The ring icon has
        # the motion lines; rotating it makes the bell read as "ringing" even
        # at the small top-bar size.
        self._bell_animation = QVariantAnimation(self)
        self._bell_animation.setStartValue(0.0)
        self._bell_animation.setEndValue(1.0)
        self._bell_animation.setDuration(1600)
        self._bell_animation.setLoopCount(-1)
        self._bell_animation.setEasingCurve(QEasingCurve.Linear)
        self._bell_animation.valueChanged.connect(self._on_bell_frame)
        self._paint_bell(0.0)

        self.auth_button = QPushButton(GUEST_LABEL)
        self.auth_button.setObjectName("authButton")
        self.auth_button.setCursor(Qt.PointingHandCursor)
        self.auth_button.setFixedHeight(40)
        self.auth_button.clicked.connect(self.auth_clicked)
        self.set_user(None)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(28, 0, 28, 0)
        layout.setSpacing(0)
        layout.addWidget(self._brand(), 1)
        layout.addWidget(self._tabs(), 1)
        layout.addWidget(self._actions(), 1)

    # ----- pieces -----
    def _brand(self) -> QWidget:
        holder = QWidget()
        holder.setObjectName("navSection")
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        logo = QLabel()
        logo.setObjectName("navLogo")
        logo.setPixmap(
            QPixmap(str(ICONS_DIR / "icon.png")).scaled(
                32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )
        logo.setFixedSize(32, 32)
        row.addWidget(logo, 0, Qt.AlignLeft | Qt.AlignVCenter)
        row.addSpacing(10)
        brand = make_label("RENTWHEELS", "navBrand", spacing=2.4)
        row.addWidget(brand, 0, Qt.AlignLeft | Qt.AlignVCenter)
        row.addStretch(1)
        return holder

    def _tabs(self) -> QWidget:
        holder = QWidget()
        holder.setObjectName("navSection")
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(28)
        row.addStretch(1)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.tab_buttons: dict[str, QPushButton] = {}
        for key, caption in NAV_TABS:
            button = QPushButton(caption)
            button.setObjectName("navItem")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda _checked, k=key: self.tab_selected.emit(k))
            self._group.addButton(button)
            self.tab_buttons[key] = button
            row.addWidget(button)
        row.addStretch(1)
        return holder

    def _actions(self) -> QWidget:
        holder = QWidget()
        holder.setObjectName("navSection")
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addStretch(1)
        row.addWidget(self.search)
        row.addWidget(self.notifications_button)
        row.addWidget(self.auth_button)
        return holder

    # ----- state -----
    def set_active(self, key: str) -> None:
        button = self.tab_buttons.get(key)
        if button is None or button.isChecked():
            return
        button.setChecked(True)

    def clear_active(self) -> None:
        """Drop the underline when a screen is not one of the top-bar tabs.

        The account page owns its own Personal / Bookings / Payments strip,
        so leaving Payments underlined in the top bar while the profile is
        showing would point at the wrong place. Qt's exclusive group refuses
        to uncheck its current button, hence the temporary non-exclusive
        window; setChecked does not emit clicked, so no tab is re-opened.
        """
        self._group.setExclusive(False)
        for button in self.tab_buttons.values():
            button.setChecked(False)
        self._group.setExclusive(True)

    def set_notification_state(self, unread: bool, count: int = 0) -> None:
        """Swap the bell to the ring icon and swing it while something is new."""
        unread = bool(unread)
        self._notifications_unread = unread
        self._notifications_count = max(0, int(count))
        self.notifications_button.setProperty("unread", unread)
        self.notifications_button.style().unpolish(self.notifications_button)
        self.notifications_button.style().polish(self.notifications_button)

        if unread:
            noun = (
                "notification"
                if self._notifications_count == 1
                else "notifications"
            )
            self.notifications_button.setToolTip(
                f"{self._notifications_count} new {noun}"
            )
            self._paint_bell(0.0)
            if (
                self._bell_animation.state()
                != QAbstractAnimation.State.Running
            ):
                self._bell_animation.start()
        else:
            self.notifications_button.setToolTip("Notifications")
            self._bell_animation.stop()
            self._paint_bell(0.0)

    def _on_bell_frame(self, value) -> None:
        """One frame of the soft ring: a sine swing around the icon's centre."""
        angle = 12.0 * math.sin(float(value) * math.tau)
        self._paint_bell(angle)

    def _paint_bell(self, angle: float) -> None:
        bell = self._bell_pixmaps[self._notifications_unread]
        if bell.isNull():
            self.notifications_button.setIcon(QIcon())
            return

        # Rotate on a slightly larger transparent canvas so the tips of the
        # ring never clip against the button's icon bounds.
        canvas = QPixmap(30, 30)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.translate(canvas.width() / 2, canvas.height() / 2)
        painter.rotate(angle)
        painter.drawPixmap(-bell.width() // 2, -bell.height() // 2, bell)
        painter.end()
        self.notifications_button.setIcon(QIcon(canvas))

    def set_user(self, name: str | None) -> None:
        """Guest shows the call to action; a customer gets the account avatar."""
        signed_in = bool(name)
        self.notifications_button.setVisible(signed_in)
        if not signed_in:
            self.set_notification_state(False, 0)
        if signed_in:
            self.auth_button.setText("")
            self.auth_button.setIcon(QIcon(str(ICONS_DIR / "account.png")))
            self.auth_button.setIconSize(QSize(30, 30))
            self.auth_button.setFixedSize(40, 40)
            self.auth_button.setToolTip(f"Signed in as {name}")
            self.auth_button.setAccessibleName("Account")
        else:
            self.auth_button.setText(GUEST_LABEL)
            self.auth_button.setIcon(QIcon())
            self.auth_button.setIconSize(QSize(18, 18))
            self.auth_button.setMinimumWidth(0)
            self.auth_button.setMaximumWidth(16777215)
            self.auth_button.setFixedHeight(40)
            self.auth_button.setToolTip("")
            self.auth_button.setAccessibleName(GUEST_LABEL)
        # The property drives the QSS (dark pill for a guest, soft tan chip
        # once a customer is signed in).
        self.auth_button.setProperty("authenticated", signed_in)
        self.auth_button.style().unpolish(self.auth_button)
        self.auth_button.style().polish(self.auth_button)
        self.auth_button.updateGeometry()


# ---------- overlay (auth gate, success) ----------
class OverlayDialog(QDialog):
    """Frameless dialog that dims the window and centres one card.

    Qt gives a modal dialog for free; the window flags + translucent
    background turn it into the modal the design asks for. The card is the
    returned `frame`, `column` is where the caller puts its content.
    """

    def __init__(
        self,
        parent: QWidget,
        *,
        object_name: str = "overlayCard",
        card_width: int = 460,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        # Window modal, not application modal: the gate opens the sign-up
        # window on top of itself, and the customer must be able to type
        # there while the gate underneath waits for the account.
        self.setWindowModality(Qt.WindowModality.WindowModal)
        if parent is not None:
            self.setGeometry(parent.window().frameGeometry())

        backdrop = QWidget(self)
        backdrop.setObjectName("dimBackdrop")
        backdrop.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizeGripEnabled(False)

        frame = QFrame(backdrop)
        frame.setObjectName(object_name)
        frame.setFixedWidth(card_width)

        column = QVBoxLayout(frame)
        column.setContentsMargins(32, 30, 32, 28)
        column.setSpacing(12)

        outer = QVBoxLayout(backdrop)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)
        outer.addWidget(frame, 0, Qt.AlignHCenter)
        outer.addStretch(1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(backdrop)

        self.frame = frame
        self.column = column

    def close_button(self) -> QToolButton:
        """The X in the corner of the card, wired to close()."""
        button = QToolButton(self.frame)
        button.setObjectName("overlayClose")
        button.setCursor(Qt.PointingHandCursor)
        button.setIcon(icon_from("close", TOKENS["text_muted"], 16))
        button.setIconSize(QSize(16, 16))
        button.clicked.connect(self.reject)
        return button

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)


# ---------- calendar ----------
#: Weekday headers, Monday first (the way the rental week is read here).
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


class MonthCalendar(QWidget):
    """Month grid for choosing a pickup or return date.

    Days the vehicle is out *all day* arrive as a set and are drawn disabled:
    the clashing date cannot be picked in the first place. A day that is only
    partly booked stays selectable on purpose -- whether the hours clash
    depends on the times, and the screen checks that when they are chosen.
    """

    date_selected = Signal(object)  # datetime.date

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("calendar")

        self._year = date.today().year
        self._month = date.today().month
        self._blocked: set[date] = set()
        self._selected: date | None = None
        self._minimum: date | None = None

        self.month_label = make_label("", "calMonth")
        self.prev_button = self._nav_button("back", "Previous month")
        self.next_button = self._nav_button("forward", "Next month")
        self.prev_button.clicked.connect(lambda: self.shift_month(-1))
        self.next_button.clicked.connect(lambda: self.shift_month(1))

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.month_label, 1)
        header.addWidget(self.prev_button)
        header.addWidget(self.next_button)

        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 8, 0, 0)
        self._grid.setSpacing(2)
        for column, name in enumerate(WEEKDAYS):
            label = make_label(name, "calWeekday")
            label.setAlignment(Qt.AlignCenter)
            self._grid.addWidget(label, 0, column)

        self._days: dict[date, QPushButton] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addLayout(header)
        root.addLayout(self._grid)

        self._rebuild()

    def _nav_button(self, icon_name: str, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("calNav")
        button.setToolTip(tooltip)
        button.setCursor(Qt.PointingHandCursor)
        button.setIcon(icon_from(icon_name, TOKENS["text_dark"], 16))
        button.setIconSize(QSize(16, 16))
        return button

    # ----- state -----
    def set_month(self, year: int, month: int) -> None:
        self._year, self._month = year, month
        self._rebuild()

    def shift_month(self, delta: int) -> None:
        month = self._month + delta
        year = self._year + (month - 1) // 12
        self.set_month(year, (month - 1) % 12 + 1)

    def set_blocked(self, days: set[date]) -> None:
        days = set(days or ())
        if days == self._blocked:
            return
        self._blocked = days
        self._rebuild()

    def set_minimum(self, day: date | None) -> None:
        if day == self._minimum:
            return
        self._minimum = day
        self._rebuild()

    def set_selected(self, day: date | None) -> None:
        if day == self._selected:
            return
        self._selected = day
        if day is not None and (day.year, day.month) != (self._year, self._month):
            self.set_month(day.year, day.month)
            return
        self._rebuild()

    @property
    def selected(self) -> date | None:
        return self._selected

    # ----- drawing -----
    def _rebuild(self) -> None:
        self.month_label.setText(f"{date(self._year, self._month, 1):%B %Y}")
        for button in self._days.values():
            self._grid.removeWidget(button)
            button.deleteLater()
        self._days.clear()

        first = date(self._year, self._month, 1)
        # Monday=0 .. Sunday=6, so the month starts at the right column.
        lead = first.weekday()
        days_in_month = (
            date(self._year + (self._month == 12), (self._month % 12) + 1, 1)
            - first
        ).days

        for index in range(days_in_month):
            day = date(self._year, self._month, index + 1)
            row, column = divmod(lead + index, 7)
            button = QPushButton(str(day.day))
            button.setObjectName("calDay")
            button.setCheckable(True)
            button.setChecked(day == self._selected)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setFixedHeight(34)
            button.setEnabled(not self._is_blocked(day))
            button.setProperty("blocked", self._is_blocked(day))
            button.setProperty("today", day == date.today())
            button.clicked.connect(lambda _checked, d=day: self._pick(d))
            self._days[day] = button
            self._grid.addWidget(button, row + 1, column)

    def _is_blocked(self, day: date) -> bool:
        if day in self._blocked:
            return True
        return self._minimum is not None and day < self._minimum

    def _pick(self, day: date) -> None:
        if self._is_blocked(day):
            return
        self._selected = day
        self._rebuild()
        self.date_selected.emit(day)
