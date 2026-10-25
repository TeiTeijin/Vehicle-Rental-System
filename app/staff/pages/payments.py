"""Payments ledger: read-only audit of money taken, plus what is still owed.

Operational rule: money is only *collected* in Bookings (same `PaymentPanel`
+ same `payment_service.record_payment` write path). This page never grows
its own pay form -- its Outstanding list deep-links into Bookings via
`BookingsPage.open_payment_for`, so there is exactly one place that writes.
"""
from __future__ import annotations

from decimal import Decimal
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import joinedload

from app.models import Booking, Payment, Penalty, Users, Vehicle
from app.services import payment_service
from app.staff import prefs, theme
from app.staff.metrics import CARD_GUTTER, PAGE_PANEL_PAD, SIDEBAR_GAP, TOOL_ROW_H
from app.staff.pages.base import StaffPage
from app.staff.tables import (
    ALIGN_LEFT,
    ALIGN_RIGHT,
    Column,
    LoadState,
    LoadStateTable,
)
from app.utils.money import money, pesos

#: Balances at or below this count as settled.
SETTLED_AT = Decimal("0.00")

METHOD_FILTERS: tuple[tuple[str, str | None], ...] = (
    ("All methods", None),
    ("Cash", "cash"),
    ("Card", "card"),
    ("GCash", "gcash"),
)

KIND_FILTERS: tuple[tuple[str, str | None], ...] = (
    ("All kinds", None),
    ("Rental", "rental"),
    ("Deposit", "deposit"),
)

STATUS_FILTERS: tuple[tuple[str, str | None], ...] = (
    ("All statuses", None),
    ("Paid", "paid"),
    ("Pending", "pending"),
    ("Failed", "failed"),
    ("Refunded", "refunded"),
)

#: Which table the single search bar applies to. One bar, never two.
SCOPE_FILTERS: tuple[tuple[str, str], ...] = (
    ("All", "all"),
    ("All payments", "takings"),
    ("Outstanding", "outstanding"),
)


def _matches(needle: str, *haystacks) -> bool:
    """Case-insensitive substring match over the given fields."""
    query = needle.strip().lower()
    if not query:
        return True
    return any(query in (hay or "").lower() for hay in haystacks)


def _payment_rows(context, method=None, kind=None, status=None, search: str = ""):
    limit = prefs.rows_limit(100)
    with context.reading() as session:
        rows = []
        for payment in payment_service.list_payments(
            session, method=method, kind=kind, status=status, limit=limit
        ):
            booking = payment.booking
            customer = booking.user if booking is not None else None
            vehicle = booking.vehicle if booking is not None else None
            if not _matches(
                search,
                f"#{booking.booking_id}" if booking is not None else "",
                customer.full_name if customer is not None else "",
                vehicle.plate_number if vehicle is not None else "",
                vehicle.make if vehicle is not None else "",
                vehicle.model if vehicle is not None else "",
                payment.reference_no or "",
            ):
                continue
            recorded_by = payment.recorder
            rows.append(
                (
                    payment.payment_id,
                    f"#{booking.booking_id}" if booking is not None else "-",
                    customer.full_name if customer is not None else "-",
                    f"{payment.amount:,.2f}",
                    payment.method.title(),
                    payment.kind.title(),
                    payment.status,
                    payment.paid_at.strftime("%d %b %Y %H:%M") if payment.paid_at else "Not cleared",
                    payment.reference_no or "-",
                    recorded_by.full_name if recorded_by is not None else "-",
                )
            )
        return rows


def _takings_total(context) -> Decimal:
    """Rental-kind takings: held deposits are never revenue."""
    with context.reading() as session:
        return payment_service.takings(session)


