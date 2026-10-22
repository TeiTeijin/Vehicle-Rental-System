from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from functools import partial

from PySide6.QtCore import QDate, QObject, QRunnable, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select

from app.models import Booking
from app.services import booking_service, payment_service
from app.services.errors import ServiceError
from app.staff import theme
from app.staff.feedback import describe
from app.staff.metrics import CARD_GUTTER, PAGE_PANEL_PAD, SIDEBAR_GAP, TOOL_ROW_H
from app.staff.pages.base import StaffPage
from app.staff.tables import ALIGN_LEFT, ALIGN_RIGHT, Column, LoadState, LoadStateTable
from app.staff.widgets import confirm, toast
from app.staff.workers import loader_pool
from app.widgets.skeleton import Skeleton
from app.utils.money import ZERO, money, pesos

#: Actions a plain staff member may perform. Anything else is admin.
COUNTER_ACTIONS = ("check_in", "check_out", "record_payment")

#: Display label -> stored booking status codes, for the filter combo.
STATUS_FILTERS: tuple[tuple[str, tuple[str, ...] | None], ...] = (
    ("All statuses", None),
    ("Pending", ("pending",)),
    ("Confirmed", ("confirmed",)),
    ("Active", ("ongoing",)),
    ("Completed", ("completed",)),
    ("Cancelled", ("cancelled",)),
)

CHANNEL_FILTERS: tuple[tuple[str, str | None], ...] = (
    ("All channels", None),
    ("Walk-in", "walk_in"),
    ("Online", "online"),
)


@dataclass
class BookingFilter:
    search: str = ""
    statuses: tuple[str, ...] | None = None
    channel: str | None = None
    overdue_only: bool = False
    from_date: date | None = None
    to_date: date | None = None


def _matches(booking: Booking, balance, filtr: BookingFilter, today: date) -> bool:
    if filtr.statuses is not None and booking.status not in filtr.statuses:
        return False
    if filtr.channel is not None and (booking.channel or "") != filtr.channel:
        return False
    if filtr.overdue_only and not (
        booking.status == "ongoing" and booking.end_date < today
    ):
        return False
    if filtr.from_date is not None and booking.end_date < filtr.from_date:
        return False
    if filtr.to_date is not None and booking.start_date > filtr.to_date:
        return False
    needle = filtr.search.strip().lower()
    if needle:
        haystacks = [
            str(booking.booking_id),
            (booking.user.full_name if booking.user else ""),
            (booking.vehicle.plate_number if booking.vehicle else ""),
            (booking.vehicle.make if booking.vehicle else ""),
            (booking.vehicle.model if booking.vehicle else ""),
        ]
        if not any(needle in (hay or "").lower() for hay in haystacks):
            return False
    return True


def _payment_state(balance) -> str:
    if balance.amount_paid <= ZERO:
        return "Unpaid"
    if balance.is_settled:
        return "Paid in full"
    return "Partial"


def _booking_rows(context, filtr: BookingFilter):
    today = date.today()
    try:
        from app.staff import prefs as _prefs
        cap = _prefs.rows_limit(500)
    except Exception:
        cap = 500
    with context.reading() as session:
        bookings = session.execute(
            select(Booking).order_by(Booking.start_date.desc()).limit(cap)
        ).scalars().all()
        rows = []
        for booking in bookings:
            balance = payment_service.booking_balance(session, booking)
            if not _matches(booking, balance, filtr, today):
                continue
            vehicle = booking.vehicle
            channel = {"walk_in": "Walk-in", "online": "Online"}.get(
                booking.channel or "", "—"
            )
            rows.append(
                (
                    f"#{booking.booking_id}",
                    booking.user.full_name,
                    f"{vehicle.plate_number} {vehicle.make} {vehicle.model}",
                    booking.start_date.strftime("%d %b %Y"),
                    booking.end_date.strftime("%d %b %Y"),
                    booking.status,
                    f"{balance.total_due:,.2f}",
                    f"{balance.amount_paid:,.2f}",
                    f"{balance.balance:,.2f}",
                    booking.created_at.strftime("%d %b"),
                    channel,
                    _payment_state(balance),
                    f"{balance.deposit_held:,.2f}",
                )
            )
        return rows


