"""Workshop log: every shop stay, what is due soon, and what it cost.

Read-mostly, like Payments and Inspections: Add / Mark completed / Cancel
all run through the one lifecycle in `maintenance_service` (the vehicle
status follows the log, never the other way round).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.models import Maintenance_Record, Vehicle
from app.services import maintenance_service
from app.services.errors import ServiceError
from app.services.maintenance_service import TYPE_LABELS, TYPES, type_label
from app.staff import theme
from app.staff.cards import Caption
from app.staff.feedback import describe
from app.staff.metrics import CARD_GUTTER, PAGE_PANEL_PAD, SIDEBAR_GAP, TOOL_ROW_H
from app.staff.pages.base import StaffPage
from app.staff.tables import ALIGN_LEFT, ALIGN_RIGHT, Column, LoadStateTable
from app.staff.widgets import toast
from app.utils.money import pesos

STATUS_FILTERS: tuple[tuple[str, str | None], ...] = (
    ("All statuses", None),
    ("Scheduled", "scheduled"),
    ("In Progress", "in_progress"),
    ("Completed", "completed"),
    ("Cancelled", "cancelled"),
)

TYPE_FILTERS: tuple[tuple[str, str | None], ...] = (
    ("All types", None),
    *((TYPE_LABELS[t], t) for t in TYPES),
)


def _matches(needle: str, *haystacks) -> bool:
    """Case-insensitive substring match over the given fields."""
    query = needle.strip().lower()
    if not query:
        return True
    return any(query in (hay or "").lower() for hay in haystacks)


def _cost_text(record: Maintenance_Record) -> str:
    if record.cost is not None:
        return f"{record.cost:,.2f}"
    if record.estimate_cost is not None:
        return f"est. {record.estimate_cost:,.2f}"
    return "-"


def _maintenance_rows(context, status=None, kind=None, search: str = ""):
    with context.reading() as session:
        stmt = (
            select(Maintenance_Record)
            .options(joinedload(Maintenance_Record.vehicle))
            .order_by(Maintenance_Record.start_date.desc())
            .limit(500)
        )
        if status is not None:
            stmt = stmt.where(Maintenance_Record.status == status)
        if kind is not None:
            stmt = stmt.where(Maintenance_Record.maintenance_type == kind)
        records = session.execute(stmt).scalars().all()

        rows = []
        for record in records:
            vehicle: Vehicle | None = record.vehicle
            vehicle_text = (
                f"{vehicle.plate_number} {vehicle.make} {vehicle.model}"
                if vehicle is not None
                else "-"
            )
            if not _matches(
                search,
                vehicle_text,
                vehicle.plate_number if vehicle is not None else "",
                vehicle.make if vehicle is not None else "",
                vehicle.model if vehicle is not None else "",
                record.description,
                record.shop or "",
            ):
                continue
            rows.append(
                (
                    record.maintenance_id,
                    vehicle_text,
                    type_label(record.maintenance_type),
                    record.description,
                    record.start_date.strftime("%d %b %Y") if record.start_date else "-",
                    record.end_date.strftime("%d %b %Y") if record.end_date else "-",
                    _cost_text(record),
                    record.status,
                )
            )
        return rows


def _due_rows(context):
    with context.reading() as session:
        due = maintenance_service.due_soon(session)
        return [
            (
                f"{vehicle.plate_number} {vehicle.make} {vehicle.model}",
                note,
                record.description,
            )
            for vehicle, record, note in due
        ]


class MaintenancePage(StaffPage):
    HEADER = False

    def __init__(self, shell) -> None:
        super().__init__(
            shell,
            "Maintenance",
            "Every workshop stay, and what kept the vehicle off the road.",
        )
        from PySide6.QtWidgets import QFrame, QSizePolicy, QVBoxLayout

        self._status: str | None = None
        self._kind: str | None = None
        self._search: str = ""
        self._maintenance_overlay = None

        self.due = LoadStateTable(
            [
                Column("Vehicle", ALIGN_LEFT),
                Column("Due", ALIGN_LEFT),
                Column("Last job", ALIGN_LEFT),
            ],
            lambda: _due_rows(self.context),
            empty_message="Nothing due in the next 14 days.",
            shimmer_rows=3,
        )

        self.table = LoadStateTable(
            [
                Column("Ref", ALIGN_RIGHT),
                Column("Vehicle", ALIGN_LEFT),
                Column("Type", ALIGN_LEFT),
                Column("Description", ALIGN_LEFT),
                Column("From", ALIGN_LEFT),
                Column("To", ALIGN_LEFT),
                Column("Cost", ALIGN_RIGHT),
                Column("Status"),
            ],
            self._load_rows,
            empty_message="No maintenance recorded yet.",
            shimmer_rows=8,
        )

        # -- shared async machinery (one pool, shimmer-then-rows) ------------
        from PySide6.QtCore import QTimer, QThreadPool

        from app.staff.async_load import AsyncTable

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._due_async = AsyncTable(self._pool, self.due, self)
        self._table_async = AsyncTable(self._pool, self.table, self)
        #: Keystroke quiet period: rapid typing becomes one reload, and the
        #: tools stay live while only the tables shimmer.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self._apply_filters)

        # -- Bookings recipe, three boxes ----------------------------------
        # Tools on top hugging its content, Due soon + Log below sharing
        # the rest. Thin CARD_GUTTER gaps, 8px page edges matching the
        # sidebar rhythm.
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

        due_box = QFrame(self)
        due_box.setObjectName(theme.OBJ_PAGE_PANEL)
        due_box.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        due_layout = QVBoxLayout(due_box)
        due_layout.setContentsMargins(
            PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD
        )
        due_layout.setSpacing(8)
        due_layout.addWidget(self._heading("Due soon"))
        due_layout.addWidget(self.due)
        self.body.addWidget(due_box)
        self._due_panel = due_box

        log_box = QFrame(self)
        log_box.setObjectName(theme.OBJ_PAGE_PANEL)
        log_box.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(
            PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD
        )
        log_layout.setSpacing(8)
        log_layout.addWidget(self._heading("Service log"))
        self.spend_line = Caption("", parent=self)
        log_layout.addWidget(self.spend_line)
        log_layout.addWidget(self.table, 1)
        self.body.addWidget(log_box, 1)
        self._log_panel = log_box

        self.table.table.itemSelectionChanged.connect(self._refresh_actions)
        self.table.table.cellDoubleClicked.connect(
            lambda _r, _c: self.open_history()
        )
        self._refresh_actions()

    def _filter_row(self):
        """Top tools row: search + type + status + Clear."""
        from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.search = QLineEdit(self)
        self.search.setObjectName("searchField")
        self.search.setPlaceholderText("Search plate, vehicle, job…")
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
        self.status_combo = self._combo(
            [label for label, _code in STATUS_FILTERS]
        )
        self.status_combo.currentIndexChanged.connect(
            lambda _i: self._apply_filters()
        )
        row.addWidget(self.status_combo)
        self.clear_button = QPushButton("Clear", self)
        self.clear_button.setObjectName("ghostButton")
        self.clear_button.setProperty("corner", "topRight")
        self.clear_button.setFixedHeight(TOOL_ROW_H)
        self.clear_button.clicked.connect(self._clear_filters)
        row.addWidget(self.clear_button)
        return row

    def _action_row(self):
        """Bottom tools row: the lifecycle actions."""
        from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.add_button = QPushButton("Log maintenance", self)
        self.add_button.setObjectName("ghostButton")
        # Concentric with the tools panel (28 - 16 = 12); bottom-left
        # outer corner only, like the Bookings Confirm button.
        self.add_button.setProperty("corner", "bottomLeft")
        self.add_button.setFixedHeight(TOOL_ROW_H)
        self.add_button.clicked.connect(self.open_add)
        row.addWidget(self.add_button)
        self.complete_button = self._action_button("Mark completed")
        self.complete_button.clicked.connect(self.open_complete)
        row.addWidget(self.complete_button)
        self.cancel_button = self._action_button("Cancel")
        self.cancel_button.clicked.connect(self.open_cancel)
        row.addWidget(self.cancel_button)
        self.history_button = self._action_button("History")
        self.history_button.clicked.connect(self.open_history)
        row.addWidget(self.history_button)
        self.open_button = self._action_button("Open vehicle")
        self.open_button.clicked.connect(self._on_open_vehicle)
        row.addWidget(self.open_button)
        hint = QLabel("Completing releases the vehicle.", self)
        hint.setObjectName("pageSubtitle")
        row.addWidget(hint)
        row.addStretch(1)
        return row

    def _action_button(self, text: str):
        from PySide6.QtWidgets import QPushButton

        button = QPushButton(text, self)
        button.setObjectName("ghostButton")
        button.setFixedHeight(TOOL_ROW_H)
        return button

    def _combo(self, items):
        from PySide6.QtWidgets import QComboBox

        combo = QComboBox(self)
        combo.addItems(items)
        combo.setFixedHeight(TOOL_ROW_H)
        return combo

    def _heading(self, text: str):
        from PySide6.QtWidgets import QLabel

        label = QLabel(text, self)
        label.setObjectName("pageSubtitle")
        return label

    # -- filtering ------------------------------------------------------

    def _load_rows(self):
        return _maintenance_rows(
            self.context, status=self._status, kind=self._kind,
            search=self._search,
        )

    def _apply_filters(self) -> None:
        self._search = self.search.text()
        _label, self._kind = TYPE_FILTERS[self.type_combo.currentIndex()]
        _label, self._status = STATUS_FILTERS[self.status_combo.currentIndex()]
        self._request_load()
        self._refresh_spend()

    def _request_load(self) -> None:
        """Shimmer now, rows land on the pool signal. Returns instantly so
        tab switches and keystrokes never stall on the queries."""
        self._due_async.request(lambda: _due_rows(self.context))
        self._table_async.request(
            self._load_rows, on_done=self._land_rows
        )

    def _land_rows(self, rows) -> None:
        self.table.set_rows(rows)
        self._refresh_actions()

    def _clear_filters(self) -> None:
        blocked_search = self.search.blockSignals(True)
        blocked = [
            (combo, combo.blockSignals(True))
            for combo in (self.type_combo, self.status_combo)
        ]
        try:
            self.search.clear()
            self.type_combo.setCurrentIndex(0)
            self.status_combo.setCurrentIndex(0)
        finally:
            self.search.blockSignals(blocked_search)
            for combo, was in blocked:
                combo.blockSignals(was)
        self._apply_filters()

    def _refresh_spend(self) -> None:
        with self.context.reading() as session:
            total, by_type, _by_vehicle = maintenance_service.spend_summary(session)
        if total <= 0:
            self.spend_line.setText("No completed spend on the log yet.")
            return
        parts = ", ".join(
            f"{type_label(kind)} {pesos(amount)}"
            for kind, amount in sorted(by_type.items())
        )
        self.spend_line.setText(f"Spent {pesos(total)} · {parts}.")

    # -- selection ------------------------------------------------------

    def selected_record_id(self) -> int | None:
        row = self.table.table.currentRow()
        if row < 0:
            return None
        try:
            return int(self.table.cell_text(row, 0))
        except ValueError:
            return None

    def selected_plate(self) -> str | None:
        row = self.table.table.currentRow()
        if row < 0:
            return None
        text = self.table.cell_text(row, 1)
        if not text or text == "-":
            return None
        # First token is the plate ("SHOP-001 Toyota Vios").
        return text.split()[0]

    def _refresh_actions(self) -> None:
        has_row = self.selected_record_id() is not None
        self.complete_button.setEnabled(has_row)
        self.cancel_button.setEnabled(has_row)
        self.history_button.setEnabled(has_row)
        self.open_button.setEnabled(has_row)

    # -- write paths (one lifecycle, same as Fleet) ---------------------

    def run(self, action: str, **kwargs) -> bool:
        record_id = self.selected_record_id()
        if record_id is None and action in ("complete", "cancel"):
            toast(self, "Select a record first.", "warn")
            return False
        try:
            with self.context.session() as session:
                record = (
                    session.get(Maintenance_Record, record_id)
                    if record_id is not None
                    else None
                )
                _ACTIONS[action](session, record, self.context, **kwargs)
        except Exception as exc:  # noqa: BLE001
            message, _field, _kind = describe(exc)
            toast(self, message, "error")
            return False

        self.refresh()
        toast(self, "Service log updated.")
        return True

    # -- overlay panels (no OS windows; same glass as bookings) ---------

    def _overlay_panel(self, panel) -> object:
        from app.staff.overlay import FrostedOverlay

        if self._maintenance_overlay is not None:
            self._maintenance_overlay.hide_overlay()
            self._maintenance_overlay.deleteLater()
            self._maintenance_overlay = None
        window = self.window()
        overlay = FrostedOverlay(window if window is not None else self)
        overlay.set_content(panel)
        overlay.show_overlay()
        self._maintenance_overlay = overlay
        return overlay

    def _hide_overlay(self) -> None:
        if self._maintenance_overlay is not None:
            self._maintenance_overlay.hide_overlay()

    def _submit(self, action: str, values: dict) -> None:
        if self.run(action, **values):
            self._hide_overlay()
        # On failure run() already toasted the reason; the form stays open.

    def open_add(self) -> bool:
        from app.staff.maintenance_panels import RecordPanel

        panel = RecordPanel(self.context, self)
        panel.submitted.connect(lambda values: self._submit("add", values))
        panel.cancelled.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def open_complete(self) -> bool:
        from app.staff.maintenance_panels import CompletePanel

        record_id = self.selected_record_id()
        if record_id is None:
            toast(self, "Select a record first.", "warn")
            return False
        with self.context.reading() as session:
            record = session.get(Maintenance_Record, record_id)
            if record is None:
                toast(self, "That record no longer exists.", "error")
                return False
            description = (
                f"{record.vehicle.plate_number} · {record.description}"
                if record.vehicle is not None
                else record.description
            )
            estimate = record.estimate_cost
        panel = CompletePanel(description, estimate, self)
        panel.submitted.connect(lambda values: self._submit("complete", values))
        panel.cancelled.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def open_cancel(self) -> bool:
        from app.staff.maintenance_panels import CancelPanel

        if self.selected_record_id() is None:
            toast(self, "Select a record first.", "warn")
            return False
        panel = CancelPanel(self)
        panel.submitted.connect(lambda values: self._submit("cancel", values))
        panel.cancelled.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def open_history(self) -> bool:
        from app.staff.maintenance_panels import HistoryPanel

        record_id = self.selected_record_id()
        if record_id is None:
            toast(self, "Select a record first.", "warn")
            return False
        with self.context.reading() as session:
            record = session.get(Maintenance_Record, record_id)
            if record is None:
                toast(self, "That record no longer exists.", "error")
                return False
            vehicle_id = record.vehicle_id
        panel = HistoryPanel(self.context, vehicle_id, self)
        panel.closed.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def _on_open_vehicle(self) -> bool:
        plate = self.selected_plate()
        if plate is None:
            toast(self, "Select a record first.", "warn")
            return False
        if not self.shell.show_page("fleet"):
            return False
        spec = next(s for s in self.shell.pages if s.key == "fleet")
        page = self.shell._page_for(spec)
        # Rows load async: highlight now if they are here, otherwise when
        # the reload lands. Navigation itself is the success signal.
        page._pending_focus = plate
        page.focus_plate(plate)
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
        for combo, filters, current in (
            (self.type_combo, TYPE_FILTERS, self._kind),
            (self.status_combo, STATUS_FILTERS, self._status),
        ):
            wanted = next(
                i for i, (_label, code) in enumerate(filters) if code == current
            )
            blocked = combo.blockSignals(True)
            try:
                combo.setCurrentIndex(wanted)
            finally:
                combo.blockSignals(blocked)
        if self._search_timer.isActive():
            self._search_timer.stop()
        self._request_load()
        self._refresh_spend()
        self._refresh_actions()


# -- the actions ----------------------------------------------------------
#
# Each takes a session and a *fresh* record (except `add`, which takes the
# values to create one), called inside `StaffContext.session()`.


def _do_add(session, _record, context, **values) -> None:
    vehicle = session.get(Vehicle, values.pop("vehicle_id"))
    if vehicle is None:
        raise ServiceError("That vehicle no longer exists.")
    maintenance_service.start_maintenance(
        session, vehicle, logged_by=context.user.user_id, **values
    )


def _do_complete(session, record, context, **values) -> None:
    if record is None:
        raise ServiceError("That record no longer exists.")
    maintenance_service.complete_maintenance(session, record, **values)


def _do_cancel(session, record, context, *, reason: str, **_) -> None:
    if record is None:
        raise ServiceError("That record no longer exists.")
    maintenance_service.cancel_maintenance(session, record, reason=reason)


_ACTIONS = {
    "add": _do_add,
    "complete": _do_complete,
    "cancel": _do_cancel,
}


def build_maintenance_page(shell) -> MaintenancePage:
    return MaintenancePage(shell)
