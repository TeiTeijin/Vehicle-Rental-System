"""Vehicle filter dropdown for the staff New Rental page.

Rebuilt ground-up from ``Downloads/filter-layout.md``. The panel
(``.filter-panel``) is a two-column grid:

- ``.filter-side`` (left, narrow): the category list (``.filter-nav-item``),
  one row per axis with a leading icon. The active row shows only the 3px ink
  bar (``--ink``); background and hover wash never change.
- ``.filter-content`` (right, larger): every section stacked in one continuous
  scroll (``.filter-section``), a separator rule between sections, and the
  Clear / Apply actions (``.filter-actions``) pinned to the bottom.

Options (``.filter-card``) stack vertically, one box per row. Brand and car
type boxes carry a picture on the left (``.filter-card-img``) with the name
beside it (``.filter-card-label``). A selected box gets an ink border and a
filled tick on the right; its background does not change. Passengers is a
stepper (``.filter-stepper``), price range is min/max inputs
(``.filter-range``).

Palette tokens live in :mod:`app.staff.theme` and match the spec: ``paper``
right panel, ``surface`` side panel, ``surface-hover`` chip/button hover,
``border`` separators and outlines, ``ink`` active bar and selected boxes,
``text`` main copy, ``muted`` inactive side text.

Deviations from the doc, all deliberate and flagged where they occur:

- "Motor CC" is kept as a 7th section (motorcycle ``cc_buckets``): the
  schema records displacement and the fleet filters on it.
- All seven vehicle classes are offered, not just the doc's five.
- "Fuel type" offers the stored ``Petrol`` value (the doc writes "Gasoline").
- Edits publish live through ``filters_changed``; Apply re-publishes the
  draft and is the explicit commit point the page closes on.
- Thumbnails start as silhouette placeholders and are swapped for real fleet
  photos via :meth:`FilterPopover.set_brand_photos` /
  :meth:`set_type_photos`.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
)

from app.services.vehicle_service import (
    CC_BUCKETS,
    FUEL_TYPES,
    SEAT_COUNTS,
    TRANSMISSIONS,
    VEHICLE_CLASSES,
    VEHICLE_CLASS_LABELS,
    VehicleFilter,
)
from app.staff import icons, theme
from app.staff.metrics import (
    FILTER_CARD_H,
    FILTER_GAP,
    FILTER_POPOVER_H,
    FILTER_POPOVER_W,
    FILTER_ROW_H,
    FILTER_THUMB,
    MAX_RATE,
    POPOVER_CATEGORY_W,
    SECTION_ROW_H,
)
from app.staff.section_row import ActiveRow

#: Right-hand option lists start a little clear of the 1px column divider.
PANE_PAD_LEFT = 14

#: Leading icon size inside a side-panel row.
NAV_ICON = 14

#: Why an option or whole axis is greyed out: the other active filters have
#: already excluded every vehicle it could have matched.
NO_FILTER_MATCH_TOOLTIP = (
    "No matches with the current other filters. "
    "Clear one of them to see these options again."
)

#: (Content key, side-panel label, icon name), in display order.
_CATEGORY_SPECS: tuple[tuple[str, str, str], ...] = (
    ("makes", "Car brand", "vehicle"),
    ("vehicle_classes", "Car type", "sliders"),
    ("cc_buckets", "Motor CC", "settings"),
    ("price", "Price range", "cash"),
    ("seats", "Passengers", "customers"),
    ("transmissions", "Transmission type", "swap"),
    ("fuel_types", "Fuel type", "info"),
)


def _placeholder_thumb(size: int) -> QPixmap:
    """Spec placeholder: an inline silhouette until a real photo is set."""
    try:
        return icons.pixmap("vehicle", theme.MUTED, size)
    except Exception:  # noqa: BLE001
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        return pixmap


def _tick_pixmap(size: int) -> QPixmap:
    try:
        return icons.pixmap("check", theme.PAPER, size)
    except Exception:  # noqa: BLE001
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        return pixmap


def _nav_icon(name: str, colour: str) -> QPixmap:
    try:
        return icons.pixmap(name, colour, NAV_ICON)
    except Exception:  # noqa: BLE001
        pixmap = QPixmap(NAV_ICON, NAV_ICON)
        pixmap.fill(Qt.GlobalColor.transparent)
        return pixmap


# -- .filter-card -------------------------------------------------------------


class FilterCard(QAbstractButton):
    """One selectable option box: ``[img] Label [tick]``, or text-only.

    Checked means selected: ink border plus a filled tick on the right, with
    the background unchanged. Disabled means muted by availability: dimmed,
    but the tick is kept.
    """

    def __init__(
        self,
        key: str,
        label: str,
        *,
        with_thumb: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._key = key
        self.setCheckable(True)
        self.setAutoExclusive(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"Toggle {label}")
        self.setFixedHeight(FILTER_CARD_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setObjectName(theme.OBJ_FILTER_CARD)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 10, 6)
        layout.setSpacing(10)

        self.thumb: QLabel | None = None
        if with_thumb:
            self.thumb = QLabel(self)
            self.thumb.setObjectName(theme.OBJ_FILTER_CARD_IMG)
            self.thumb.setFixedSize(FILTER_THUMB, FILTER_THUMB)
            self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.thumb.setPixmap(_placeholder_thumb(FILTER_THUMB - 8))
            layout.addWidget(self.thumb)

        self.name = QLabel(label, self)
        self.name.setObjectName(theme.OBJ_FILTER_CARD_LABEL)
        layout.addWidget(self.name, 1)

        self.tick = QLabel(self)
        self.tick.setObjectName(theme.OBJ_FILTER_CARD_TICK)
        self.tick.setFixedSize(18, 18)
        self.tick.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tick.setPixmap(_tick_pixmap(12))
        self.tick.setVisible(False)
        layout.addWidget(self.tick, 0, Qt.AlignmentFlag.AlignVCenter)

        self.toggled.connect(self._sync_tick)
        self._sync_tick(self.isChecked())

    @property
    def key(self) -> str:
        return self._key

    def set_available(self, available: bool) -> None:
        """Grey the card when the other filters leave it unreachable.

        A dead card without a reason reads as a broken app, so the tooltip
        carries the explanation to every part of the row a pointer can hit.
        """
        self.setEnabled(available)
        tip = f"Toggle {self.name.text()}" if available else NO_FILTER_MATCH_TOOLTIP
        self.setToolTip(tip)
        self.name.setToolTip(tip)
        if self.thumb is not None:
            self.thumb.setToolTip(tip)

    def set_photo(self, pixmap: QPixmap | None) -> None:
        """Swap the placeholder silhouette for a real car photo."""
        if self.thumb is None:
            return
        if pixmap is None or pixmap.isNull():
            self.thumb.setPixmap(_placeholder_thumb(FILTER_THUMB - 8))
        else:
            self.thumb.setPixmap(
                pixmap.scaled(
                    FILTER_THUMB - 4,
                    FILTER_THUMB - 4,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def _sync_tick(self, checked: bool) -> None:
        self.tick.setVisible(checked)
        # A dynamic property drives the QSS selected border; the background
        # rule never changes, per the spec.
        self.setProperty("selected", "true" if checked else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        # QSS borders on a QAbstractButton subclass need a styled paint pass;
        # the visible content comes from the child widgets.
        painter = QStylePainter(self)
        try:
            option = QStyleOptionButton()
            option.initFrom(self)
            painter.drawPrimitive(QStyle.PrimitiveElement.PE_Widget, option)
        finally:
            painter.end()


# -- Car brand section --------------------------------------------------------


class _BrandRow(ActiveRow):
    """One brand card. The row is the visible card; a hidden box holds the
    filter tick, so a brand greyed by availability can still be unticked by
    its name.

    Deliberately no highlight paint here: the right panel carries selection
    only (border + tick). The fleet-in-view state is still tracked through
    :meth:`set_active` for callers, but nothing is drawn for it.
    """

    def __init__(self, label: str, box: QCheckBox, parent: QWidget | None = None) -> None:
        super().__init__(label, parent=parent)
        self.setAutoExclusive(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"Toggle {label}")
        self.setFixedHeight(FILTER_CARD_H)
        self.setObjectName(theme.OBJ_FILTER_CARD)
        self._box = box
        self._box.setVisible(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 10, 6)
        layout.setSpacing(10)

        self.thumb = QLabel(self)
        self.thumb.setObjectName(theme.OBJ_FILTER_CARD_IMG)
        self.thumb.setFixedSize(FILTER_THUMB, FILTER_THUMB)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setPixmap(_placeholder_thumb(FILTER_THUMB - 8))
        layout.addWidget(self.thumb)

        self.name = QLabel(label, self)
        self.name.setObjectName(theme.OBJ_FILTER_CARD_LABEL)
        layout.addWidget(self.name, 1)

        self.tick = QLabel(self)
        self.tick.setObjectName(theme.OBJ_FILTER_CARD_TICK)
        self.tick.setFixedSize(18, 18)
        self.tick.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tick.setPixmap(_tick_pixmap(12))
        self.tick.setVisible(False)
        layout.addWidget(self.tick, 0, Qt.AlignmentFlag.AlignVCenter)

        box.toggled.connect(self._sync_selected)
        self._sync_selected(box.isChecked())

    def set_photo(self, pixmap: QPixmap | None) -> None:
        """Swap the placeholder silhouette for a real car photo."""
        if pixmap is None or pixmap.isNull():
            self.thumb.setPixmap(_placeholder_thumb(FILTER_THUMB - 8))
        else:
            self.thumb.setPixmap(
                pixmap.scaled(
                    FILTER_THUMB - 4,
                    FILTER_THUMB - 4,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def set_available(self, available: bool) -> None:
        tip = f"Toggle {self.label}" if available else NO_FILTER_MATCH_TOOLTIP
        self.setToolTip(tip)
        self.name.setToolTip(tip)
        self.thumb.setToolTip(tip)

    def nextCheckState(self) -> None:  # noqa: N802
        self._box.setChecked(not self._box.isChecked())

    def _sync_selected(self, checked: bool) -> None:
        self.tick.setVisible(checked)
        self.setProperty("selected", "true" if checked else "false")
        # Dim the row when its tick is muted, without disabling the row
        # itself, so the name stays clickable.
        muted = not self._box.isEnabled() and not checked
        self.name.setProperty("muted", "true" if muted else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.style().unpolish(self.name)
        self.style().polish(self.name)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        # Selection border only. The label comes from the child QLabel, and
        # -- unlike the side panel -- no bar, wash, or highlight is painted.
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.rect().adjusted(1, 1, -1, -1)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(
                QColor(theme.INK) if self._box.isChecked() else QColor(theme.BORDER)
            )
            painter.drawRoundedRect(rect, 8, 8)
        finally:
            painter.end()


class _BrandPane(QWidget):
    """The Car brand ``.filter-section``: heading, search, picture cards."""

    #: Emitted with a brand name whenever its tick changes.
    brand_toggled = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("filterPane")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(PANE_PAD_LEFT, 10, 10, 10)
        layout.setSpacing(FILTER_GAP)

        self.heading = QLabel("Car brand", self)
        self.heading.setObjectName(theme.OBJ_FILTER_GROUP_HEADING)
        layout.addWidget(self.heading)

        self.search = QLineEdit(self)
        self.search.setObjectName(theme.OBJ_SEARCH_FIELD)
        self.search.setPlaceholderText("Search makes")
        self.search.setFixedHeight(FILTER_ROW_H)
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)

        # No scroll of its own: the whole right column is the one scroll
        # region, so the brand list runs on with it.
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(FILTER_GAP)
        self._rows_layout.addStretch(1)
        layout.addLayout(self._rows_layout, 1)

        self.rows: dict[str, _BrandRow] = {}
        self.boxes: dict[str, QCheckBox] = {}
        self.order: list[str] = []
        self._needle = ""

        self.search.textChanged.connect(self._on_search_changed)

    def set_brands(self, brands: Sequence[str]) -> None:
        brands = list(brands)
        selected = {name for name, box in self.boxes.items() if box.isChecked()}
        enabled = {name: box.isEnabled() for name, box in self.boxes.items()}

        for row in self.rows.values():
            self._rows_layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        for box in self.boxes.values():
            box.setParent(None)
            box.deleteLater()
        self.rows = {}
        self.boxes = {}
        self.order = brands

        insert_at = self._rows_layout.count() - 1
        for brand in brands:
            box = QCheckBox(self)
            box.setObjectName(theme.OBJ_FILTER_CHECK)
            box.setVisible(False)
            row = _BrandRow(brand, box, parent=self)
            box.toggled.connect(
                lambda _on, name=brand: self.brand_toggled.emit(name)
            )
            self._rows_layout.insertWidget(insert_at, row)
            insert_at += 1
            self.rows[brand] = row
            self.boxes[brand] = box
            if brand in selected:
                box.setChecked(True)
            if brand in enabled:
                box.setEnabled(enabled[brand])

        self._apply_needle()

    def set_photos(self, photos: dict[str, QPixmap]) -> None:
        for name, pixmap in photos.items():
            row = self.rows.get(name)
            if row is not None:
                row.set_photo(pixmap)

    def selected(self) -> set[str]:
        return {name for name, box in self.boxes.items() if box.isChecked()}

    def clear(self) -> None:
        for box in self.boxes.values():
            box.setChecked(False)

    def set_boxes_enabled(self, values: set[str] | None) -> None:
        for name, box in self.boxes.items():
            available = values is None or name in values
            box.setEnabled(available)
            row = self.rows.get(name)
            if row is not None:
                row.set_available(available)
                row._sync_selected(box.isChecked())

    def visible_brands(self) -> list[str]:
        needle = self._needle.lower()
        if not needle:
            return list(self.order)
        return [name for name in self.order if needle in name.lower()]

    def _apply_needle(self) -> None:
        needle = self._needle.lower()
        for name, row in self.rows.items():
            row.setVisible(not needle or needle in name.lower())

    def _on_search_changed(self, text: str) -> None:
        self._needle = text
        self._apply_needle()


# -- picture / text card sections ----------------------------------------------


class _CardGroup(QWidget):
    """A ``.filter-section`` holding a vertical list of ``.filter-card``."""

    def __init__(
        self,
        heading: str,
        items: Sequence[tuple[str, str]],
        *,
        with_thumbs: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(theme.OBJ_FILTER_GROUP)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(PANE_PAD_LEFT, 10, 10, 10)
        layout.setSpacing(FILTER_GAP)

        self.heading = QLabel(heading, self)
        self.heading.setObjectName(theme.OBJ_FILTER_GROUP_HEADING)
        layout.addWidget(self.heading)

        self._cards_layout = QVBoxLayout()
        self._cards_layout.setSpacing(FILTER_GAP)
        layout.addLayout(self._cards_layout)

        self.boxes: dict[str, FilterCard] = {}
        for key, label in items:
            card = FilterCard(key, label, with_thumb=with_thumbs, parent=self)
            card.setObjectName(theme.OBJ_FILTER_CARD)
            self.boxes[key] = card
            self._cards_layout.addWidget(card)

    def set_photos(self, photos: dict[str, QPixmap]) -> None:
        for key, pixmap in photos.items():
            card = self.boxes.get(key)
            if card is not None:
                card.set_photo(pixmap)

    def selected(self) -> set[str]:
        return {key for key, card in self.boxes.items() if card.isChecked()}

    def clear(self) -> None:
        for card in self.boxes.values():
            card.setChecked(False)

    def set_boxes_enabled(self, values: set | None) -> None:
        for key, card in self.boxes.items():
            card.set_available(values is None or key in values)


# -- Passengers stepper ----------------------------------------------------------


class _SeatGroup(QWidget):
    """Passengers ``.filter-stepper`` (``[ - ] value [ + ]``, exact match).

    ``boxes`` are hidden proxies so existing callers
    (``boxes["7"].setChecked(...)``) keep working; the visible control is the
    stepper. ``None`` (shown as "Any") means no passenger filter.
    """

    seats_changed = Signal()

    #: Stepper stops: None == Any (no filter), else an exact seat count.
    OPTIONS: tuple[int | None, ...] = (None, *SEAT_COUNTS)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(theme.OBJ_FILTER_GROUP)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(PANE_PAD_LEFT, 10, 10, 10)
        layout.setSpacing(FILTER_GAP)

        self.heading = QLabel("Passengers", self)
        self.heading.setObjectName(theme.OBJ_FILTER_GROUP_HEADING)
        layout.addWidget(self.heading)

        stepper = QHBoxLayout()
        stepper.setSpacing(8)

        self.minus = QPushButton("\u2013", self)
        self.minus.setObjectName(theme.OBJ_FILTER_STEPPER_BTN)
        self.minus.setFixedSize(36, FILTER_ROW_H)
        self.minus.setCursor(Qt.CursorShape.PointingHandCursor)
        self.plus = QPushButton("+", self)
        self.plus.setObjectName(theme.OBJ_FILTER_STEPPER_BTN)
        self.plus.setFixedSize(36, FILTER_ROW_H)
        self.plus.setCursor(Qt.CursorShape.PointingHandCursor)
        self.value_label = QLabel("Any", self)
        self.value_label.setObjectName(theme.OBJ_FILTER_STEPPER_VALUE)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value_label.setFixedHeight(FILTER_ROW_H)

        stepper.addWidget(self.minus)
        stepper.addWidget(self.value_label, 1)
        stepper.addWidget(self.plus)
        layout.addLayout(stepper)

        self._index = 0
        self.boxes: dict[str, QCheckBox] = {}
        for n in SEAT_COUNTS:
            proxy = QCheckBox(str(n), self)
            proxy.setObjectName(theme.OBJ_FILTER_CHECK)
            proxy.setVisible(False)
            proxy.toggled.connect(
                lambda on, key=str(n): self._proxy_toggled(key, on)
            )
            self.boxes[str(n)] = proxy

        self.minus.clicked.connect(lambda: self._step(-1))
        self.plus.clicked.connect(lambda: self._step(1))
        self._sync_ui()

    def value(self) -> int | None:
        return self.OPTIONS[self._index]

    def set_value(self, seats: int | None) -> None:
        try:
            self._index = list(self.OPTIONS).index(seats)
        except ValueError:
            self._index = 0
        self._sync_ui()
        self._sync_proxies()

    def selected(self) -> set[str]:
        current = self.value()
        return set() if current is None else {str(current)}

    def clear(self) -> None:
        self._index = 0
        self._sync_ui()
        self._sync_proxies()

    def set_boxes_enabled(self, values: set | None) -> None:
        for key, proxy in self.boxes.items():
            proxy.setEnabled(values is None or int(key) in values)

    def _step(self, delta: int) -> None:
        self._index = max(0, min(len(self.OPTIONS) - 1, self._index + delta))
        self._sync_ui()
        self._sync_proxies()
        self.seats_changed.emit()

    def _sync_ui(self) -> None:
        current = self.value()
        self.value_label.setText("Any" if current is None else str(current))

    def _sync_proxies(self) -> None:
        current = self.value()
        for key, proxy in self.boxes.items():
            blocked = proxy.blockSignals(True)
            try:
                proxy.setChecked(current is not None and int(key) == current)
            finally:
                proxy.blockSignals(blocked)

    def _proxy_toggled(self, key: str, on: bool) -> None:
        if on:
            self.set_value(int(key))
            self.seats_changed.emit()
        elif self.value() == int(key):
            self.set_value(None)
            self.seats_changed.emit()


# -- Price range -----------------------------------------------------------------


class _NoWheelSpin(QSpinBox):
    """A spin box the mouse wheel cannot turn by accident.

    Hovering the Min/Max inputs while scrolling the panel used to rewrite the
    price bounds; the wheel is ignored until the field has keyboard focus.
    """

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class _PriceGroup(QWidget):
    """Price range ``.filter-range``: Min / Max inputs, price per day."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(theme.OBJ_FILTER_GROUP)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(PANE_PAD_LEFT, 10, 10, 10)
        layout.setSpacing(FILTER_GAP)

        self.heading = QLabel("Price range", self)
        self.heading.setObjectName(theme.OBJ_FILTER_GROUP_HEADING)
        layout.addWidget(self.heading)

        self.min_rate = self._spin()
        self.max_rate = self._spin()

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.min_rate, 1)
        between = QLabel("\u2013")
        between.setObjectName(theme.OBJ_FILTER_GROUP_HEADING)
        between.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(between)
        row.addWidget(self.max_rate, 1)
        layout.addLayout(row)
        hint = QLabel("Price per day", self)
        hint.setObjectName(theme.OBJ_FILTER_GROUP_HEADING)
        layout.addWidget(hint)

    @staticmethod
    def _spin() -> _NoWheelSpin:
        spin = _NoWheelSpin()
        spin.setObjectName(theme.OBJ_FILTER_SPIN)
        spin.setRange(0, MAX_RATE)
        spin.setSpecialValueText("Any")
        spin.setPrefix("\u20b1 ")
        # The "Any" text plus a 7-digit max makes Qt reserve ~137px per box,
        # forcing the whole right panel wider than its viewport (cut-off
        # boxes). Let the pair squeeze and share the row instead.
        spin.setMinimumWidth(60)
        spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return spin

    def clear(self) -> None:
        self.min_rate.setMaximum(MAX_RATE)
        self.max_rate.setMaximum(MAX_RATE)
        self.min_rate.setValue(0)
        self.max_rate.setValue(0)