#: Shimmer rows in the table mimic. 8 x 36 + 7 x 8 = 344px: fills a
#: typical panel without scrolling. Fixed, never derived from viewport
#: geometry (not final until the event loop runs).
BOOKINGS_SKELETON_ROWS = 8

#: Filter keystroke quiet period before a reload is queued. Rapid typing
#: becomes one worker run; stale results are dropped by generation.
FILTER_DEBOUNCE_MS = 120


class _BookingsLoadSignals(QObject):
    loaded = Signal(int, list)
    failed = Signal(int, object)


class _BookingsLoader(QRunnable):
    """Run _booking_rows off the GUI thread.

    Opens its own session via context.reading() and emits plain
    formatted tuples only -- never ORM rows across threads.
    """

    def __init__(self, context, filtr: BookingFilter, gen: int, signals: _BookingsLoadSignals) -> None:
        super().__init__()
        self._context = context
        self._filtr = filtr
        self._gen = gen
        self._signals = signals

    def run(self) -> None:
        try:
            rows = _booking_rows(self._context, self._filtr)
        except Exception as exc:  # noqa: BLE001 - forwarded to the GUI slot
            self._signals.failed.emit(self._gen, exc)
        else:
            self._signals.loaded.emit(self._gen, rows)


class BookingsPage(StaffPage):
    HEADER = False

    def __init__(self, shell) -> None:
        super().__init__(
            shell,
            "Bookings",
            "Every rental, its state, and what is still owed on it.",
        )
        self._filter = BookingFilter()
        # Two white main containers (dashboard sheet recipe, 28px): tools
        # on top, data below, thin gap between. The controls themselves
        # stay transparent -- only the containers are white. 8+8 split
        # with the shell gap: sidebar-right (8) + page-left (8) = 16,
        # visually equal to the window-to-sidebar margin.
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
        self.table = LoadStateTable(
            [
                Column("Ref", ALIGN_RIGHT),
                Column("Customer", ALIGN_LEFT),
                Column("Vehicle", ALIGN_LEFT),
                Column("From", ALIGN_LEFT),
                Column("To", ALIGN_LEFT),
                Column("Status"),
                Column("Total due", ALIGN_RIGHT),
                Column("Paid", ALIGN_RIGHT),
                Column("Owed", ALIGN_RIGHT),
                Column("Booked", ALIGN_LEFT),
                Column("Channel", ALIGN_LEFT),
                Column("Payment", ALIGN_LEFT),
                Column("Deposit", ALIGN_RIGHT),
            ],
            # Empty until the first async refresh: constructing with the
            # real loader would run ~500 rows synchronously inside
            # show_page, before the skeleton can paint a frame.
            loader=lambda: [],
            empty_message="No bookings yet.",
        )
        self.table.set_loader(partial(_booking_rows, self.context, self._filter))
        data_layout.addWidget(self.table, 1)
        self.body.addWidget(data, 1)
        self._data_panel = data
        self.table.table.itemSelectionChanged.connect(self._refresh_actions)
        self._selected: Booking | None = None
        self._booking_overlay = None
        #: Booking to highlight once the next async reload lands (deep-links
        #: from Payments Collect and Inspections Open booking, which navigate
        #: before rows have arrived).
        self._pending_focus: int | None = None

        # -- async reload machinery (whole-page shimmer) ------------------
        self._pool = loader_pool()
        self._load_signals = _BookingsLoadSignals(self)
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
        for width in (130, 120, 110, 130, 130, 90):
            filter_row.addWidget(self._skel_bar(filter_host, width=width))
        tools_layout.addWidget(filter_host)
        action_host = QWidget(skel_tools)
        action_row = self._skel_row(action_host)
        for width in (110, 110, 120, 150, 100, 100, 100):
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
        for _ in range(BOOKINGS_SKELETON_ROWS):
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
        self._pool.start(_BookingsLoader(self.context, replace(self._filter), gen, self._load_signals))

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
            self.focus_booking(self._pending_focus)
            self._pending_focus = None
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
        self.search.setPlaceholderText("Search ref, customer, plate…")
        self.search.textChanged.connect(lambda _text: self._apply_filters())
        # Concentric with the tools panel (28 - 16 = 12); panel-facing
        # outer corner only, inner corners keep the 6px box radius.
        self.search.setProperty("corner", "topLeft")
        self.search.setFixedHeight(TOOL_ROW_H)
        row.addWidget(self.search, 1)
        self.status_combo = self._combo([label for label, _codes in STATUS_FILTERS])
        self.status_combo.setFixedHeight(TOOL_ROW_H)
        row.addWidget(self.status_combo)
        self.channel_combo = self._combo([label for label, _code in CHANNEL_FILTERS])
        self.channel_combo.setFixedHeight(TOOL_ROW_H)
        row.addWidget(self.channel_combo)
        self.overdue_check = QCheckBox("Overdue only", self)
        overdue_font = QFont("Inter")
        overdue_font.setPixelSize(12)
        overdue_font.setWeight(QFont.Weight.DemiBold)
        self.overdue_check.setFont(overdue_font)
        self.overdue_check.setStyleSheet(
            f"color: {theme.MUTED}; background: transparent;"
        )
        self.overdue_check.toggled.connect(lambda _on: self._apply_filters())
        row.addWidget(self.overdue_check)
        self.from_edit = QDateEdit(self)
        self.from_edit.setCalendarPopup(True)
        self.from_edit.setDisplayFormat("dd MMM yyyy")
        self.from_edit.setDate(QDate.currentDate().addMonths(-3))
        self.from_edit.dateChanged.connect(lambda _day: self._apply_filters())
        # Compact vertical padding via QSS (compact=true) + floor height:
        # keeps the 36px row while giving the yyyy section room.
        self.from_edit.setProperty("compact", True)
        self.from_edit.setMinimumHeight(TOOL_ROW_H)
        row.addWidget(self.from_edit)
        self.to_edit = QDateEdit(self)
        self.to_edit.setCalendarPopup(True)
        self.to_edit.setDisplayFormat("dd MMM yyyy")
        self.to_edit.setDate(QDate.currentDate().addMonths(3))
        self.to_edit.dateChanged.connect(lambda _day: self._apply_filters())
        self.to_edit.setProperty("compact", True)
        self.to_edit.setMinimumHeight(TOOL_ROW_H)
        row.addWidget(self.to_edit)
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
        _label, code = CHANNEL_FILTERS[self.channel_combo.currentIndex()]
        self._filter.channel = code
        self._filter.overdue_only = self.overdue_check.isChecked()
        self._filter.from_date = self.from_edit.date().toPython()
        self._filter.to_date = self.to_edit.date().toPython()
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
        self.channel_combo.setCurrentIndex(0)
        self.overdue_check.setChecked(False)
        self.from_edit.setDate(QDate.currentDate().addMonths(-3))
        self.to_edit.setDate(QDate.currentDate().addMonths(3))

    # -- action row -----------------------------------------------------

    def _action_button(self, text: str, action: str, *, danger: bool = False) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName("dangerButton" if danger else "ghostButton")
        button.setFixedHeight(TOOL_ROW_H)
        button.clicked.connect(lambda: self._on_action_button(action))
        return button

    def _action_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.confirm_button = self._action_button("Confirm", "confirm")
        # Concentric with the tools panel (28 - 16 = 12); bottom-left
        # outer corner only.
        self.confirm_button.setProperty("corner", "bottomLeft")
        self.check_in_button = self._action_button("Check in", "check_in")
        self.check_out_button = self._action_button("Check out", "check_out")
        self.pay_button = self._action_button("Record payment", "record_payment")
        self.extend_button = self._action_button("Extend", "extend")
        self.cancel_button = self._action_button("Cancel", "cancel", danger=True)
        self.details_button = self._action_button("Details", "details")
        for button in (
            self.confirm_button,
            self.check_in_button,
            self.check_out_button,
            self.pay_button,
            self.extend_button,
            self.cancel_button,
            self.details_button,
        ):
            row.addWidget(button)
        row.addStretch(1)
        self._action_buttons = {
            "confirm": self.confirm_button,
            "check_in": self.check_in_button,
            "check_out": self.check_out_button,
            "record_payment": self.pay_button,
            "extend": self.extend_button,
            "cancel": self.cancel_button,
        }
        self._refresh_actions()
        return row

    def _refresh_actions(self) -> None:
        for action, button in self._action_buttons.items():
            button.setEnabled(self.can(action))

    def _on_action_button(self, action: str) -> None:
        if action == "check_in":
            self.open_check_in()
        elif action == "check_out":
            self.open_check_out()
        elif action == "record_payment":
            self.open_record_payment()
        elif action == "extend":
            self.open_extend()
        elif action == "cancel":
            self.open_cancel()
        elif action == "details":
            self.open_details()
        else:
            self.run(action)

    def focus_booking(self, booking_id: int) -> bool:
        """Select a row by ref and scroll it into view (wizard landing)."""
        for row in range(self.table.row_count()):
            if self.table.cell_text(row, 0) == f"#{booking_id}":
                self.table.table.selectRow(row)
                item = self.table.table.item(row, 0)
                if item is not None:
                    self.table.table.scrollToItem(item)
                return True
        return False

    def refresh(self) -> None:
        super().refresh()
        # Async: shimmer shows synchronously so show_page returns fast
        # and the tab cuts instantly; rows land on the loaded signal.
        self._request_load(full=True)
        self._refresh_actions()

    def selected_booking_id(self) -> int | None:
        row = self.table.table.currentRow()
        if row < 0:
            return None
        text = self.table.cell_text(row, 0)
        if not text.startswith("#"):
            return None
        return int(text[1:])

    def selected_booking_dates(self) -> tuple[date, date] | None:
        """Current (From, To) of the selected row, for the extend dialog."""
        from datetime import datetime as _datetime

        row = self.table.table.currentRow()
        if row < 0:
            return None
        try:
            start = _datetime.strptime(
                self.table.cell_text(row, 3), "%d %b %Y"
            ).date()
            end = _datetime.strptime(
                self.table.cell_text(row, 4), "%d %b %Y"
            ).date()
        except ValueError:
            return None
        return start, end

    def can(self, action: str) -> bool:
        if self.context.is_admin:
            return True
        return action in COUNTER_ACTIONS

    def run(self, action: str, **kwargs) -> bool:
        booking_id = self.selected_booking_id()
        if booking_id is None:
            toast(self, "Select a booking first.", "warn")
            return False
        return self.run_for(booking_id, action, **kwargs)

    def run_for(self, booking_id: int, action: str, **kwargs) -> bool:
        if not self.can(action):
            message = "That action is for administrators."
            toast(self, message, "error")
            return False

        try:
            # Re-read here, not from the list view: a booking settled by a
            # colleague a moment ago must not be the one cancelled.
            with self.context.session() as session:
                fresh = session.get(Booking, booking_id)
                if fresh is None:
                    raise ServiceError("That booking no longer exists.")
                _ACTIONS[action](session, fresh, self.context, **kwargs)
        except Exception as exc:  # noqa: BLE001
            message, _field, _kind = describe(exc)
            toast(self, message, "error")
            return False

        self._request_load(full=False)
        toast(self, f"#{booking_id} updated.")
        return True

    def confirm_booking(self) -> bool:
        return self.run("confirm")

    def cancel_booking(self, reason: str) -> bool:
        if not confirm(
            self,
            "Cancel this booking?",
            detail="The customer will be told to collect their deposit back.",
            destructive=True,
            pref="behavior/confirm_cancel",
        ):
            return False
        return self.run("cancel", reason=reason)

    # -- action dialogs (collect kwargs, then run like the tests do) --------

    def _selected_or_warn(self) -> int | None:
        booking_id = self.selected_booking_id()
        if booking_id is None:
            toast(self, "Select a booking first.", "warn")
        return booking_id

    # -- overlay panels (no OS windows; same glass as check-details) --------

    def _overlay_panel(self, panel: QWidget) -> object:
        from app.staff.overlay import FrostedOverlay

        if self._booking_overlay is not None:
            self._booking_overlay.hide_overlay()
            self._booking_overlay.deleteLater()
            self._booking_overlay = None
        window = self.window()
        overlay = FrostedOverlay(window if window is not None else self)
        overlay.set_content(panel)
        overlay.show_overlay()
        self._booking_overlay = overlay
        return overlay

    def _hide_overlay(self) -> None:
        if self._booking_overlay is not None:
            self._booking_overlay.hide_overlay()

    def _submit(self, action: str, values: dict) -> None:
        if self.run(action, **values):
            self._hide_overlay()
        # On failure run() already toasted the reason; the form stays open
        # so the clerk can fix it instead of starting over.

    def _submit_for(self, booking_id: int, action: str, values: dict) -> None:
        if self.run_for(booking_id, action, **values):
            self._hide_overlay()
        # Same contract as _submit: the form stays open on failure.

    def open_check_in(self) -> bool:
        booking_id = self._selected_or_warn()
        if booking_id is None:
            return False
        from app.staff.booking_panels import CheckInPanel

        with self.context.reading() as session:
            booking = session.get(Booking, booking_id)
            if booking is None:
                from app.staff.widgets import toast as _toast

                _toast(self, "That booking no longer exists.", "error")
                return False
            floor = int(booking.vehicle.mileage or 0)
        panel = CheckInPanel(self, current_mileage=floor)
        panel.submitted.connect(lambda values: self._submit("check_in", values))
        panel.cancelled.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def open_check_out(self) -> bool:
        booking_id = self._selected_or_warn()
        if booking_id is None:
            return False
        from app.staff.booking_panels import CheckOutPanel

        with self.context.reading() as session:
            booking = session.get(Booking, booking_id)
            if booking is None:
                from app.staff.widgets import toast as _toast

                _toast(self, "That booking no longer exists.", "error")
                return False
            floor = int(booking.vehicle.mileage or 0)
        panel = CheckOutPanel(
            self._outstanding(booking_id), self, pickup_mileage=floor
        )
        panel.submitted.connect(lambda values: self._submit("check_out", values))
        panel.cancelled.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def open_record_payment(self) -> bool:
        booking_id = self._selected_or_warn()
        if booking_id is None:
            return False
        return self.open_payment_for(booking_id)

    def open_payment_for(self, booking_id: int) -> bool:
        """Open the pay form for one booking without needing a table selection.

        The Payments ledger deep-links here: collecting stays a Bookings
        action (same panel, same `record_payment` write path), so the ledger
        never grows its own pay form. Focus the row when it is already
        loaded, but open the form regardless -- the outstanding is re-read
        from the database, not from the list view.
        """
        from app.staff.booking_panels import PaymentPanel
        from app.staff.widgets import toast as _toast

        with self.context.reading() as session:
            if session.get(Booking, booking_id) is None:
                _toast(self, "That booking no longer exists.", "error")
                return False
        try:
            self.focus_booking(booking_id)
        except Exception:  # noqa: BLE001 - highlight only, never blocks payment
            pass
        panel = PaymentPanel(self._outstanding(booking_id), self)
        panel.submitted.connect(
            lambda values: self._submit_for(booking_id, "record_payment", values)
        )
        panel.cancelled.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def open_extend(self) -> bool:
        dates = self.selected_booking_dates()
        if self._selected_or_warn() is None or dates is None:
            return False
        _start, end = dates
        from app.staff.booking_panels import ExtendPanel

        panel = ExtendPanel(
            self.context, self.selected_booking_id(), end, self
        )
        panel.submitted.connect(lambda values: self._submit("extend", values))
        panel.cancelled.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def open_cancel(self) -> bool:
        if self._selected_or_warn() is None:
            return False
        from app.staff.booking_panels import ReasonPanel

        panel = ReasonPanel(self)
        panel.cancelled.connect(self._hide_overlay)
        panel.submitted.connect(self._on_cancel_reason)
        self._overlay_panel(panel)
        return True

    def _on_cancel_reason(self, values: dict) -> bool:
        if self.cancel_booking(values["reason"]):
            self._hide_overlay()
            return True
        return False

    def open_details(self) -> bool:
        booking_id = self._selected_or_warn()
        if booking_id is None:
            return False
        from app.staff.booking_panels import BookingDetailsPanel

        panel = BookingDetailsPanel(self.context, booking_id, self)
        panel.closed.connect(self._on_details_closed)
        self._overlay_panel(panel)
        return True

    def _on_details_closed(self) -> None:
        self._request_load(full=False)
        self._hide_overlay()

    def _outstanding(self, booking_id: int) -> Decimal:
        with self.context.reading() as session:
            booking = session.get(Booking, booking_id)
            if booking is None:
                return money(0)
            return payment_service.booking_balance(session, booking).balance


