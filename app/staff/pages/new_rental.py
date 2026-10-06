from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from app.services.vehicle_service import (
    VehicleFilter,
    axis_availability,
    count_filtered_vehicles,
    rate_bounds,
    showcase_vehicle_rows,
    vehicle_brands,
)
from app.staff import theme
from app.staff.brand_rows import BrandSection
from app.staff.feedback import describe
from app.staff.filter_popover import FilterPopover
from app.staff.metrics import (
    BRAND_ROW_GAP,
    FILTER_BAR_H,
    FILTER_GAP,
    FILTER_ROW_H,
    LOAD_MORE_PX,
    SECTION_TRACK_MS,
)
from app.staff.pages.base import StaffPage
from app.staff.vehicle_cards import MediaResolver, to_rental_vehicle

#: Brands appended per scroll gesture. Not a page break -- brands are appended as
#: the list is scrolled, so this only sets how many queries one gesture makes.
BRANDS_PER_PAGE = 4

#: Quiet period after a filter change before the availability question is asked.
#: Long enough that clicking three boxes in a row is one set of queries, short
#: enough that the greying still feels attached to the click.
AVAILABILITY_DEBOUNCE_MS = 90


class NewRentalPage(StaffPage):
    PANEL = True
    HEADER = False

    #: Emitted with the brand currently at the top of the viewport.
    section_changed = Signal(str)

    def __init__(self, shell) -> None:
        super().__init__(shell, "New Rental", "Write a rental for a walk-in customer.")

        self._pool = QThreadPool(self)
        self._brands: list[str] = []
        self._offset = 0
        self._exhausted = False
        self._loading = False
        self._media_requested: set[int] = set()
        self._pending_media: list[int] = []
        self._resolvers: list[MediaResolver] = []
        self._sections: list[BrandSection] = []
        self._current_section: str | None = None
        self._filters = VehicleFilter()
        self._brand_total = 0
        self._matched = 0
        self._total = 0

        self._build_filter_bar()
        self._build_popover()

        # -- fleet list ----------------------------------------------------
        self._rows = QWidget(self._content)
        self._rows.setObjectName("brandRails")
        self._rows_layout = QVBoxLayout(self._rows)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(BRAND_ROW_GAP)
        self._rows_layout.addStretch(1)
        self.body.addWidget(self._rows, 1)

        self._empty = QLabel("No vehicles match these filters.", self._content)
        self._empty.setObjectName("nrEmpty")
        self._empty.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        self._empty.setVisible(False)
        self.body.addWidget(self._empty, 1)

        # -- scrolling ----------------------------------------------------
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(SECTION_TRACK_MS)
        self._settle_timer.timeout.connect(self._track_section)

        self._availability_timer = QTimer(self)
        self._availability_timer.setSingleShot(True)
        self._availability_timer.setInterval(AVAILABILITY_DEBOUNCE_MS)
        self._availability_timer.timeout.connect(self._refresh_availability)

    # -- the pinned bar and the popover -------------------------------------

    def _build_filter_bar(self) -> None:
        bar = self.pinned_bar

        self.search = QLineEdit(bar)
        self.search.setObjectName(theme.OBJ_SEARCH_FIELD)
        self.search.setPlaceholderText("Search make, model, or plate")
        self.search.setFixedHeight(FILTER_BAR_H)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search_changed)
        self._pinned_layout.addWidget(self.search)

        self.filters_button = QPushButton("Filters", bar)
        self.filters_button.setObjectName(theme.OBJ_FILTER_BUTTON)
        self.filters_button.setFixedHeight(FILTER_BAR_H)
        self.filters_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.filters_button.setToolTip("Filter the fleet")
        self.filters_button.clicked.connect(self.toggle_popover)
        self._pinned_layout.addWidget(
            self.filters_button, 0, Qt.AlignmentFlag.AlignVCenter
        )

        self.count_label = QLabel("", bar)
        self.count_label.setObjectName("nrCount")
        self._pinned_layout.addWidget(self.count_label)
        sp = QSpacerItem(24, 10, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        self._pinned_layout.addItem(sp)

        self.clear_button = QPushButton("Clear all", bar)
        self.clear_button.setObjectName("filterClear")
        self.clear_button.setFixedHeight(FILTER_ROW_H)
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.clicked.connect(self.clear_filters)
        self._pinned_layout.addWidget(self.clear_button)

        self.show_pinned_bar(FILTER_BAR_H)

    def _build_popover(self) -> None:
        self.filter_popover = FilterPopover(self)
        self.filter_popover.setWindowFlags(Qt.WindowType.Popup)
        self.filter_popover.filters_changed.connect(self._on_filters_changed)
        # Ticking a box inside a `Qt.Popup` otherwise closes it on the mouse
        # release that delivered the click, which would make every single
        # adjustment dismiss the panel.
        self.filter_popover.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def toggle_popover(self) -> None:
        if self.filter_popover.isVisible():
            self.close_popover()
        else:
            self.open_popover()

    def open_popover(self) -> None:
        anchor = self.filters_button.mapToGlobal(
            QPoint(0, self.filters_button.height())
        )
        screen = self.window().screen() if self.window() else None
        available = (
            screen.availableGeometry() if screen is not None else None
        )
        x, y = anchor.x(), anchor.y()
        if available is not None:
            if x + self.filter_popover.width() > available.right():
                x = max(available.left(), available.right() - self.filter_popover.width())
            # And downwards, which is the edge a short window runs into: the panel
            # is 420px tall and the fleet sheet is not always that deep.
            if y + self.filter_popover.height() > available.bottom():
                y = max(available.top(), available.bottom() - self.filter_popover.height())

        self.filter_popover.move(x, y)
        self.filter_popover.show()
        # The brand pane's own search takes the focus so a clerk can type a make
        # straight away, but only when the Brand column is the one open.
        if self.filter_popover.current_category() == "makes":
            self.filter_popover.brand_pane.search.setFocus()

    def close_popover(self) -> None:
        self.filter_popover.hide()

    # -- public state ------------------------------------------------------

    @property
    def slots(self) -> list:
        return [slot for section in self._sections for slot in section.slots]

    @property
    def brands_shown(self) -> list[str]:
        return [section.brand for section in self._sections]

    @property
    def current_section(self) -> str | None:
        return self._current_section

    @property
    def filters(self) -> VehicleFilter:
        return self._filters

    @property
    def matched(self) -> int:
        return self._matched

    def clear_filters(self) -> None:
        blocked = self.search.blockSignals(True)
        try:
            self.search.clear()
            self.filter_popover.clear()
        finally:
            self.search.blockSignals(blocked)
        self._apply_filters(VehicleFilter(), force=True)

    # -- data --------------------------------------------------------------

    def refresh(self) -> None:
        super().refresh()
        with self.context.reading() as session:
            self._brands = vehicle_brands(session)
            self._brand_total = len(self._brands)
        self._clear_sections()
        self.filter_popover.set_brands(self._brands)
        self._offset = 0
        self._exhausted = False
        self._media_requested = set()
        self._current_section = None
        self.scroll_area.verticalScrollBar().setValue(0)
        self._apply_filters(self._filters, force=True)
        # The first page is laid out but its geometry is not final until the event
        # loop has run the layout, so the first section can only be read then.
        QTimer.singleShot(0, self._track_section)

    def _on_search_changed(self, _text: str) -> None:
        self._apply_filters(self._read_filter())

    def _on_filters_changed(self, _filters: VehicleFilter) -> None:
        self._apply_filters(self._read_filter())

    def _read_filter(self) -> VehicleFilter:
        filters = self.filter_popover.current_filter()
        needle = (self.search.text() or "").strip()
        return filters if not needle else VehicleFilter(
            makes=filters.makes,
            vehicle_classes=filters.vehicle_classes,
            cc_buckets=filters.cc_buckets,
            min_rate=filters.min_rate,
            max_rate=filters.max_rate,
            seats=filters.seats,
            transmissions=filters.transmissions,
            fuel_types=filters.fuel_types,
            search=needle,
        )

    def _apply_filters(
        self, filters: VehicleFilter, *, force: bool = False
    ) -> None:
        if not force and filters == self._filters:
            return
        self._filters = filters
        self._clear_sections()
        self._current_section = None
        self._offset = 0
        self._exhausted = False
        self.scroll_area.verticalScrollBar().setValue(0)
        self._load_page()
        self._update_count()
        # Debounced: see the note where the timer is built.
        self._availability_timer.start()

    def _refresh_availability(self) -> None:
        exempt = self.filter_popover.last_touched_axis()
        try:
            with self.context.reading() as session:
                reachable = axis_availability(
                    session, self._filters, exclude=exempt
                )
                _low, high = rate_bounds(
                    session, self._filters, exclude=exempt == "min_rate"
                )
        except Exception:  # noqa: BLE001
            # Availability is a refinement, not the answer. If it cannot be had,
            # every column is put back live: leaving the previous greys in place
            # would claim to know something after failing to find out.
            self.filter_popover.clear_availability()
            return
        self.filter_popover.set_availability(
            reachable, rate_high=high, exempt=exempt
        )

    def _load_page(self) -> None:
        if self._loading or self._exhausted:
            return
        self._loading = True
        try:
            page_brands = self._brands[self._offset : self._offset + BRANDS_PER_PAGE]
            if not page_brands:
                self._exhausted = True
                return

            with self.context.reading() as session:
                rows = showcase_vehicle_rows(
                    session,
                    category=None,
                    statuses=None,
                    makes=page_brands,
                    filters=self._filters,
                )
                grouped: dict[str, list] = {}
                for vehicle, url in rows:
                    grouped.setdefault(vehicle.make, []).append(
                        to_rental_vehicle(vehicle, url)
                    )
        finally:
            self._loading = False

        vehicles = []
        insert_at = self._rows_layout.count() - 1
        for brand in page_brands:
            brand_vehicles = grouped.get(brand, [])
            if not brand_vehicles:
                continue
            section = BrandSection(brand, brand_vehicles, self._pool, self._rows)
            section.setObjectName("brandSection")
            section.setProperty("brand", brand)
            self._rows_layout.insertWidget(insert_at, section)
            insert_at += 1
            self._sections.append(section)
            vehicles.extend(brand_vehicles)

        self._offset += len(page_brands)
        if self._offset >= len(self._brands):
            self._exhausted = True

        # Not re-set here: the list is the whole fleet and does not narrow as the
        # grid loads. Rebuilding it from the sections on screen would both empty
        # the axis on every filter change and drop brands the clerk can still see.
        self._request_media(vehicles)
        self._update_count()
        self._sync_empty_state()
        QTimer.singleShot(0, self._fill_viewport)
        QTimer.singleShot(0, self._track_section)

    def _fill_viewport(self) -> None:
        if self._loading or self._exhausted or self._list_overflows():
            return
        self._load_page()

    def scroll_to_section(self, brand: str) -> None:
        section = next((s for s in self._sections if s.brand == brand), None)
        if section is None:
            return

        bar = self.scroll_area.verticalScrollBar()
        offset = dict(self._section_offsets()).get(brand)
        if offset is None:
            return
        bar.setValue(max(0, self._rows.y() + offset))

        self._set_current_section(brand)

    def _set_current_section(self, brand: str | None) -> None:
        if brand == self._current_section:
            return
        self._current_section = brand
        self.filter_popover.set_active_section(brand)
        self.section_changed.emit(brand)

    def _clear_sections(self) -> None:
        for section in self._sections:
            self._rows_layout.removeWidget(section)
            section.setParent(None)
            section.deleteLater()
        self._sections = []

    # -- scrolling ---------------------------------------------------------

    def _on_scrolled(self, _value: int) -> None:
        bar = self.scroll_area.verticalScrollBar()
        if bar.maximum() - bar.value() <= LOAD_MORE_PX:
            self._load_page()

        self._settle_timer.start()

    def _section_offsets(self) -> list[tuple[str, int]]:
        offsets: list[tuple[str, int]] = []
        y = 0
        for section in self._sections:
            offsets.append((section.brand, y))
            y += section.sizeHint().height() + BRAND_ROW_GAP
        return offsets

    def _track_section(self) -> None:
        if not self._sections:
            return

        bar = self.scroll_area.verticalScrollBar()
        line = bar.value() - self._rows.y()

        current = None
        for brand, y in self._section_offsets():
            if y > line:
                break
            current = brand

        self._set_current_section(current)

    def _update_count(self) -> None:
        try:
            with self.context.reading() as session:
                matched = count_filtered_vehicles(session, self._filters)
                total = count_filtered_vehicles(session, VehicleFilter())
        except Exception as exc:  # noqa: BLE001
            message, _, _ = describe(exc)
            self.count_label.setText(f"Count unavailable: {message}")
            return

        self._matched = matched
        self._total = total
        if matched == total:
            self.count_label.setText(
                f"{total:,} vehicle{'s' if total != 1 else ''}"
            )
        else:
            self.count_label.setText(f"{matched:,} of {total:,} vehicles")
        self.clear_button.setEnabled(not self._filters.is_empty())

    def _list_overflows(self) -> bool:
        viewport = self.scroll_area.viewport().height()
        return viewport <= 0 or self._rows.sizeHint().height() > viewport

    def _sync_empty_state(self) -> None:
        empty = self._exhausted and not self._sections
        self._empty.setVisible(empty)
        self._rows.setVisible(not empty)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._flush_media()
        # Re-read the section in view against a real viewport. `refresh()` runs
        # before the page is shown, so at that point the viewport's height is
        # whatever the last layout left behind and the read is only provisional.
        QTimer.singleShot(0, self._track_section)

    def _flush_media(self) -> None:
        if not self._pending_media:
            return
        ids, self._pending_media = self._pending_media, []
        self._media_requested.update(ids)
        resolver = MediaResolver(self.context, ids)
        resolver.signals.resolved.connect(self._apply_photo_urls)
        self._resolvers.append(resolver)
        self._pool.start(resolver)

    def _request_media(self, vehicles) -> None:
        seen = self._media_requested
        queued = set(self._pending_media)
        for vehicle in vehicles:
            vid = vehicle.vehicle_id
            if vid not in seen and vid not in queued and vehicle.photo_url is None:
                self._pending_media.append(vid)

    def _apply_photo_urls(self, urls: dict[int, str]) -> None:
        for section in self._sections:
            section.set_photo_urls(urls)


def build_new_rental_page(shell) -> NewRentalPage:
    return NewRentalPage(shell)
