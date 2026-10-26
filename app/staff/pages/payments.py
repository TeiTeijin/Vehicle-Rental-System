"""Payments ledger: read-only audit of money taken, plus what is still owed.

Operational rule: money is only *collected* in Bookings (same `PaymentPanel`
+ same `payment_service.record_payment` write path). This page never grows
its own pay form -- its Outstanding list deep-links into Bookings via
`BookingsPage.open_payment_for`, so there is exactly one place that writes.
"""
from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Qt
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import joinedload

from app.models import Booking, Payment, Penalty, Users, Vehicle
from app.services import payment_service
from app.staff import prefs
from app.staff.metrics import CARD_GUTTER, SIDEBAR_GAP
from app.staff.pages.base import StaffPage
from app.staff.tables import (
    ALIGN_LEFT,
    ALIGN_RIGHT,
    Column,
    LoadState,
    LoadStateTable,
)
from app.staff.ui_kit import (
    SelectionBar,
    StatusChipRow,
    WarmHost,
    WarmPanel,
)
from app.utils.money import money, pesos

#: Balances at or below this count as settled.
SETTLED_AT = Decimal("0.00")

#: Hard ceiling for the local Load more cap (same as Bookings).
MAX_ROW_CAP = 500
#: Growth per Load more press (same as Bookings).
ROW_STEP = 50

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
    ("Deposit applied", "deposit_applied"),
)

#: Display labels for the Kind column (the stored enum uses underscores).
KIND_LABELS = {
    "rental": "Rental",
    "deposit": "Deposit",
    "deposit_applied": "Deposit applied",
}

#: Visible kind chips. The hidden combo keeps every stored code, including
#: the ledger-adjustment kind, so programmatic callers lose nothing.
KIND_CHIP_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    ("all" if code is None else code, label)
    for label, code in KIND_FILTERS
)

_KIND_CHIP_INDEX = {
    key: index for index, (key, _label) in enumerate(KIND_CHIP_OPTIONS)
}
_KIND_CHIP_KEY = {index: key for key, index in _KIND_CHIP_INDEX.items()}

#: Human labels for the method column ("gcash" -> "GCash").
METHOD_LABELS = {
    "cash": "Cash",
    "card": "Card",
    "gcash": "GCash",
}

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


def _payment_rows(
    context,
    method=None,
    kind=None,
    status=None,
    search: str = "",
    limit: int | None = None,
):
    cap = prefs.rows_limit(100) if limit is None else max(1, int(limit))
    with context.reading() as session:
        rows = []
        for payment in payment_service.list_payments(
            session,
            method=method,
            kind=kind,
            status=status,
            limit=cap,
            search=search,
        ):
            booking = payment.booking
            customer = booking.user if booking is not None else None
            recorded_by = payment.recorder
            if payment.kind == "deposit_applied":
                taken_at = "Ledger adjustment"
            elif payment.paid_at:
                taken_at = payment.paid_at.strftime("%d %b %Y\n%H:%M")
            else:
                taken_at = "Not cleared"
            rows.append(
                (
                    payment.payment_id,
                    f"#{booking.booking_id}" if booking is not None else "—",
                    customer.full_name if customer is not None else "—",
                    f"{payment.amount:,.2f}",
                    METHOD_LABELS.get(
                        (payment.method or "").lower(),
                        (payment.method or "—").title(),
                    ),
                    KIND_LABELS.get(payment.kind or "", "Payment"),
                    payment.status,
                    taken_at,
                    payment.reference_no or "—",
                    recorded_by.full_name if recorded_by is not None else "—",
                )
            )
        return rows


def _takings_total(context) -> Decimal:
    """Rental-kind takings: held deposits are never revenue."""
    with context.reading() as session:
        return payment_service.takings(session)