def _outstanding_rows(context, search: str = ""):
    limit = prefs.rows_limit(100)
    with context.reading() as session:
        penalty_total = (
            select(func.coalesce(func.sum(Penalty.amount), 0))
            .where(Penalty.booking_id == Booking.booking_id)
            .correlate(Booking)
            .scalar_subquery()
        )
        paid_total = (
            select(func.coalesce(func.sum(Payment.amount), 0))
            .where(
                Payment.booking_id == Booking.booking_id,
                Payment.status == "paid",
                Payment.kind.in_(("rental", "deposit_applied")),
            )
            .correlate(Booking)
            .scalar_subquery()
        )
        balance_expr = Booking.total_cost + penalty_total - paid_total
        stmt = (
            select(
                Booking,
                penalty_total.label("penalties"),
                paid_total.label("paid"),
                balance_expr.label("balance"),
            )
            .options(joinedload(Booking.user), joinedload(Booking.vehicle))
            .where(
                Booking.status.in_(("confirmed", "ongoing", "completed")),
                balance_expr > SETTLED_AT,
            )
            .order_by(balance_expr.desc())
            .limit(limit)
        )
        needle = search.strip().lower().lstrip("#")
        if needle:
            pattern = f"%{needle}%"
            stmt = stmt.where(
                or_(
                    cast(Booking.booking_id, String).like(pattern),
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

        rows = []
        for booking, penalties, paid, owed in session.execute(stmt).all():
            vehicle = booking.vehicle
            rental = money(booking.total_cost, field="total_cost")
            penalty = money(penalties or 0, field="penalty_total")
            paid_amount = money(paid or 0, field="amount_paid")
            due = money(rental + penalty, field="total_due")
            balance = money(owed or 0, field="balance")
            vehicle_text = f"{vehicle.plate_number} {vehicle.make} {vehicle.model}"
            rows.append(
                (
                    f"#{booking.booking_id}",
                    booking.user.full_name,
                    vehicle_text,
                    booking.status,
                    f"{rental:,.2f}",
                    f"{penalty:,.2f}",
                    f"{due:,.2f}",
                    f"{paid_amount:,.2f}",
                    f"{balance:,.2f}",
                )
            )
        return rows


class PaymentsPage(StaffPage):
    HEADER = False

    def __init__(self, shell) -> None:
        super().__init__(
            shell,
            "Payments",
            "Everything taken, and everything still owed.",
        )
        from PySide6.QtWidgets import (
            QFrame,
            QSizePolicy,
            QVBoxLayout,
        )

        self._method: str | None = None
        self._kind: str | None = None
        self._status: str | None = None
        self._search: str = ""
        self._scope: str = "all"

        self.takings = LoadStateTable(
            [
                Column("Ref", ALIGN_RIGHT),
                Column("Booking", ALIGN_LEFT),
                Column("Customer", ALIGN_LEFT),
                Column("Amount", ALIGN_RIGHT),
                Column("Method", ALIGN_LEFT),
                Column("Kind", ALIGN_LEFT),
                Column("Status"),
                Column("Taken at", ALIGN_LEFT),
                Column("Reference", ALIGN_LEFT),
                Column("Recorded by", ALIGN_LEFT),
            ],
            self._load_takings,
            empty_message="No payments recorded yet.",
            shimmer_rows=8,
        )

        self.outstanding = LoadStateTable(
            [
                Column("Booking", ALIGN_LEFT),
                Column("Customer", ALIGN_LEFT),
                Column("Vehicle", ALIGN_LEFT),
                Column("Status"),
                Column("Rental", ALIGN_RIGHT),
                Column("Penalties", ALIGN_RIGHT),
                Column("Total due", ALIGN_RIGHT),
                Column("Paid", ALIGN_RIGHT),
                Column("Owed", ALIGN_RIGHT),
            ],
            self._load_outstanding,
            empty_message="Nothing outstanding. Every rental is settled.",
            shimmer_rows=8,
        )

        # -- shared async machinery (one pool, shimmer-then-rows) ------------
        from PySide6.QtCore import QTimer

        from app.staff.async_load import AsyncTable
        from app.staff.workers import loader_pool

        self._pool = loader_pool()
        self._takings_async = AsyncTable(self._pool, self.takings, self)
        self._outstanding_async = AsyncTable(self._pool, self.outstanding, self)
        #: Keystroke quiet period: rapid typing becomes one reload, and the
        #: tools stay live while only the tables shimmer.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self._apply_filters)

        # -- Bookings recipe, three boxes: tools + two data containers -------
        # Tools (filters + actions, transparent controls) on top hugging its
        # content, All payments and Outstanding below sharing the rest evenly.
        # Thin CARD_GUTTER gaps, 8px page edges matching the sidebar rhythm.
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

        takings_box = QFrame(self)
        takings_box.setObjectName(theme.OBJ_PAGE_PANEL)
        takings_box.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        takings_layout = QVBoxLayout(takings_box)
        takings_layout.setContentsMargins(
            PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD
        )
        takings_layout.setSpacing(8)
        takings_layout.addWidget(self._heading("All payments"))
        self.takings_caption = self._heading("")
        takings_layout.addWidget(self.takings_caption)
        takings_layout.addWidget(self.takings, 1)
        self.body.addWidget(takings_box, 1)
        self._takings_panel = takings_box

        outstanding_box = QFrame(self)
        outstanding_box.setObjectName(theme.OBJ_PAGE_PANEL)
        outstanding_box.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        outstanding_layout = QVBoxLayout(outstanding_box)
        outstanding_layout.setContentsMargins(
            PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD
        )
        outstanding_layout.setSpacing(8)
        outstanding_layout.addWidget(self._heading("Outstanding balances"))
        self.outstanding_caption = self._heading("")
        outstanding_layout.addWidget(self.outstanding_caption)
        outstanding_layout.addWidget(self.outstanding, 1)
        self.body.addWidget(outstanding_box, 1)
        self._outstanding_panel = outstanding_box

        self.outstanding.table.itemSelectionChanged.connect(
            self._refresh_collect
        )
        self.outstanding.table.cellDoubleClicked.connect(
            lambda _r, _c: self._on_collect()
        )
        self._refresh_collect()

    def _filter_row(self):
        """Top tools row: one search bar + scope + takings filters + Clear."""
        from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.search = QLineEdit(self)
        self.search.setObjectName("searchField")
        self.search.setPlaceholderText("Search ref, customer, plate…")
        # Concentric with the tools panel (28 - 16 = 12); panel-facing
        # outer corner only, like the Bookings search.
        self.search.setProperty("corner", "topLeft")
        self.search.setFixedHeight(TOOL_ROW_H)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        row.addWidget(self.search, 1)
        self.scope_combo = self._combo([label for label, _code in SCOPE_FILTERS])
        self.scope_combo.currentIndexChanged.connect(
            lambda _i: self._apply_filters()
        )
        row.addWidget(self.scope_combo)
        self.method_combo = self._combo(
            [label for label, _code in METHOD_FILTERS]
        )
        self.method_combo.currentIndexChanged.connect(
            lambda _i: self._apply_filters()
        )
        row.addWidget(self.method_combo)
        self.kind_combo = self._combo([label for label, _code in KIND_FILTERS])
        self.kind_combo.currentIndexChanged.connect(
            lambda _i: self._apply_filters()
        )
        row.addWidget(self.kind_combo)
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
        """Bottom tools row: Collect action + takings total, mirroring Bookings."""
        from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.collect_button = QPushButton("Collect in Bookings", self)
        self.collect_button.setObjectName("ghostButton")
        # Concentric with the tools panel (28 - 16 = 12); bottom-left
        # outer corner only, like the Bookings Confirm button.
        self.collect_button.setProperty("corner", "bottomLeft")
        self.collect_button.setFixedHeight(TOOL_ROW_H)
        self.collect_button.clicked.connect(self._on_collect)
        row.addWidget(self.collect_button)
        hint = QLabel("Collecting happens in Bookings.", self)
        hint.setObjectName("pageSubtitle")
        row.addWidget(hint)
        row.addStretch(1)
        self.takings_total = QLabel("", self)
        self.takings_total.setObjectName("pageSubtitle")
        row.addWidget(self.takings_total)
        return row

    def _clear_filters(self) -> None:
        blocked_search = self.search.blockSignals(True)
        blocked_combos = [
            (combo, combo.blockSignals(True))
            for combo in (
                self.scope_combo,
                self.method_combo,
                self.kind_combo,
                self.status_combo,
            )
        ]
        try:
            self.search.clear()
            self.scope_combo.setCurrentIndex(0)
            self.method_combo.setCurrentIndex(0)
            self.kind_combo.setCurrentIndex(0)
            self.status_combo.setCurrentIndex(0)
        finally:
            self.search.blockSignals(blocked_search)
            for combo, was in blocked_combos:
                combo.blockSignals(was)
        self._apply_filters()

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

    # -- takings ledger -------------------------------------------------

    def _scoped_search(self, table: str) -> str:
        """The needle for one table: empty unless its box is in scope."""
        if self._scope == "all" or self._scope == table:
            return self._search
        return ""

    def _load_takings(self):
        return _payment_rows(
            self.context,
            self._method,
            self._kind,
            self._status,
            search=self._scoped_search("takings"),
        )

    def _load_outstanding(self):
        return _outstanding_rows(
            self.context, search=self._scoped_search("outstanding")
        )

    def _apply_filters(self) -> None:
        self._search = self.search.text()
        _label, self._scope = SCOPE_FILTERS[self.scope_combo.currentIndex()]
        _label, self._method = METHOD_FILTERS[self.method_combo.currentIndex()]
        _label, self._kind = KIND_FILTERS[self.kind_combo.currentIndex()]
        _label, self._status = STATUS_FILTERS[self.status_combo.currentIndex()]
        self._request_load()

    def _request_load(self) -> None:
        """Shimmer now, rows land on the pool signal. Returns instantly so
        tab switches and keystrokes never stall on the queries."""
        self._takings_async.request(self._load_takings)
        self._outstanding_async.request(
            self._load_outstanding, on_done=self._land_outstanding
        )

    def _land_outstanding(self, rows) -> None:
        self.outstanding.set_rows(rows)
        self._refresh_collect()

    def _refresh_takings_total(self) -> None:
        total = _takings_total(self.context)
        self.takings_total.setText(
            f"{pesos(total)} collected (rental only; deposits held separately)."
        )
        limit = prefs.rows_limit(100)
        self.takings_caption.setText(
            f"Latest {limit} payments (change the row limit in Settings to see more)."
        )

    # -- outstanding -> Bookings handoff --------------------------------

    def selected_outstanding_booking_id(self) -> int | None:
        row = self.outstanding.table.currentRow()
        if row < 0:
            return None
        text = self.outstanding.cell_text(row, 0)
        if not text.startswith("#"):
            return None
        try:
            return int(text[1:])
        except ValueError:
            return None

    def _refresh_collect(self) -> None:
        self.collect_button.setEnabled(
            self.selected_outstanding_booking_id() is not None
        )

    def _on_collect(self) -> bool:
        from app.staff.widgets import toast

        booking_id = self.selected_outstanding_booking_id()
        if booking_id is None:
            toast(self, "Select an outstanding booking first.", "warn")
            return False
        if not self.shell.show_page("bookings"):
            return False
        spec = next(s for s in self.shell.pages if s.key == "bookings")
        page = self.shell._page_for(spec)
        page._pending_focus = booking_id
        return page.open_payment_for(booking_id)

    def refresh(self) -> None:
        super().refresh()
        # Controls fire _apply_filters (which loads) when reset
        # programmatically, so block their signals and load once below.
        # Async: shimmer shows synchronously so show_page returns fast
        # and the tab cuts instantly; rows land on the loaded signal.
        blocked_search = self.search.blockSignals(True)
        try:
            self.search.setText(self._search)
        finally:
            self.search.blockSignals(blocked_search)
        for combo, filters, current in (
            (self.scope_combo, SCOPE_FILTERS, self._scope),
            (self.method_combo, METHOD_FILTERS, self._method),
            (self.kind_combo, KIND_FILTERS, self._kind),
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
        self._refresh_takings_total()
        limit = prefs.rows_limit(100)
        self.outstanding_caption.setText(
            f"Largest {limit} balances (change the row limit in Settings to see more)."
        )
        self._refresh_collect()

    @property
    def all_settled(self) -> bool:
        return self.outstanding.state is LoadState.EMPTY


def build_payments_page(shell) -> PaymentsPage:
    return PaymentsPage(shell)
