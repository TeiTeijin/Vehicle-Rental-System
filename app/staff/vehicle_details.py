"""Check-details view for the staff New Rental page.

Built on the dashboard `Card` so a vehicle popup reads as part of the same
product: white, 28px radius, 1px outline, painted layered shadow, 26px
padding, with the dashboard's own `SectionLabel`, status boxes, `MoneyNumber`
and `Caption` blocks.

Layout, top to bottom (no scrolling; the card sizes to content): hero photo
(white box) + big bold name/rate/status box, spec sheet, availability list +
date checker, maintenance, then Rent this vehicle + Close.

The content lives in `VehicleDetailsPanel` (a plain widget) so it can be
shown two ways without duplicating anything:

- `VehicleDetailsDialog`: thin modal-window wrapper (kept for compat).
- `VehicleDetailsOverlay`: the real presentation — dim + card embedded
  inside the staff window itself. No new OS window is ever opened.
"""

from __future__ import annotations

from PySide6.QtCore import QDate, QEvent, QRectF, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QFormLayout,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.models import Booking, Maintenance_Record, Vehicle
from app.services import booking_service, image_cache
from app.services.errors import ConflictError
from app.staff import icons, theme
from app.staff.cards import (
    Caption,
    Card,
    MoneyNumber,
    Pill,
    SectionLabel,
)


#: Photo banner height inside the details view: tall enough that real photos
#: are never cropped at the top.
PHOTO_H = 300

#: Width the hero photo is cropped for (overlay card inner width, roughly).
PHOTO_W = 600

#: Placeholder text for columns the row leaves NULL.
DASH = "\u2014"

#: Booking states that keep a vehicle from being free.
ACTIVE_BOOKING_STATES = ("pending", "confirmed", "ongoing")

#: Overlay card width; the dim fills the whole window around it.
OVERLAY_CARD_W = 640

#: Frosted-glass backdrop: blur radius applied to the frozen page snapshot.
#: Medium strength; single constant so the look is tunable in one place.
BACKDROP_BLUR_RADIUS = 12

#: Dim baked over the blurred snapshot (same ink as DimOverlay).
BACKDROP_DIM = 110

#: Snapshot downscale before blurring: faster and smoother (classic frosted
#: trick). The small image is blurred cheaply, then upscaled.
BACKDROP_DOWNSCALE = 4

#: Debounce for re-rendering the backdrop on window resizes while open.
BACKDROP_RERENDER_MS = 120


