from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import partial

from PySide6.QtCore import QObject, QRunnable, QTimer, Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select

from app.models import Vehicle
from app.services import booking_service, fleet_service
from app.services.errors import ServiceError
from app.staff import theme
from app.staff.feedback import describe
from app.staff.metrics import CARD_GUTTER, PAGE_PANEL_PAD, SIDEBAR_GAP, TOOL_ROW_H
from app.staff.pages.base import StaffPage
from app.staff.tables import ALIGN_LEFT, ALIGN_RIGHT, Column, LoadState, LoadStateTable
from app.staff.widgets import confirm, toast
from app.staff.workers import loader_pool, media_pool
from app.widgets.skeleton import Skeleton


#: Display label -> stored vehicle status codes, for the filter combo.
STATUS_FILTERS: tuple[tuple[str, tuple[str, ...] | None], ...] = (
    ("All statuses", None),
    ("Available", ("available",)),
    ("Rented", ("rented",)),
    ("Reserved", ("reserved",)),
    ("Maintenance", ("maintenance",)),
    ("Retired", ("retired",)),
)


@dataclass
class FleetFilter:
    search: str = ""
    statuses: tuple[str, ...] | None = None
    needs_attention_only: bool = False


def _matches_fleet(
    vehicle: Vehicle,
    overdue: str,
    issues: list[str],
    filtr: FleetFilter,
) -> bool:
    if filtr.statuses is not None and vehicle.status not in filtr.statuses:
        return False
    if filtr.needs_attention_only and not (issues or overdue == "Yes"):
        return False
    needle = filtr.search.strip().lower()
    if needle:
        haystacks = [
            vehicle.plate_number or "",
            vehicle.make or "",
            vehicle.model or "",
            f"{vehicle.make} {vehicle.model}",
        ]
        if not any(needle in (hay or "").lower() for hay in haystacks):
            return False
    return True


def _fleet_rows(context, filtr: FleetFilter):
    today = date.today()
    with context.reading() as session:
        vehicles = session.execute(
            select(Vehicle).order_by(Vehicle.created_at.desc(), Vehicle.vehicle_id.desc())
        ).scalars().all()

        rows = []
        for vehicle in vehicles:
            open_bookings = booking_service.active_bookings(session, vehicle)
            due = min((b.end_date for b in open_bookings), default=None)

            blocked = fleet_service.maintenance_conflict(
                session, vehicle, today, today + timedelta(days=30)
            )
            issues = []
            if blocked:
                issues.append(f"Maintenance until {blocked.end_date:%d %b}")
            if not fleet_service.is_rentable(vehicle):
                issues.append("Not rentable")

            overdue = "Yes" if due and due < today else "-"
            if not _matches_fleet(vehicle, overdue, issues, filtr):
                continue

            rows.append(
                (
                    vehicle.plate_number,
                    f"{vehicle.make} {vehicle.model}",
                    str(vehicle.year),
                    f"{vehicle.daily_rate:,.2f}",
                    f"{vehicle.mileage:,} km" if vehicle.mileage else "-",
                    theme.status_label(vehicle.status),
                    due.strftime("%d %b") if due else "-",
                    overdue,
                    "; ".join(issues) or "-",
                )
            )
        return rows


#: Shimmer rows in the table mimic. Same recipe as Bookings: fixed count,
#: never derived from viewport geometry (not final until the event loop).
FLEET_SKELETON_ROWS = 8

#: Filter keystroke quiet period before a reload is queued.
FILTER_DEBOUNCE_MS = 120


class _FleetLoadSignals(QObject):
    loaded = Signal(int, list)
    failed = Signal(int, object)


class _FleetLoader(QRunnable):
    """Run _fleet_rows off the GUI thread."""

    def __init__(self, context, filtr: FleetFilter, gen: int, signals: _FleetLoadSignals) -> None:
        super().__init__()
        self._context = context
        self._filtr = filtr
        self._gen = gen
        self._signals = signals

    def run(self) -> None:
        try:
            rows = _fleet_rows(self._context, self._filtr)
        except Exception as exc:  # noqa: BLE001 - forwarded to the GUI slot
            self._signals.failed.emit(self._gen, exc)
        else:
            self._signals.loaded.emit(self._gen, rows)


