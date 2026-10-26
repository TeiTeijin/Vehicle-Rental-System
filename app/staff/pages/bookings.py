from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from functools import partial

from PySide6.QtCore import QDate, QObject, QRunnable, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFrame,
    QLabel,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QWidget,
)
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import joinedload, selectinload

from app.models import Booking, Users, Vehicle
from app.services import booking_service, payment_service
from app.services.errors import ServiceError
from app.staff import prefs, theme
from app.staff.feedback import describe
from app.staff.metrics import CARD_GUTTER, PAGE_PANEL_PAD, SIDEBAR_GAP, TOOL_ROW_H
from app.staff.pages.base import StaffPage
from app.staff.tables import ALIGN_LEFT, ALIGN_RIGHT, Column, LoadState, LoadStateTable
from app.staff.ui_kit import (
    SelectionBar,
    StatusChipRow,
    WarmHost,
    WarmPanel,
)
from app.staff.widgets import confirm, toast
from app.staff.workers import emit_safely, loader_pool
from app.widgets.skeleton import Skeleton
from app.utils.money import ZERO, money, pesos

#: Actions a plain staff member may perform. Anything else is admin.
COUNTER_ACTIONS = ("check_in", "check_out", "record_payment")

#: The table shows status as a human label ("Active" for "ongoing"); the
#: action map speaks the stored code, so read the label back through this.
_STATUS_CODES_BY_LABEL = {
    theme.status_label(code): code for code in theme.STATUS_LABELS
}

#: Booking statuses in which each row action is valid. Details is absent on
#: purpose: any selected row may be opened by any staff role.
ACTION_STATUSES: dict[str, tuple[str, ...]] = {
    "confirm": ("pending",),
    "check_in": ("pending", "confirmed"),
    "check_out": ("ongoing",),
    "record_payment": ("pending", "confirmed", "ongoing", "completed"),
    "extend": ("pending", "confirmed", "ongoing"),
    "cancel": ("pending", "confirmed"),
}

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

#: Reference filter chips -> the stored booking status each one selects.
#: ``status_combo`` keeps its old labels (Active/Completed) as the hidden
#: compatibility bridge, so the chip labels can read like the counter.
STATUS_CHIP_OPTIONS: tuple[tuple[str, str], ...] = (
    ("all", "All"),
    ("pending", "Pending"),
    ("confirmed", "Confirmed"),
    ("ongoing", "Checked in"),
    ("completed", "Checked out"),
    ("cancelled", "Cancelled"),
)

#: Stored status -> the index of its row in ``STATUS_FILTERS``.
_STATUS_FILTER_INDEX = {
    "all": 0,
    "pending": 1,
    "confirmed": 2,
    "ongoing": 3,
    "completed": 4,
    "cancelled": 5,
}

#: The same mapping in reverse, used when the hidden combo moves.
_STATUS_FILTER_KEY = {index: key for key, index in _STATUS_FILTER_INDEX.items()}


@dataclass
class BookingFilter:
    search: str = ""
    statuses: tuple[str, ...] | None = None
    channel: str | None = None
    overdue_only: bool = False
    from_date: date | None = None
    to_date: date | None = None


def _payment_state(balance) -> str:
    if balance.amount_paid <= ZERO:
        return "Unpaid"
    if balance.is_settled:
        return "Paid in full"
    return "Partial"


