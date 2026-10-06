from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
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
from app.staff import theme
from app.staff.metrics import (
    FILTER_GAP,
    FILTER_POPOVER_H,
    FILTER_POPOVER_PAD,
    FILTER_POPOVER_W,
    FILTER_ROW_H,
    MAX_RATE,
    POPOVER_CATEGORY_W,
    SECTION_ROW_H,
)
from app.staff.section_row import ActiveRow, paint_active_row

#: The right-hand options column starts a little clear of the 1px divider.
PANE_PAD_LEFT = 14

#: The thin rule between brand rows, matching the QSS's divider colour.
ROW_RULE = QColor("#E4DCD0")

#: The seven categories, as (stack key, label). Order is the contract: the test
#: asserts the list, and it reads down the axis the brief asked for.
_CATEGORY_SPECS: tuple[tuple[str, str], ...] = (
    ("makes", "Brand"),
    ("vehicle_classes", "Vehicle type"),
    ("cc_buckets", "Engine size"),
    ("price", "Price range"),
    ("seats", "Passengers"),
    ("transmissions", "Transmission"),
    ("fuel_types", "Fuel type"),
)

#: Axis keys whose options are a plain grid of checkboxes, in the order the
#: page's availability dict answers them.
_CHECK_AXES: tuple[str, ...] = (
    "makes",
    "vehicle_classes",
    "cc_buckets",
    "seats",
    "transmissions",
    "fuel_types",
)


class _BrandRow(ActiveRow):
    def __init__(self, label: str, box: QCheckBox, parent: QWidget | None = None) -> None:
        super().__init__(label, parent=parent)
        self.setAutoExclusive(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"Toggle {label}")
        self.setFixedHeight(FILTER_ROW_H)
        self._box = box

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 10, 0)
        layout.setSpacing(0)
        layout.addWidget(
            box,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

    def nextCheckState(self) -> None:  # noqa: N802
        self._box.setChecked(not self._box.isChecked())

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            paint_active_row(
                painter,
                self.rect(),
                active=False,
                hovered=self._hovered or self.hasFocus(),
                label=self._label_text,
            )
            painter.fillRect(
                16, self.height() - 1, max(0, self.width() - 32), 1, ROW_RULE
            )
        finally:
            painter.end()


class _BrandPane(QWidget):
    #: Emitted with a brand name whenever its tick changes.
    brand_toggled = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("filterPane")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(PANE_PAD_LEFT, 10, 10, 10)
        layout.setSpacing(FILTER_GAP)

        self.search = QLineEdit(self)
        self.search.setObjectName(theme.OBJ_SEARCH_FIELD)
        self.search.setPlaceholderText("Search makes")
        self.search.setFixedHeight(FILTER_ROW_H)
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)

        # No scroll of its own: the whole right column is one scroll region, so
        # the brand list runs on with it instead of ending at an inner edge.
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(0)
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

        for row in self.rows.values():
            self._rows_layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self.rows = {}
        self.boxes = {}
        self.order = brands

        insert_at = self._rows_layout.count() - 1
        for brand in brands:
            box = QCheckBox(self)
            box.setObjectName(theme.OBJ_FILTER_CHECK)
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

        self._apply_needle()

    def selected(self) -> set[str]:
        return {name for name, box in self.boxes.items() if box.isChecked()}

    def clear(self) -> None:
        for box in self.boxes.values():
            box.setChecked(False)

    def set_boxes_enabled(self, values: set[str] | None) -> None:
        for name, box in self.boxes.items():
            box.setEnabled(values is None or name in values)

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


