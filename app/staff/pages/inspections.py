"""Handover checks: read-only audit of pre/post-rental inspections.

Like Payments, this page never writes -- reports are created at check-in /
check-out in Bookings. Its one action deep-links there: Open booking lands
on the report's booking so damage can be acted on.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.models import Booking, Inspection_Report
from app.staff import theme
from app.staff.metrics import CARD_GUTTER, PAGE_PANEL_PAD, SIDEBAR_GAP, TOOL_ROW_H
from app.staff.pages.base import StaffPage
from app.staff.tables import ALIGN_LEFT, ALIGN_RIGHT, Column, LoadStateTable

TYPE_FILTERS: tuple[tuple[str, str | None], ...] = (
    ("All types", None),
    ("Pre rental", "pre-rental"),
    ("Post rental", "post-rental"),
)


def _matches(needle: str, *haystacks) -> bool:
    """Case-insensitive substring match over the given fields."""
    query = needle.strip().lower()
    if not query:
        return True
    return any(query in (hay or "").lower() for hay in haystacks)


def _inspection_rows(
    context,
    inspection_type: str | None = None,
    damage_only: bool = False,
    search: str = "",
):
    try:
        from app.staff import prefs as _prefs
        cap = _prefs.rows_limit(500)
    except Exception:
        cap = 500
    with context.reading() as session:
        stmt = (
            select(Inspection_Report)
            .options(
                joinedload(Inspection_Report.booking).joinedload(Booking.vehicle),
                joinedload(Inspection_Report.booking).joinedload(Booking.user),
                joinedload(Inspection_Report.inspector),
            )
            .order_by(Inspection_Report.inspected_at.desc())
            .limit(cap)
        )
        if inspection_type is not None:
            stmt = stmt.where(Inspection_Report.inspection_type == inspection_type)
        reports = session.execute(stmt).scalars().all()

        rows = []
        for report in reports:
            if damage_only and not (report.damage_notes or "").strip():
                continue
            booking = report.booking
            vehicle = booking.vehicle if booking is not None else None
            if not _matches(
                search,
                f"#{booking.booking_id}" if booking is not None else "",
                f"{vehicle.plate_number} {vehicle.make} {vehicle.model}" if vehicle else "",
                vehicle.plate_number if vehicle else "",
                booking.user.full_name if booking is not None else "",
                report.inspector.full_name if report.inspector is not None else "",
            ):
                continue
            rows.append(
                (
                    f"#{booking.booking_id}" if booking is not None else "-",
                    f"{vehicle.plate_number} {vehicle.make} {vehicle.model}" if vehicle else "-",
                    booking.user.full_name if booking is not None else "-",
                    report.inspection_type.replace("-", " ").title(),
                    report.inspected_at.strftime("%d %b %Y %H:%M") if report.inspected_at else "-",
                    f"{report.mileage_reading:,} km" if report.mileage_reading is not None else "-",
                    (report.fuel_level or "-").replace("_", " ").title(),
                    report.damage_notes or "None recorded",
                    "Yes" if report.photo_url else "No",
                    report.inspector.full_name if report.inspector is not None else "-",
                )
            )
        return rows


class InspectionsPage(StaffPage):
    HEADER = False

    def __init__(self, shell) -> None:
        super().__init__(
            shell,
            "Inspections",
            "Every handover check, and anything found at the time.",
        )
        from PySide6.QtWidgets import QFrame, QSizePolicy, QVBoxLayout

        self._inspection_type: str | None = None
        self._damage_only: bool = False
        self._search: str = ""

        self.table = LoadStateTable(
            [
                Column("Booking", ALIGN_RIGHT),
                Column("Vehicle", ALIGN_LEFT),
                Column("Customer", ALIGN_LEFT),
                Column("Type", ALIGN_LEFT),
                Column("When", ALIGN_LEFT),
                Column("Odometer", ALIGN_RIGHT),
                Column("Fuel", ALIGN_LEFT),
                Column("Damage", ALIGN_LEFT),
                Column("Photo"),
                Column("Inspector", ALIGN_LEFT),
            ],
            self._load_rows,
            empty_message="No inspections recorded yet.",
            shimmer_rows=8,
        )

        # -- shared async machinery (one pool, shimmer-then-rows) ------------
        from PySide6.QtCore import QTimer, QThreadPool

        from app.staff.async_load import AsyncTable

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._table_async = AsyncTable(self._pool, self.table, self)
        #: Keystroke quiet period: rapid typing becomes one reload, and the
        #: tools stay live while only the table shimmers.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self._apply_filters)

        # -- Bookings recipe: two white main containers on cream ------------
        # Tools (filters + actions, transparent controls) on top hugging its
        # content, data below taking the rest. Thin CARD_GUTTER gap, 8px page
        # edges matching the sidebar rhythm.
        self.layout().setContentsMargins(SIDEBAR_GAP, 0, SIDEBAR_GAP, 0)
        self.body.setSpacing(CARD_GUTTER)

        tools = QFrame(self)
        tools.setObjectName(theme.OBJ_PAGE_PANEL)
        tools.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        tools_layout = QVBoxLayout(tools)
        tools_layout.setContentsMargins(
            PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD
        )
        tools_layout.setSpacing(8)
        tools_layout.addLayout(self._filter_row())
        tools_layout.addLayout(self._action_row())
        self.body.addWidget(tools)
        self._tools_panel = tools

        data = QFrame(self)
        data.setObjectName(theme.OBJ_PAGE_PANEL)
        # Takes all leftover height so the data box bottom sits level with
        # the sidebar's bottom edge; tools stays hugged up top.
        data.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        data_layout = QVBoxLayout(data)
        data_layout.setContentsMargins(
            PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD
        )
        data_layout.setSpacing(0)
        data_layout.addWidget(self.table, 1)
        self.body.addWidget(data, 1)
        self._data_panel = data

        self.table.table.itemSelectionChanged.connect(self._refresh_actions)
        self.table.table.cellDoubleClicked.connect(
            lambda _r, _c: self._on_open_booking()
        )
        self._refresh_actions()

    def _filter_row(self):
        """Top tools row: search + type + damage-only + Clear."""
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLineEdit, QPushButton

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.search = QLineEdit(self)
        self.search.setObjectName("searchField")
        self.search.setPlaceholderText("Search booking, plate, customer…")
        # Concentric with the tools panel (28 - 16 = 12); panel-facing
        # outer corner only, like the Bookings search.
        self.search.setProperty("corner", "topLeft")
        self.search.setFixedHeight(TOOL_ROW_H)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        row.addWidget(self.search, 1)
        self.type_combo = self._combo([label for label, _code in TYPE_FILTERS])
        self.type_combo.currentIndexChanged.connect(
            lambda _i: self._apply_filters()
        )
        row.addWidget(self.type_combo)
        self.damage_check = QCheckBox("Damage only", self)
        damage_font = QFont("Inter")
        damage_font.setPixelSize(12)
        damage_font.setWeight(QFont.Weight.DemiBold)
        self.damage_check.setFont(damage_font)
        self.damage_check.setStyleSheet(
            f"color: {theme.MUTED}; background: transparent;"
        )
        self.damage_check.toggled.connect(lambda _on: self._apply_filters())
        row.addWidget(self.damage_check)
        self.clear_button = QPushButton("Clear", self)
        self.clear_button.setObjectName("ghostButton")
        self.clear_button.setProperty("corner", "topRight")
        self.clear_button.setFixedHeight(TOOL_ROW_H)
        self.clear_button.clicked.connect(self._clear_filters)
        row.addWidget(self.clear_button)
        return row

    def _action_row(self):
        """Bottom tools row: Open booking, mirroring the Bookings actions."""
        from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.open_button = QPushButton("Open booking", self)
        self.open_button.setObjectName("ghostButton")
        # Concentric with the tools panel (28 - 16 = 12); bottom-left
        # outer corner only, like the Bookings Confirm button.
        self.open_button.setProperty("corner", "bottomLeft")
        self.open_button.setFixedHeight(TOOL_ROW_H)
        self.open_button.clicked.connect(self._on_open_booking)
        row.addWidget(self.open_button)
        hint = QLabel("Reports are written at check-in / check-out.", self)
        hint.setObjectName("pageSubtitle")
        row.addWidget(hint)
        row.addStretch(1)
        return row

    def _combo(self, items):
        from PySide6.QtWidgets import QComboBox

        combo = QComboBox(self)
        combo.addItems(items)
        combo.setFixedHeight(TOOL_ROW_H)
        return combo

    # -- filtering ------------------------------------------------------

    def _load_rows(self):
        return _inspection_rows(
            self.context,
            inspection_type=self._inspection_type,
            damage_only=self._damage_only,
            search=self._search,
        )

    def _apply_filters(self) -> None:
        self._search = self.search.text()
        _label, self._inspection_type = TYPE_FILTERS[self.type_combo.currentIndex()]
        self._damage_only = self.damage_check.isChecked()
        self._request_load()

    def _request_load(self) -> None:
        """Shimmer now, rows land on the pool signal. Returns instantly so
        tab switches and keystrokes never stall on the queries."""
        self._table_async.request(
            self._load_rows, on_done=self._land_rows
        )

    def _land_rows(self, rows) -> None:
        self.table.set_rows(rows)
        self._refresh_actions()

    def _clear_filters(self) -> None:
        blocked_search = self.search.blockSignals(True)
        blocked_combo = self.type_combo.blockSignals(True)
        blocked_check = self.damage_check.blockSignals(True)
        try:
            self.search.clear()
            self.type_combo.setCurrentIndex(0)
            self.damage_check.setChecked(False)
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
        self.open_button.setEnabled(self.selected_booking_id() is not None)

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