def _outstanding_rows(context, search: str = "", limit: int | None = None):
    cap = prefs.rows_limit(100) if limit is None else max(1, int(limit))
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
            .limit(cap)
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
            # Reference layout: vehicle name on the first line, plate below.
            vehicle_text = (
                f"{vehicle.make} {vehicle.model}\n{vehicle.plate_number}"
                if vehicle is not None
                else "—"
            )
            rows.append(
                (
                    f"#{booking.booking_id}",
                    booking.user.full_name,
                    vehicle_text,
                    booking.status,
                    f"{rental:,.2f}",
                    f"{penalty:,.2f}" if penalty > SETTLED_AT else "—",
                    f"{due:,.2f}",
                    f"{paid_amount:,.2f}"
                    if paid_amount > SETTLED_AT
                    else "—",
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
        from PySide6.QtWidgets import QSizePolicy

        self._method: str | None = None
        self._kind: str | None = None
        self._status: str | None = None
        self._search: str = ""
        self._scope: str = "all"
        #: Which table owns the current selection. Selecting a card clears the
        #: other card, so the cream bar always names exactly one row.
        self._active_source: str | None = None
        base_cap = min(MAX_ROW_CAP, max(1, prefs.rows_limit(100)))
        self._takings_cap = base_cap
        self._outstanding_cap = base_cap

        self.takings = LoadStateTable(
            [
                Column("Ref", ALIGN_RIGHT, kind="muted"),
                Column("Booking", ALIGN_LEFT, kind="strong"),
                Column("Customer", ALIGN_LEFT, kind="strong"),
                Column("Amount", ALIGN_RIGHT, kind="money"),
                Column("Method", ALIGN_LEFT),
                Column("Kind", ALIGN_LEFT, kind="kind"),
                Column("Status", ALIGN_LEFT, kind="status"),
                Column("Taken at", ALIGN_LEFT, wrap=True, colour="#4C453B"),
                Column("Reference", ALIGN_LEFT, kind="muted"),
                Column("Recorded by", ALIGN_LEFT),
            ],
            self._load_takings,
            empty_message="No payments recorded yet.",
            shimmer_rows=8,
            variant="warm",
            row_height=56,
        )

        self.outstanding = LoadStateTable(
            [
                Column("Booking", ALIGN_LEFT, kind="strong"),
                Column("Customer", ALIGN_LEFT, kind="strong"),
                Column("Vehicle", ALIGN_LEFT, wrap=True, colour="#4C453B"),
                Column("Status", ALIGN_LEFT, kind="status"),
                Column("Rental", ALIGN_RIGHT, kind="money"),
                Column("Penalties", ALIGN_RIGHT, kind="money"),
                Column("Total due", ALIGN_RIGHT, kind="money"),
                Column("Paid", ALIGN_RIGHT, kind="money"),
                Column("Owed", ALIGN_RIGHT, kind="money", colour="#9B2C1F"),
            ],
            self._load_outstanding,
            empty_message="Nothing outstanding. Every rental is settled.",
            shimmer_rows=8,
            variant="warm",
            row_height=56,
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

        # -- warm recipe: tools host, cream selection bar, two data cards ----
        self.layout().setContentsMargins(SIDEBAR_GAP, 0, SIDEBAR_GAP, 0)
        self.body.setSpacing(CARD_GUTTER)

        tools = WarmHost(self)
        self._tools_panel = tools

        self.filter_card = WarmPanel(tools)
        self._filter_card = self.filter_card
        self.filter_card.body.addLayout(self._filter_row())
        self.filter_card.body.addLayout(self._chip_row())
        # Hidden compatibility bridge for callers and tests that still drive
        # the old dropdowns. The visible controls are the chips above.
        self.filter_card.body.addWidget(self.kind_combo)
        tools.body.addWidget(self.filter_card)

        self.selection_bar = SelectionBar(tools)
        self._selection_bar = self.selection_bar
        self._build_selection_actions()
        tools.body.addWidget(self.selection_bar)
        self.body.addWidget(tools)

        takings_box = WarmPanel(self)
        self._takings_panel = takings_box
        takings_box.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        takings_box.body.setSpacing(8)
        self.takings_caption = self._card_caption()
        takings_box.body.addLayout(
            self._card_head("All payments", self.takings_caption)
        )
        takings_box.body.addWidget(self.takings, 1)
        (
            self.takings_footer,
            self.takings_footer_count,
            self.takings_load_more,
        ) = self._table_footer(takings_box)
        takings_box.body.addWidget(self.takings_footer)
        self.body.addWidget(takings_box, 6)

        outstanding_box = WarmPanel(self)
        self._outstanding_panel = outstanding_box
        outstanding_box.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        outstanding_box.body.setSpacing(8)
        self.outstanding_caption = self._card_caption()
        outstanding_box.body.addLayout(
            self._card_head("Outstanding balances", self.outstanding_caption)
        )
        outstanding_box.body.addWidget(self.outstanding, 1)
        (
            self.outstanding_footer,
            self.outstanding_footer_count,
            self.outstanding_load_more,
        ) = self._table_footer(outstanding_box)
        outstanding_box.body.addWidget(self.outstanding_footer)
        self.body.addWidget(outstanding_box, 5)

        self.takings.table.itemSelectionChanged.connect(
            self._on_takings_selected
        )
        self.outstanding.table.itemSelectionChanged.connect(
            self._on_outstanding_selected
        )
        self.outstanding.table.cellDoubleClicked.connect(
            lambda _r, _c: self._on_collect()
        )
        self._refresh_selection()

    # -- construction ----------------------------------------------------

    def _filter_row(self):
        """Top tools row: one search bar + method + status + Clear."""
        from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.search = QLineEdit(self)
        self.search.setObjectName("warmInput")
        self.search.setPlaceholderText("Search ref, customer, plate…")
        self.search.setFixedHeight(40)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        row.addWidget(self.search, 1)
        # The scope dropdown stays available to code/tests but the reference
        # has no visible scope control; one search bar filters both cards.
        self.scope_combo = self._combo([label for label, _code in SCOPE_FILTERS])
        self.scope_combo.currentIndexChanged.connect(
            lambda _i: self._apply_filters()
        )
        self.scope_combo.setVisible(False)
        row.addWidget(self.scope_combo)
        self.method_combo = self._combo(
            [label for label, _code in METHOD_FILTERS]
        )
        self.method_combo.currentIndexChanged.connect(
            lambda _i: self._apply_filters()
        )
        row.addWidget(self.method_combo)
        self.status_combo = self._combo(
            [label for label, _code in STATUS_FILTERS]
        )
        self.status_combo.currentIndexChanged.connect(
            lambda _i: self._apply_filters()
        )
        row.addWidget(self.status_combo)
        self.clear_button = QPushButton("Clear", self)
        self.clear_button.setObjectName("paymentsClear")
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.setFixedHeight(40)
        self.clear_button.clicked.connect(self._clear_filters)
        row.addWidget(self.clear_button)
        return row

    def _chip_row(self):
        """Bottom tools row: kind chips left, takings summary right."""
        from PySide6.QtWidgets import QHBoxLayout, QLabel

        self.kind_combo = self._combo([label for label, _code in KIND_FILTERS])
        self.kind_combo.currentIndexChanged.connect(
            self._on_kind_combo_changed
        )
        self.kind_combo.setVisible(False)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.kind_chips = StatusChipRow(
            KIND_CHIP_OPTIONS, parent=self.filter_card
        )
        self.kind_chips.changed.connect(self._on_kind_chip)
        row.addWidget(self.kind_chips)
        row.addStretch(1)
        self.takings_total = QLabel("", self)
        self.takings_total.setObjectName("paymentsTakings")
        self.takings_total.setTextFormat(Qt.TextFormat.RichText)
        self.takings_total.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        row.addWidget(self.takings_total)
        return row

    def _build_selection_actions(self) -> None:
        from PySide6.QtWidgets import QPushButton

        self.collect_button = QPushButton("Collect in Bookings", self)
        self.collect_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collect_button.clicked.connect(self._on_collect)
        self.view_booking_button = QPushButton("View booking", self)
        self.view_booking_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.view_booking_button.clicked.connect(self._on_view_booking)
        self.selection_bar.add_action(self.collect_button, kind="primary")
        self.selection_bar.add_action(self.view_booking_button, kind="action")

    def _combo(self, items):
        from PySide6.QtWidgets import QComboBox

        combo = QComboBox(self)
        combo.addItems(items)
        combo.setObjectName("warmInput")
        combo.setFixedHeight(40)
        return combo

    def _card_caption(self):
        from PySide6.QtWidgets import QLabel

        label = QLabel("", self)
        label.setObjectName("warmCardCaption")
        return label

    def _card_head(self, title: str, caption):
        from PySide6.QtWidgets import QHBoxLayout, QLabel

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        label = QLabel(title, self)
        label.setObjectName("warmCardTitle")
        row.addWidget(label)
        row.addWidget(caption)
        row.addStretch(1)
        return row

    def _table_footer(self, parent):
        from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

        footer = QWidget(parent)
        footer.setObjectName("warmTableFooter")
        row = QHBoxLayout(footer)
        row.setContentsMargins(4, 4, 4, 0)
        row.setSpacing(12)
        count = QLabel("", footer)
        count.setObjectName("warmFooterRange")
        row.addWidget(count, 1)
        button = QPushButton("Load more", footer)
        button.setObjectName("warmLoadMore")
        button.setFixedHeight(32)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self._load_more)
        button.hide()
        row.addWidget(button)
        return footer, count, button

    # -- filters ---------------------------------------------------------

    def _on_kind_combo_changed(self, index: int) -> None:
        self.kind_chips.select(_KIND_CHIP_KEY.get(index, "all"))
        self._apply_filters()

    def _on_kind_chip(self, key: str) -> None:
        index = _KIND_CHIP_INDEX.get(key, 0)
        if self.kind_combo.currentIndex() != index:
            blocked = self.kind_combo.blockSignals(True)
            try:
                self.kind_combo.setCurrentIndex(index)
            finally:
                self.kind_combo.blockSignals(blocked)
        self._apply_filters()

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
            self.kind_chips.select("all")
        finally:
            self.search.blockSignals(blocked_search)
            for combo, was in blocked_combos:
                combo.blockSignals(was)
        self._apply_filters()

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
            limit=self._takings_cap,
        )

    def _load_outstanding(self):
        return _outstanding_rows(
            self.context,
            search=self._scoped_search("outstanding"),
            limit=self._outstanding_cap,
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
        self.takings_load_more.hide()
        self.outstanding_load_more.hide()
        self._takings_async.request(
            self._load_takings, on_done=self._land_takings
        )
        self._outstanding_async.request(
            self._load_outstanding, on_done=self._land_outstanding
        )

    def _land_takings(self, rows) -> None:
        self.takings.set_rows(rows)
        self._update_takings_footer(len(rows))
        self.takings_caption.setText(
            f"Latest {self._takings_cap} payments · showing {len(rows)}"
        )
        self._refresh_selection()

    def _land_outstanding(self, rows) -> None:
        self.outstanding.set_rows(rows)
        self._update_outstanding_footer(len(rows))
        self.outstanding_caption.setText(
            f"Largest {self._outstanding_cap} balances · showing {len(rows)}"
        )
        self._refresh_selection()

    @staticmethod
    def _range_text(showing: int, cap: int) -> str:
        return f"1–{showing} of {cap}" if showing else f"0 of {cap}"

    def _update_takings_footer(self, showing: int) -> None:
        self.takings_footer_count.setText(
            self._range_text(showing, self._takings_cap)
        )
        # The reference keeps Load more on the card while a larger cap can
        # still be requested; an empty filter result hides it.
        self.takings_load_more.setVisible(
            showing > 0 and self._takings_cap < MAX_ROW_CAP
        )

    def _update_outstanding_footer(self, showing: int) -> None:
        self.outstanding_footer_count.setText(
            self._range_text(showing, self._outstanding_cap)
        )
        self.outstanding_load_more.setVisible(
            showing > 0 and self._outstanding_cap < MAX_ROW_CAP
        )

    def _load_more(self) -> None:
        """Grow whichever footer's Load more was pressed."""
        button = self.sender()
        if button is self.takings_load_more:
            if self._takings_cap >= MAX_ROW_CAP:
                return
            self._takings_cap = min(MAX_ROW_CAP, self._takings_cap + ROW_STEP)
            self.takings_load_more.hide()
            self._takings_async.request(
                self._load_takings, on_done=self._land_takings
            )
        elif button is self.outstanding_load_more:
            if self._outstanding_cap >= MAX_ROW_CAP:
                return
            self._outstanding_cap = min(
                MAX_ROW_CAP, self._outstanding_cap + ROW_STEP
            )
            self.outstanding_load_more.hide()
            self._outstanding_async.request(
                self._load_outstanding, on_done=self._land_outstanding
            )

    def _refresh_takings_total(self) -> None:
        total = _takings_total(self.context)
        self.takings_total.setText(
            f"<b>{pesos(total)}</b> collected · "
            "rental only, deposits held separately"
        )

    # -- selection -------------------------------------------------------

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

    def selected_booking_id(self) -> int | None:
        """Booking behind the selected row, from either card."""
        if self._active_source == "outstanding":
            return self.selected_outstanding_booking_id()
        if self._active_source == "takings":
            row = self.takings.table.currentRow()
            if row < 0:
                return None
            text = self.takings.cell_text(row, 1)
            if not text.startswith("#"):
                return None
            try:
                return int(text[1:])
            except ValueError:
                return None
        return None

    def _on_takings_selected(self) -> None:
        if self.takings.table.selectionModel().hasSelection():
            self._active_source = "takings"
            self._clear_table_selection(self.outstanding)
        elif self._active_source == "takings":
            self._active_source = None
        self._refresh_selection()

    def _on_outstanding_selected(self) -> None:
        if self.outstanding.table.selectionModel().hasSelection():
            self._active_source = "outstanding"
            self._clear_table_selection(self.takings)
        elif self._active_source == "outstanding":
            self._active_source = None
        self._refresh_selection()

    @staticmethod
    def _clear_table_selection(table) -> None:
        model = table.table.selectionModel()
        if model is None:
            return
        blocked = model.blockSignals(True)
        try:
            model.clearSelection()
        finally:
            model.blockSignals(blocked)

    def _refresh_collect(self) -> None:
        """Compatibility name kept for the original selection contract."""
        self._refresh_selection()

    def _refresh_selection(self) -> None:
        booking_id = self.selected_booking_id()
        self.selection_bar.set_active(booking_id is not None)
        has_row = booking_id is not None
        self.collect_button.setEnabled(has_row)
        self.view_booking_button.setEnabled(has_row)
        if not has_row:
            return
        if self._active_source == "takings":
            row = self.takings.table.currentRow()
            customer = self.takings.cell_text(row, 2)
            kind = self.takings.cell_text(row, 5)
            reference = self.takings.cell_text(row, 0)
            self.selection_bar.set_parts(
                "Selected ",
                f"#{booking_id}",
                f"{customer} · Ref {reference} · {kind}",
            )
        else:
            row = self.outstanding.table.currentRow()
            customer = self.outstanding.cell_text(row, 1)
            owed = self.outstanding.cell_text(row, 8)
            self.selection_bar.set_parts(
                "Selected ",
                f"#{booking_id}",
                f"{customer} · ₱{owed} owed",
            )

    # -- outstanding -> Bookings handoff --------------------------------

    def _on_collect(self) -> bool:
        from app.staff.widgets import toast

        booking_id = self.selected_booking_id()
        if booking_id is None:
            toast(self, "Select a payment or booking first.", "warn")
            return False
        if not self.shell.show_page("bookings"):
            return False
        spec = next(s for s in self.shell.pages if s.key == "bookings")
        page = self.shell._page_for(spec)
        page._pending_focus = booking_id
        return page.open_payment_for(booking_id)

    def _on_view_booking(self) -> bool:
        from app.staff.widgets import toast

        booking_id = self.selected_booking_id()
        if booking_id is None:
            toast(self, "Select a payment or booking first.", "warn")
            return False
        if not self.shell.show_page("bookings"):
            return False
        spec = next(s for s in self.shell.pages if s.key == "bookings")
        page = self.shell._page_for(spec)
        page._pending_focus = booking_id
        opener = getattr(page, "open_details_for", None)
        if callable(opener):
            return opener(booking_id)
        return page.open_details()

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
        self.kind_chips.select(self._kind or "all")
        if self._search_timer.isActive():
            self._search_timer.stop()
        self.takings_caption.setText(f"Latest {self._takings_cap} payments")
        self.outstanding_caption.setText(
            f"Largest {self._outstanding_cap} balances"
        )
        self._request_load()
        self._refresh_takings_total()
        self._refresh_selection()

    @property
    def all_settled(self) -> bool:
        return self.outstanding.state is LoadState.EMPTY


def build_payments_page(shell) -> PaymentsPage:
    return PaymentsPage(shell)