def _hero_pixmap(make: str, model: str, year: int, width: int) -> QPixmap:
    """Best cached photo, centre-cropped to the banner; silhouette fallback."""
    try:
        cached = image_cache.load(make, model, year)
        payload = getattr(cached, "data", None)
    except Exception:  # noqa: BLE001
        payload = None
    if payload:
        image = QImage()
        if image.loadFromData(payload):
            scaled = image.scaled(
                width,
                PHOTO_H,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = max(0, (scaled.width() - width) // 2)
            y = max(0, (scaled.height() - PHOTO_H) // 2)
            cropped = scaled.copy(x, y, width, PHOTO_H)
            return QPixmap.fromImage(cropped)
    try:
        return icons.pixmap("vehicle", theme.MUTED, 64)
    except Exception:  # noqa: BLE001
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        return pixmap


def _status_box(status: str, parent: QWidget) -> Pill:
    """Status badge as a box (radius 8), never a pill."""
    return Pill(
        theme.status_label(status),
        parent=parent,
        colour=theme.TEXT,
        background=theme.SURFACE,
        dot_colour=theme.STATUS_COLOURS.get(status, theme.MUTED),
        radius=8,
    )


class VehicleDetailsPanel(QWidget):
    """The check-details content: no window of its own.

    Emits `rent_requested` with the vehicle id when the clerk picks Rent this
    vehicle, and `close_requested` on Close. Hosts (dialog or overlay) decide
    what those mean.
    """

    rent_requested = Signal(int)
    close_requested = Signal()

    def __init__(self, context, vehicle_id: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Transparent by QSS: the blanket QWidget cream would otherwise paint
        # a sharp square behind the rounded Card (radius 8 cutouts).
        self.setObjectName("vehicleDetailsPanel")
        self._context = context
        self._vehicle_id = vehicle_id
        #: Last clean availability check, as (start, end) dates; None until checked.
        self.checked_window: tuple[QDate, QDate] | None = None

        self.spec_values: dict[str, str] = {}
        self.missing_label = QLabel("", self)
        self.missing_label.setObjectName(theme.OBJ_ERROR)
        self.missing_label.setWordWrap(True)
        self.missing_label.setVisible(False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        self.card = Card(self, radius=8)
        self.card.setObjectName("vehicleDetailsCard")
        outer.addWidget(self.card)
        body = self.card.body()
        self.card.layout().addLayout(body)
        self._body = body

        # Rent/Close live INSIDE the white card (added at the end of
        # _build), so no cream dialog background is left showing.
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        buttons.addStretch(1)
        # Literal QSS names, matching InfoDialog: the theme BTN constants
        # ("btnPrimary"/"btnSecondary") do not match any stylesheet rule.
        self.close_button = QPushButton("Close", self)
        self.close_button.setObjectName("secondaryButton")
        self.close_button.clicked.connect(self.close_requested.emit)
        self.rent_button = QPushButton("Rent this vehicle", self)
        self.rent_button.setObjectName("primaryButton")
        self.rent_button.clicked.connect(self._on_rent)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.rent_button)
        self._buttons = buttons
        self._build()

    # -- data ---------------------------------------------------------------

    def _read_vehicle(self) -> dict | None:
        with self._context.reading() as session:
            vehicle = session.get(Vehicle, self._vehicle_id)
            if vehicle is None:
                return None
            category = vehicle.category.category_name if vehicle.category else DASH
            bookings = (
                session.query(Booking)
                .filter(
                    Booking.vehicle_id == vehicle.vehicle_id,
                    Booking.status.in_(ACTIVE_BOOKING_STATES),
                )
                .order_by(Booking.start_date)
                .limit(3)
                .all()
            )
            booking_rows = [
                {
                    "ref": row.booking_id,
                    "customer": row.user.full_name if row.user else DASH,
                    "start": row.start_date,
                    "end": row.end_date,
                    "status": row.status,
                }
                for row in bookings
            ]
            maintenance = (
                session.query(Maintenance_Record)
                .filter(Maintenance_Record.vehicle_id == vehicle.vehicle_id)
                .order_by(Maintenance_Record.start_date.desc())
                .limit(3)
                .all()
            )
            maintenance_rows = [
                {
                    "start": row.start_date,
                    "end": row.end_date,
                    "description": row.description or DASH,
                    "status": row.status,
                }
                for row in maintenance
            ]
            return {
                "name": f"{vehicle.make} {vehicle.model}".strip(),
                "subtitle": (
                    f"{vehicle.year} \u00b7 {vehicle.plate_number} \u00b7 {category}"
                ),
                "rate": vehicle.daily_rate,
                "status": vehicle.status,
                "make": vehicle.make or "",
                "model": vehicle.model or "",
                "year": int(vehicle.year or 0),
                "specs": [
                    ("Plate", vehicle.plate_number or DASH),
                    ("Seats", str(vehicle.seats) if vehicle.seats is not None else DASH),
                    ("Transmission", vehicle.transmission or DASH),
                    ("Fuel", vehicle.fuel_type or DASH),
                    ("Body style", vehicle.body_style or DASH),
                    ("Class", vehicle.vehicle_class or DASH),
                    (
                        "Engine",
                        f"{vehicle.engine_cc} cc"
                        if vehicle.engine_cc is not None
                        else DASH,
                    ),
                    (
                        "Mileage",
                        f"{vehicle.mileage:,} km" if vehicle.mileage is not None else DASH,
                    ),
                    ("Category", category),
                ],
                "bookings": booking_rows,
                "maintenance": maintenance_rows,
            }

    # -- layout ---------------------------------------------------------------

    def _build(self) -> None:
        data = self._read_vehicle()
        if data is None:
            self.missing_label.setText("That vehicle no longer exists.")
            self.missing_label.setVisible(True)
            self._body.addWidget(self.missing_label)
            self.rent_button.setEnabled(False)
            self._body.addLayout(self._buttons)
            return

        self.photo = QLabel(self.card)
        self.photo.setFixedHeight(PHOTO_H)
        # Ignored horizontally: the crop is wider than some hosts, and the
        # label must take the layout width instead of overflowing it.
        self.photo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Scoped: an unqualified rule would cascade white onto the pixmap's
        # transparent corners and flatten the silhouette fallback.
        self.photo.setStyleSheet(
            "QLabel#vehicleDetailsPhoto { background: #FFFFFF; }"
        )
        self.photo.setPixmap(
            _hero_pixmap(data["make"], data["model"], data["year"], PHOTO_W)
        )
        self.photo.setObjectName("vehicleDetailsPhoto")
        self._body.addWidget(self.photo)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.name_label = SectionLabel(data["name"], parent=self.card)
        self.name_label.setFontSize(22, QFont.Weight.Bold)
        head.addWidget(self.name_label, 1)
        head.addWidget(_status_box(data["status"], self.card))
        self._body.addLayout(head)

        self.subtitle_label = Caption(data["subtitle"], parent=self.card)
        self._body.addWidget(self.subtitle_label)

        price_row = QHBoxLayout()
        price_row.setContentsMargins(0, 0, 0, 0)
        price_row.setSpacing(6)
        self.price = MoneyNumber(parent=self.card)
        self.price.set_colours(dim=theme.MUTED, bright=theme.TEXT)
        self.price.set_amount(data["rate"] or 0)
        price_row.addWidget(self.price)
        price_row.addWidget(Caption("/ day", parent=self.card))
        price_row.addStretch(1)
        self._body.addLayout(price_row)

        self._body.addWidget(SectionLabel("Specifications", parent=self.card))
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        for key, value in data["specs"]:
            form.addRow(Caption(key, parent=self.card), self._spec_value(value))
            self.spec_values[key] = value
        self._body.addLayout(form)

        self._body.addWidget(SectionLabel("Availability", parent=self.card))
        if not data["bookings"] and data["status"] == "available":
            self._body.addWidget(
                Pill(
                    "Available now",
                    parent=self.card,
                    colour=theme.TEXT,
                    background=theme.SURFACE,
                    dot_colour=theme.OK,
                    radius=8,
                )
            )
        elif not data["bookings"]:
            self._body.addWidget(
                Caption(
                    f"Currently {theme.status_label(data['status']).lower()}.",
                    parent=self.card,
                )
            )
        for row in data["bookings"]:
            self._body.addWidget(
                Caption(
                    f"#{row['ref']} \u00b7 {row['customer']} \u00b7 "
                    f"{row['start']:%d %b} \u2192 {row['end']:%d %b} \u00b7 "
                    f"{row['status']}",
                    parent=self.card,
                )
            )

        self._build_checker()

        self._body.addWidget(SectionLabel("Maintenance", parent=self.card))
        if not data["maintenance"]:
            self._body.addWidget(
                Caption("No maintenance records.", parent=self.card)
            )
        for row in data["maintenance"]:
            end = f" \u2192 {row['end']:%d %b %Y}" if row["end"] else ""
            self._body.addWidget(
                Caption(
                    f"{row['start']:%d %b %Y}{end} \u00b7 "
                    f"{row['description']} \u00b7 {row['status']}",
                    parent=self.card,
                )
            )

        self._body.addLayout(self._buttons)

    def _build_checker(self) -> None:
        # Check shares the heading row: label left, button right.
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.check_heading = SectionLabel("Check a period", parent=self.card)
        head.addWidget(self.check_heading, 1)
        self.check_button = QPushButton("Check", self.card)
        self.check_button.setObjectName("secondaryButton")
        self.check_button.clicked.connect(self._on_check)
        head.addWidget(self.check_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._body.addLayout(head)

        grid = QFormLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)

        today = QDate.currentDate()
        self.start_edit = QDateEdit(self.card)
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setDisplayFormat("dd MMM yyyy")
        self.start_edit.setDate(today)
        # Past days are unclickable in the calendar; hand-typed past dates
        # are still caught by the validation in _on_check.
        self.start_edit.setMinimumDate(today)
        self.end_edit = QDateEdit(self.card)
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDisplayFormat("dd MMM yyyy")
        self.end_edit.setDate(today.addDays(1))
        self.end_edit.setMinimumDate(today)
        # The return cannot precede the pick-up: End's floor follows Start.
        self.start_edit.dateChanged.connect(self._on_start_changed)
        grid.addRow(Caption("Start", parent=self.card), self.start_edit)
        grid.addRow(Caption("End", parent=self.card), self.end_edit)
        self._body.addLayout(grid)

        self.check_error = QLabel("", self.card)
        self.check_error.setObjectName(theme.OBJ_ERROR)
        self.check_error.setWordWrap(True)
        self.check_error.setVisible(False)
        self._body.addWidget(self.check_error)

        self.check_result = QLabel("", self.card)
        self.check_result.setWordWrap(True)
        self.check_result.setVisible(False)
        self._body.addWidget(self.check_result)

    def _on_start_changed(self, day: QDate) -> None:
        # The return cannot precede the pick-up; keep the calendar honest.
        self.end_edit.setMinimumDate(day)
        if self.end_edit.date() < day:
            self.end_edit.setDate(day)

    def _on_check(self) -> None:
        from app.domain.rental_calculator import RentalCalculator

        start = self.start_edit.date()
        end = self.end_edit.date()
        today = QDate.currentDate()
        error = ""
        if end < start:
            error = "End cannot be before start."
        elif start < today:
            error = "Start is in the past."
        if error:
            self.checked_window = None
            self.check_error.setText(error)
            self.check_error.setVisible(True)
            self.check_result.setVisible(False)
            return
        self.check_error.setVisible(False)

        start_py, end_py = start.toPython(), end.toPython()
        # The same rules the booking service will apply: half-open dates,
        # rented/reserved judged by overlap, maintenance/retired always blocked.
        try:
            with self._context.reading() as session:
                vehicle = session.get(Vehicle, self._vehicle_id)
                if vehicle is None:
                    raise ConflictError("That vehicle no longer exists.")
                booking_service.assert_available(
                    session, vehicle, start_py, end_py
                )
        except ConflictError as exc:
            # No prefill: the wizard must not silently reuse clashing dates.
            self.checked_window = None
            self.check_result.setText(f"Not available \u2014 {exc}")
            self.check_result.setStyleSheet(f"color: {theme.DANGER};")
            self.check_result.setVisible(True)
            return

        days = RentalCalculator.rental_days(start_py, end_py)
        self.checked_window = (start, end)
        self.check_result.setText(
            f"Available {start_py:%d %b %Y} \u2192 "
            f"{end_py:%d %b %Y} ({days} day{'s' if days != 1 else ''})."
        )
        self.check_result.setStyleSheet(f"color: {theme.OK};")
        self.check_result.setVisible(True)

    def _spec_value(self, value: str) -> QLabel:
        label = Caption(value, parent=self.card, colour=theme.TEXT)
        label.setWordWrap(True)
        return label

    def _on_rent(self) -> None:
        self.rent_requested.emit(self._vehicle_id)


class VehicleDetailsDialog(QDialog):
    """Thin modal-window wrapper around the panel (kept for compat)."""

    rent_requested = Signal(int)

    def __init__(self, context, vehicle_id: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Vehicle details")
        self.setModal(True)
        self.setMinimumWidth(600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.panel = VehicleDetailsPanel(context, vehicle_id, self)
        layout.addWidget(self.panel)

        # Alias the panel's pieces so existing callers keep working. The
        # vehicle bit is only present when the row still exists.
        panel = self.panel
        self.spec_values = panel.spec_values
        self.missing_label = panel.missing_label
        self.photo = getattr(panel, "photo", None)
        self.name_label = getattr(panel, "name_label", None)
        self.subtitle_label = getattr(panel, "subtitle_label", None)
        self.price = getattr(panel, "price", None)
        self.start_edit = getattr(panel, "start_edit", None)
        self.end_edit = getattr(panel, "end_edit", None)
        self.check_button = getattr(panel, "check_button", None)
        self.check_error = getattr(panel, "check_error", None)
        self.check_result = getattr(panel, "check_result", None)
        self.close_button = panel.close_button
        self.rent_button = panel.rent_button
        self.panel.close_requested.connect(self.reject)
        self.panel.rent_requested.connect(self._on_panel_rent)

    @property
    def checked_window(self):
        return self.panel.checked_window

    def _on_panel_rent(self, vehicle_id: int) -> None:
        self.rent_requested.emit(vehicle_id)
        self.accept()


class VehicleDetailsOverlay(QWidget):
    """Check details embedded in the staff window: frosted glass holding a
    centered 8px card.

    Not a window at all (`isWindow()` is False), so nothing new ever opens on
    the taskbar. Covers `window` edge to edge, blocks mouse input behind it,
    and closes on Esc or a click outside the card.

    The backdrop is snapshot frosted glass (v9): on open, the live page is
    frozen (`window.grab()`), blurred, dimmed, and shown full-window behind
    the crisp white card. Qt Widgets has no CSS `backdrop-filter`, so the
    freeze-blur-dim picture is the pure-Qt equivalent. If the snapshot ever
    fails, the scoped dim alone is the fallback and the modal still works.
    """

    def __init__(self, context, vehicle_id: int, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        #: False while the rental wizard owns this overlay: Escape and
        #: outside clicks must not discard a half-filled rental.
        self._dismissible = True
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Scoped dim over the whole window: the selector matches only the
        # overlay itself, so no `background` ever cascades onto descendants
        # (seen before: an unqualified rule turned the white Card dim). The
        # blurred snapshot in `backdrop` carries its own baked-in dim and
        # covers this; this fill is the fallback when the grab fails.
        self.setStyleSheet(
            "VehicleDetailsOverlay { background: rgba(23, 23, 23, 110);"
            " border: none; }"
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Frosted-glass picture, behind everything else. Mouse-transparent so
        # outside-click-to-close (handled by our own mousePressEvent) keeps
        # working: children get first shot at clicks, and a full-window
        # label would otherwise eat them all.
        self.backdrop = QLabel(self)
        self.backdrop.setObjectName("vehicleDetailsBackdrop")
        self.backdrop.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._blur_timer = QTimer(self)
        self._blur_timer.setSingleShot(True)
        self._blur_timer.setInterval(BACKDROP_RERENDER_MS)
        self._blur_timer.timeout.connect(self._on_blur_timeout)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(0)

        middle = QHBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(0)
        middle.addStretch(1)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("vehicleDetailsScroll")
        self.scroll.viewport().setObjectName("vehicleDetailsViewport")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Scoped like the overlay dim: an unqualified rule would cascade
        # transparency onto the card itself.
        self.scroll.setStyleSheet(
            "QScrollArea#vehicleDetailsScroll { background: transparent;"
            " border: none; }"
            " QScrollArea#vehicleDetailsScroll QWidget#vehicleDetailsViewport {"
            " background: transparent; border: none; }"
        )
        self.scroll.setFixedWidth(OVERLAY_CARD_W)
        middle.addWidget(self.scroll)
        middle.addStretch(1)
        # No stretches around `middle`: they split the height three ways and
        # left the card visibly short. The scroll takes the full height; the
        # max-height cap in _fit() bounds it on short windows.
        outer.addLayout(middle, 1)

        self.panel = VehicleDetailsPanel(context, vehicle_id, self.scroll)
        self.scroll.setWidget(self.panel)
        self.backdrop.lower()
        self.hide()

    def set_dismissible(self, flag: bool) -> None:
        """Allow or refuse Escape/outside-click closing.

        Defaults to True. The rental wizard turns this off so a stray click
        cannot discard the form, then back on before hiding.
        """
        self._dismissible = bool(flag)

    def _render_backdrop(self) -> bool:
        """Freeze, blur and dim the live page into the backdrop label.

        Returns True when a frosted picture is showing; False when the grab
        failed and the plain scoped dim is carrying the modal instead. Never
        raises: a backdrop must never break the modal.
        """
        try:
            window = self._window
            if window is None or window.size().isEmpty():
                return False
            src = window.grab()
            if src.isNull():
                return False
            dpr = src.devicePixelRatio()
            small = src.scaled(
                max(1, src.width() // BACKDROP_DOWNSCALE),
                max(1, src.height() // BACKDROP_DOWNSCALE),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            effect = QGraphicsBlurEffect()
            effect.setBlurRadius(BACKDROP_BLUR_RADIUS)
            scene = QGraphicsScene()
            item = QGraphicsPixmapItem(small)
            item.setGraphicsEffect(effect)
            scene.addItem(item)
            scene.setSceneRect(QRectF(small.rect()))
            blurred = QPixmap(small.size())
            blurred.setDevicePixelRatio(small.devicePixelRatio())
            blurred.fill(Qt.GlobalColor.transparent)
            painter = QPainter(blurred)
            try:
                scene.render(
                    painter, QRectF(blurred.rect()), scene.sceneRect()
                )
            finally:
                painter.end()
            target = self.size()
            if target.isEmpty():
                target = window.size()
            full = blurred.scaled(
                max(1, int(target.width() * dpr)),
                max(1, int(target.height() * dpr)),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            full.setDevicePixelRatio(dpr)
            painter = QPainter(full)
            try:
                painter.fillRect(
                    full.rect(), QColor(23, 23, 23, BACKDROP_DIM)
                )
            finally:
                painter.end()
            self.backdrop.setPixmap(full)
            return True
        except Exception:  # noqa: BLE001 - backdrop must never break modal
            return False

    def _render_soon(self) -> None:
        """Debounced re-render for window resizes while open."""
        if not self._blur_timer.isActive():
            self._blur_timer.start()
        else:
            self._blur_timer.stop()
            self._blur_timer.start()

    def _on_blur_timeout(self) -> None:
        if self.isVisible():
            self._render_backdrop()

    def show_overlay(self) -> None:
        self._fit()
        self._render_backdrop()
        self._window.installEventFilter(self)
        self.raise_()
        self.backdrop.lower()
        self.show()
        self.setFocus()

    def hide_overlay(self) -> None:
        self._window.removeEventFilter(self)
        self._blur_timer.stop()
        self.backdrop.clear()
        self.hide()

    def _fit(self) -> None:
        self.setGeometry(self._window.rect())
        self.backdrop.setGeometry(self.rect())
        self.scroll.setMaximumHeight(max(320, self._window.height() - 96))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        # Never crash on teardown-time events: Qt can deliver stragglers
        # (e.g. a resize posted before deleteLater) after either side is
        # half-destroyed, and an exception here would surface inside
        # whatever call happens to pump the loop next.
        try:
            is_window_resize = (
                watched is self._window and event.type() == QEvent.Type.Resize
            )
        except (AttributeError, RuntimeError):
            return False
        if is_window_resize:
            try:
                self._fit()
                if self.isVisible():
                    self._render_soon()
            except (AttributeError, RuntimeError):
                pass
        try:
            return super().eventFilter(watched, event)
        except RuntimeError:
            return False

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() == Qt.Key.Key_Escape and self._dismissible:
            self.hide_overlay()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # Outside-click-to-close only: presses inside the card column do
        # nothing at all (most card children ignore mouse presses, so those
        # propagate up here -- closing on them was the bug). The scroll is
        # the stable frame: wizard steps swap panels, never the scroll.
        if (
            self._dismissible
            and event.button() == Qt.MouseButton.LeftButton
            and not self.scroll.geometry().contains(event.position().toPoint())
        ):
            self.hide_overlay()
        super().mousePressEvent(event)
