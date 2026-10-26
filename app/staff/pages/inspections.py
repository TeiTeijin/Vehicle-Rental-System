"""Handover checks: read-only audit of pre/post-rental inspections.

Like Payments, this page never writes -- reports are created at check-in /
check-out in Bookings. Its one action deep-links there: Open booking lands
on the report's booking so damage can be acted on.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QWidget,
)
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import joinedload

from app.models import Booking, Inspection_Report, Users, Vehicle
from app.staff import prefs
from app.staff.metrics import CARD_GUTTER, PAGE_PANEL_PAD, SIDEBAR_GAP
from app.staff.pages.base import StaffPage
from app.staff.tables import ALIGN_LEFT, ALIGN_RIGHT, Column, LoadStateTable
from app.staff.ui_kit import (
    SelectionBar,
    StatusChipRow,
    WarmHost,
    WarmPanel,
    WARM_RED,
)

TYPE_FILTERS: tuple[tuple[str, str | None], ...] = (
    ("All types", None),
    ("Pre rental", "pre-rental"),
    ("Post rental", "post-rental"),
)

#: Same codes as the dropdown, with the reference's short chip labels.
TYPE_CHIP_OPTIONS: tuple[tuple[str, str], ...] = (
    ("all", "All"),
    ("pre-rental", "Pre rental"),
    ("post-rental", "Post rental"),
)


def _danger_dot():
    """Small red dot for the damage-only toggle, as drawn by the reference."""
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(WARM_RED))
        painter.drawEllipse(4, 4, 10, 10)
    finally:
        painter.end()
    return QIcon(pixmap)


def _inspection_rows(
    context,
    inspection_type: str | None = None,
    damage_only: bool = False,
    search: str = "",
    cap: int | None = None,
):
    if cap is None:
        try:
            cap = prefs.rows_limit(500)
        except Exception:
            cap = 500
    cap = max(1, int(cap))
    with context.reading() as session:
        stmt = select(Inspection_Report).options(
            joinedload(Inspection_Report.booking).joinedload(Booking.vehicle),
            joinedload(Inspection_Report.booking).joinedload(Booking.user),
            joinedload(Inspection_Report.inspector),
        )
        # Search and the damage filter precede the cap so an old match cannot
        # be hidden by newer, non-matching handovers.
        if inspection_type is not None:
            stmt = stmt.where(
                Inspection_Report.inspection_type == inspection_type
            )
        if damage_only:
            stmt = stmt.where(
                Inspection_Report.damage_notes.isnot(None),
                func.trim(Inspection_Report.damage_notes) != "",
            )
        needle = search.strip().lower()
        if needle:
            pattern = f"%{needle}%"
            stmt = stmt.where(
                or_(
                    cast(Inspection_Report.booking_id, String).like(
                        f"%{needle.lstrip('#')}%"
                    ),
                    Inspection_Report.booking.has(
                        or_(
                            Booking.user.has(
                                func.lower(Users.full_name).like(pattern)
                            ),
                            Booking.vehicle.has(
                                or_(
                                    func.lower(Vehicle.plate_number).like(
                                        pattern
                                    ),
                                    func.lower(Vehicle.make).like(pattern),
                                    func.lower(Vehicle.model).like(pattern),
                                )
                            ),
                        )
                    ),
                    Inspection_Report.inspector.has(
                        func.lower(Users.full_name).like(pattern)
                    ),
                )
            )
        reports = session.execute(
            stmt.order_by(Inspection_Report.inspected_at.desc()).limit(cap)
        ).scalars().all()

        rows = []
        for report in reports:
            booking = report.booking
            vehicle = booking.vehicle if booking is not None else None
            rows.append(
                (
                    f"#{booking.booking_id}" if booking is not None else "-",
                    f"{vehicle.make} {vehicle.model}\n{vehicle.plate_number}"
                    if vehicle
                    else "-",
                    booking.user.full_name if booking is not None else "-",
                    report.inspection_type.replace("-", " ").title(),
                    report.inspected_at.strftime("%d %b %Y\n%H:%M")
                    if report.inspected_at
                    else "-",
                    f"{report.mileage_reading:,} km"
                    if report.mileage_reading is not None
                    else "-",
                    (report.fuel_level or "-").replace("_", " ").title(),
                    report.damage_notes or "None recorded",
                    "Yes" if report.photo_url else "No",
                    report.inspector.full_name
                    if report.inspector is not None
                    else "-",
                )
            )
        return rows


class InspectionsPage(StaffPage):
    HEADER = False

    #: The reference shows ten handovers per page, and 64px rows make that
    #: page fit the audit card exactly at 1440x1000.
    PAGE_SIZE = 10
    ROW_HEIGHT = 64

    def __init__(self, shell) -> None:
        super().__init__(
            shell,
            "Inspections",
            "Every handover check, and anything found at the time.",
        )
        self._inspection_type: str | None = None
        self._damage_only: bool = False
        self._search: str = ""
        #: Rows fetched by the last load, and how many of them the page shows.
        #: Load more reveals the next slice without another query.
        self._all_rows: list = []
        self._visible_rows: int = 0
        self._page_size: int = self.PAGE_SIZE
        try:
            self._row_cap = max(1, int(prefs.rows_limit(500)))
        except Exception:
            self._row_cap = 500

        self.table = LoadStateTable(
            [
                Column("Booking", ALIGN_LEFT, kind="strong"),
                Column("Vehicle", ALIGN_LEFT, kind="strong"),
                Column("Customer", ALIGN_LEFT),
                Column("Type", ALIGN_LEFT, kind="inspection"),
                Column("When", ALIGN_LEFT, kind="muted"),
                Column("Odometer", ALIGN_RIGHT),
                Column("Fuel", ALIGN_LEFT),
                Column("Damage", ALIGN_LEFT, kind="damage"),
                Column("Photo"),
                Column("Inspector", ALIGN_LEFT),
            ],
            self._load_rows,
            empty_message="No inspections recorded yet.",
            shimmer_rows=8,
            variant="warm",
            row_height=self.ROW_HEIGHT,
        )

        # -- shared async machinery (one pool, shimmer-then-rows) ------------
        from app.staff.async_load import AsyncTable
        from app.staff.workers import loader_pool

        self._pool = loader_pool()
        self._table_async = AsyncTable(self._pool, self.table, self)
        #: Keystroke quiet period: rapid typing becomes one reload, and the
        #: tools stay live while only the table shimmers.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self._apply_filters)

        # -- Warm list recipe: white tools card + cream selection bar -------
        # Thin CARD_GUTTER gaps and 8px page edges matching the sidebar.
        self.layout().setContentsMargins(SIDEBAR_GAP, 0, SIDEBAR_GAP, 0)
        self.body.setSpacing(CARD_GUTTER)

        self._tools_panel = WarmHost(self)
        self._filter_card = WarmPanel(self._tools_panel)
        self._filter_card.body.addLayout(self._filter_row())
        self._filter_card.body.addLayout(self._chip_row())
        self._tools_panel.body.addWidget(self._filter_card)

        self.selection_bar = SelectionBar(self._tools_panel)
        self._selection_bar = self.selection_bar
        self._build_actions()
        self._tools_panel.body.addWidget(self.selection_bar)
        self.body.addWidget(self._tools_panel)

        data = WarmPanel(self)
        # Takes all leftover height so the data box bottom sits level with
        # the sidebar's bottom edge; tools stays hugged up top.
        data.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        data.body.setContentsMargins(
            PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD
        )
        data.body.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        self.data_title = QLabel("Inspections", data)
        self.data_title.setObjectName("warmCardTitle")
        title_row.addWidget(self.data_title)
        self.count_caption = QLabel("", data)
        self.count_caption.setObjectName("pageSubtitle")
        title_row.addWidget(self.count_caption)
        title_row.addStretch(1)
        data.body.addLayout(title_row)
        data.body.addWidget(self.table, 1)

        footer = QWidget(data)
        footer.setObjectName("warmTableFooter")
        footer_row = QHBoxLayout(footer)
        footer_row.setContentsMargins(4, 8, 4, 0)
        footer_row.setSpacing(12)
        self.footer_count = QLabel("", footer)
        footer_font = self.footer_count.font()
        footer_font.setPixelSize(12)
        self.footer_count.setFont(footer_font)
        self.footer_count.setStyleSheet(
            "color: #6A645C; background: transparent;"
        )
        footer_row.addWidget(self.footer_count, 1)
        self.load_more_button = QPushButton("Load more", footer)
        self.load_more_button.setObjectName("warmLoadMore")
        self.load_more_button.setFixedHeight(32)
        self.load_more_button.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        self.load_more_button.clicked.connect(self._load_more)
        self.load_more_button.hide()
        footer_row.addWidget(self.load_more_button)
        self._footer = footer
        data.body.addWidget(footer)

        self.body.addWidget(data, 1)
        self._data_panel = data
        self._update_footer(0, 0)

        self.table.table.itemSelectionChanged.connect(self._refresh_actions)
        self.table.table.cellDoubleClicked.connect(
            lambda _r, _c: self._on_open_booking()
        )
        self._refresh_actions()

    def _filter_row(self):
        """Top tools row: search + type + damage-only + Clear."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.search = QLineEdit(self)
        self.search.setObjectName("warmInput")
        self.search.setPlaceholderText("Search booking, plate, customer…")
        self.search.setFixedHeight(40)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        row.addWidget(self.search, 1)
        self.type_combo = self._combo([label for label, _code in TYPE_FILTERS])
        self.type_combo.currentIndexChanged.connect(
            self._on_type_combo_changed
        )
        row.addWidget(self.type_combo)
        self.damage_check = QPushButton("Damage only", self)
        self.damage_check.setObjectName("warmCheck")
        self.damage_check.setCheckable(True)
        self.damage_check.setFixedHeight(40)
        self.damage_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.damage_check.setIcon(_danger_dot())
        self.damage_check.setIconSize(QSize(9, 9))
        self.damage_check.toggled.connect(lambda _on: self._apply_filters())
        row.addWidget(self.damage_check)
        self.clear_button = QPushButton("Clear", self)
        self.clear_button.setObjectName("warmCheck")
        self.clear_button.setFixedHeight(40)
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.clicked.connect(self._clear_filters)
        row.addWidget(self.clear_button)
        return row

    def _chip_row(self):
        """Filter chips on the left, the read-only note on the right."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.type_chips = StatusChipRow(
            TYPE_CHIP_OPTIONS, parent=self._filter_card
        )
        self.type_chips.changed.connect(self._on_type_chip)
        row.addWidget(self.type_chips)
        row.addStretch(1)
        self.hint_label = QLabel(
            "Reports are written at check-in / check-out.", self
        )
        self.hint_label.setObjectName("pageSubtitle")
        self.hint_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        row.addWidget(self.hint_label)
        return row

    def _build_actions(self):
        """Selection bar: the one read-mostly action, Open booking."""
        self.open_button = QPushButton("Open booking", self)
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_button.clicked.connect(self._on_open_booking)
        self.selection_bar.add_action(self.open_button, kind="primary")

    def _combo(self, items):
        combo = QComboBox(self)
        combo.setObjectName("warmInput")
        combo.addItems(items)
        combo.setFixedHeight(40)
        return combo

    # -- filtering ------------------------------------------------------

    def _load_rows(self):
        return _inspection_rows(
            self.context,
            inspection_type=self._inspection_type,
            damage_only=self._damage_only,
            search=self._search,
            cap=self._row_cap,
        )

    def _type_key(self, index: int) -> str:
        _label, code = TYPE_FILTERS[index]
        return code or "all"

    def _sync_type_chip(self, index: int) -> None:
        self.type_chips.select(self._type_key(index))

    def _on_type_combo_changed(self, index: int) -> None:
        self._sync_type_chip(index)
        self._apply_filters()

    def _on_type_chip(self, key: str) -> None:
        index = next(
            i
            for i in range(len(TYPE_FILTERS))
            if self._type_key(i) == key
        )
        blocked = self.type_combo.blockSignals(True)
        try:
            self.type_combo.setCurrentIndex(index)
        finally:
            self.type_combo.blockSignals(blocked)
        self._apply_filters()

    def _apply_filters(self) -> None:
        self._search = self.search.text()
        _label, self._inspection_type = TYPE_FILTERS[
            self.type_combo.currentIndex()
        ]
        self._damage_only = self.damage_check.isChecked()
        self._request_load()

    def _request_load(self) -> None:
        """Shimmer now, rows land on the pool signal. Returns instantly so
        tab switches and keystrokes never stall on the queries."""
        self._table_async.request(
            self._load_rows, on_done=self._land_rows
        )

    def _land_rows(self, rows) -> None:
        self._all_rows = list(rows)
        self._visible_rows = min(self._page_size, len(self._all_rows))
        self._render_rows()

    def _render_rows(self) -> None:
        visible = self._all_rows[: self._visible_rows]
        self.count_caption.setText(
            f"Latest {self._row_cap} inspections · showing {len(visible)}."
        )
        self.table.set_rows(visible)
        self._update_footer(len(visible), len(self._all_rows))
        self._refresh_actions()

    def _update_footer(self, showing: int, total: int) -> None:
        if showing > 1:
            span = f"1–{showing}"
        else:
            span = str(showing)
        self.footer_count.setText(f"{span} of {total}")
        self.load_more_button.setVisible(showing < total)

    def _load_more(self) -> None:
        if self._visible_rows >= len(self._all_rows):
            return
        self._visible_rows = min(
            self._visible_rows + self._page_size, len(self._all_rows)
        )
        self._render_rows()

    def _clear_filters(self) -> None:
        blocked_search = self.search.blockSignals(True)
        blocked_combo = self.type_combo.blockSignals(True)
        blocked_check = self.damage_check.blockSignals(True)
        try:
            self.search.clear()
            self.type_combo.setCurrentIndex(0)
            self.damage_check.setChecked(False)
            self.type_chips.select("all")
        finally:
            self.search.blockSignals(blocked_search)
            self.type_combo.blockSignals(blocked_combo)
            self.damage_check.blockSignals(blocked_check)
        self._apply_filters()

    # -- Open booking handoff -------------------------------------------

    def selected_booking_id(self) -> int | None:
        row = self.table.table.currentRow()
        if row < 0:
            return None
        text = self.table.cell_text(row, 0)
        if not text.startswith("#"):
            return None
        try:
            return int(text[1:])
        except ValueError:
            return None

    def _refresh_actions(self) -> None:
        booking_id = self.selected_booking_id()
        has_row = booking_id is not None
        self.selection_bar.set_active(has_row)
        self.open_button.setEnabled(has_row)
        if has_row:
            row = self.table.table.currentRow()
            customer = self.table.cell_text(row, 2) if row >= 0 else ""
            vehicle = self.table.cell_text(row, 1) if row >= 0 else ""
            vehicle = vehicle.splitlines()[0] if vehicle else ""
            self.selection_bar.set_parts(
                f"Selected #{booking_id} · ", customer, vehicle
            )

    def _on_open_booking(self) -> bool:
        from app.staff.widgets import toast

        booking_id = self.selected_booking_id()
        if booking_id is None:
            toast(self, "Select a report first.", "warn")
            return False
        if not self.shell.show_page("bookings"):
            return False
        spec = next(s for s in self.shell.pages if s.key == "bookings")
        page = self.shell._page_for(spec)
        # Rows load async: highlight now if they are here, otherwise when
        # the reload lands. Navigation itself is the success signal.
        page._pending_focus = booking_id
        page.focus_booking(booking_id)
        return True

    def refresh(self) -> None:
        super().refresh()
        # Async: shimmer shows synchronously so show_page returns fast
        # and the tab cuts instantly; rows land on the loaded signal.
        blocked_search = self.search.blockSignals(True)
        try:
            self.search.setText(self._search)
        finally:
            self.search.blockSignals(blocked_search)
        wanted = next(
            i for i, (_label, code) in enumerate(TYPE_FILTERS)
            if code == self._inspection_type
        )
        blocked_combo = self.type_combo.blockSignals(True)
        try:
            self.type_combo.setCurrentIndex(wanted)
        finally:
            self.type_combo.blockSignals(blocked_combo)
        self._sync_type_chip(wanted)
        blocked_check = self.damage_check.blockSignals(True)
        try:
            self.damage_check.setChecked(self._damage_only)
        finally:
            self.damage_check.blockSignals(blocked_check)
        if self._search_timer.isActive():
            self._search_timer.stop()
        self._request_load()
        self._refresh_actions()


def build_inspections_page(shell) -> InspectionsPage:
    return InspectionsPage(shell)