def _booking_rows(context, filtr: BookingFilter, cap: int | None = None):
    today = date.today()
    if cap is None:
        try:
            from app.staff import prefs as _prefs
            cap = _prefs.rows_limit(100)
        except Exception:
            cap = 500
    cap = max(1, int(cap))
    with context.reading() as session:
        stmt = select(Booking).options(
            joinedload(Booking.user),
            joinedload(Booking.vehicle),
            selectinload(Booking.payments),
            selectinload(Booking.penalties),
        )
        # Every filter and search runs before the cap. Filtering the capped
        # list in Python would hide a matching old booking behind newer rows.
        if filtr.statuses is not None:
            stmt = stmt.where(Booking.status.in_(filtr.statuses))
        if filtr.channel is not None:
            stmt = stmt.where(Booking.channel == filtr.channel)
        if filtr.overdue_only:
            stmt = stmt.where(
                Booking.status == "ongoing", Booking.end_date < today
            )
        if filtr.from_date is not None:
            stmt = stmt.where(Booking.end_date >= filtr.from_date)
        if filtr.to_date is not None:
            stmt = stmt.where(Booking.start_date <= filtr.to_date)
        needle = filtr.search.strip().lower()
        if needle:
            pattern = f"%{needle}%"
            stmt = stmt.where(
                or_(
                    cast(Booking.booking_id, String).like(
                        f"%{needle.lstrip('#')}%"
                    ),
                    func.lower(Booking.reference_code).like(pattern),
                    Booking.user.has(
                        func.lower(Users.full_name).like(pattern)
                    ),
                    Booking.vehicle.has(
                        or_(
                            func.lower(Vehicle.plate_number).like(pattern),
                            func.lower(Vehicle.make).like(pattern),
                            func.lower(Vehicle.model).like(pattern),
                        )
                    ),
                )
            )
        bookings = session.execute(
            stmt.order_by(
                Booking.created_at.desc(), Booking.booking_id.desc()
            ).limit(cap)
        ).scalars().all()
        rows = []
        for booking in bookings:
            balance = payment_service.booking_balance(session, booking)
            vehicle = booking.vehicle
            channel = {"walk_in": "Walk-in", "online": "Online"}.get(
                booking.channel or "", "—"
            )
            rows.append(
                (
                    f"#{booking.booking_id}",
                    booking.user.full_name,
                    f"{vehicle.make} {vehicle.model}\n{vehicle.plate_number}",
                    f"{booking.start_date:%d %b %Y}\n"
                    f"→ {booking.end_date:%d %b %Y}",
                    booking.status,
                    _payment_state(balance),
                    f"{balance.total_due:,.2f}",
                    f"{balance.amount_paid:,.2f}",
                    f"{balance.balance:,.2f}",
                    channel,
                    booking.created_at.strftime("%d %b"),
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

    def __init__(self, context, filtr: BookingFilter, gen: int, signals: _BookingsLoadSignals, *, cap: int | None = None) -> None:
        super().__init__()
        self._context = context
        self._filtr = filtr
        self._gen = gen
        self._cap = cap
        self._loaded = signals.loaded  # bound while the page is alive
        self._failed = signals.failed

    def run(self) -> None:
        try:
            rows = _booking_rows(self._context, self._filtr, cap=self._cap)
        except Exception as exc:  # noqa: BLE001 - forwarded to the GUI slot
            emit_safely(self._failed, self._gen, exc)
        else:
            emit_safely(self._loaded, self._gen, rows)


class BookingsPage(StaffPage):
    HEADER = False

    def __init__(self, shell) -> None:
        super().__init__(
            shell,
            "Bookings",
            "Every rental, its state, and what is still owed on it.",
        )
        self._filter = BookingFilter()
        # Local load-more cap: starts at the counter's rows-per-page pref and
        # grows by 50 up to 500. Filtering and searching still happen before
        # this cap in the query.
        self._row_cap = min(500, max(1, prefs.rows_limit(100)))
        self.layout().setContentsMargins(SIDEBAR_GAP, 0, SIDEBAR_GAP, 0)
        self.body.setSpacing(CARD_GUTTER)

        # The page title lives in the sidebar/shell, not in a second heading
        # above the tools card. Keep the old count label as a hidden
        # compatibility shim for callers that still read it.
        self.count_caption = QLabel("", self)
        self.count_caption.setVisible(False)

        # One transparent host keeps the old `_tools_panel` containment
        # contract: the white filter card, the hidden combo bridge, the chip
        # row, and the cream selection bar all live inside it.
        tools = WarmHost(self)
        self._tools_panel = tools
        self.body.addWidget(tools)

        self.filter_card = WarmPanel(tools)
        self.filter_card.body.addLayout(self._filter_row())
        # The old status dropdown remains as an off-screen compatibility
        # bridge; the visible control is the chip row below.
        self.status_combo.setVisible(False)
        self.filter_card.body.addWidget(self.status_combo)
        self._status_chips = StatusChipRow(
            STATUS_CHIP_OPTIONS, parent=self.filter_card
        )
        self._status_chips.changed.connect(self._on_status_chip)
        #: Public alias matching the other warm list pages.
        self.status_chips = self._status_chips
        self.filter_card.body.addWidget(self._status_chips)
        tools.body.addWidget(self.filter_card)

        self.selection_bar = SelectionBar(tools)
        tools.body.addWidget(self.selection_bar)
        self._action_row()

        data = WarmPanel(self)
        data.body.setContentsMargins(
            PAGE_PANEL_PAD,
            PAGE_PANEL_PAD,
            PAGE_PANEL_PAD,
            PAGE_PANEL_PAD,
        )
        data.body.setSpacing(8)
        # Takes all leftover height so the data box bottom sits level with
        # the sidebar's bottom edge; tools stays hugged up top.
        data.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self.table = LoadStateTable(
            [
                Column("Ref", ALIGN_LEFT, kind="strong", colour="#4C453B"),
                Column("Customer", ALIGN_LEFT, kind="strong"),
                Column("Vehicle", ALIGN_LEFT, wrap=True),
                Column("Dates", ALIGN_LEFT, colour="#4C453B"),
                Column("Status", ALIGN_LEFT, kind="status"),
                Column("Payment", ALIGN_LEFT, kind="payment"),
                Column("Total due", ALIGN_RIGHT, kind="money"),
                Column("Paid", ALIGN_RIGHT, kind="money"),
                Column("Owed", ALIGN_RIGHT, kind="money"),
                Column("Channel", ALIGN_LEFT, kind="muted"),
                Column("Booked", ALIGN_LEFT, kind="muted"),
            ],
            # Empty until the first async refresh: constructing with the
            # real loader would run ~500 rows synchronously inside
            # show_page, before the skeleton can paint a frame.
            loader=lambda: [],
            empty_message="No bookings yet.",
            variant="warm",
            row_height=56,
        )
        self.table.set_loader(partial(_booking_rows, self.context, self._filter))
        data.body.addWidget(self.table, 1)

        footer = QWidget(data)
        footer.setObjectName("warmTableFooter")
        footer_row = QHBoxLayout(footer)
        footer_row.setContentsMargins(4, 8, 4, 0)
        footer_row.setSpacing(12)
        self.footer_count = QLabel("", footer)
        footer_font = QFont("Inter")
        footer_font.setPixelSize(12)
        self.footer_count.setFont(footer_font)
        self.footer_count.setStyleSheet(
            "color: #6A645C; background: transparent;"
        )
        footer_row.addWidget(self.footer_count, 1)
        self.load_more_button = QPushButton("Load more", footer)
        self.load_more_button.setObjectName("warmLoadMore")
        self.load_more_button.setFixedHeight(32)
        self.load_more_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.load_more_button.clicked.connect(self._load_more)
        self.load_more_button.hide()
        footer_row.addWidget(self.load_more_button)
        self._footer = footer
        data.body.addWidget(footer)

        self.body.addWidget(data, 1)
        self._data_panel = data
        self._update_footer(0)
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
        """Two warm panel clones mimicking tools + table. Built once."""
        skel_tools = WarmPanel(self)
        skel_tools.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        tools_layout = skel_tools.body
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

        skel_data = WarmPanel(self)
        skel_data.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        data_layout = skel_data.body
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
        self._pool.start(
            _BookingsLoader(
                self.context,
                replace(self._filter),
                gen,
                self._load_signals,
                cap=self._row_cap,
            )
        )

    def _on_rows_loaded(self, gen: int, rows: list) -> None:
        if gen != self._load_gen:
            return
        self.table._populate(rows)
        self.table.state = LoadState.LOADED if rows else LoadState.EMPTY
        self.table.last_error = None
        self.table._sync_stack()
        self._paint_owed()
        self._done_gen = gen
        self._hide_skeleton()
        self.count_caption.setText(
            f"Latest {self._row_cap} bookings (showing {len(rows)})."
        )
        self._update_footer(len(rows))
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
        self._update_footer(0)
        self._refresh_actions()

    # -- load more -------------------------------------------------------

    def _update_footer(self, showing: int) -> None:
        self.footer_count.setText(
            f"Showing {showing} of latest {self._row_cap}"
        )
        more = showing == self._row_cap and self._row_cap < 500
        self.load_more_button.setVisible(more)

    def _load_more(self) -> None:
        if self._row_cap >= 500:
            return
        self._row_cap = min(500, self._row_cap + 50)
        self.load_more_button.hide()
        self._request_load(full=False)

    def _paint_owed(self) -> None:
        """Red for money still owed, muted for a settled row."""
        for row in range(self.table.row_count()):
            item = self.table.table.item(row, 8)
            if item is None:
                continue
            try:
                amount = Decimal(item.text().replace(",", ""))
            except Exception:  # noqa: BLE001 - leave unexpected text alone
                continue
            colour = "#9B2C1F" if amount > ZERO else "#7D7265"
            item.setForeground(QBrush(QColor(colour)))

    # -- filter row -----------------------------------------------------

    def _combo(self, items: list[str], on_change=None) -> QComboBox:
        combo = QComboBox(self)
        combo.addItems(items)
        combo.currentIndexChanged.connect(
            on_change or (lambda _index: self._apply_filters())
        )
        return combo

    @staticmethod
    def _filter_label(text: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        font = QFont("Inter")
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold)
        label.setFont(font)
        label.setStyleSheet(
            f"color: {theme.MUTED}; background: transparent;"
        )
        return label

    def _filter_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.search = QLineEdit(self)
        self.search.setObjectName("warmInput")
        self.search.setPlaceholderText("Search ref, customer, plate…")
        self.search.textChanged.connect(lambda _text: self._apply_filters())
        self.search.setFixedHeight(40)
        row.addWidget(self.search, 1)
        self.status_combo = self._combo(
            [label for label, _codes in STATUS_FILTERS],
            on_change=self._on_status_combo_changed,
        )
        self.status_combo.setFixedHeight(40)
        self.channel_combo = self._combo(
            [label for label, _code in CHANNEL_FILTERS]
        )
        self.channel_combo.setObjectName("warmInput")
        self.channel_combo.setFixedHeight(40)
        row.addWidget(self.channel_combo)
        self.overdue_check = QPushButton("Overdue only", self)
        self.overdue_check.setObjectName("warmCheck")
        self.overdue_check.setCheckable(True)
        overdue_font = QFont("Inter")
        overdue_font.setPixelSize(12)
        overdue_font.setWeight(QFont.Weight.DemiBold)
        self.overdue_check.setFont(overdue_font)
        self.overdue_check.setFixedHeight(32)
        self.overdue_check.toggled.connect(lambda _on: self._apply_filters())
        row.addWidget(self.overdue_check)
        row.addWidget(self._filter_label("From", self))
        self.from_edit = QDateEdit(self)
        self.from_edit.setObjectName("warmInput")
        self.from_edit.setCalendarPopup(True)
        self.from_edit.setDisplayFormat("dd MMM yyyy")
        self.from_edit.setDate(QDate.currentDate().addMonths(-3))
        self.from_edit.dateChanged.connect(lambda _day: self._apply_filters())
        self.from_edit.setFixedHeight(40)
        row.addWidget(self.from_edit)
        row.addWidget(self._filter_label("To", self))
        self.to_edit = QDateEdit(self)
        self.to_edit.setObjectName("warmInput")
        self.to_edit.setCalendarPopup(True)
        self.to_edit.setDisplayFormat("dd MMM yyyy")
        self.to_edit.setDate(QDate.currentDate().addMonths(3))
        self.to_edit.dateChanged.connect(lambda _day: self._apply_filters())
        self.to_edit.setFixedHeight(40)
        row.addWidget(self.to_edit)
        self.clear_button = QPushButton("Clear", self)
        self.clear_button.setObjectName("ghostButton")
        self.clear_button.setFixedHeight(40)
        self.clear_button.clicked.connect(self._clear_filters)
        row.addWidget(self.clear_button)
        return row

    def _on_status_chip(self, key: str) -> None:
        index = _STATUS_FILTER_INDEX.get(key, 0)
        if self.status_combo.currentIndex() != index:
            # The combo's own handler applies the filter and mirrors the chip.
            self.status_combo.setCurrentIndex(index)
        else:
            self._apply_filters()

    def _on_status_combo_changed(self, index: int) -> None:
        chips = getattr(self, "_status_chips", None)
        if chips is not None:
            chips.select(_STATUS_FILTER_KEY.get(index, "all"))
        self._apply_filters()

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
        if hasattr(self, "_status_chips"):
            self._status_chips.select("all")
        self.channel_combo.setCurrentIndex(0)
        self.overdue_check.setChecked(False)
        self.from_edit.setDate(QDate.currentDate().addMonths(-3))
        self.to_edit.setDate(QDate.currentDate().addMonths(3))
        # A blocked search signal means a search-only clear would otherwise
        # leave the old query in place when every other control is already
        # at its default. One explicit apply covers all combinations.
        self._apply_filters()

    # -- action row -----------------------------------------------------

    def _action_button(self, text: str, action: str, *, danger: bool = False) -> QPushButton:
        button = QPushButton(text, self)
        button.setFixedHeight(36)
        button.clicked.connect(lambda: self._on_action_button(action))
        return button

    def _action_row(self):
        self.confirm_button = self._action_button("Confirm", "confirm")
        self.check_in_button = self._action_button("Check in", "check_in")
        self.check_out_button = self._action_button("Check out", "check_out")
        self.pay_button = self._action_button("Record payment", "record_payment")
        self.extend_button = self._action_button("Extend", "extend")
        self.details_button = self._action_button("Details", "details")
        self.cancel_button = self._action_button("Cancel booking", "cancel", danger=True)
        self._action_buttons = {
            "confirm": self.confirm_button,
            "check_in": self.check_in_button,
            "check_out": self.check_out_button,
            "record_payment": self.pay_button,
            "extend": self.extend_button,
            "cancel": self.cancel_button,
            "details": self.details_button,
        }
        for button, kind in (
            (self.confirm_button, "action"),
            (self.check_in_button, "action"),
            (self.check_out_button, "action"),
            (self.pay_button, "primary"),
            (self.extend_button, "action"),
            (self.details_button, "action"),
            (self.cancel_button, "danger"),
        ):
            self.selection_bar.add_action(button, kind=kind)
        self._refresh_actions()
        return self.selection_bar.actions

    def _refresh_actions(self) -> None:
        if not hasattr(self, "_action_buttons"):
            return
        booking_id = self.selected_booking_id()
        self.selection_bar.set_active(booking_id is not None)
        if booking_id is not None:
            row = self.table.table.currentRow()
            customer = self.table.cell_text(row, 1)
            vehicle = self.table.cell_text(row, 2).splitlines()[0]
            self.selection_bar.set_parts(
                f"#{booking_id} · ", customer, vehicle
            )
        status = self.selected_booking_status()
        for action, button in self._action_buttons.items():
            button.setEnabled(self.action_allowed(action, status))

    def action_allowed(self, action: str, status: str | None) -> bool:
        """Whether `action` applies to a row in `status` and this role.

        The service layer re-checks the transition; this only keeps the
        toolbar honest, so a clerk never opens Check out on a completed
        rental or performs a row action with nothing selected.
        """
        if status is None:
            return False
        if action == "details":
            return True
        if not self.can(action):
            return False
        return status in ACTION_STATUSES.get(action, ())

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
        if not hasattr(self, "table"):
            return None
        row = self.table.table.currentRow()
        if row < 0:
            return None
        text = self.table.cell_text(row, 0)
        if not text.startswith("#"):
            return None
        return int(text[1:])

    def selected_booking_status(self) -> str | None:
        # The action row is built before the table; until then nothing is
        # selected, so every action stays disabled.
        table = getattr(self, "table", None)
        if table is None:
            return None
        row = table.table.currentRow()
        if row < 0:
            return None
        label = table.cell_text(row, 4)
        return _STATUS_CODES_BY_LABEL.get(label) or label.strip().lower() or None

    def selected_booking_dates(self) -> tuple[date, date] | None:
        """Current (From, To) of the selected row, for the extend dialog."""
        from datetime import datetime as _datetime

        row = self.table.table.currentRow()
        if row < 0:
            return None
        parts = [
            part.strip().lstrip("→ ").strip()
            for part in self.table.cell_text(row, 3).splitlines()
            if part.strip()
        ]
        if len(parts) != 2:
            return None
        try:
            start = _datetime.strptime(parts[0], "%d %b %Y").date()
            end = _datetime.strptime(parts[1], "%d %b %Y").date()
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
        overlay = FrostedOverlay(
            window if window is not None else self, card_width=540
        )
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

    def _submit_for(self, booking_id: int, action: str, values: dict) -> bool:
        if self.run_for(booking_id, action, **values):
            self._hide_overlay()
            return True
        # Same contract as _submit: the form stays open on failure.
        return False

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
            outstanding = payment_service.booking_balance(session, booking).balance
            customer = booking.user.full_name
            vehicle_name = f"{booking.vehicle.make} {booking.vehicle.model}"
        panel = CheckInPanel(
            self,
            current_mileage=floor,
            outstanding=outstanding,
            booking_id=booking_id,
            customer=customer,
            vehicle=vehicle_name,
        )
        panel.submitted.connect(
            lambda values: self._submit_for(booking_id, "check_in", values)
        )
        panel.collect_requested.connect(
            lambda: self._collect_pickup_balance(booking_id)
        )
        panel.cancelled.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def _collect_pickup_balance(self, booking_id: int) -> None:
        """Hand the clerk straight from check-in to the payment form."""
        self._hide_overlay()
        self.open_payment_for(booking_id)

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
            customer = booking.user.full_name
            vehicle_name = f"{booking.vehicle.make} {booking.vehicle.model}"
        panel = CheckOutPanel(
            self._outstanding(booking_id),
            self,
            pickup_mileage=floor,
            booking_id=booking_id,
            customer=customer,
            vehicle=vehicle_name,
        )
        panel.submitted.connect(
            lambda values: self._submit_check_out(booking_id, values)
        )
        panel.cancelled.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def _submit_check_out(self, booking_id: int, values: dict) -> bool:
        """Quote the return; a penalty shortfall is collected before closing."""
        try:
            with self.context.reading() as session:
                booking = session.get(Booking, booking_id)
                if booking is None:
                    toast(self, "That booking no longer exists.", "error")
                    return False
                balance = payment_service.booking_balance(session, booking)
                quote = booking_service.checkout_quote(
                    session,
                    booking,
                    actual_return_date=date.today(),
                    damage_charge=values.get("damage_charge"),
                )
                customer = booking.user.full_name
                vehicle_name = f"{booking.vehicle.make} {booking.vehicle.model}"
        except Exception as exc:  # noqa: BLE001 - surfaced as a toast
            message, _field, _kind = describe(exc)
            toast(self, message, "error")
            return False

        if quote.balance_due <= ZERO:
            return self._submit_for(booking_id, "check_out", values)

        from app.staff.booking_panels import PaymentPanel

        settlement_total = max(
            ZERO,
            balance.total_due
            + quote.late_fee
            + quote.damage_charge
            - quote.deposit_applied,
        )
        panel = PaymentPanel(
            quote.balance_due,
            self,
            heading="Settle and check out",
            total=settlement_total,
            paid=balance.amount_paid,
            booking_id=booking_id,
            customer=customer,
            vehicle=vehicle_name,
        )

        def _settle(payment: dict) -> None:
            combined = dict(values)
            combined.update(
                settlement_amount=payment["amount"],
                settlement_method=payment["method"],
                settlement_reference=payment.get("reference_no") or "",
            )
            self._submit_for(booking_id, "check_out", combined)

        panel.submitted.connect(_settle)
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
            booking = session.get(Booking, booking_id)
            if booking is None:
                _toast(self, "That booking no longer exists.", "error")
                return False
            balance = payment_service.booking_balance(session, booking)
            customer = booking.user.full_name
            vehicle_name = f"{booking.vehicle.make} {booking.vehicle.model}"
        try:
            self.focus_booking(booking_id)
        except Exception:  # noqa: BLE001 - highlight only, never blocks payment
            pass
        panel = PaymentPanel(
            balance.balance,
            self,
            total=balance.total_due,
            paid=balance.amount_paid,
            show_kind=True,
            booking_id=booking_id,
            customer=customer,
            vehicle=vehicle_name,
        )
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
        booking_id = self.selected_booking_id()
        if self._selected_or_warn() is None or booking_id is None:
            return False
        from app.staff.booking_panels import ReasonPanel
        from app.staff.widgets import toast as _toast

        with self.context.reading() as session:
            booking = session.get(Booking, booking_id)
            if booking is None:
                _toast(self, "That booking no longer exists.", "error")
                return False
            balance = payment_service.booking_balance(session, booking)
            customer = booking.user.full_name
            vehicle_name = f"{booking.vehicle.make} {booking.vehicle.model}"
            paid_so_far = balance.amount_paid + balance.deposit_held
            refund_due = balance.deposit_held

        panel = ReasonPanel(
            self,
            booking_id=booking_id,
            customer=customer,
            vehicle=vehicle_name,
            paid_so_far=paid_so_far,
            refund_due=refund_due,
        )
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
    from app.services.errors import StateError

    # The manual Confirm button means "verified paid": rental covered in
    # full plus the refundable deposit on file. The wizard's Pay button
    # keeps its own path (fee-only walk-ins stay confirmed + OUTSTANDING),
    # so the service stays permissive and only this button gates.
    balance = payment_service.booking_balance(session, booking)
    try:
        from app.services import settings_service as _settings

        required_deposit = _settings.deposit_for_class(
            session, getattr(booking.vehicle, "vehicle_class", None)
        )
    except Exception:  # noqa: BLE001 - fall back to code constants
        required_deposit = booking_service.deposit_for_vehicle(booking.vehicle)
    problems = []
    if balance.balance > ZERO:
        problems.append(f"rental balance {pesos(balance.balance)} still owed")
    if balance.deposit_held < required_deposit:
        problems.append(f"deposit {pesos(required_deposit)} not recorded")
    if problems:
        raise StateError(
            "Cannot confirm: " + "; ".join(problems) + ". Record payment first."
        )
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
        require_settlement=True,
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
    settlement_amount: Decimal | None = None,
    settlement_method: str | None = None,
    settlement_reference: str | None = None,
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
        settlement_amount=settlement_amount,
        settlement_method=settlement_method,
        settlement_reference=settlement_reference,
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
    kind: str = "rental",
    **_,
) -> None:
    payment_service.record_payment(
        session,
        booking,
        amount,
        method,
        kind=kind,
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
