from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
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
from app.staff.vehicle_details import VehicleDetailsOverlay
from app.staff.widgets import toast
from app.staff.metrics import (
    BRAND_ROW_GAP,
    FILTER_BAR_H,
    FILTER_GAP,
    LOAD_MORE_PX,
    SECTION_TRACK_MS,
)
from app.staff.pages.base import StaffPage
from app.staff.vehicle_cards import MediaResolver, to_rental_vehicle
from app.staff.workers import media_pool

#: Brands appended per scroll gesture. Not a page break -- brands are appended as
#: the list is scrolled, so this only sets how many queries one gesture makes.
BRANDS_PER_PAGE = 4

#: Quiet period after a filter change before the availability question is asked.
#: Long enough that clicking three boxes in a row is one set of queries, short
#: enough that the greying still feels attached to the click.
AVAILABILITY_DEBOUNCE_MS = 90

#: How far past the viewport edges a brand starts building its cards, so a
#: scroll lands on cards that are already there rather than on grey slots.
SECTION_PRELOAD_PX = 600

#: Statuses New Rental shows. A car in the shop or retired cannot be booked;
#: one that is out or reserved now can still take a later date. The rails and
#: the count both read this tuple so they cannot disagree.
VISIBLE_STATUSES: tuple[str, ...] = ("available", "rented", "reserved")

#: Coalescing window for per-card media requests: a section coming into view
#: asks about each of its cards; this makes that one resolver run.
MEDIA_COALESCE_MS = 60


def popover_position(anchor: QPoint, size: QSize, available: QRect) -> QPoint:
    """Top-left for a popup of `size` opening at `anchor`, kept on-screen.

    The popup drops below the anchor and is pulled back inside `available` on
    every edge (`QRect` edges are inclusive, hence the +1). When the popup is
    bigger than the work area it is pinned to the top-left corner rather than
    hanging off both edges at once.
    """
    w = min(size.width(), available.width())
    h = min(size.height(), available.height())
    x = min(max(anchor.x(), available.left()), available.right() - w + 1)
    y = min(max(anchor.y(), available.top()), available.bottom() - h + 1)
    return QPoint(max(x, available.left()), max(y, available.top()))


