"""The staff sign-in screen.

Sign-in is the role gate. ``auth_service.sign_in`` accepts any user in the
table, so this view is where a `customer` row is turned away -- the check is
here as well as in ``StaffContext.require_staff`` because refusing at the door
gives a clearer message than refusing on the first page.

Three details worth keeping:

  * The error is shown in one fixed place above the button, not as a popup, so
    the password field keeps focus and the user can just try again.
  * The email is lowercased on the way in, matching ``sign_in``, so an address
    typed with capitals still works instead of failing with "incorrect
    password" for a reason that is not about the password.
  * The window is a fixed size while this is showing. The app behind it opens
    at 1180x760 for tables of bookings, and a sign-in form stretched to that
    width is a form stretched to that width.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    Qt,
)
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models import Users
from app.services.auth_service import AuthError, sign_in
from app.staff.context import StaffContext, StaffUser
from app.staff.theme import (
    OBJ_LABEL_LIFTED,
    OBJ_LABEL_REST,
    OBJ_LOGIN_BUTTON,
    OBJ_LOGIN_CAPTION,
    OBJ_LOGIN_ERROR,
    OBJ_LOGIN_FIELD,
    OBJ_LOGIN_FOOT,
    OBJ_LOGIN_ROOT,
    OBJ_LOGIN_FORM,
    OBJ_LOGIN_WORDMARK,
)
from app.utils.fonts import load_fonts

#: The sign-in window is exactly this size and no other. Chosen so the form
#: sits comfortably at 16px type without the fields stretching, and so the
#: error line has room to appear without the window jumping.
LOGIN_SIZE = 432, 512

#: Reserved clear space at the top of a field, matched to the transparent
#: `border-top` in the stylesheet. The lifted label flies up *into* this band
#: rather than above the widget: a QLineEdit is a QAbstractScrollArea, so a
#: child positioned at a negative y is clipped by the viewport and never
#: painted at all.
FIELD_TOP = 20
#: The rule under the field.
FIELD_BOTTOM = 1
#: Label type sizes, and where it sits in each state.
LABEL_REST_PX = 16
LABEL_LIFTED_PX = 12
#: The lifted label sits just inside the reserved band.
LABEL_LIFT_Y = 3


#: Drawn over a masked field. Qt's own password dot is a fixed-size
#: decoration -- it stays 11x11px whatever the field's font size, so it
#: cannot be tuned -- and PySide6 does not bind `QLineEdit::setEchoChar`, so
#: the character cannot be swapped either.
MASK_CHAR = "\u2022"


class FloatingField(QLineEdit):
    """A line edit whose label starts inside as a placeholder and lifts out.

    The label is a child of the edit rather than a sibling in a layout, because
    it has to cross the field's top edge. Sizing comes from `sizeHint` after the
    font is set, so the two states are measured rather than guessed.
    """

    def __init__(
        self, caption: str, parent: QWidget | None = None, *, masked: bool = False
    ) -> None:
        super().__init__(parent)
        self.setObjectName(OBJ_LOGIN_FIELD)
        # The floating label does the placeholder's job. Leaving Qt's own
        # placeholder set as well would leave the word sitting underneath the
        # lifted label.
        self.setPlaceholderText("")

        self._caption = caption
        self._masked = masked
        self._label = QLabel(caption, self)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._lifted = False

        self._fly = QPropertyAnimation(self._label, b"pos", self)
        self._fly.setDuration(150)
        self._fly.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.textChanged.connect(lambda _text: self._restack())
        self._restack(animate=False)

    def _restack(self, *, animate: bool = True) -> None:
        """Put the label where it belongs for the current focus/text state."""
        # Derived, not remembered. Caching this in the focus handlers left it
        # stale whenever the text changed without a focus change -- clearing
        # the field, which is exactly what sign-out does to the password.
        # A field holding text keeps its label up: the words are the answer
        # now, so the caption is a label again rather than a prompt.
        self._lifted = self.hasFocus() or bool(self.text())
        font = QFont(self.font())
        font.setPixelSize(LABEL_LIFTED_PX if self._lifted else LABEL_REST_PX)
        self._label.setFont(font)
        self._label.setObjectName(
            OBJ_LABEL_LIFTED if self._lifted else OBJ_LABEL_REST
        )
        # A stylesheet change only lands on a re-apply.
        self._label.style().unpolish(self._label)
        self._label.style().polish(self._label)

        # The label is a bare child of the line edit, moved by hand and not in
        # a layout, so nothing else will ever size it: it keeps whatever
        # geometry it had when it was built. That geometry was measured with
        # the fallback font, before Inter was registered, so it came out
        # narrower and shorter than the caption needs and the line edit
        # clipped the letters off the end. This is the only place that knows
        # which font is in play, so it is the only place that can size it.
        self._label.adjustSize()

        label_h = self._label.height()
        if self._lifted:
            y = LABEL_LIFT_Y
        else:
            # Centred in the text area, which is what is left between the
            # reserved top band and the rule.
            text_h = max(0, self.height() - FIELD_TOP - FIELD_BOTTOM)
            y = FIELD_TOP + max(0, (text_h - label_h) // 2)
        target = QPoint(1, y)

        self._fly.stop()
        if animate and self._label.pos() != target:
            self._fly.setStartValue(self._label.pos())
            self._fly.setEndValue(target)
            self._fly.start()
        else:
            self._label.move(target)

    def paintEvent(self, event) -> None:
        """Draw a masked field's own dots instead of Qt's oversized ones.

        The text is swapped for a same-length run of `MASK_CHAR` only for the
        duration of the paint, and the real value is put back afterwards, so
        the widget keeps the genuine text the whole time it is not painting.
        Editing, selection, undo, paste and the caret all stay Qt's problem.

        Painting the same number of characters in the same font also means Qt
        positions the caret and the selection for us, which it could not do if
        we drew the dots on top of a paint of the real text.
        """
        if not self._masked:
            super().paintEvent(event)
            return

        secret = QLineEdit.text(self)
        shown = MASK_CHAR * len(secret)
        if shown == secret:
            super().paintEvent(event)
            return

        cursor = self.cursorPosition()
        blocked = self.blockSignals(True)
        try:
            QLineEdit.setText(self, shown)
            self.setCursorPosition(min(cursor, len(shown)))
            super().paintEvent(event)
        finally:
            QLineEdit.setText(self, secret)
            self.setCursorPosition(min(cursor, len(secret)))
            self.blockSignals(blocked)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._restack()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._restack()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Every resize, not just the resting state: a lifted label centred for
        # a shorter field would sit in the wrong place, and skipping this while
        # lifted is what left the label stranded mid-flight.
        self._restack(animate=False)


class LoginView(QWidget):
    """Wordmark, caption, two fields, a button, then a callback with the user."""

    def __init__(
        self,
        context: StaffContext,
        on_signed_in,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(OBJ_LOGIN_ROOT)
        # A bare QWidget ignores `background` in a stylesheet unless this is
        # set, because it has no paintEvent of its own to draw one. Without it
        # the rule is accepted and silently discarded, and the window behind
        # shows through -- which is what left the sign-in showing the page's
        # cream whatever colour was asked for here.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.context = context
        self._on_signed_in = on_signed_in

        # The stylesheet names the bundled family by name, so the faces have to
        # be registered before anything here is shown. Idempotent, and a failed
        # load degrades the typography rather than stopping a sign-in.
        load_fonts()

        wordmark = QLabel("Rent Desk", self)
        wordmark.setObjectName(OBJ_LOGIN_WORDMARK)
        wordmark.setAlignment(Qt.AlignmentFlag.AlignCenter)

        caption = QLabel("Staff Sign in", self)
        caption.setObjectName(OBJ_LOGIN_CAPTION)
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Both labels also get centred in the layout below. `setAlignment`
        # alone does nothing here: a QLabel with word wrap off reports a
        # maximum width equal to its text, so the layout hands it exactly the
        # width of the words and there is nothing left for the alignment to
        # distribute. Centring the widget in its row does the actual work.
        # The two together also behave if the form is ever made wider.

        self.email = FloatingField("Email", self)
        self.password = FloatingField("Password", self, masked=True)
        self.password.returnPressed.connect(self.attempt_sign_in)
        self.email.returnPressed.connect(self._focus_password)

        self.error = QLabel("", self)
        self.error.setObjectName(OBJ_LOGIN_ERROR)
        self.error.setWordWrap(True)
        self.error.setVisible(False)

        self.submit = QPushButton("Sign in", self)
        self.submit.setObjectName(OBJ_LOGIN_BUTTON)
        self.submit.setDefault(True)
        self.submit.setMinimumHeight(44)
        self.submit.clicked.connect(self.attempt_sign_in)

        self.hint = QLabel(self._foot_text(), self)
        self.hint.setObjectName(OBJ_LOGIN_FOOT)
        self.hint.setWordWrap(True)

        form = QWidget(self)
        form.setObjectName(OBJ_LOGIN_FORM)
        form.setFixedWidth(300)
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(0)
        form_layout.addWidget(wordmark, 0, Qt.AlignmentFlag.AlignHCenter)
        form_layout.addSpacing(6)
        form_layout.addWidget(caption, 0, Qt.AlignmentFlag.AlignHCenter)
        form_layout.addSpacing(30)
        form_layout.addWidget(self.email)
        form_layout.addSpacing(16)
        form_layout.addWidget(self.password)
        form_layout.addSpacing(10)
        form_layout.addWidget(self.error)
        form_layout.addSpacing(14)
        form_layout.addWidget(self.submit)
        form_layout.addSpacing(16)
        form_layout.addWidget(self.hint)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(form)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        self._focus_password()

    def _foot_text(self) -> str:
        if self.context.selection.is_demo:
            # State the credentials rather than pointing at the seeder. On the
            # demo database there is nothing to protect, and "the password
            # printed by the seeder" sends the first-time user to a terminal.
            return "Demo data, not the live branch.\ndemo-password"
        return "Accounts come from your branch administrator."

    # -- behaviour -----------------------------------------------------------

    def _focus_password(self) -> None:
        self.password.setFocus()
        self.password.selectAll()

    def attempt_sign_in(self) -> Users | None:
        """Authenticate, then hand the user to the shell.

        Returns the user on success and ``None`` on any failure, having already
        shown the reason. A button handler returning ``None`` is easy to
        misread as a no-op, so callers that care should check
        :attr:`context.user`.
        """
        self.error.setVisible(False)
        email = self.email.text().strip().lower()
        password = self.password.text()

        if not email or not password:
            # Checked here so an empty field does not spend a bcrypt round.
            self._show_error("Enter your email address and password.")
            return None

        # The snapshot is taken *inside* the session. `sign_in` returns a
        # `Users` row, and the `rollback()` that ends a read session expires
        # every attribute on it, so the fields have to be copied out before the
        # context manager closes.
        try:
            with self.context.reading() as session:
                row = sign_in(session, email, password)
                identity = StaffUser.from_row(row)
        except AuthError as exc:
            self._show_error(str(exc))
            self.password.clear()
            self.password.setFocus()
            return None
        except Exception as exc:  # noqa: BLE001
            from app.staff.feedback import describe

            message, _field, _kind = describe(exc)
            self._show_error(message)
            return None

        user = identity
        if user.role not in ("staff", "admin"):
            # A valid account, wrong door. Deliberately not "no such user":
            # this branch is only reachable after a correct password, so
            # hiding it would tell a legitimate customer nothing useful while
            # helping nobody.
            self._show_error(
                f"{user.email} is a customer account. Staff sign-in only."
            )
            self.password.clear()
            return None

        self.context.sign_in(user)
        self._on_signed_in(user)
        return user

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(True)