class _PanelCard(QFrame):
    """A floating white panel: the dropdown's own surfaces.

    Painted explicitly (white fill, 1px outline, fixed radius) because a
    translucent popup cannot rely on styled or auto-filled backgrounds --
    both stay see-through on the glass. Children sit inside the layout
    margins so their square corners never poke past the rounded border.
    """

    def __init__(self, radius: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._radius = radius
        self.setFrameShape(QFrame.Shape.NoFrame)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.setBrush(QColor(theme.PAPER))
            painter.setPen(QColor(theme.BORDER))
            painter.drawRoundedRect(rect, self._radius, self._radius)
        finally:
            painter.end()


#: Radius of the floating side card: the app sidebar's own radius.
SIDE_CARD_RADIUS = 28
#: Radius of the floating right card: unified with the dashboard sheet
#: and the sidebar (28px) so both floating cards are twins.
CONTENT_CARD_RADIUS = 28
#: Radius of the cream outer box, concentric with the cards inside it:
#: outer = inner + padding = 28 + 12 = 40 (exact now both cards match).
OUTER_RADIUS = 40
#: Padding inside the side card, mirroring the sidebar's inset rhythm.
SIDE_CARD_PAD = 10
#: Padding inside the right card, mirroring the dashboard sheet's inset.
CONTENT_CARD_PAD = 12
#: Gap between the two floating cards.
CARD_GAP = 12
#: Cream frame around the two cards: the outer box is the staff page bg.
OUTER_PAD = 12


class _PanesCompat:
    """Keeps ``pop.panes.currentWidget()`` working without a stacked widget."""

    def __init__(self) -> None:
        self._current: QWidget | None = None

    def addWidget(self, _widget: QWidget) -> None:
        return None

    def setCurrentWidget(self, widget: QWidget) -> None:
        self._current = widget

    def currentWidget(self) -> QWidget | None:
        return self._current


# -- .filter-dropdown ------------------------------------------------------------
#
# Outer wrapper (.filter-dropdown): the trigger button (.filter-trigger) lives
# on the page's pinned bar and toggles this panel (.filter-panel).


class FilterPopover(QFrame):
    filters_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(theme.OBJ_FILTER_POPOVER)
        self.setProperty("class", theme.OBJ_CARD)
        self.setFixedSize(FILTER_POPOVER_W, FILTER_POPOVER_H)
        # The popup window itself paints nothing (transparent): the two
        # floating cards paint their own rounded frames. Frameless keeps the
        # native frame off; Popup keeps dismiss behavior (callers re-assert
        # flags, see new_rental._build_popover).
        self.setWindowFlags(
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._suspend = 0
        self._touched: str | None = None
        self._muted: set[str] = set()
        self._min_rate_saved: int | None = None
        self._max_rate_saved: int | None = None
        self._syncing_nav = False

        # -- .filter-panel: cream box holding two floating cards ---------
        # The outer box is the staff page bg (cream); both cards float at
        # the unified 28px sheet radius (sidebar + dashboard sheet twins).
        root = QHBoxLayout(self)
        root.setContentsMargins(OUTER_PAD, OUTER_PAD, OUTER_PAD, OUTER_PAD)
        root.setSpacing(CARD_GAP)

        # -- .filter-side: floating sidebar-like card --------------------
        self._side_card = _PanelCard(SIDE_CARD_RADIUS, self)
        self._side_card.setObjectName(theme.OBJ_FILTER_SIDE_CARD)
        self._side_card.setFixedWidth(POPOVER_CATEGORY_W + SIDE_CARD_PAD * 2)
        side_layout = QVBoxLayout(self._side_card)
        side_layout.setContentsMargins(
            SIDE_CARD_PAD, SIDE_CARD_PAD, SIDE_CARD_PAD, SIDE_CARD_PAD
        )
        side_layout.setSpacing(0)

        self._category_scroll = QScrollArea(self._side_card)
        self._category_scroll.setObjectName("filterCategoryScroll")
        self._category_scroll.setFixedWidth(POPOVER_CATEGORY_W)
        self._category_scroll.setWidgetResizable(True)
        self._category_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._category_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._category_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._category_host = QWidget()
        self._category_host.setObjectName("filterCategoryHost")
        self._category_layout = QVBoxLayout(self._category_host)
        self._category_layout.setContentsMargins(0, 0, 0, 0)
        self._category_layout.setSpacing(0)
        self._category_scroll.setWidget(self._category_host)
        side_layout.addWidget(self._category_scroll, 1)
        root.addWidget(self._side_card)

        # -- right card: floating dashboard-sheet-like panel -------------
        self._content_card = _PanelCard(CONTENT_CARD_RADIUS, self)
        self._content_card.setObjectName(theme.OBJ_FILTER_CONTENT_CARD)
        content_card_layout = QVBoxLayout(self._content_card)
        content_card_layout.setContentsMargins(
            CONTENT_CARD_PAD, CONTENT_CARD_PAD, CONTENT_CARD_PAD, CONTENT_CARD_PAD
        )
        content_card_layout.setSpacing(0)

        # -- .filter-content: one continuous scroll, no chrome ------------
        self._content_scroll = QScrollArea(self._content_card)
        self._content_scroll.setObjectName("filterPaneScroll")
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._content_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._content_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        # Legacy alias.
        self._pane_scroll = self._content_scroll
        content_card_layout.addWidget(self._content_scroll, 1)

        self._content_host = QWidget()
        self._content_host.setObjectName(theme.OBJ_FILTER_CONTENT)
        self._content_layout = QVBoxLayout(self._content_host)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        self._content_scroll.setWidget(self._content_host)

        # Compat only: tracks which section the nav points at.
        self.panes = _PanesCompat()
        self._panes: dict[str, QWidget] = {}

        self.brand_pane = _BrandPane()
        self.type_group = _CardGroup(
            "Car type",
            [(value, VEHICLE_CLASS_LABELS[value]) for value in VEHICLE_CLASSES],
            with_thumbs=True,
        )
        self.cc_group = _CardGroup(
            "Motor CC", [(label, label) for label, _, _ in CC_BUCKETS]
        )
        self.price_group = _PriceGroup()
        self.seat_group = _SeatGroup()
        self.transmission_group = _CardGroup(
            "Transmission type", [(value, value) for value in TRANSMISSIONS]
        )
        self.fuel_group = _CardGroup(
            "Fuel type", [(value, value) for value in FUEL_TYPES]
        )

        self._sections: dict[str, QWidget] = {
            "makes": self.brand_pane,
            "vehicle_classes": self.type_group,
            "cc_buckets": self.cc_group,
            "price": self.price_group,
            "seats": self.seat_group,
            "transmissions": self.transmission_group,
            "fuel_types": self.fuel_group,
        }
        ordered_keys = [key for key, _title, _icon in _CATEGORY_SPECS]
        for index, key in enumerate(ordered_keys):
            widget = self._sections[key]
            self._content_layout.addWidget(widget)
            self._panes[key] = widget
            self.panes.addWidget(widget)
            # Separator line between sections, per the spec.
            if index < len(ordered_keys) - 1:
                rule = QFrame(self._content_host)
                rule.setObjectName("filterPopoverDivider")
                rule.setFrameShape(QFrame.Shape.NoFrame)
                rule.setFixedHeight(1)
                self._content_layout.addWidget(rule)
        self._content_layout.addStretch(1)

        self._axis_widgets: dict[str, QWidget] = {
            "makes": self.brand_pane,
            "vehicle_classes": self.type_group,
            "cc_buckets": self.cc_group,
            "seats": self.seat_group,
            "transmissions": self.transmission_group,
            "fuel_types": self.fuel_group,
        }
        self._check_groups: dict[str, _CardGroup] = {
            "vehicle_classes": self.type_group,
            "cc_buckets": self.cc_group,
            "transmissions": self.transmission_group,
            "fuel_types": self.fuel_group,
        }

        # -- .filter-actions: pinned to the bottom of the right card ----
        self._footer_rule = QFrame(self._content_card)
        self._footer_rule.setObjectName("filterPopoverDivider")
        self._footer_rule.setFrameShape(QFrame.Shape.NoFrame)
        self._footer_rule.setFixedHeight(1)
        content_card_layout.addWidget(self._footer_rule)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 10, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)
        self.clear_btn = QPushButton("Clear", self._content_card)
        self.clear_btn.setObjectName(theme.OBJ_FILTER_CLEAR)
        self.clear_btn.setFixedHeight(FILTER_ROW_H)
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn = QPushButton("Apply", self._content_card)
        self.apply_btn.setObjectName(theme.OBJ_FILTER_APPLY)
        self.apply_btn.setFixedHeight(FILTER_ROW_H)
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.setDefault(True)
        footer.addWidget(self.clear_btn)
        footer.addWidget(self.apply_btn)
        content_card_layout.addLayout(footer)
        root.addWidget(self._content_card, 1)

        self._build_categories()
        self._connect_controls()
        self.panes.setCurrentWidget(self.brand_pane)
        self._content_scroll.verticalScrollBar().valueChanged.connect(
            self._on_content_scrolled
        )
        self.clear_btn.clicked.connect(self.clear)
        self.apply_btn.clicked.connect(self.apply)

    # -- .filter-side ---------------------------------------------------------

    def _build_categories(self) -> None:
        self._categories: dict[str, ActiveRow] = {}
        for index, (key, title, icon_name) in enumerate(_CATEGORY_SPECS):
            row = ActiveRow(title, parent=self._category_host)
            row.setFixedHeight(SECTION_ROW_H)
            row.setToolTip(title)
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.set_glyph(
                _nav_icon(icon_name, theme.MUTED),
                _nav_icon(icon_name, theme.INK),
                gap=8,
            )
            row.set_reserve(16)
            row.clicked.connect(
                lambda _checked=False, k=key: self._show_category(k)
            )
            self._categories[key] = row
            self._category_layout.addWidget(row)
            if index < len(_CATEGORY_SPECS) - 1:
                rule = QFrame(self._category_host)
                rule.setObjectName("filterPopoverDivider")
                rule.setFrameShape(QFrame.Shape.NoFrame)
                rule.setFixedHeight(1)
                self._category_layout.addWidget(rule)
        self._category_layout.addStretch(1)
        self._categories["makes"].set_active(True)

    def _connect_controls(self) -> None:
        for axis, group in self._check_groups.items():
            for card in group.boxes.values():
                card.toggled.connect(
                    lambda _on, a=axis: self._control_changed(a)
                )
        self.brand_pane.brand_toggled.connect(
            lambda _name: self._control_changed("makes")
        )
        self.seat_group.seats_changed.connect(lambda: self._control_changed("seats"))
        self.price_group.min_rate.valueChanged.connect(
            lambda _value: self._rate_edited()
        )
        self.price_group.max_rate.valueChanged.connect(
            lambda _value: self._rate_edited()
        )

    def _show_category(self, key: str) -> None:
        """Nav click scrolls the content to that section (spec behaviour)."""
        pane = self._panes.get(key)
        if pane is not None:
            self.panes.setCurrentWidget(pane)
            if not self._syncing_nav:
                bar = self._content_scroll.verticalScrollBar()
                bar.setValue(max(0, pane.pos().y()))
        for name, row in self._categories.items():
            row.set_active(name == key)

    def _on_content_scrolled(self, _value: int) -> None:
        """Content scroll moves the side highlight (spec behaviour)."""
        y = self._content_scroll.verticalScrollBar().value()
        current: str | None = None
        for key, _title, _icon in _CATEGORY_SPECS:
            widget = self._sections.get(key)
            if widget is None:
                continue
            if widget.pos().y() <= y + 4:
                current = key
        if current is None:
            return
        if self.current_category() == current:
            return
        self._syncing_nav = True
        try:
            for name, row in self._categories.items():
                row.set_active(name == current)
            pane = self._panes.get(current)
            if pane is not None:
                self.panes.setCurrentWidget(pane)
        finally:
            self._syncing_nav = False

    def paintEvent(self, event) -> None:  # noqa: N802
        # Cream container (the staff page bg): a translucent popup fills
        # nothing by itself, so the outer box is painted explicitly. The
        # side and right cards float white on top of it.
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.setBrush(QColor(theme.BG))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, OUTER_RADIUS, OUTER_RADIUS)
        finally:
            painter.end()

    def category_titles(self) -> list[str]:
        return [title for _key, title, _icon in _CATEGORY_SPECS]

    def current_category(self) -> str:
        for key, row in self._categories.items():
            if row.is_active():
                return key
        return "makes"

    # -- state ------------------------------------------------------------------

    def set_brands(self, brands: Sequence[str]) -> None:
        self._suspend += 1
        try:
            self.brand_pane.set_brands(brands)
        finally:
            self._suspend -= 1

    def set_brand_photos(self, photos: dict[str, QPixmap]) -> None:
        self.brand_pane.set_photos(photos)

    def set_type_photos(self, photos: dict[str, QPixmap]) -> None:
        self.type_group.set_photos(photos)

    def brand_row(self, name: str) -> _BrandRow:
        return self.brand_pane.rows[name]

    def visible_brands(self) -> list[str]:
        return self.brand_pane.visible_brands()

    def set_active_section(self, brand: str | None) -> None:
        # Tracked for callers, but never painted: the right panel carries
        # selection only, no highlight.
        for name, row in self.brand_pane.rows.items():
            row.set_active(name == brand)

    def current_filter(self) -> VehicleFilter:
        low = self.price_group.min_rate.value()
        high = self.price_group.max_rate.value()
        min_rate = None if low == 0 else low
        max_rate = None if high == 0 else high
        if min_rate is not None and max_rate is not None and min_rate > max_rate:
            min_rate, max_rate = max_rate, min_rate
        seats_value = self.seat_group.value()
        return VehicleFilter(
            makes=frozenset(self.brand_pane.selected()),
            vehicle_classes=frozenset(self.type_group.selected()),
            cc_buckets=frozenset(self.cc_group.selected()),
            min_rate=min_rate,
            max_rate=max_rate,
            seats=frozenset({seats_value}) if seats_value is not None else frozenset(),
            transmissions=frozenset(self.transmission_group.selected()),
            fuel_types=frozenset(self.fuel_group.selected()),
        )

    def last_touched_axis(self) -> str | None:
        return self._touched

    def clear(self) -> None:
        self._suspend += 1
        try:
            for group in self._check_groups.values():
                group.clear()
            self.brand_pane.clear()
            self.seat_group.clear()
            self.price_group.clear()
            self._min_rate_saved = None
            self._max_rate_saved = None
            self._reset_availability()
            self._touched = None
        finally:
            self._suspend -= 1
        self.filters_changed.emit(self.current_filter())

    def apply(self) -> VehicleFilter:
        """Explicit commit point for the pinned Apply button."""
        current = self.current_filter()
        self.filters_changed.emit(current)
        return current

    # -- availability -----------------------------------------------------------

    def set_availability(
        self,
        availability: dict,
        *,
        rate_high: int | None = None,
        exempt: str | None = None,
    ) -> None:
        before = self.current_filter()
        self._suspend += 1
        try:
            for axis, values in availability.items():
                widget = self._axis_widgets.get(axis)
                if widget is None:
                    continue
                if not values and axis != exempt:
                    self._muted.add(axis)
                    widget.setEnabled(False)
                    self._set_axis_muted(axis, True)
                    self._set_axis_boxes(axis, set())
                    continue
                self._muted.discard(axis)
                widget.setEnabled(True)
                self._set_axis_muted(axis, False)
                self._set_axis_boxes(axis, values or None)
            if exempt != "min_rate":
                self._apply_rate_ceiling(
                    rate_high if rate_high is not None else MAX_RATE
                )
        finally:
            self._suspend -= 1
        after = self.current_filter()
        if not self._suspend and after != before:
            self.filters_changed.emit(after)

    def clear_availability(self) -> None:
        before = self.current_filter()
        self._suspend += 1
        try:
            self._reset_availability()
        finally:
            self._suspend -= 1
        after = self.current_filter()
        if not self._suspend and after != before:
            self.filters_changed.emit(after)

    def muted_axes(self) -> list[str]:
        return sorted(self._muted)

    def _reset_availability(self) -> None:
        self._muted = set()
        for axis, widget in self._axis_widgets.items():
            widget.setEnabled(True)
            self._set_axis_muted(axis, False)
            self._set_axis_boxes(axis, None)
        self.price_group.setEnabled(True)
        self._apply_rate_ceiling(MAX_RATE)

    def _set_axis_muted(self, axis: str, muted: bool) -> None:
        """Explain a greyed axis on every part of it a pointer can land on."""
        widget = self._axis_widgets.get(axis)
        if widget is None:
            return
        tip = NO_FILTER_MATCH_TOOLTIP if muted else ""
        widget.setToolTip(tip)
        heading = getattr(widget, "heading", None)
        if heading is not None:
            heading.setToolTip(tip)
        if axis == "makes":
            self.brand_pane.search.setToolTip(tip)
        elif axis == "seats":
            for child in (
                self.seat_group.minus,
                self.seat_group.value_label,
                self.seat_group.plus,
            ):
                child.setToolTip(tip)

    def _set_axis_boxes(self, axis: str, values: set | None) -> None:
        if axis == "makes":
            self.brand_pane.set_boxes_enabled(
                None if values is None else set(values)
            )
            return
        if axis == "seats":
            for key, proxy in self.seat_group.boxes.items():
                proxy.setEnabled(values is None or int(key) in values)
            return
        group = self._check_groups.get(axis)
        if group is None:
            return
        group.set_boxes_enabled(None if values is None else set(values))

    def _apply_rate_ceiling(self, ceiling: int) -> None:
        min_spin = self.price_group.min_rate
        max_spin = self.price_group.max_rate

        for spin, attr in (
            (min_spin, "_min_rate_saved"),
            (max_spin, "_max_rate_saved"),
        ):
            saved = getattr(self, attr)
            if saved is not None and saved <= ceiling:
                spin.setMaximum(MAX_RATE)
                spin.setValue(saved)
                setattr(self, attr, None)

        before_min, before_max = min_spin.value(), max_spin.value()
        min_spin.setMaximum(ceiling)
        max_spin.setMaximum(ceiling)
        if min_spin.value() < before_min and self._min_rate_saved is None:
            self._min_rate_saved = before_min
        if max_spin.value() < before_max and self._max_rate_saved is None:
            self._max_rate_saved = before_max

    # -- publishing -------------------------------------------------------------

    def _control_changed(self, axis: str) -> None:
        if self._suspend:
            return
        self._touched = axis
        self.filters_changed.emit(self.current_filter())

    def _rate_edited(self) -> None:
        if self._suspend:
            return
        # A clerk's own entry cancels any bound the ceiling was holding down:
        # whatever they typed is newer than what was clamped away.
        self._min_rate_saved = None
        self._max_rate_saved = None
        self._touched = "min_rate"
        self.filters_changed.emit(self.current_filter())