class FleetPage(StaffPage):
    HEADER = False

    def __init__(self, shell) -> None:
        super().__init__(
            shell,
            "Fleet",
            "Every vehicle, whether it is out, and what is stopping it.",
        )
        self._filter = FleetFilter()
        # Two white main containers (bookings recipe, 28px): tools on top,
        # data below, thin gap between. 8+8 split with the shell gap.
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
        data.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        data_layout = QVBoxLayout(data)
        data_layout.setContentsMargins(
            PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD
        )
        data_layout.setSpacing(0)
        self.table = LoadStateTable(
            [
                Column("Plate", ALIGN_LEFT),
                Column("Vehicle", ALIGN_LEFT),
                Column("Year", ALIGN_RIGHT),
                Column("Rate / day", ALIGN_RIGHT),
                Column("Odometer", ALIGN_RIGHT),
                Column("Status"),
                Column("Due back", ALIGN_RIGHT),
                Column("Overdue"),
                Column("Blocked by", ALIGN_LEFT),
            ],
            # Empty until the first async refresh: constructing with the
            # real loader would run synchronously inside show_page.
            loader=lambda: [],
            empty_message="No vehicles yet. Add one from the admin tools.",
        )
        self.table.set_loader(partial(_fleet_rows, self.context, self._filter))
        data_layout.addWidget(self.table, 1)
        self.body.addWidget(data, 1)
        self._data_panel = data
        self.table.table.itemSelectionChanged.connect(self._refresh_actions)
        self._fleet_overlay = None
        #: Plate to highlight once the next async reload lands (deep-links
        #: from the Maintenance page, which navigates before rows arrive).
        self._pending_focus: str | None = None

        # -- async reload machinery (whole-page shimmer) ------------------
        self._pool = loader_pool()
        # Media warming gets its own pool so a slow photo fetch never holds up list reloads.
        self._media_pool = media_pool()
        self._load_signals = _FleetLoadSignals(self)
        self._load_signals.loaded.connect(self._on_rows_loaded)
        self._load_signals.failed.connect(self._on_rows_failed)
        self._load_gen = 0
        self._done_gen = 0
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(FILTER_DEBOUNCE_MS)
        self._filter_timer.timeout.connect(self._on_filter_timeout)
        self._skel_bars: list[Skeleton] = []
        self._skel_tools, self._skel_data = self._build_skeleton()
        self.body.addWidget(self._skel_tools)
        self.body.addWidget(self._skel_data, 1)
        self._skel_tools.hide()
        self._skel_data.hide()

    # -- whole-page skeleton (tools + table shimmer) --------------------

    def _skel_bar(self, parent: QWidget, *, width: int | None = None) -> Skeleton:
        bar = Skeleton(parent)
        bar.setFixedHeight(TOOL_ROW_H)
        if width is not None:
            bar.setFixedWidth(width)
            bar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        else:
            bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._skel_bars.append(bar)
        return bar

    @staticmethod
    def _skel_row(parent: QWidget) -> QHBoxLayout:
        row = QHBoxLayout(parent)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        parent.setObjectName("bookingsSkeletonRow")
        return row

    def _build_skeleton(self) -> tuple[QFrame, QFrame]:
        """Two pagePanel clones mimicking tools + table. Built once."""
        skel_tools = QFrame(self)
        skel_tools.setObjectName(theme.OBJ_PAGE_PANEL)
        skel_tools.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        tools_layout = QVBoxLayout(skel_tools)
        tools_layout.setContentsMargins(PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD)
        tools_layout.setSpacing(8)
        filter_host = QWidget(skel_tools)
        filter_row = self._skel_row(filter_host)
        filter_row.addWidget(self._skel_bar(filter_host))
        for width in (130, 140, 90):
            filter_row.addWidget(self._skel_bar(filter_host, width=width))
        tools_layout.addWidget(filter_host)
        action_host = QWidget(skel_tools)
        action_row = self._skel_row(action_host)
        for width in (100, 100, 110, 130, 110):
            action_row.addWidget(self._skel_bar(action_host, width=width))
        action_row.addStretch(1)
        tools_layout.addWidget(action_host)

        skel_data = QFrame(self)
        skel_data.setObjectName(theme.OBJ_PAGE_PANEL)
        skel_data.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        data_layout = QVBoxLayout(skel_data)
        data_layout.setContentsMargins(PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD)
        data_layout.setSpacing(8)
        data_layout.addWidget(self._skel_bar(skel_data))
        for _ in range(FLEET_SKELETON_ROWS):
            data_layout.addWidget(self._skel_bar(skel_data))
        return skel_tools, skel_data

    def _show_skeleton(self, *, full: bool) -> None:
        if full:
            self._tools_panel.hide()
        self._data_panel.hide()
        self._skel_tools.setVisible(full)
        self._skel_data.show()
        for bar in self._skel_bars:
            if bar.parentWidget() is self._skel_tools and not full:
                continue
            bar.set_busy(True)

    def _hide_skeleton(self) -> None:
        for bar in self._skel_bars:
            bar.set_busy(False)
        self._skel_tools.hide()
        self._skel_data.hide()
        self._tools_panel.show()
        self._data_panel.show()

    def _request_load(self, *, full: bool) -> None:
        """Show shimmer now, fill from a pool thread, swap back on signal."""
        self._load_gen += 1
        gen = self._load_gen
        self._show_skeleton(full=full)
        self._pool.start(_FleetLoader(self.context, replace(self._filter), gen, self._load_signals))

    def _on_rows_loaded(self, gen: int, rows: list) -> None:
        if gen != self._load_gen:
            return
        self.table._populate(rows)
        self.table.state = LoadState.LOADED if rows else LoadState.EMPTY
        self.table.last_error = None
        self.table._sync_stack()
        self._done_gen = gen
        self._hide_skeleton()
        if self._pending_focus is not None:
            self.focus_plate(self._pending_focus)
            self._pending_focus = None
        # Selection can survive a repopulate (same row index), in which
        # case no itemSelectionChanged fires -- refresh explicitly.
        self._refresh_actions()

    def _on_rows_failed(self, gen: int, exc: BaseException) -> None:
        if gen != self._load_gen:
            return
        message, _field, _kind = describe(exc)
        self.table._table.setRowCount(0)
        self.table.last_error = exc
        self.table.state = LoadState.FAILED
        self.table._error.setText(f"Could not load this list.\n{message}")
        self.table._error_detail.setText("Nothing has been changed. Retrying is safe.")
        self.table._sync_stack()
        self._done_gen = gen
        self._hide_skeleton()
        self._refresh_actions()

    # -- filter row -----------------------------------------------------

    def _combo(self, items: list[str]) -> QComboBox:
        combo = QComboBox(self)
        combo.addItems(items)
        combo.currentIndexChanged.connect(lambda _index: self._apply_filters())
        return combo

    def _filter_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.search = QLineEdit(self)
        self.search.setObjectName("searchField")
        self.search.setPlaceholderText("Search plate, make, model…")
        self.search.textChanged.connect(lambda _text: self._apply_filters())
        # Concentric with the tools panel (28 - 16 = 12); panel-facing
        # outer corner only, inner corners keep the 6px box radius.
        self.search.setProperty("corner", "topLeft")
        self.search.setFixedHeight(TOOL_ROW_H)
        row.addWidget(self.search, 1)
        self.status_combo = self._combo([label for label, _codes in STATUS_FILTERS])
        self.status_combo.setFixedHeight(TOOL_ROW_H)
        row.addWidget(self.status_combo)
        self.attention_check = QCheckBox("Needs attention", self)
        attention_font = QFont("Inter")
        attention_font.setPixelSize(12)
        attention_font.setWeight(QFont.Weight.DemiBold)
        self.attention_check.setFont(attention_font)
        self.attention_check.setStyleSheet(
            f"color: {theme.MUTED}; background: transparent;"
        )
        self.attention_check.toggled.connect(lambda _on: self._apply_filters())
        row.addWidget(self.attention_check)
        self.clear_button = QPushButton("Clear", self)
        self.clear_button.setObjectName("ghostButton")
        self.clear_button.setProperty("corner", "topRight")
        self.clear_button.setFixedHeight(TOOL_ROW_H)
        self.clear_button.clicked.connect(self._clear_filters)
        row.addWidget(self.clear_button)
        return row

    def _apply_filters(self) -> None:
        self._filter.search = self.search.text()
        _label, codes = STATUS_FILTERS[self.status_combo.currentIndex()]
        self._filter.statuses = codes
        self._filter.needs_attention_only = self.attention_check.isChecked()
        # Debounced: rapid keystrokes become one worker run. The tools
        # panel stays live (table-only shimmer); tab switches use full.
        self._filter_timer.start()

    def _on_filter_timeout(self) -> None:
        self._request_load(full=False)

    def _clear_filters(self) -> None:
        blocked = self.search.blockSignals(True)
        try:
            self.search.clear()
        finally:
            self.search.blockSignals(blocked)
        self.status_combo.setCurrentIndex(0)
        self.attention_check.setChecked(False)

    # -- action row -----------------------------------------------------

    def _action_button(self, text: str, *, danger: bool = False) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName("dangerButton" if danger else "ghostButton")
        button.setFixedHeight(TOOL_ROW_H)
        return button

    def _action_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.add_button = self._action_button("Add")
        # Concentric with the tools panel (28 - 16 = 12); bottom-left
        # outer corner only.
        self.add_button.setProperty("corner", "bottomLeft")
        self.add_button.clicked.connect(self.open_add)
        self.edit_button = self._action_button("Edit")
        self.edit_button.clicked.connect(self.open_edit)
        self.details_button = self._action_button("Details")
        self.details_button.clicked.connect(self.open_details)
        self.bulk_button = self._action_button("Bulk update")
        self.bulk_button.clicked.connect(self.open_bulk)
        self.retire_button = self._action_button("Retire", danger=True)
        self.retire_button.clicked.connect(self._on_retire_button)
        for button in (
            self.add_button,
            self.edit_button,
            self.details_button,
            self.bulk_button,
            self.retire_button,
        ):
            row.addWidget(button)
        row.addStretch(1)
        self._refresh_actions()
        return row

    def _refresh_actions(self) -> None:
        admin = self.context.is_admin
        table = getattr(self, "table", None)
        has_row = self.selected_plate() is not None if table is not None else False
        self.add_button.setEnabled(admin)
        self.edit_button.setEnabled(admin and has_row)
        self.details_button.setEnabled(has_row)
        self.bulk_button.setEnabled(admin)
        self.retire_button.setEnabled(admin and has_row)
        status = self._selected_status()
        if status == "retired":
            self.retire_button.setText("Return to service")
            self.retire_button.setObjectName("ghostButton")
        else:
            self.retire_button.setText("Retire")
            self.retire_button.setObjectName("dangerButton")
        self.retire_button.style().unpolish(self.retire_button)
        self.retire_button.style().polish(self.retire_button)

    # -- selection ------------------------------------------------------

    def selected_plate(self) -> str | None:
        if getattr(self, "table", None) is None:
            return None
        row = self.table.table.currentRow()
        if row < 0:
            return None
        text = self.table.cell_text(row, 0)
        return text or None

    def focus_plate(self, plate: str) -> bool:
        """Select the row for one plate and scroll it into view (landing)."""
        for row in range(self.table.row_count()):
            if self.table.cell_text(row, 0) == plate:
                self.table.table.selectRow(row)
                item = self.table.table.item(row, 0)
                if item is not None:
                    self.table.table.scrollToItem(item)
                return True
        return False

    def _selected_status(self) -> str | None:
        if getattr(self, "table", None) is None:
            return None
        row = self.table.table.currentRow()
        if row < 0:
            return None
        label = self.table.cell_text(row, 5)
        for code in ("available", "reserved", "rented", "maintenance", "retired"):
            if theme.status_label(code) == label:
                return code
        return None

    def _resolve_id(self, plate: str) -> int | None:
        with self.context.reading() as session:
            row = session.execute(
                select(Vehicle).where(Vehicle.plate_number == plate)
            ).scalar_one_or_none()
            return row.vehicle_id if row is not None else None

    # -- overlay panels (no OS windows; same glass as bookings) ---------

    def _overlay_panel(self, panel: QWidget) -> object:
        from app.staff.overlay import FrostedOverlay

        if self._fleet_overlay is not None:
            self._fleet_overlay.hide_overlay()
            self._fleet_overlay.deleteLater()
            self._fleet_overlay = None
        window = self.window()
        overlay = FrostedOverlay(window if window is not None else self)
        # No bar widget on the fleet overlay (wheel/keys still scroll).
        overlay.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        overlay.set_content(panel)
        overlay.show_overlay()
        self._fleet_overlay = overlay
        return overlay

    def _hide_overlay(self) -> None:
        if self._fleet_overlay is not None:
            self._fleet_overlay.hide_overlay()

    # -- actions --------------------------------------------------------

    def open_add(self) -> bool:
        from app.staff.fleet_panels import VehicleFormPanel

        if not self.context.is_admin:
            toast(self, "That action is for administrators.", "error")
            return False
        panel = VehicleFormPanel(self.context, None, self)
        panel.submitted.connect(self._on_add_submit)
        panel.cancelled.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def _warm_media(self, vehicle_id: int, plate: str, *, force: bool = False) -> None:
        """Fetch photos + 3D after a save so new/renamed vehicles skip the
        first-Details-open wait. Freshness-aware; success toasts, failure
        stays quiet (the form hint already spoke)."""
        from app.staff.fleet_panels import _MediaRefresh, _MediaSignals

        signals = _MediaSignals(self)
        signals.done.connect(
            lambda rows: toast(self, f"Media ready for {plate} ({len(rows)}).")
            if rows
            else None
        )
        signals.failed.connect(lambda _exc: None)
        self._media_pool.start(
            _MediaRefresh(self.context, vehicle_id, signals, force=force)
        )

    def _on_add_submit(self, values: dict) -> None:
        try:
            with self.context.session() as session:
                vehicle = fleet_service.add_vehicle(session, **values)
                plate = vehicle.plate_number
                vehicle_id = vehicle.vehicle_id
        except Exception as exc:  # noqa: BLE001
            message, _field, _kind = describe(exc)
            toast(self, message, "error")
            return
        self._hide_overlay()
        self._request_load(full=False)
        toast(self, f"{plate} added to the fleet.")
        self._warm_media(vehicle_id, plate)

    def open_edit(self) -> bool:
        from app.staff.fleet_panels import VehicleFormPanel

        if not self.context.is_admin:
            toast(self, "That action is for administrators.", "error")
            return False
        plate = self.selected_plate()
        if plate is None:
            toast(self, "Select a vehicle first.", "warn")
            return False
        vehicle_id = self._resolve_id(plate)
        if vehicle_id is None:
            toast(self, "That vehicle no longer exists.", "error")
            return False
        panel = VehicleFormPanel(self.context, vehicle_id, self)
        panel.submitted.connect(lambda values: self._on_edit_submit(vehicle_id, values))
        panel.cancelled.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def _on_edit_submit(self, vehicle_id: int, values: dict) -> None:
        try:
            with self.context.session() as session:
                vehicle = session.get(Vehicle, vehicle_id)
                if vehicle is None:
                    raise ServiceError("That vehicle no longer exists.")
                old_identity = (
                    vehicle.make or "",
                    vehicle.model or "",
                    vehicle.year,
                    vehicle.category.category_name
                    if vehicle.category is not None else "",
                )
                new_rate = values.pop("daily_rate")
                changes = dict(values)
                # Rate goes through the logger so extensions stay explainable.
                from app.utils.money import money as _money

                if _money(new_rate, field="daily_rate") != _money(vehicle.daily_rate, field="daily_rate"):
                    fleet_service.set_daily_rate(
                        session, vehicle, new_rate,
                        changed_by=self.context.user.user_id,
                    )
                # Category arrives as a name; the column wants an id.
                category_name = changes.pop("category_name", None)
                if category_name is not None:
                    changes["category_id"] = fleet_service.category_id_for(session, category_name)
                fleet_service.update_vehicle(session, vehicle, **changes)
                plate = vehicle.plate_number
                new_identity = (
                    values.get("make", old_identity[0]),
                    values.get("model", old_identity[1]),
                    values.get("year", old_identity[2]),
                    values.get("category_name", old_identity[3]),
                )
                renamed = tuple(new_identity) != tuple(old_identity)
        except Exception as exc:  # noqa: BLE001
            message, _field, _kind = describe(exc)
            toast(self, message, "error")
            return
        self._hide_overlay()
        self._request_load(full=False)
        toast(self, f"{plate} updated.")
        if renamed:
            # Fresh rows would otherwise keep the old model's photos.
            self._warm_media(vehicle_id, plate, force=True)

    def open_details(self) -> bool:
        from app.staff.fleet_panels import FleetDetailsPanel

        plate = self.selected_plate()
        if plate is None:
            toast(self, "Select a vehicle first.", "warn")
            return False
        vehicle_id = self._resolve_id(plate)
        if vehicle_id is None:
            toast(self, "That vehicle no longer exists.", "error")
            return False
        panel = FleetDetailsPanel(self.context, vehicle_id, self)
        panel.closed.connect(self._hide_overlay)
        panel.edit_requested.connect(lambda _vid: (self._hide_overlay(), self.open_edit()))
        panel.refreshed.connect(lambda message: toast(self, message))
        self._overlay_panel(panel)
        return True

    def open_bulk(self) -> bool:
        from app.staff.fleet_panels import BulkPanel

        if not self.context.is_admin:
            toast(self, "That action is for administrators.", "error")
            return False
        panel = BulkPanel(self.context, self)
        panel.submitted.connect(self._on_bulk_submit)
        panel.cancelled.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def _on_bulk_submit(self, values: dict) -> None:
        try:
            with self.context.session() as session:
                staff_id = self.context.user.user_id
                if values["mode"] == "status":
                    result = fleet_service.bulk_set_status(
                        session, values["ids"], values["new_status"],
                    )
                else:
                    result = fleet_service.bulk_set_rate(
                        session, values["ids"], values["new_rate"],
                        changed_by=staff_id,
                        only_at_rate=values.get("only_at_rate"),
                        note="bulk update",
                    )
        except Exception as exc:  # noqa: BLE001
            message, _field, _kind = describe(exc)
            toast(self, message, "error")
            return
        self._hide_overlay()
        self._request_load(full=False)
        errors = len(result["errors"])
        toast(
            self,
            f"Bulk update: {result['updated']} changed, "
            f"{result['skipped']} skipped"
            + (f", {errors} refused." if errors else "."),
            "error" if errors else "ok",
        )

    def _on_retire_button(self) -> None:
        plate = self.selected_plate()
        if plate is None:
            toast(self, "Select a vehicle first.", "warn")
            return
        if not self.context.is_admin:
            toast(self, "That action is for administrators.", "error")
            return
        if self._selected_status() == "retired":
            if not confirm(
                self,
                f"Return {plate} to service?",
                detail="It becomes bookable again.",
                pref="behavior/confirm_retire",
            ):
                return
            try:
                with self.context.session() as session:
                    vehicle = session.get(Vehicle, self._resolve_id(plate))
                    if vehicle is None:
                        raise ServiceError("That vehicle no longer exists.")
                    fleet_service.unretire_vehicle(session, vehicle)
            except Exception as exc:  # noqa: BLE001
                message, _field, _kind = describe(exc)
                toast(self, message, "error")
                return
            self._request_load(full=False)
            toast(self, f"{plate} is back in service.")
            return
        if not confirm(
            self,
            f"Retire {plate}?",
            detail="It leaves the fleet but stays in history. Refused while it holds a booking.",
            destructive=True,
            pref="behavior/confirm_retire",
        ):
            return
        try:
            with self.context.session() as session:
                vehicle = session.get(Vehicle, self._resolve_id(plate))
                if vehicle is None:
                    raise ServiceError("That vehicle no longer exists.")
                fleet_service.retire_vehicle(session, vehicle)
        except Exception as exc:  # noqa: BLE001
            message, _field, _kind = describe(exc)
            toast(self, message, "error")
            return
        self._request_load(full=False)
        toast(self, f"{plate} retired.")

    def refresh(self) -> None:
        super().refresh()
        # Async: shimmer shows synchronously so show_page returns fast
        # and the tab cuts instantly; rows land on the loaded signal.
        self._request_load(full=True)
        self._refresh_actions()


def build_fleet_page(shell) -> FleetPage:
    return FleetPage(shell)
