"""Screen 1: the vehicle page.

Left, the photos: front, both sides and the 3D model, all from the CarImages
API through app/ui/vehicle_photo.py. Right, everything the counter knows about
the vehicle (app/services/vehicle_service.py reads the live columns) plus the
next window the vehicle is free, so "Book this vehicle" is never a dead end.

The page never holds a database row: it reads what it needs, keeps plain
values, and hands the router a vehicle id (see app/ui/main_window.py).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from app.database import SessionLocal
from app.models import Vehicle
from app.services import booking_service, vehicle_photos, vehicle_service
from app.services.errors import DataUnavailable
from app.ui import theme
from app.ui.theme import (
    TOKENS,
    divider,
    hint_label,
    make_label,
    overline,
    panel,
    per_day_text,
    primary_button,
    short_day,
    spec_tile,
)
from app.ui.vehicle_photo import VehiclePhoto, VehicleModelView

#: How tall the photo area sits before the view tabs.
PHOTO_HEIGHT = 360

#: The four view buttons, in the order the design shows them.
VIEW_TABS = (
    ("front", "FRONT"),
    ("left", "LEFT SIDE"),
    ("right", "RIGHT SIDE"),
    ("3d", "3D MODEL"),
)


class VehicleDetailsPage(QWidget):
    """The vehicle page. Emits what the customer asked for; the router routes."""

    book_requested = Signal(int)
    back_requested = Signal()

    def __init__(self, vehicle_id: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.vehicle_id = vehicle_id
        self._ref = None
        self._vehicle_name = ""

        self.photo = VehiclePhoto()
        self.photo.setMinimumHeight(PHOTO_HEIGHT)
        self.model_view = VehicleModelView()
        self._media_stack = QStackedLayout()
        self._media_stack.setContentsMargins(0, 0, 0, 0)
        self._media_stack.addWidget(self.photo)
        self._media_stack.addWidget(self.model_view)

        media_frame = QFrame()
        media_frame.setObjectName("photoFrame")
        media_col = QVBoxLayout(media_frame)
        media_col.setContentsMargins(10, 10, 10, 10)
        media_col.addLayout(self._media_stack)

        self.view_tabs: dict[str, QPushButton] = {}
        tabs_row = QHBoxLayout()
        tabs_row.setContentsMargins(0, 0, 0, 0)
        tabs_row.setSpacing(8)
        for key, caption in VIEW_TABS:
            button = QPushButton(caption)
            button.setObjectName("viewTab")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda _checked, k=key: self.show_view(k))
            self.view_tabs[key] = button
            tabs_row.addWidget(button)
        tabs_row.addStretch(1)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        left.addWidget(media_frame)
        left.addLayout(tabs_row)
        left.addWidget(self._included_panel())
        left.addStretch(1)

        # ----- right column: the facts -----
        self.eyebrow = overline("")
        self.name = make_label("", "pageTitle")
        self.price = make_label("", "pagePrice")
        self.summary = make_label("", "pageSummary")
        self.status_chip = theme.status_chip("", "muted")

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(self.name, 1)
        title_row.addWidget(self.status_chip, 0, Qt.AlignVCenter)

        self.specs_grid = QGridLayout()
        self.specs_grid.setContentsMargins(0, 0, 0, 0)
        self.specs_grid.setSpacing(10)

        self.availability = make_label("", "availabilityLine")
        self.availability_note = hint_label("", tone="muted")

        self.book_button = primary_button("BOOK THIS VEHICLE")
        self.book_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.book_button.clicked.connect(lambda: self.book_requested.emit(self.vehicle_id))

        book_panel, book_col = panel("panelTan", spacing=10)
        book_col.addWidget(overline("Next available"))
        book_col.addWidget(self.availability)
        book_col.addWidget(self.availability_note)
        book_col.addWidget(self.book_button)
        book_col.addWidget(hint_label(
            "Free cancellation up to 24 hours before pickup. "
            "Pay online or at the counter.",
        ))

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(12)
        right.addWidget(self.eyebrow)
        right.addLayout(title_row)
        right.addWidget(self.price)
        right.addWidget(self.summary)
        right.addWidget(divider())
        right.addLayout(self.specs_grid)
        right.addWidget(book_panel)
        right.addStretch(1)

        self.error = hint_label("", tone="danger")
        self.error.hide()

        self.back_button = theme.text_button("\u2190  Back to vehicles")
        self.back_button.setIcon(theme.icon_from("back", TOKENS["text_dark"], 14))
        self.back_button.clicked.connect(self.back_requested)

        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(28)
        columns.addLayout(left, 3)
        columns.addLayout(right, 2)

        body = QVBoxLayout()
        body.setContentsMargins(40, 24, 40, 32)
        body.setSpacing(16)
        body.addWidget(self.back_button, 0, Qt.AlignLeft)
        body.addWidget(self.error)
        body.addLayout(columns)
        body.addStretch(1)

        holder = QWidget()
        holder.setObjectName("pageBody")
        holder.setLayout(body)
        holder.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(holder)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

        self.show_view("front")
        self.reload()

    # ---------- data ----------
    def reload(self) -> None:
        """Read the vehicle and everything around it, then fill the widgets."""
        try:
            with SessionLocal() as session:
                vehicle = session.get(Vehicle, self.vehicle_id)
                if vehicle is None:
                    self._show_error("That vehicle is no longer in the fleet.")
                    return
                self._ref = vehicle_photos.ref_for(vehicle)
                self._vehicle_name = vehicle_service.display_name(vehicle)

                values = {
                    "eyebrow": f"{vehicle.make} / {vehicle_service.body_label(vehicle)} / {vehicle.year}".upper(),
                    "name": self._vehicle_name.upper(),
                    "price": per_day_text(vehicle.daily_rate),
                    "summary": vehicle_service.summary_line(vehicle),
                    "specs": vehicle_service.spec_values(vehicle),
                    "status": vehicle.status,
                    "mileage": vehicle_service.mileage_label(vehicle),
                    "rate": vehicle.daily_rate,
                    "available_from": None,
                    "conflict": None,
                }

                if vehicle.status == "available":
                    start, end = booking_service.next_available_window(session, vehicle)
                    values["available_from"] = (start, end)
                else:
                    window = booking_service.occupied_windows(session, vehicle)
                    values["conflict"] = window[-1] if window else None
        except DataUnavailable as exc:
            self._show_error(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - a remote DB can drop mid-page
            self._show_error(f"Could not load this vehicle: {exc}")
            return

        self.error.hide()
        self._fill(values)

    def _fill(self, values: dict) -> None:
        self.eyebrow.setText(values["eyebrow"])
        self.name.setText(values["name"])
        self.price.setText(values["price"])
        self.summary.setText(values["summary"])

        while self.specs_grid.count():
            item = self.specs_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        specs = values["specs"]
        tiles = (
            ("seats", "Passengers", specs["passengers"]),
            ("transmission", "Transmission", specs["transmission"]),
            ("fuel", "Fuel", specs["fuel"]),
            ("body", "Body", specs["body"]),
        )
        for index, (icon_name, caption, value) in enumerate(tiles):
            self.specs_grid.addWidget(spec_tile(icon_name, caption, value), index // 2, index % 2)
        self.specs_grid.addWidget(
            spec_tile("mileage", "Mileage", values["mileage"]), 2, 0, 1, 2
        )

        bookable = values["status"] == "available"
        self.book_button.setEnabled(bookable)
        if bookable:
            self.status_chip.setText("AVAILABLE")
            self.status_chip.setProperty("tone", "green")
            start, end = values["available_from"] or (None, None)
            if start is not None:
                self.availability.setText(
                    f"Free from {short_day(start)} to {short_day(end)}"
                )
            else:
                self.availability.setText("Ready to book")
            self.availability_note.setText(
                "Pick your dates and times on the next step. "
                "The calendar only greys out days that are fully booked."
            )
        else:
            self.status_chip.setText(values["status"].upper())
            self.status_chip.setProperty("tone", "amber")
            conflict = values["conflict"]
            if conflict is not None:
                start, end = conflict
                self.availability.setText(f"Back {short_day(end)}")
                self.availability_note.setText(
                    f"This vehicle is out until {theme.moment_text(end)}."
                )
            else:
                self.availability.setText("Not bookable right now")
                self.availability_note.setText(
                    "The fleet manager has this vehicle off the road. "
                    "Pick another one from the list."
                )
        self.status_chip.style().unpolish(self.status_chip)
        self.status_chip.style().polish(self.status_chip)

        if self._ref is not None:
            self.photo.set_vehicle(self._ref, self._current_view())

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()
        self.book_button.setEnabled(False)
        self.name.setText("COULD NOT LOAD THIS VEHICLE")

    # ---------- views ----------
    def _current_view(self) -> str:
        for key, button in self.view_tabs.items():
            if button.isChecked():
                return key
        return "front"

    def show_view(self, key: str) -> None:
        """Switch the media area between the photos and the 3D model."""
        for name, button in self.view_tabs.items():
            button.setChecked(name == key)

        if key == "3d":
            self._media_stack.setCurrentWidget(self.model_view)
            if self._ref is not None and self.model_view.vehicle is None:
                self.model_view.set_vehicle(self._ref)
        else:
            self._media_stack.setCurrentWidget(self.photo)
            if self._ref is not None:
                self.photo.set_view(key)

    # ---------- the "what you get" panel ----------
    @staticmethod
    def _included_panel() -> QWidget:
        from app.services import pricing

        frame, col = panel("panel", spacing=8)
        col.addWidget(overline("Every rental includes"))
        for icon_name, caption in (
            ("shield", f"Standard insurance - {theme.pesos_text(pricing.INSURANCE_FEE)}"),
            ("receipt", f"Service fee - {theme.pesos_text(pricing.SERVICE_FEE)}"),
            ("percent", "12% VAT, shown on the receipt"),
            ("route", "Unlimited mileage within the metro"),
        ):
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(10)
            glyph = QLabel()
            glyph.setPixmap(theme.icon_from(icon_name, TOKENS["text_muted"], 16).pixmap(16, 16))
            glyph.setFixedSize(16, 16)
            line.addWidget(glyph)
            line.addWidget(make_label(caption, "valueCaption"), 1)
            col.addWidget(row)
        return frame


__all__ = ["PHOTO_HEIGHT", "VIEW_TABS", "VehicleDetailsPage"]