class _CheckGroup(QWidget):
    def __init__(
        self,
        heading: str,
        items: Sequence[tuple[str, str]],
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

        self.boxes: dict[str, QCheckBox] = {}
        for key, label in items:
            box = QCheckBox(label, self)
            box.setObjectName(theme.OBJ_FILTER_CHECK)
            box.setFixedHeight(FILTER_ROW_H)
            self.boxes[key] = box
            layout.addWidget(box)

    def selected(self) -> set[str]:
        return {key for key, box in self.boxes.items() if box.isChecked()}

    def clear(self) -> None:
        for box in self.boxes.values():
            box.setChecked(False)

    def set_boxes_enabled(self, values: set | None) -> None:
        for key, box in self.boxes.items():
            box.setEnabled(values is None or key in values)


class _PriceGroup(QWidget):
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
        between = QLabel("to")
        between.setObjectName(theme.OBJ_FILTER_GROUP_HEADING)
        between.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(between)
        row.addWidget(self.max_rate, 1)
        layout.addLayout(row)

    @staticmethod
    def _spin() -> QSpinBox:
        spin = QSpinBox()
        spin.setObjectName(theme.OBJ_FILTER_SPIN)
        spin.setRange(0, MAX_RATE)
        spin.setSpecialValueText("Any")
        spin.setPrefix("\u20b1 ")
        return spin

    def clear(self) -> None:
        self.min_rate.setMaximum(MAX_RATE)
        self.max_rate.setMaximum(MAX_RATE)
        self.min_rate.setValue(0)
        self.max_rate.setValue(0)


class FilterPopover(QFrame):
    filters_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(theme.OBJ_FILTER_POPOVER)
        self.setProperty("class", theme.OBJ_CARD)
        self.setFixedSize(FILTER_POPOVER_W, FILTER_POPOVER_H)

        self._suspend = 0
        self._touched: str | None = None
        self._muted: set[str] = set()
        self._min_rate_saved: int | None = None
        self._max_rate_saved: int | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(*FILTER_POPOVER_PAD)
        root.setSpacing(0)

        self._category_scroll = QScrollArea(self)
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
        root.addWidget(self._category_scroll)

        self._divider = QFrame(self)
        self._divider.setObjectName("filterPopoverDivider")
        self._divider.setFrameShape(QFrame.Shape.NoFrame)
        self._divider.setFixedWidth(1)
        root.addWidget(self._divider)

        # One scroll region for the entire right column: the wheel works
        # anywhere over it and the content runs on as a single surface.
        self._pane_scroll = QScrollArea(self)
        self._pane_scroll.setObjectName("filterPaneScroll")
        self._pane_scroll.setWidgetResizable(True)
        self._pane_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._pane_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        root.addWidget(self._pane_scroll, 1)

        self.panes = QStackedWidget(self)
        self.panes.setObjectName("filterPanes")
        self._pane_scroll.setWidget(self.panes)

        self.brand_pane = _BrandPane()
        self.type_group = _CheckGroup(
            "Vehicle type",
            [(value, VEHICLE_CLASS_LABELS[value]) for value in VEHICLE_CLASSES],
        )
        self.cc_group = _CheckGroup(
            "Engine size", [(label, label) for label, _, _ in CC_BUCKETS]
        )
        self.seat_group = _CheckGroup(
            "Passengers", [(str(n), str(n)) for n in SEAT_COUNTS]
        )
        self.transmission_group = _CheckGroup(
            "Transmission", [(value, value) for value in TRANSMISSIONS]
        )
        self.fuel_group = _CheckGroup(
            "Fuel type", [(value, value) for value in FUEL_TYPES]
        )
        self.price_group = _PriceGroup()

        self._panes: dict[str, QWidget] = {
            "makes": self.brand_pane,
            "vehicle_classes": self.type_group,
            "cc_buckets": self.cc_group,
            "price": self.price_group,
            "seats": self.seat_group,
            "transmissions": self.transmission_group,
            "fuel_types": self.fuel_group,
        }
        for pane in (
            self.brand_pane,
            self.type_group,
            self.cc_group,
            self.seat_group,
            self.transmission_group,
            self.fuel_group,
            self.price_group,
        ):
            self.panes.addWidget(pane)

        self._axis_widgets: dict[str, QWidget] = {
            "makes": self.brand_pane,
            "vehicle_classes": self.type_group,
            "cc_buckets": self.cc_group,
            "seats": self.seat_group,
            "transmissions": self.transmission_group,
            "fuel_types": self.fuel_group,
        }
        self._check_groups: dict[str, _CheckGroup] = {
            "vehicle_classes": self.type_group,
            "cc_buckets": self.cc_group,
            "seats": self.seat_group,
            "transmissions": self.transmission_group,
            "fuel_types": self.fuel_group,
        }

        self._build_categories()
        self._connect_controls()
        self.panes.setCurrentWidget(self.brand_pane)

    # -- structure ----------------------------------------------------------

    def _build_categories(self) -> None:
        self._categories: dict[str, ActiveRow] = {}
        for index, (key, title) in enumerate(_CATEGORY_SPECS):
            row = ActiveRow(title, parent=self._category_host)
            row.setFixedHeight(SECTION_ROW_H)
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.clicked.connect(
                lambda _checked=False, k=key: self._show_category(k)
            )
            self._categories[key] = row
            self._category_layout.addWidget(row)
            # A rule between categories, not after the last one: the seven rows
            # are one list of axes, and the line is what keeps them reading as
            # separate choices rather than one long menu.
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
            for box in group.boxes.values():
                box.toggled.connect(
                    lambda _on, a=axis: self._control_changed(a)
                )
        self.brand_pane.brand_toggled.connect(
            lambda _name: self._control_changed("makes")
        )
        self.price_group.min_rate.valueChanged.connect(
            lambda _value: self._rate_edited()
        )
        self.price_group.max_rate.valueChanged.connect(
            lambda _value: self._rate_edited()
        )

    def _show_category(self, key: str) -> None:
        pane = self._panes.get(key)
        if pane is not None:
            self.panes.setCurrentWidget(pane)
            # A category opens at its top: carrying the previous one's offset
            # would land the clerk halfway down a list they have not read.
            self._pane_scroll.verticalScrollBar().setValue(0)

    def category_titles(self) -> list[str]:
        return [title for _key, title in _CATEGORY_SPECS]

    def current_category(self) -> str:
        for key, row in self._categories.items():
            if row.is_active():
                return key
        return "makes"

    # -- state --------------------------------------------------------------

    def set_brands(self, brands: Sequence[str]) -> None:
        self._suspend += 1
        try:
            self.brand_pane.set_brands(brands)
        finally:
            self._suspend -= 1

    def brand_row(self, name: str) -> _BrandRow:
        return self.brand_pane.rows[name]

    def visible_brands(self) -> list[str]:
        return self.brand_pane.visible_brands()

    def set_active_section(self, brand: str | None) -> None:
        for name, row in self.brand_pane.rows.items():
            row.set_active(name == brand)

    def current_filter(self) -> VehicleFilter:
        low = self.price_group.min_rate.value()
        high = self.price_group.max_rate.value()
        min_rate = None if low == 0 else low
        max_rate = None if high == 0 else high
        if min_rate is not None and max_rate is not None and min_rate > max_rate:
            min_rate, max_rate = max_rate, min_rate
        return VehicleFilter(
            makes=frozenset(self.brand_pane.selected()),
            vehicle_classes=frozenset(self.type_group.selected()),
            cc_buckets=frozenset(self.cc_group.selected()),
            min_rate=min_rate,
            max_rate=max_rate,
            seats=frozenset(int(value) for value in self.seat_group.selected()),
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
            self.price_group.clear()
            self._min_rate_saved = None
            self._max_rate_saved = None
            self._reset_availability()
            self._touched = None
        finally:
            self._suspend -= 1
        self.filters_changed.emit(self.current_filter())

    # -- availability -------------------------------------------------------

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
                    continue
                self._muted.discard(axis)
                widget.setEnabled(True)
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
            self._set_axis_boxes(axis, None)
        self.price_group.setEnabled(True)
        self._apply_rate_ceiling(MAX_RATE)

    def _set_axis_boxes(self, axis: str, values: set | None) -> None:
        if axis == "makes":
            self.brand_pane.set_boxes_enabled(
                None if values is None else set(values)
            )
            return
        group = self._check_groups.get(axis)
        if group is None:
            return
        convert = int if axis == "seats" else str
        for key, box in group.boxes.items():
            box.setEnabled(values is None or convert(key) in values)

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

    # -- publishing ---------------------------------------------------------

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
