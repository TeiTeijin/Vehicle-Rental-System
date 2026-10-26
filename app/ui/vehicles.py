"""The showroom's vehicle card and the paged catalog behind the home page.

Two pieces live here:

* :class:`VehicleCard` -- one vehicle, drawn from a plain :class:`Card`. The
  home page's shelves and the catalog grid both use it; shelf cards defer
  their photo until they scroll into view (`defer_photo=True`).
* :class:`CatalogPage` -- the listing behind "See all", the type chips and
  the top bar's search. Items arrive pre-picked from the home page's
  in-memory snapshot (app/ui/home.py), so paging never touches the database.

Thumbnails come from the CarImages API through app/ui/vehicle_photo.py;
vehicle copy comes from app/services/vehicle_service.py alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services import vehicle_service
from app.ui import theme
from app.ui.theme import (
    GRID_PAGE_SIZE,
    TOKENS,
    ghost_button,
    hint_label,
    make_label,
    per_day_text,
    section_title,
    status_chip,
    text_button,
)
from app.ui.vehicle_photo import VehiclePhoto

#: How many cards sit side by side on a normal window.
COLUMNS = 3

#: Statuses a card can carry, and the chip colour each one gets. (The shelves
#: and the grid list available vehicles; the other tones are here for when a
#: status change lands between a snapshot and a click.)
STATUS_TONES = {
    "available": "green",
    "reserved": "amber",
    "rented": "dark",
    "maintenance": "red",
    "retired": "muted",
}


@dataclass(frozen=True)
class Card:
    """A vehicle reduced to what the card draws."""

    vehicle_id: int
    name: str
    meta: str
    rate: str
    status: str
    ref: object


def card_for(item: vehicle_service.ShopVehicle) -> Card:
    """The card view of one showroom row."""
    return Card(
        vehicle_id=item.vehicle_id,
        name=item.name,
        meta=item.meta,
        rate=per_day_text(item.rate),
        status=item.status,
        ref=item.photo_ref,
    )


class VehicleCard(QFrame):
    """One vehicle on a shelf or in the grid.

    The whole card is clickable; VIEW DETAILS is the explicit door. A shelf
    card gets a fixed `width` and `defer_photo=True`: it shows the shimmer
    until the home page calls `ensure_photo()` (the card scrolled into view),
    because otherwise one snapshot would queue every shelf's fetches at once.
    """

    open_requested = Signal(int)

    def __init__(
        self,
        card: Card,
        parent: QWidget | None = None,
        *,
        width: int | None = None,
        defer_photo: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("gridCard")
        self.setCursor(Qt.PointingHandCursor)
        self.card = card
        self._deferred = defer_photo
        if width is not None:
            self.setFixedWidth(width)

        col = QVBoxLayout(self)
        col.setContentsMargins(12, 12, 12, 14)
        col.setSpacing(10)

        self.photo = VehiclePhoto(thumbnail=True)
        self.photo.setFixedHeight(150)
        if defer_photo:
            self.photo.set_waiting()
        else:
            self.photo.set_vehicle(card.ref)
        col.addWidget(self.photo)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(make_label(card.name, "gridName"), 1)
        head.addWidget(
            status_chip(card.status, STATUS_TONES.get(card.status, "muted")),
            0,
            Qt.AlignTop,
        )
        col.addLayout(head)

        col.addWidget(make_label(card.meta, "gridMeta", wrap=True))
        col.addStretch(1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addWidget(make_label(card.rate, "gridPrice"), 1)
        button = ghost_button("VIEW DETAILS")
        button.clicked.connect(lambda: self.open_requested.emit(self.card.vehicle_id))
        footer.addWidget(button)
        col.addLayout(footer)

    # ---------- deferred photos ----------
    @property
    def photo_pending(self) -> bool:
        return self._deferred

    def ensure_photo(self) -> None:
        """Load the photo now -- the card has just scrolled into view."""
        if not self._deferred:
            return
        self._deferred = False
        self.photo.set_vehicle(self.card.ref)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_requested.emit(self.card.vehicle_id)
            event.accept()
            return
        super().mousePressEvent(event)


class CatalogPage(QWidget):
    """The paged grid behind "See all", the type chips and the search box."""

    vehicle_selected = Signal(int)
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self._items: list[vehicle_service.ShopVehicle] = []
        self._shown = 0
        self._empty_text = "Nothing to show right now."
        self._cards: list[VehicleCard] = []

        self.back_button = text_button("Back to showroom")
        self.back_button.setIcon(theme.icon_from("back", TOKENS["text_dark"], 14))
        self.back_button.clicked.connect(self.back_requested)

        self.title = section_title("")
        self.count_label = hint_label("", tone="muted")
        self.count_label.setWordWrap(False)
        self.summary = hint_label("", tone="muted")
        self.summary.setWordWrap(True)
        self.summary.hide()

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(14)
        header.addWidget(self.back_button, 0, Qt.AlignVCenter)
        header.addWidget(self.title, 1)
        header.addWidget(self.count_label, 0, Qt.AlignBottom)

        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(18)
        for column in range(COLUMNS):
            self.grid.setColumnStretch(column, 1)

        self.empty = hint_label("", tone="muted")
        self.empty.setObjectName("emptyState")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(140)
        self.empty.hide()

        self.more_button = ghost_button("LOAD MORE VEHICLES")
        self.more_button.clicked.connect(self.load_more)
        self.more_button.hide()

        body = QVBoxLayout()
        body.setContentsMargins(40, 28, 40, 32)
        body.setSpacing(16)
        body.addLayout(header)
        body.addWidget(self.summary)
        body.addWidget(self.empty)
        body.addLayout(self.grid)
        body.addWidget(self.more_button, 0, Qt.AlignHCenter)
        body.addStretch(1)

        holder = QWidget()
        holder.setObjectName("pageBody")
        holder.setLayout(body)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("pageScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setWidget(holder)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._scroll)

    # ---------- api ----------
    def open_listing(
        self,
        title: str,
        items: Sequence[vehicle_service.ShopVehicle],
        *,
        empty_text: str,
        summary: str = "",
    ) -> None:
        """Show a fresh listing, scrolled back to the top."""
        self._items = list(items)
        self._shown = 0
        self._empty_text = empty_text
        self._clear_cards()
        self.title.setText(title)
        self.summary.setText(summary)
        self.summary.setVisible(bool(summary))
        self._scroll.verticalScrollBar().setValue(0)
        self._render_page()

    def load_more(self) -> None:
        self._render_page()

    # ---------- rendering ----------
    def _render_page(self) -> None:
        chunk = self._items[self._shown : self._shown + GRID_PAGE_SIZE]
        for item in chunk:
            widget = VehicleCard(card_for(item))
            widget.open_requested.connect(self.vehicle_selected)
            index = len(self._cards)
            self.grid.addWidget(widget, index // COLUMNS, index % COLUMNS)
            self._cards.append(widget)
        self._shown += len(chunk)

        total = len(self._items)
        if total == 0:
            self.count_label.setText("")
            self.empty.setText(self._empty_text)
            self.empty.show()
        else:
            self.empty.hide()
            self.count_label.setText(
                f"Showing {self._shown} of {total} "
                f"vehicle{'' if total == 1 else 's'}"
            )
        self.more_button.setVisible(self._shown < total)

    def _clear_cards(self) -> None:
        for widget in self._cards:
            self.grid.removeWidget(widget)
            widget.deleteLater()
        self._cards.clear()


__all__ = ["COLUMNS", "STATUS_TONES", "Card", "VehicleCard", "CatalogPage", "card_for"]
