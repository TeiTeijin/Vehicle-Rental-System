"""Screen 4: pay and confirm (step 3 of 3).

The left column is the booking as it will be written (vehicle, window,
branches) plus how the customer wants to pay; the right column is the money,
computed once by app/services/pricing.py -- the same call screen 3 made, so the
two screens can never disagree.

Paying is a mock: app/services/card_validation.py checks the form (Luhn,
expiry, CVV) and answers `ok`/`not ok`. A declined card stops here, before
anything is written; a successful one (or cash, which is paid at the counter)
writes the booking through app/services/booking_service.py and shows the
reference code. The card number and CVV are dropped with the widgets -- only
`CardDetails` (brand + last four) ever reaches the database.
"""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression, QSize, Qt, Signal
from PySide6.QtGui import (
    QGuiApplication,
    QImage,
    QPixmap,
    QRegularExpressionValidator,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.database import SessionLocal
from app.models import Branch, Users, Vehicle
from app.services import (
    booking_service,
    card_validation,
    pricing,
    vehicle_photos,
    vehicle_service,
)
from app.services.errors import ServiceError
from app.ui import theme
from app.ui.session import BookingDraft
from app.ui.theme import (
    BOOKING_STEPS,
    TOKENS,
    divider,
    hint_label,
    icon_from,
    make_label,
    moment_text,
    overline,
    panel,
    per_day_text,
    primary_button,
    section_title,
    text_button,
    value_row,
)
from app.ui.vehicle_photo import VehiclePhoto

STEP_INDEX = 3

#: (key, title, note, logo file). The keys are the service's own method names.
#: The artwork lives beside the other customer icons; the names keep the
#: supplied spaces so the source files can be dropped in without renaming.
PAYMENT_METHODS: tuple[tuple[str, str, str, str], ...] = (
    ("card", "Credit / debit card", "Visa or Mastercard, charged now.", "visa mastercard 1.png"),
    ("gcash", "GCash", "Scan the QR, then enter your reference.", "gcash 1.png"),
    ("cash", "Cash at the counter", "Pay when you pick the vehicle up.", "cash 1.png"),
)


class ClickableQrLabel(QLabel):
    """The checkout QR is clickable so it can open at scanning size."""

    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
                event.accept()
                return
        super().mouseReleaseEvent(event)


def qr_preview_pixmap(source: QPixmap) -> QPixmap:
    """Crop the supplied GCash poster down to its QR block.

    The poster is tall: scaling the whole thing to fit a preview makes the QR
    itself no larger than the checkout thumbnail. This finds the dense black
    block in the middle of the artwork and crops around it, with a little white
    quiet zone. Unknown artwork falls back to the original pixmap.
    """
    if source.isNull():
        return source

    image = source.toImage().convertToFormat(QImage.Format.Format_Grayscale8)
    scan = image.scaled(
        400,
        778,
        Qt.KeepAspectRatio,
        Qt.FastTransformation,
    )
    width, height = scan.width(), scan.height()
    left, right = int(width * 0.10), int(width * 0.90)
    top, bottom = int(height * 0.15), int(height * 0.75)

    row_threshold = max(8, int(width * 0.04))
    rows = [
        y
        for y in range(top, bottom)
        if sum(
            1
            for x in range(left, right)
            if scan.pixelColor(x, y).value() < 80
        )
        > row_threshold
    ]
    if not rows:
        return source

    groups: list[tuple[int, int]] = []
    start = previous = None
    for y in rows:
        if start is None:
            start = previous = y
        elif y == previous + 1:
            previous = y
        else:
            groups.append((start, previous))
            start = previous = y
    if start is not None:
        groups.append((start, previous))
    y0, y1 = max(groups, key=lambda group: group[1] - group[0])
    if y1 - y0 < 20:
        return source

    column_threshold = max(8, int((y1 - y0 + 1) * 0.05))
    columns = [
        x
        for x in range(left, right)
        if sum(
            1
            for y in range(y0, y1 + 1)
            if scan.pixelColor(x, y).value() < 80
        )
        > column_threshold
    ]
    if not columns:
        return source
    x0, x1 = min(columns), max(columns)
    if not 0.8 <= (x1 - x0 + 1) / max(1, y1 - y0 + 1) <= 1.2:
        return source

    scale_x = image.width() / width
    scale_y = image.height() / height
    x0 = int(x0 * scale_x)
    x1 = int((x1 + 1) * scale_x)
    y0 = int(y0 * scale_y)
    y1 = int((y1 + 1) * scale_y)

    side = max(x1 - x0, y1 - y0)
    pad = int(side * 0.10)
    half = side // 2 + pad
    centre_x = (x0 + x1) // 2
    centre_y = (y0 + y1) // 2
    crop_left = max(0, centre_x - half)
    crop_top = max(0, centre_y - half)
    crop_right = min(image.width(), centre_x + half)
    crop_bottom = min(image.height(), centre_y + half)
    cropped = source.copy(
        crop_left,
        crop_top,
        crop_right - crop_left,
        crop_bottom - crop_top,
    )
    return cropped if not cropped.isNull() else source


class QrPreviewDialog(QDialog):
    """The GCash QR in a fixed-size, centered preview card.

    A checkout-column QR is only about 170px wide; phone cameras can struggle
    with that at arm's length. The preview is deliberately fixed after it
    opens: the source pixmap is scaled once to fit the screen, and resizing the
    window cannot make the QR grow or jump around.
    """

    def __init__(self, source: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.source = source
        self.setObjectName("qrFullscreen")
        self.setWindowTitle("GCash QR")
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setModal(True)

        screen = (
            parent.screen()
            if parent is not None
            else QGuiApplication.primaryScreen()
        )
        available = screen.availableGeometry() if screen is not None else None
        if available is not None:
            width = max(520, min(820, available.width() - 100))
            height = max(560, min(960, available.height() - 100))
        else:
            width, height = 820, 960
        self.setFixedSize(width, height)
        self._centered = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 26)
        outer.setSpacing(16)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(14)
        title = QVBoxLayout()
        title.setContentsMargins(0, 0, 0, 0)
        title.setSpacing(3)
        title.addWidget(make_label("Scan to pay with GCash", "qrFullscreenTitle"))
        title.addWidget(
            make_label(
                "Point your phone camera at the QR code. Press Esc when done.",
                "qrFullscreenSubtitle",
                wrap=True,
            )
        )
        head.addLayout(title, 1)

        close = QToolButton()
        close.setObjectName("qrFullscreenClose")
        close.setCursor(Qt.PointingHandCursor)
        close.setIcon(icon_from("close", "#FFFFFF", 18))
        close.setIconSize(QSize(18, 18))
        close.setFixedSize(38, 38)
        close.clicked.connect(self.close)
        head.addWidget(close, 0, Qt.AlignTop)
        outer.addLayout(head)

        self.card = QFrame()
        self.card.setObjectName("qrFullscreenCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(0)
        self.image = QLabel()
        self.image.setObjectName("qrFullscreenImage")
        self.image.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(self.image)
        outer.addWidget(self.card, 1, Qt.AlignCenter)

        footer = make_label(
            "Enter the reference number shown on your GCash receipt.",
            "qrFullscreenFooter",
        )
        footer.setAlignment(Qt.AlignCenter)
        outer.addWidget(footer)

        self._fit_qr()

    def _fit_qr(self) -> None:
        if self.source.isNull():
            self.image.setText("GCash QR unavailable")
            return
        size = max(240, min(self.width() - 100, self.height() - 210))
        scaled = self.source.scaled(
            size,
            size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.image.setPixmap(scaled)
        self.image.setFixedSize(scaled.size())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._centered:
            screen = self.screen() or QGuiApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                self.move(available.center() - self.rect().center())
            self._centered = True

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)


class MethodCard(QFrame):
    """One payment option. Clicking the card selects it."""

    chosen = Signal(str)

    def __init__(
        self,
        key: str,
        title: str,
        note: str,
        logo_file: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.key = key
        self.setObjectName("methodCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("selected", False)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(12)

        self.logo = QLabel()
        self.logo.setObjectName("methodLogo")
        pixmap = QPixmap(str(theme.ICONS_DIR / logo_file))
        if pixmap.isNull():
            self.logo.setPixmap(
                icon_from("card", TOKENS["text_dark"], 22).pixmap(22, 22)
            )
        else:
            self.logo.setPixmap(
                pixmap.scaled(
                    66,
                    36,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
        self.logo.setFixedSize(78, 44)
        self.logo.setAlignment(Qt.AlignCenter)
        self.logo.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(self.logo, 0, Qt.AlignVCenter)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        self.title_label = make_label(title, "methodTitle")
        self.note_label = make_label(note, "methodNote", wrap=True)
        for label in (self.title_label, self.note_label):
            # Clicks land on the card itself, so the whole tile is the target.
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text.addWidget(self.title_label)
        text.addWidget(self.note_label)
        row.addLayout(text, 1)

        self.check = QLabel()
        self.check.setPixmap(icon_from("check", "#FFFFFF", 18).pixmap(18, 18))
        self.check.setFixedSize(18, 18)
        self.check.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.check.hide()
        row.addWidget(self.check, 0, Qt.AlignTop)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.check.setVisible(selected)
        for widget in (self, self.logo, self.title_label, self.note_label):
            widget.setProperty("selected", selected)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self.chosen.emit(self.key)


class CheckoutSuccess(theme.OverlayDialog):
    """The receipt the customer keeps: the reference code, big."""

    def __init__(self, parent: QWidget, *, reference: str, receipt: str) -> None:
        super().__init__(parent, object_name="overlayCard", card_width=440)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(make_label("Booking confirmed", "overlayTitle"), 1)
        head.addWidget(self.close_button(), 0, Qt.AlignTop)
        self.column.addLayout(head)

        tick = QLabel()
        tick.setPixmap(icon_from("check", TOKENS["green"], 34).pixmap(34, 34))
        tick.setFixedSize(34, 34)
        self.column.addWidget(tick, 0, Qt.AlignLeft)

        self.column.addWidget(
            make_label(
                "Show this reference at the counter. It is also in "
                "Bookings, under your account.",
                "overlaySubtitle",
                wrap=True,
            )
        )
        self.column.addSpacing(6)

        code_panel, code_col = panel("panelTan", spacing=4, margins=(18, 14, 18, 14))
        code_col.addWidget(overline("Reference"))
        code = make_label(reference, "pageTitle")
        code_col.addWidget(code)
        self.column.addWidget(code_panel)

        self.column.addWidget(make_label(receipt, "overlaySubtitle", wrap=True))

        self.view_button = primary_button("VIEW MY BOOKINGS")
        self.view_button.clicked.connect(self.accept)
        self.column.addWidget(self.view_button)

        keep = text_button("Keep browsing")
        keep.clicked.connect(self.reject)
        self.column.addWidget(keep, 0, Qt.AlignHCenter)


class BookingCheckoutPage(QWidget):
    """Review, pay, confirm. Emits `booked` once the booking is saved."""

    back_requested = Signal()
    booked = Signal(object)

    def __init__(
        self, draft: BookingDraft, user_id: int | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.draft = draft
        self._user_id = user_id
        self._quote = None
        self._name = ""
        self._meta = ""
        self._rate = 0
        self._ref = None
        self._pickup_branch = "\u2014"
        self._return_branch = "\u2014"
        self._moments: dict[str, tuple[QLabel, QLabel]] = {}
        self._method = draft.payment_method or "card"
        self._fields: dict[str, QLineEdit] = {}
        self._qr_dialog: QrPreviewDialog | None = None

        # ----- head -----
        self.back_button = text_button("\u2190  Change dates")
        self.back_button.setIcon(icon_from("back", TOKENS["text_dark"], 14))
        self.back_button.clicked.connect(self.back_requested)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self.back_button, 0, Qt.AlignLeft | Qt.AlignVCenter)
        head.addSpacing(24)
        head.addWidget(theme.step_strip(BOOKING_STEPS, STEP_INDEX), 1)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(14)
        left.addLayout(head)
        left.addWidget(self._booking_panel())
        left.addWidget(self._payment_panel())
        left.addStretch(1)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(14)
        right.addWidget(self._price_panel())
        right.addStretch(1)

        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(28)
        columns.addLayout(left, 3)
        columns.addLayout(right, 2)

        body = QVBoxLayout()
        body.setContentsMargins(40, 24, 40, 32)
        body.setSpacing(16)
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

        self.reload()

    # ---------- construction ----------
    def _booking_panel(self) -> QWidget:
        frame, col = panel("panel", spacing=12)
        col.addWidget(section_title("Your booking"))

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        self.photo = VehiclePhoto(thumbnail=True)
        self.photo.setFixedSize(150, 96)
        row.addWidget(self.photo, 0, Qt.AlignTop)

        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(4)
        self.vehicle_name = make_label("", "gridName")
        self.vehicle_meta = make_label("", "gridMeta")
        self.vehicle_rate = make_label("", "gridPrice")
        info.addWidget(self.vehicle_name)
        info.addWidget(self.vehicle_meta)
        info.addWidget(self.vehicle_rate)
        info.addStretch(1)
        row.addLayout(info, 1)
        col.addLayout(row)

        col.addWidget(divider())
        moments = QHBoxLayout()
        moments.setContentsMargins(0, 0, 0, 0)
        moments.setSpacing(18)
        for caption, icon in (("Pickup", "map_pin"), ("Return", "route")):
            block = QVBoxLayout()
            block.setContentsMargins(0, 0, 0, 0)
            block.setSpacing(3)
            block.addWidget(overline(caption))
            value = make_label("\u2014", "valueAmount")
            value.setWordWrap(True)
            block.addWidget(value)
            branch = make_label("", "gridMeta")
            branch.setWordWrap(True)
            block.addWidget(branch)
            moments.addLayout(block, 1)
            self._moments[caption.lower()] = (value, branch)
        col.addLayout(moments)
        return frame

    def _payment_panel(self) -> QWidget:
        frame, col = panel("panel", spacing=12)
        col.addWidget(section_title("How would you like to pay?"))

        self.method_cards: dict[str, MethodCard] = {}
        for key, title, note, logo_file in PAYMENT_METHODS:
            card = MethodCard(key, title, note, logo_file)
            card.chosen.connect(self._choose_method)
            self.method_cards[key] = card
            col.addWidget(card)

        # The three forms share one slot: only the chosen method's fields show.
        self.method_forms = QStackedLayout()
        self.method_forms.setContentsMargins(0, 0, 0, 0)
        self.method_forms.addWidget(self._card_form())
        self.method_forms.addWidget(self._gcash_form())
        self.method_forms.addWidget(self._cash_form())
        col.addLayout(self.method_forms)

        self.error = hint_label("", tone="danger")
        self.error.hide()
        col.addWidget(self.error)

        self.terms = QCheckBox("I agree to the rental terms and cancellation policy")
        col.addWidget(self.terms)
        return frame

    def _card_form(self) -> QWidget:
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 4, 0, 0)
        col.setSpacing(10)

        grid = QHBoxLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        grid.addWidget(self._field("cardholder", "Name on card", "Juan Dela Cruz"), 1)
        grid.addWidget(self._field("card_number", "Card number", "4242 4242 4242 4242"), 1)
        col.addLayout(grid)

        small = QHBoxLayout()
        small.setContentsMargins(0, 0, 0, 0)
        small.setSpacing(12)
        small.addWidget(self._field("expiry", "Expiry (MM / YY)", "08 / 29"), 1)
        small.addWidget(self._field("cvv", "CVV", "123"), 1)
        small.addStretch(1)
        col.addLayout(small)

        col.addWidget(
            hint_label(
                "Test gateway: nothing is charged, and a card ending 0002 "
                "is always declined so the failure path can be tried.",
            )
        )
        self._fields["card_number"].setMaxLength(23)
        self._fields["expiry"].setMaxLength(7)
        self._fields["cvv"].setMaxLength(4)
        self._fields["card_number"].textEdited.connect(self._format_number)
        self._fields["expiry"].textEdited.connect(self._format_expiry)
        self._fields["cvv"].textEdited.connect(self._format_cvv)
        return holder

    def _gcash_form(self) -> QWidget:
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 4, 0, 0)
        col.setSpacing(10)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)

        self.gcash_qr = ClickableQrLabel()
        self.gcash_qr.setObjectName("gcashQr")
        self._gcash_qr_source = QPixmap(
            str(theme.ICONS_DIR / "Gcash_QR.jpg")
        )
        self._gcash_qr_preview = qr_preview_pixmap(self._gcash_qr_source)
        if not self._gcash_qr_source.isNull():
            self.gcash_qr.setPixmap(
                self._gcash_qr_source.scaled(
                    172,
                    344,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
            self.gcash_qr.setToolTip("Click to view a larger QR")
            self.gcash_qr.setCursor(Qt.PointingHandCursor)
            self.gcash_qr.clicked.connect(self._show_gcash_qr_preview)
        else:
            self.gcash_qr.setText("GCash QR unavailable")
        self.gcash_qr.setAlignment(Qt.AlignCenter)
        self.gcash_qr.setFixedWidth(184)

        qr_column = QVBoxLayout()
        qr_column.setContentsMargins(0, 0, 0, 0)
        qr_column.setSpacing(6)
        qr_column.addWidget(self.gcash_qr, 0, Qt.AlignTop)
        self.qr_expand_link = text_button("View full size")
        self.qr_expand_link.setObjectName("textButton")
        self.qr_expand_link.clicked.connect(self._show_gcash_qr_preview)
        if self._gcash_qr_source.isNull():
            self.qr_expand_link.hide()
        qr_column.addWidget(self.qr_expand_link, 0, Qt.AlignHCenter)
        row.addLayout(qr_column, 0)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(10)
        right.addWidget(
            make_label(
                "Scan this QR in GCash, send the exact amount, then enter the "
                "reference number shown on your receipt. Tap the QR or View "
                "full size to enlarge it.",
                "methodNote",
                wrap=True,
            )
        )
        right.addWidget(
            self._field(
                "gcash_reference",
                "GCash reference number",
                "Up to 15 digits",
            )
        )
        right.addWidget(
            hint_label(
                "The reference is stored on the booking so the branch can "
                "match your payment. Enter up to 15 digits.",
            )
        )
        right.addStretch(1)
        row.addLayout(right, 1)
        col.addLayout(row)
        self._fields["gcash_reference"].setMaxLength(
            card_validation.GCASH_REFERENCE_MAX_DIGITS
        )
        self._fields["gcash_reference"].setValidator(
            QRegularExpressionValidator(
                QRegularExpression(
                    f"[0-9]{{0,{card_validation.GCASH_REFERENCE_MAX_DIGITS}}}"
                ),
                self._fields["gcash_reference"],
            )
        )
        return holder

    def _show_gcash_qr_preview(self) -> None:
        source = getattr(self, "_gcash_qr_preview", QPixmap())
        if source.isNull():
            source = getattr(self, "_gcash_qr_source", QPixmap())
        if source.isNull():
            return
        dialog = QrPreviewDialog(source, self.window())
        self._qr_dialog = dialog
        dialog.destroyed.connect(self._forget_qr_dialog)
        dialog.show()

    def _forget_qr_dialog(self, *_args) -> None:
        self._qr_dialog = None

    def _cash_form(self) -> QWidget:
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 4, 0, 0)
        col.setSpacing(10)
        self.cash_note = hint_label("", tone="muted")
        col.addWidget(self.cash_note)
        col.addWidget(
            hint_label(
                "The booking is held (pending) and the branch collects the "
                "total when the keys are handed over.",
            )
        )
        return holder

    def _field(self, key: str, caption: str, placeholder: str) -> QWidget:
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        col.addWidget(make_label(caption, "comboCaption"))
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        col.addWidget(edit)
        self._fields[key] = edit
        return holder

    def _price_panel(self) -> QWidget:
        frame, col = panel("panel", spacing=10, margins=(24, 22, 24, 22))
        col.addWidget(section_title("Price breakdown"))

        self._rows = {
            "rental": value_row("Rental", "\u2014", icon_name="car"),
            "insurance": value_row("Insurance", "\u2014", icon_name="shield"),
            "service": value_row("Service fee", "\u2014", icon_name="receipt"),
            "vat": value_row("VAT (12%)", "\u2014", icon_name="percent"),
        }
        for row in self._rows.values():
            col.addWidget(row)
        self._rental_caption = self._rows["rental"].findChild(QLabel, "valueCaption")
        col.addWidget(divider())
        self._rows["total"] = value_row("Total", "\u2014", strong=True)
        col.addWidget(self._rows["total"])

        self.pay_button = primary_button("CONFIRM && PAY")  # && : & is a mnemonic marker
        self.pay_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.pay_button.clicked.connect(self._confirm)
        col.addWidget(self.pay_button)
        self.pay_hint = hint_label("", tone="muted")
        col.addWidget(self.pay_hint)
        return frame

    # ---------- data ----------
    def reload(self) -> None:
        """Read the vehicle and the branches, then price the draft."""
        try:
            with SessionLocal() as session:
                vehicle = session.get(Vehicle, self.draft.vehicle_id)
                if vehicle is None:
                    self._fail("That vehicle is no longer in the fleet.")
                    return
                self._name = vehicle_service.display_name(vehicle)
                self._meta = vehicle_service.summary_line(vehicle)
                self._rate = vehicle.daily_rate
                self._ref = vehicle_photos.ref_for(vehicle)

                pickup_branch = session.get(Branch, self.draft.pickup_branch_id)
                return_branch = (
                    session.get(Branch, self.draft.return_branch_id)
                    if self.draft.return_branch_id
                    else None
                )
                drop_branch = return_branch or pickup_branch
                self._pickup_branch = pickup_branch.name if pickup_branch else "\u2014"
                self._return_branch = drop_branch.name if drop_branch else "\u2014"
        except ServiceError as exc:
            self._fail(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - the database is remote
            self._fail(f"Could not load this booking: {exc}")
            return

        self.vehicle_name.setText(self._name)
        self.vehicle_meta.setText(self._meta)
        self.vehicle_rate.setText(per_day_text(self._rate))
        if self._ref is not None:
            self.photo.set_vehicle(self._ref)

        pickup_dt, return_dt = self.draft.pickup_dt, self.draft.return_dt
        if pickup_dt is not None and return_dt is not None:
            pickup_value, pickup_branch = self._moments["pickup"]
            return_value, return_branch = self._moments["return"]
            pickup_value.setText(moment_text(pickup_dt))
            pickup_branch.setText(self._pickup_branch)
            return_value.setText(moment_text(return_dt))
            return_branch.setText(self._return_branch)

        self._choose_method(self._method)
        self._price()

    def _price(self) -> None:
        pickup_dt, return_dt = self.draft.pickup_dt, self.draft.return_dt
        if pickup_dt is None or return_dt is None:
            self._fail("Pickup and return dates are missing: go back a step.")
            return
        try:
            self._quote = pricing.quote(self._rate, pickup_dt, return_dt)
        except ServiceError as exc:
            self._fail(str(exc))
            return

        quote = self._quote
        self._rental_caption.setText(
            f"Rental ({quote.days} day{'' if quote.days == 1 else 's'} "
            f"at {theme.pesos_text(self._rate)})"
        )
        for key, text in (
            ("rental", quote.subtotal_text),
            ("insurance", quote.insurance_text),
            ("service", quote.service_fee_text),
            ("vat", quote.vat_text),
            ("total", quote.total_text),
        ):
            self._set_row(key, text)

        self.pay_button.setText(f"CONFIRM && PAY {quote.total_text}")
        self.cash_note.setText(
            f"{quote.total_text} is due at the counter when you pick up."
        )
        self.pay_hint.setText(
            "A reference code is issued as soon as the booking is saved."
        )

    def _set_row(self, key: str, text: str) -> None:
        amount = self._rows[key].findChild(QLabel, "valueAmount")
        if amount is not None:
            amount.setText(text)

    # ---------- interaction ----------
    def _format_number(self, text: str) -> None:
        self._fields["card_number"].setText(card_validation.format_number(text))

    def _format_expiry(self, text: str) -> None:
        self._fields["expiry"].setText(card_validation.format_expiry(text))

    def _format_cvv(self, text: str) -> None:
        self._fields["cvv"].setText(card_validation.format_cvv(text))

    def _choose_method(self, key: str) -> None:
        self._method = key
        for name, card in self.method_cards.items():
            card.set_selected(name == key)
        index = [name for name, _t, _n, _logo in PAYMENT_METHODS].index(key)
        self.method_forms.setCurrentIndex(index)

    def _fail(self, message: str, field: str | None = None) -> None:
        self.error.setText(message)
        self.error.show()
        if field and field in self._fields:
            self._fields[field].setFocus()

    def _clear_error(self) -> None:
        self.error.hide()

    # ---------- paying ----------
    def _confirm(self) -> None:
        self._clear_error()
        if not self.terms.isChecked():
            self._fail("Please agree to the rental terms before paying.")
            return
        if self.draft.pickup_dt is None or self.draft.return_dt is None:
            self._fail("Pickup and return dates are missing: go back a step.")
            return
        if self._user_id is None:
            self._fail("Please sign in again before paying.")
            return

        details = None
        gcash_reference = ""
        if self._method == "card":
            try:
                details = card_validation.validate_card(
                    self._fields["cardholder"].text(),
                    self._fields["card_number"].text(),
                    self._fields["expiry"].text(),
                    self._fields["cvv"].text(),
                )
            except ServiceError as exc:
                self._fail(str(exc), getattr(exc, "field", None))
                return
        if self._method == "gcash":
            try:
                gcash_reference = card_validation.validate_gcash_reference(
                    self._fields["gcash_reference"].text()
                )
            except ServiceError as exc:
                self._fail(str(exc), getattr(exc, "field", None))
                return

        self.pay_button.setEnabled(False)
        self.pay_button.setText("PROCESSING...")
        try:
            reference, receipt = self._write_booking(details, gcash_reference)
        except ServiceError as exc:
            self._fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - the database is remote
            self._fail(f"Could not save the booking: {exc}")
        else:
            self.draft.payment_method = self._method
            self.draft.card_last4 = details.last4 if details else None
            self.draft.reference_code = reference
            dialog = CheckoutSuccess(
                self.window(), reference=reference, receipt=receipt
            )
            dialog.exec()
            self.booked.emit(self.draft)
        finally:
            self.pay_button.setEnabled(True)
            self._price()

    def _write_booking(
        self, details, gcash_reference: str = ""
    ) -> tuple[str, str]:
        """Charge (mock), then save. Returns (reference, receipt line)."""
        method = self._method
        with SessionLocal() as session:
            vehicle = session.get(Vehicle, self.draft.vehicle_id)
            user = session.get(Users, self._user_id)
            pickup_branch = session.get(Branch, self.draft.pickup_branch_id)
            return_branch = (
                session.get(Branch, self.draft.return_branch_id)
                if self.draft.return_branch_id
                else None
            )
            if vehicle is None or user is None or pickup_branch is None:
                raise ServiceError("This booking is missing its vehicle or branch.")

            quote = pricing.quote(
                vehicle.daily_rate, self.draft.pickup_dt, self.draft.return_dt
            )
            result = card_validation.collect(
                method,
                quote.total,
                details,
                reference=gcash_reference,
            )
            if not result.ok:
                # Declined: nothing is written, the customer keeps their dates.
                raise ServiceError(result.message)

            booking = booking_service.create_online_booking(
                session,
                user=user,
                vehicle=vehicle,
                pickup_dt=self.draft.pickup_dt,
                return_dt=self.draft.return_dt,
                pickup_branch=pickup_branch,
                return_branch=return_branch,
                payment_method=method,
                card_last4=details.last4 if details else None,
                payment_reference=result.reference or None,
            )
            session.commit()
            return booking.reference_code or "", result.message


__all__ = ["BOOKING_STEPS", "BookingCheckoutPage", "MethodCard", "PAYMENT_METHODS"]