class NewRentalPage(StaffPage):
    PANEL = True
    HEADER = False

    #: Emitted with the brand currently at the top of the viewport.
    section_changed = Signal(str)

    def __init__(self, shell) -> None:
        super().__init__(shell, "New Rental", "Write a rental for a walk-in customer.")

        self._pool = media_pool()
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
        #: Vehicle picked via a details dialog's Rent button (Phase 1 handoff
        #: for the future write-a-rental form), plus the checked window the
        #: pick was made against, if the checker ran.
        self._selected_vehicle_id: int | None = None
        self._selected_window = None
        self._details_overlay = None
        #: Customer id pushed from the Customers tab ("New rental"): the next
        #: wizard run pre-resolves Step 3 to them, checks included.
        self._pending_customer_id: int | None = None

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
        self._settle_timer.timeout.connect(self._update_visible_sections)

        self._media_timer = QTimer(self)
        self._media_timer.setSingleShot(True)
        self._media_timer.setInterval(MEDIA_COALESCE_MS)
        self._media_timer.timeout.connect(self._flush_media)

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

        self.filters_button = QPushButton("Filters \u25be", bar)
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
        self.clear_button.setFixedHeight(FILTER_BAR_H)
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.clicked.connect(self.clear_filters)
        self._pinned_layout.addWidget(self.clear_button)

        self.show_pinned_bar(FILTER_BAR_H)

    def _build_popover(self) -> None:
        self.filter_popover = FilterPopover(self)
        # setWindowFlags replaces: keep the popover's frameless hint so the
        # rounded card is the only frame (see FilterPopover.__init__).
        self.filter_popover.setWindowFlags(
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        )
        self.filter_popover.filters_changed.connect(self._on_filters_changed)
        # Ticking a box inside a `Qt.Popup` otherwise closes it on the mouse
        # release that delivered the click, which would make every single
        # adjustment dismiss the panel.
        self.filter_popover.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # Pinned Apply/Clear footer (filter-layout.md `.filter-actions`):
        # Apply re-publishes the draft (live preview already did) and closes;
        # Clear resets via the popover (which publishes once).
        self.filter_popover.apply_btn.clicked.connect(self.close_popover)

    def toggle_popover(self) -> None:
        if self.filter_popover.isVisible():
            self.close_popover()
        else:
            self.open_popover()

    def open_popover(self) -> None:
        popover = self.filter_popover
        anchor = self.filters_button.mapToGlobal(
            QPoint(0, self.filters_button.height())
        )
        # The screen under the anchor, not the window's: on multi-monitor or
        # mixed-DPI setups the window's screen can be a different display
        # with different geometry, which is how the panel ended up hanging
        # off the right edge it was clamped against.
        screen = QGuiApplication.screenAt(anchor)
        if screen is None and self.window() is not None:
            screen = self.window().screen()
        if screen is None:
            screen = QGuiApplication.primaryScreen()

        if screen is not None:
            popover.move(
                popover_position(
                    anchor, popover.size(), screen.availableGeometry()
                )
            )
        else:
            popover.move(anchor)
        popover.show()
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

    @property
    def selected_vehicle_id(self) -> int | None:
        return self._selected_vehicle_id

    @property
    def selected_window(self):
        return self._selected_window

    @property
    def pending_customer_id(self) -> int | None:
        return self._pending_customer_id

    def set_pending_customer(self, user_id: int) -> None:
        """Attach a customer from the Customers tab to the next wizard run."""
        self._pending_customer_id = user_id

    def _open_vehicle_details(self, vehicle_id: int) -> None:
        # Embedded overlay inside the staff window: no new OS window opens.
        if self._details_overlay is not None:
            self._details_overlay.hide_overlay()
            self._details_overlay.deleteLater()
            self._details_overlay = None
        window = self.window()
        overlay = VehicleDetailsOverlay(
            self.context, vehicle_id, window if window is not None else self
        )
        overlay.panel.rent_requested.connect(self._on_rent_requested)
        overlay.panel.close_requested.connect(overlay.hide_overlay)
        overlay.show_overlay()
        self._details_overlay = overlay

    def _on_rent_requested(self, vehicle_id: int) -> None:
        from PySide6.QtCore import QDate

        from app.staff.rental_wizard import RentalWizard

        self._selected_vehicle_id = vehicle_id
        overlay = self._details_overlay
        window = None
        if overlay is not None:
            self._selected_window = overlay.panel.checked_window
            window = overlay.panel.checked_window
        start, end = window if window is not None else (None, None)
        if start is None or end is None:
            today = QDate.currentDate()
            start, end = today, today.addDays(1)
        self._wizard = RentalWizard(
            self.context,
            overlay,
            vehicle_id,
            start,
            end,
            navigate=self.shell.show_page,
            notify=lambda message: toast(self, message, "ok"),
            customer_id=self._pending_customer_id,
        )
        self._pending_customer_id = None
        # The wizard owns the overlay from here: Escape and outside clicks
        # must ask before throwing the half-filled rental away.
        if overlay is not None:
            overlay.set_dismissible(False)
        self._wizard.start()

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
        # One-shot intent: a customer attached by the Customers tab is for
        # the next rental only. Leaving and coming back must not silently
        # attach them to a different car.
        self._pending_customer_id = None
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
                    # A rented or reserved car can still be booked for later
                    # dates; only the shop and retired cars leave this view.
                    # Fleet remains the all-status list.
                    statuses=VISIBLE_STATUSES,
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
            for slot in section.slots:
                slot.details_requested.connect(self._open_vehicle_details)
                slot.media_needed.connect(
                    lambda _vid, vehicle=slot.vehicle: self._on_slot_media(vehicle)
                )
            vehicles.extend(brand_vehicles)

        self._offset += len(page_brands)
        if self._offset >= len(self._brands):
            self._exhausted = True

        # Not re-set here: the list is the whole fleet and does not narrow as the
        # grid loads. Rebuilding it from the sections on screen would both empty
        # the axis on every filter change and drop brands the clerk can still see.
        self._update_filter_thumbs(vehicles)
        self._update_count()
        self._sync_empty_state()
        self._update_visible_sections()
        QTimer.singleShot(0, self._fill_viewport)
        QTimer.singleShot(0, self._track_section)

    def _update_filter_thumbs(self, vehicles) -> None:
        """Feed real fleet photos into the brand / car-type cards.

        Uses the synchronous image cache only (no network in the dropdown);
        a cache miss keeps the silhouette placeholder, which the media
        pipeline may fill on later pages.
        """
        try:
            from app.services import image_cache
        except Exception:  # noqa: BLE001
            return
        brand_pix: dict[str, QPixmap] = {}
        type_pix: dict[str, QPixmap] = {}
        for vehicle in vehicles:
            if vehicle.make in brand_pix and (
                not vehicle.vehicle_class or vehicle.vehicle_class in type_pix
            ):
                continue
            try:
                cached = image_cache.load(
                    vehicle.make, vehicle.model, vehicle.year
                )
            except Exception:  # noqa: BLE001
                cached = None
            if cached is None or not getattr(cached, "data", None):
                continue
            image = QImage()
            if not image.loadFromData(cached.data):
                continue
            pixmap = QPixmap.fromImage(image)
            if pixmap.isNull():
                continue
            brand_pix.setdefault(vehicle.make, pixmap)
            if vehicle.vehicle_class:
                type_pix.setdefault(vehicle.vehicle_class, pixmap)
        try:
            if brand_pix:
                self.filter_popover.set_brand_photos(brand_pix)
            if type_pix:
                self.filter_popover.set_type_photos(type_pix)
        except Exception:  # noqa: BLE001
            return

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
                matched = count_filtered_vehicles(
                    session, self._filters, statuses=VISIBLE_STATUSES
                )
                total = count_filtered_vehicles(
                    session, VehicleFilter(), statuses=VISIBLE_STATUSES
                )
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
        self._update_visible_sections()
        # Re-read the section in view against a real viewport. `refresh()` runs
        # before the page is shown, so at that point the viewport's height is
        # whatever the last layout left behind and the read is only provisional.
        QTimer.singleShot(0, self._track_section)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # A taller window brings more brands (and a wider one more cards per
        # rail) into view without any scroll signal.
        QTimer.singleShot(0, self._update_visible_sections)

    def _update_visible_sections(self) -> None:
        """Start the rails of every brand at (or just past) the viewport.

        A brand below the fold keeps its grey slots until it scrolls up, so a
        page-turn never resolves urls or downloads photos for cards nobody is
        looking at. The preload margin is what makes the scroll feel warm.
        """
        if not self._sections:
            return
        bar = self.scroll_area.verticalScrollBar()
        line = bar.value() - self._rows.y()
        top = line - SECTION_PRELOAD_PX
        bottom = line + self.scroll_area.viewport().height() + SECTION_PRELOAD_PX
        for section, (_brand, y) in zip(self._sections, self._section_offsets()):
            height = section.sizeHint().height() + BRAND_ROW_GAP
            if y + height >= top and y <= bottom:
                section.rail.start()

    def _flush_media(self) -> None:
        if not self._pending_media:
            return
        ids, self._pending_media = self._pending_media, []
        self._media_requested.update(ids)
        resolver = MediaResolver(self.context, ids)
        resolver.signals.resolved.connect(self._apply_photo_urls)
        self._resolvers.append(resolver)
        self._pool.start(resolver)

    def _on_slot_media(self, vehicle) -> None:
        """A card just built without a photo url: resolve it now.

        Requests are coalesced for a beat so a whole visible section is one
        resolver run instead of one run per card.
        """
        self._request_media([vehicle])
        if not self._media_timer.isActive():
            self._media_timer.start()

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