# -- the actions ----------------------------------------------------------
#
# Each takes a session and a *fresh* booking, called inside
# `StaffContext.session()`.


def _inspector_row(session, context):
    from app.models import Users

    return session.get(Users, context.user.user_id)


def _do_confirm(session, booking, context, **_) -> None:
    booking_service.confirm_booking(session, booking)


def _do_cancel(session, booking, context, *, reason: str, **_) -> None:
    booking_service.cancel_booking(session, booking, reason=reason)


def _do_check_in(session, booking, context, *, mileage: int, fuel_level: str, damage_notes: str, **_) -> None:
    booking_service.check_in(
        session,
        booking,
        inspector=_inspector_row(session, context),
        mileage_reading=mileage,
        fuel_level=fuel_level,
        damage_notes=damage_notes or None,
    )


def _do_check_out(
    session,
    booking,
    context,
    *,
    mileage: int,
    fuel_level: str,
    damage_notes: str,
    damage_charge: Decimal | None,
    **_,
) -> None:
    booking_service.check_out(
        session,
        booking,
        inspector=_inspector_row(session, context),
        actual_return_date=date.today(),
        mileage_reading=mileage,
        fuel_level=fuel_level,
        damage_notes=damage_notes or None,
        damage_charge=damage_charge,
    )


def _do_extend(session, booking, context, *, new_end_date, **_) -> None:
    booking_service.extend_booking(session, booking, new_end_date)


def _do_record_payment(
    session,
    booking,
    context,
    *,
    amount: Decimal,
    method: str,
    reference_no: str,
    note: str,
    **_,
) -> None:
    payment_service.record_payment(
        session,
        booking,
        amount,
        method,
        recorded_by=context.user.user_id,
        reference_no=reference_no or None,
        note=note or None,
    )


_ACTIONS = {
    "confirm": _do_confirm,
    "cancel": _do_cancel,
    "check_in": _do_check_in,
    "check_out": _do_check_out,
    "record_payment": _do_record_payment,
    "extend": _do_extend,
}


def build_bookings_page(shell) -> BookingsPage:
    return BookingsPage(shell)
