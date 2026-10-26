"""Workshop log: every shop stay, what is due soon, and what it cost.

Read-mostly, like Payments and Inspections: Add / Mark completed / Cancel
all run through the one lifecycle in `maintenance_service` (the vehicle
status follows the log, never the other way round).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, or_, select
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
from app.staff.ui_kit import (
    SelectionBar,
    StatusChipRow,
    WarmHost,
    WarmPanel,
)
from app.staff.widgets import toast
from app.utils.money import pesos

STATUS_FILTERS: tuple[tuple[str, str | None], ...] = (
    ("All statuses", None),
    ("Scheduled", "scheduled"),
    ("In Progress", "in_progress"),
    ("Completed", "completed"),
    ("Cancelled", "cancelled"),
)

#: Same status codes as the hidden combo, with the reference's short labels.
STATUS_CHIP_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    ("all" if code is None else code, "All" if code is None else label)
    for label, code in STATUS_FILTERS
)

TYPE_FILTERS: tuple[tuple[str, str | None], ...] = (
    ("All types", None),
    *((TYPE_LABELS[t], t) for t in TYPES),
)


def _cost_text(record: Maintenance_Record) -> str:
    if record.cost is not None:
        return f"{record.cost:,.2f}"
    if record.estimate_cost is not None:
        return f"est. {record.estimate_cost:,.2f}"
    return "-"


def _maintenance_rows(context, status=None, kind=None, search: str = ""):
    try:
        from app.staff import prefs as _prefs
        cap = _prefs.rows_limit(500)
    except Exception:
        cap = 500
    with context.reading() as session:
        stmt = select(Maintenance_Record).options(
            joinedload(Maintenance_Record.vehicle)
        )
        # Search precedes the cap; filtering the capped rows would hide an old
        # job whose plate, vehicle, description or shop matches.
        if status is not None:
            stmt = stmt.where(Maintenance_Record.status == status)
        if kind is not None:
            stmt = stmt.where(Maintenance_Record.maintenance_type == kind)
        needle = search.strip().lower()
        if needle:
            pattern = f"%{needle}%"
            stmt = stmt.where(
                or_(
                    Maintenance_Record.vehicle.has(
                        or_(
                            func.lower(Vehicle.plate_number).like(pattern),
                            func.lower(Vehicle.make).like(pattern),
                            func.lower(Vehicle.model).like(pattern),
                        )
                    ),
                    func.lower(Maintenance_Record.description).like(pattern),
                    func.lower(Maintenance_Record.shop).like(pattern),
                )
            )
        records = session.execute(
            stmt.order_by(Maintenance_Record.start_date.desc()).limit(cap)
        ).scalars().all()

        rows = []
        for record in records:
            vehicle: Vehicle | None = record.vehicle
            vehicle_text = (
                f"{vehicle.plate_number} {vehicle.make} {vehicle.model}"
                if vehicle is not None
                else "-"
            )
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
                Column("Vehicle", ALIGN_LEFT, kind="strong"),
                Column("Due", ALIGN_LEFT),
                Column("Last job", ALIGN_LEFT, kind="muted"),
            ],
            lambda: _due_rows(self.context),
            empty_message="Nothing due in the next 14 days.",
            shimmer_rows=3,
            variant="warm",
        )

        self.table = LoadStateTable(
            [
                Column("Ref", ALIGN_RIGHT, kind="muted"),
                Column("Vehicle", ALIGN_LEFT, kind="strong"),
                Column("Type", ALIGN_LEFT),
                Column("Description", ALIGN_LEFT),
                Column("From", ALIGN_LEFT, kind="muted"),
                Column("To", ALIGN_LEFT, kind="muted"),
                Column("Cost", ALIGN_RIGHT, kind="money"),
                Column("Status", kind="status"),
            ],
            self._load_rows,
            empty_message="No maintenance recorded yet.",
            shimmer_rows=8,
            variant="warm",
        )

        # -- shared async machinery (one pool, shimmer-then-rows) ------------
        from PySide6.QtCore import QTimer

        from app.staff.async_load import AsyncTable
        from app.staff.workers import loader_pool

        self._pool = loader_pool()
        self._due_async = AsyncTable(self._pool, self.due, self)
        self._table_async = AsyncTable(self._pool, self.table, self)
        #: Keystroke quiet period: rapid typing becomes one reload, and the
        #: tools stay live while only the tables shimmer.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self._apply_filters)

        # -- Warm list recipe: tools, Due soon, Service log -----------------
        # Tools hug their content; Due soon + Log below share the rest.
        # Thin CARD_GUTTER gaps and the same page edges as before.
        self.layout().setContentsMargins(SIDEBAR_GAP, 0, SIDEBAR_GAP, 0)
        self.body.setSpacing(CARD_GUTTER)

        # No second page heading above the tools card; keep the old count
        # label hidden for callers that still read it.
        self.count_caption = Caption("", parent=self)
        self.count_caption.setVisible(False)

        self._tools_panel = WarmHost(self)
        self._filter_card = WarmPanel(self._tools_panel)
        self._filter_card.body.addLayout(self._filter_row())
        self.status_chips = StatusChipRow(
            STATUS_CHIP_OPTIONS, parent=self._filter_card
        )
        self._filter_card.body.addWidget(self.status_chips)
        self.status_chips.changed.connect(self._on_status_chip)
        self.hint_label = Caption("Completing releases the vehicle.", parent=self)
        self.hint_label.setObjectName("pageSubtitle")
        self._filter_card.body.addWidget(self.hint_label)
        self._tools_panel.body.addWidget(self._filter_card)

        self.selection_bar = SelectionBar(self._tools_panel)
        self._selection_bar = self.selection_bar
        self._build_actions()
        self._tools_panel.body.addWidget(self.selection_bar)
        self.body.addWidget(self._tools_panel)

        due_box = WarmPanel(self)
        due_box.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        due_layout = due_box.body
        due_layout.setSpacing(8)
        due_layout.addWidget(self._heading("Due soon"))
        due_layout.addWidget(self.due)
        self.body.addWidget(due_box)
        self._due_panel = due_box

        log_box = WarmPanel(self)
        log_box.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        log_layout = log_box.body
        log_layout.setSpacing(8)
        log_layout.addWidget(self._heading("Service log"))
        self.spend_line = Caption("", parent=self)
        log_layout.addWidget(self.spend_line)
        log_layout.addWidget(self.table, 1)
        self.body.addWidget(log_box, 1)
        self._log_panel = log_box
        # Compatibility name used by the other list pages; the service log
        # is this page's data card.
        self._data_panel = log_box

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
            self._on_status_combo_changed
        )
        row.addWidget(self.status_combo)
        self.status_combo.hide()
        self.clear_button = QPushButton("Clear", self)
        self.clear_button.setObjectName("ghostButton")
        self.clear_button.setProperty("corner", "topRight")
        self.clear_button.setFixedHeight(TOOL_ROW_H)
        self.clear_button.clicked.connect(self._clear_filters)
        row.addWidget(self.clear_button)
        return row

    def _build_actions(self):
        """Selection bar: the lifecycle actions."""
        from PySide6.QtWidgets import QPushButton

        self.add_button = QPushButton("Log maintenance", self)
        self.add_button.setObjectName("ghostButton")
        # Kept for the old concentric-corner contract; the selection bar
        # supplies the actual rounded style now.
        self.add_button.setProperty("corner", "bottomLeft")
        self.add_button.setFixedHeight(TOOL_ROW_H)
        self.add_button.clicked.connect(self.open_add)
        self.complete_button = self._action_button("Mark completed")
        self.complete_button.clicked.connect(self.open_complete)
        self.cancel_button = self._action_button("Cancel", danger=True)
        self.cancel_button.clicked.connect(self.open_cancel)
        self.history_button = self._action_button("History")
        self.history_button.clicked.connect(self.open_history)
        self.open_button = self._action_button("Open vehicle")
        self.open_button.clicked.connect(self._on_open_vehicle)
        self._action_buttons = [
            self.add_button,
            self.complete_button,
            self.cancel_button,
            self.history_button,
            self.open_button,
        ]
        self.selection_bar.add_action(self.add_button)
        for button in self._action_buttons[1:]:
            kind = "danger" if button is self.cancel_button else "action"
            self.selection_bar.add_action(button, kind=kind)

    def _action_button(self, text: str, *, danger: bool = False):
        from PySide6.QtWidgets import QPushButton

        button = QPushButton(text, self)
        button.setObjectName("dangerButton" if danger else "ghostButton")
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

    def _status_key(self, index: int) -> str:
        _label, code = STATUS_FILTERS[index]
        return code or "all"

    def _sync_status_chip(self, index: int) -> None:
        self.status_chips.select(self._status_key(index))

    def _on_status_combo_changed(self, index: int) -> None:
        self._sync_status_chip(index)
        self._apply_filters()

    def _on_status_chip(self, key: str) -> None:
        index = next(
            i for i in range(len(STATUS_FILTERS))
            if self._status_key(i) == key
        )
        blocked = self.status_combo.blockSignals(True)
        try:
            self.status_combo.setCurrentIndex(index)
        finally:
            self.status_combo.blockSignals(blocked)
        self._apply_filters()

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
        try:
            from app.staff import prefs as _prefs

            limit = _prefs.rows_limit(500)
        except Exception:
            limit = 500
        self.count_caption.setText(
            f"Latest {limit} service records (showing {len(rows)})."
        )
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
            self.status_chips.select("all")
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
        record_id = self.selected_record_id()
        has_row = record_id is not None
        self.selection_bar.set_active(has_row)
        self.add_button.setEnabled(True)
        self.complete_button.setEnabled(has_row)
        self.cancel_button.setEnabled(has_row)
        self.history_button.setEnabled(has_row)
        self.open_button.setEnabled(has_row)
        if has_row:
            row = self.table.table.currentRow()
            vehicle = self.table.cell_text(row, 1) if row >= 0 else ""
            description = self.table.cell_text(row, 3) if row >= 0 else ""
            self.selection_bar.set_parts(
                f"#{record_id} ", vehicle, description
            )

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
        overlay = FrostedOverlay(
            window if window is not None else self,
            card_width=getattr(panel, "sheet_width", 640),
        )
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
        self._sync_status_chip(self.status_combo.currentIndex())
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
