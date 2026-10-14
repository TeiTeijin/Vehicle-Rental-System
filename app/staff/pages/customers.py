from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import partial

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
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
from sqlalchemy import func, select

from app.models import Booking, Users
from app.services import customer_service, payment_service
from app.services.booking_service import check_licence
from app.services.errors import ServiceError
from app.staff import theme
from app.staff.feedback import describe
from app.staff.metrics import CARD_GUTTER, PAGE_PANEL_PAD, SIDEBAR_GAP, TOOL_ROW_H
from app.staff.pages.base import StaffPage
from app.staff.tables import ALIGN_LEFT, ALIGN_RIGHT, Column, LoadState, LoadStateTable
from app.staff.widgets import confirm, toast
from app.utils.money import ZERO
from app.widgets.skeleton import Skeleton

#: Licences inside this window are flagged, not treated as blocking.
EXPIRING_SOON_DAYS = 30

#: Display label -> licence bucket, for the filter combo.
LICENCE_FILTERS: tuple[tuple[str, str | None], ...] = (
    ("All licences", None),
    ("Valid", "valid"),
    ("Expiring soon", "expiring"),
    ("Expired", "expired"),
    ("Not on file", "missing"),
)

#: Actions a plain staff member may perform. Anything else is admin.
COUNTER_ACTIONS = ("add", "settle")


@dataclass
class CustomerFilter:
    search: str = ""
    licence: str | None = None
    blocked_only: bool = False
    has_active: bool = False
    has_balance: bool = False
    flagged_only: bool = False


def _licence_bucket(expiry: date | None, today: date, soon: date) -> str:
    if expiry is None:
        return "missing"
    if expiry < today:
        return "expired"
    if expiry <= soon:
        return "expiring"
    return "valid"


def _matches_customer(
    customer: Users,
    bucket: str,
    blocked_text: str,
    owed,
    has_open: bool,
    filtr: CustomerFilter,
) -> bool:
    if filtr.licence is not None and bucket != filtr.licence:
        return False
    if filtr.blocked_only and blocked_text != "Yes":
        return False
    if filtr.has_active and not has_open:
        return False
    if filtr.has_balance and owed <= ZERO:
        return False
    if filtr.flagged_only and not (customer.flag_reason or "").strip():
        return False
    needle = filtr.search.strip().lower()
    if needle:
        haystacks = [
            customer.full_name or "",
            customer.email or "",
            customer.phone or "",
            customer.license_number or "",
        ]
        if not any(needle in (hay or "").lower() for hay in haystacks):
            return False
    return True


def _customer_rows(context, filtr: CustomerFilter):
    today = date.today()
    soon = today + timedelta(days=EXPIRING_SOON_DAYS)

    with context.reading() as session:
        rental_counts = dict(
            session.execute(
                select(Booking.user_id, func.count(Booking.booking_id)).group_by(
                    Booking.user_id
                )
            ).all()
        )

        customers = session.execute(
            select(Users)
            .where(Users.role == "customer")
            .order_by(Users.created_at.desc(), Users.user_id.desc())
        ).scalars().all()

        rows = []
        for customer in customers:
            expiry = customer.license_expiry
            bucket = _licence_bucket(expiry, today, soon)
            if bucket == "missing":
                licence = "Not on file"
                blocked = "Yes"
            elif bucket == "expired":
                licence = f"Expired {expiry:%d %b %Y}"
                blocked = "Yes"
            elif bucket == "expiring":
                licence = f"Expires {expiry:%d %b %Y}"
                blocked = "Soon"
            else:
                licence = f"Valid to {expiry:%d %b %Y}"
                blocked = "-"

            owed = customer_service.outstanding_for(session, customer)
            has_open = any(
                b.status in ("pending", "confirmed", "ongoing")
                for b in customer.bookings
            )
            if not _matches_customer(customer, bucket, blocked, owed, has_open, filtr):
                continue

            rows.append(
                (
                    customer.full_name,
                    customer.email,
                    customer.phone or "-",
                    customer.license_number or "-",
                    licence,
                    blocked,
                    rental_counts.get(customer.user_id, 0),
                    f"{owed:,.2f}",
                    (customer.source or "walk_in").replace("_", " ").title(),
                    (customer.flag_reason or "").strip() or "-",
                    "No" if customer.is_active else "Yes",
                    customer.created_at.strftime("%d %b %Y") if customer.created_at else "-",
                )
            )
        return rows


#: Shimmer rows in the table mimic. Same recipe as Bookings.
CUSTOMERS_SKELETON_ROWS = 8

#: Filter keystroke quiet period before a reload is queued.
FILTER_DEBOUNCE_MS = 120


class _CustomersLoadSignals(QObject):
    loaded = Signal(int, list)
    failed = Signal(int, object)


class _CustomersLoader(QRunnable):
    """Run _customer_rows off the GUI thread."""

    def __init__(self, context, filtr: CustomerFilter, gen: int, signals: _CustomersLoadSignals) -> None:
        super().__init__()
        self._context = context
        self._filtr = filtr
        self._gen = gen
        self._signals = signals

    def run(self) -> None:
        try:
            rows = _customer_rows(self._context, self._filtr)
        except Exception as exc:  # noqa: BLE001 - forwarded to the GUI slot
            self._signals.failed.emit(self._gen, exc)
        else:
            self._signals.loaded.emit(self._gen, rows)


class CustomersPage(StaffPage):
    HEADER = False

    def __init__(self, shell) -> None:
        super().__init__(
            shell,
            "Customers",
            "Everyone who rents, and whether their licence is still good.",
        )
        self._filter = CustomerFilter()
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
        tools_layout.addLayout(self._flag_row())
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
                Column("Name", ALIGN_LEFT),
                Column("Email", ALIGN_LEFT),
                Column("Phone", ALIGN_LEFT),
                Column("Licence no.", ALIGN_LEFT),
                Column("Licence", ALIGN_LEFT),
                Column("Blocked"),
                Column("Rentals", ALIGN_RIGHT),
                Column("Owed", ALIGN_RIGHT),
                Column("Source", ALIGN_LEFT),
                Column("Flag", ALIGN_LEFT),
                Column("Off", ALIGN_LEFT),
                Column("Since", ALIGN_LEFT),
            ],
            # Empty until the first async refresh.
            loader=lambda: [],
            empty_message="No customers yet.",
        )
        self.table.set_loader(partial(_customer_rows, self.context, self._filter))
        data_layout.addWidget(self.table, 1)
        self.body.addWidget(data, 1)
        self._data_panel = data
        self.table.table.itemSelectionChanged.connect(self._refresh_actions)
        self._customer_overlay = None
        self._customer_overlay = None

        # -- async reload machinery (whole-page shimmer) ------------------
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._load_signals = _CustomersLoadSignals(self)
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
        for width in (100, 100, 110, 130, 140, 110):
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
        for _ in range(CUSTOMERS_SKELETON_ROWS):
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
        self._pool.start(_CustomersLoader(self.context, replace(self._filter), gen, self._load_signals))

    def _on_rows_loaded(self, gen: int, rows: list) -> None:
        if gen != self._load_gen:
            return
        self.table._populate(rows)
        self.table.state = LoadState.LOADED if rows else LoadState.EMPTY
        self.table.last_error = None
        self.table._sync_stack()
        self._done_gen = gen
        self._hide_skeleton()
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

    # -- filter rows ----------------------------------------------------

    def _combo(self, items: list[str]) -> QComboBox:
        combo = QComboBox(self)
        combo.addItems(items)
        combo.currentIndexChanged.connect(lambda _index: self._apply_filters())
        return combo

    def _check(self, text: str) -> QCheckBox:
        check = QCheckBox(text, self)
        font = QFont("Inter")
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold)
        check.setFont(font)
        check.setStyleSheet(f"color: {theme.MUTED}; background: transparent;")
        check.toggled.connect(lambda _on: self._apply_filters())
        return check

    def _filter_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.search = QLineEdit(self)
        self.search.setObjectName("searchField")
        self.search.setPlaceholderText("Search name, email, phone, licence…")
        self.search.textChanged.connect(lambda _text: self._apply_filters())
        # Concentric with the tools panel (28 - 16 = 12).
        self.search.setProperty("corner", "topLeft")
        self.search.setFixedHeight(TOOL_ROW_H)
        row.addWidget(self.search, 1)
        self.licence_combo = self._combo([label for label, _code in LICENCE_FILTERS])
        self.licence_combo.setFixedHeight(TOOL_ROW_H)
        row.addWidget(self.licence_combo)
        self.blocked_check = self._check("Blocked only")
        row.addWidget(self.blocked_check)
        self.clear_button = QPushButton("Clear", self)
        self.clear_button.setObjectName("ghostButton")
        self.clear_button.setProperty("corner", "topRight")
        self.clear_button.setFixedHeight(TOOL_ROW_H)
        self.clear_button.clicked.connect(self._clear_filters)
        row.addWidget(self.clear_button)
        return row

    def _flag_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.active_check = self._check("Has active rental")
        self.balance_check = self._check("Balance due")
        self.flagged_check = self._check("Flagged")
        row.addWidget(self.active_check)
        row.addWidget(self.balance_check)
        row.addWidget(self.flagged_check)
        row.addStretch(1)
        return row

    def _apply_filters(self) -> None:
        self._filter.search = self.search.text()
        _label, code = LICENCE_FILTERS[self.licence_combo.currentIndex()]
        self._filter.licence = code
        self._filter.blocked_only = self.blocked_check.isChecked()
        self._filter.has_active = self.active_check.isChecked()
        self._filter.has_balance = self.balance_check.isChecked()
        self._filter.flagged_only = self.flagged_check.isChecked()
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
        self.licence_combo.setCurrentIndex(0)
        self.blocked_check.setChecked(False)
        self.active_check.setChecked(False)
        self.balance_check.setChecked(False)
        self.flagged_check.setChecked(False)

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
        self.settle_button = self._action_button("Settle balance")
        self.settle_button.clicked.connect(self.open_settle)
        self.deactivate_button = self._action_button("Deactivate", danger=True)
        self.deactivate_button.clicked.connect(self._on_deactivate_button)
        self.export_button = self._action_button("Export PDF")
        self.export_button.clicked.connect(self.open_export)
        for button in (
            self.add_button,
            self.edit_button,
            self.details_button,
            self.settle_button,
            self.deactivate_button,
            self.export_button,
        ):
            row.addWidget(button)
        row.addStretch(1)
        self._refresh_actions()
        return row

    def can(self, action: str) -> bool:
        if self.context.is_admin:
            return True
        return action in COUNTER_ACTIONS

    def _refresh_actions(self) -> None:
        table = getattr(self, "table", None)
        has_row = self.selected_email() is not None if table is not None else False
        self.add_button.setEnabled(self.can("add"))
        self.edit_button.setEnabled(self.context.is_admin and has_row)
        self.details_button.setEnabled(has_row)
        self.settle_button.setEnabled(self.can("settle") and has_row)
        self.deactivate_button.setEnabled(self.context.is_admin and has_row)
        self.export_button.setEnabled(has_row)
        active = self._selected_active()
        if active is False:
            self.deactivate_button.setText("Reactivate")
            self.deactivate_button.setObjectName("ghostButton")
        else:
            self.deactivate_button.setText("Deactivate")
            self.deactivate_button.setObjectName("dangerButton")
        self.deactivate_button.style().unpolish(self.deactivate_button)
        self.deactivate_button.style().polish(self.deactivate_button)

    # -- selection ------------------------------------------------------

    def selected_email(self) -> str | None:
        if getattr(self, "table", None) is None:
            return None
        row = self.table.table.currentRow()
        if row < 0:
            return None
        text = self.table.cell_text(row, 1)
        return text or None

    def _selected_active(self) -> bool | None:
        if getattr(self, "table", None) is None:
            return None
        row = self.table.table.currentRow()
        if row < 0:
            return None
        # "Off" column: "Yes" means deactivated.
        return self.table.cell_text(row, 10) != "Yes"

    def _resolve_id(self, email: str) -> int | None:
        with self.context.reading() as session:
            row = session.execute(
                select(Users).where(Users.email == email)
            ).scalar_one_or_none()
            return row.user_id if row is not None else None

    # -- overlay panels (no OS windows; same glass as bookings) ---------

    def _overlay_panel(self, panel: QWidget) -> object:
        from app.staff.overlay import FrostedOverlay

        if self._customer_overlay is not None:
            self._customer_overlay.hide_overlay()
            self._customer_overlay.deleteLater()
            self._customer_overlay = None
        window = self.window()
        overlay = FrostedOverlay(window if window is not None else self)
        overlay.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        overlay.set_content(panel)
        overlay.show_overlay()
        self._customer_overlay = overlay
        return overlay

    def _hide_overlay(self) -> None:
        if self._customer_overlay is not None:
            self._customer_overlay.hide_overlay()

    # -- actions --------------------------------------------------------

    def open_add(self) -> bool:
        from app.staff.customer_panels import CustomerFormPanel

        if not self.can("add"):
            toast(self, "That action is for administrators.", "error")
            return False
        panel = CustomerFormPanel(self.context, None, parent=self)
        panel.submitted.connect(self._on_add_submit)
        panel.cancelled.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def _store_docs(self, licence: str, values: dict) -> dict:
        paths = {}
        for side, key in (("front", "front_src"), ("back", "back_src")):
            src = values.get(key)
            if src:
                try:
                    paths[side] = customer_service.store_licence_doc(licence, side, src)
                except Exception as exc:  # noqa: BLE001 - docs are best-effort
                    toast(self, describe(exc)[0], "error")
        return paths

    def _on_add_submit(self, values: dict) -> None:
        try:
            with self.context.session() as session:
                customer = customer_service.create_customer(
                    session,
                    full_name=values["full_name"],
                    email=values["email"],
                    phone=values["phone"],
                    address=values["address"],
                    license_number=values["license_number"],
                    license_expiry=values["license_expiry"],
                    birthdate=values.get("birthdate"),
                    emergency_contact=values.get("emergency_contact"),
                    source=values.get("source") or "walk_in",
                )
                for side, key in (("front", "front_src"), ("back", "back_src")):
                    src = values.get(key)
                    if src:
                        path = customer_service.store_licence_doc(
                            customer.license_number, side, src
                        )
                        if side == "front":
                            customer.licence_front_path = path
                        else:
                            customer.licence_back_path = path
                name = customer.full_name
        except Exception as exc:  # noqa: BLE001
            message, _field, _kind = describe(exc)
            toast(self, message, "error")
            return
        self._hide_overlay()
        self._request_load(full=False)
        toast(self, f"{name} added.")

    def open_edit(self) -> bool:
        from app.staff.customer_panels import CustomerFormPanel

        if not self.context.is_admin:
            toast(self, "That action is for administrators.", "error")
            return False
        email = self.selected_email()
        if email is None:
            toast(self, "Select a customer first.", "warn")
            return False
        user_id = self._resolve_id(email)
        if user_id is None:
            toast(self, "That customer no longer exists.", "error")
            return False
        panel = CustomerFormPanel(self.context, user_id, parent=self)
        panel.submitted.connect(lambda values: self._on_edit_submit(user_id, values))
        panel.cancelled.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def _on_edit_submit(self, user_id: int, values: dict) -> None:
        try:
            with self.context.session() as session:
                customer = session.get(Users, user_id)
                if customer is None:
                    raise ServiceError("That customer no longer exists.")
                values = dict(values)
                front_src = values.pop("front_src", None)
                back_src = values.pop("back_src", None)
                values.pop("source", None)
                customer_service.update_customer(session, customer, **values)
                if front_src:
                    customer.licence_front_path = customer_service.store_licence_doc(
                        customer.license_number, "front", front_src
                    )
                if back_src:
                    customer.licence_back_path = customer_service.store_licence_doc(
                        customer.license_number, "back", back_src
                    )
                name = customer.full_name
        except Exception as exc:  # noqa: BLE001
            message, _field, _kind = describe(exc)
            toast(self, message, "error")
            return
        self._hide_overlay()
        self._request_load(full=False)
        toast(self, f"{name} updated.")

    def open_details(self) -> bool:
        from app.staff.customer_panels import CustomerDetailsPanel

        email = self.selected_email()
        if email is None:
            toast(self, "Select a customer first.", "warn")
            return False
        user_id = self._resolve_id(email)
        if user_id is None:
            toast(self, "That customer no longer exists.", "error")
            return False
        panel = CustomerDetailsPanel(self.context, user_id, self)
        panel.closed.connect(self._hide_overlay)
        panel.edit_requested.connect(lambda _uid: (self._hide_overlay(), self.open_edit()))
        panel.rental_requested.connect(self._on_new_rental)
        self._overlay_panel(panel)
        return True

    def _on_new_rental(self, user_id: int) -> None:
        """Route to New Rental with this customer pre-selected in the wizard."""
        self._hide_overlay()
        if self.shell.show_page("new_rental"):
            page = self.shell._page_for(
                next(spec for spec in self.shell.pages if spec.key == "new_rental")
            )
            page.set_pending_customer(user_id)
            toast(self, "Customer attached: pick a vehicle to start the rental.")

    def open_settle(self) -> bool:
        from app.staff.customer_panels import SettlePanel

        if not self.can("settle"):
            toast(self, "That action is for administrators.", "error")
            return False
        email = self.selected_email()
        if email is None:
            toast(self, "Select a customer first.", "warn")
            return False
        user_id = self._resolve_id(email)
        if user_id is None:
            toast(self, "That customer no longer exists.", "error")
            return False
        panel = SettlePanel(self.context, user_id, self)
        panel.submitted.connect(lambda values: self._on_settle_submit(values))
        panel.cancelled.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def _on_settle_submit(self, values: dict) -> None:
        try:
            with self.context.session() as session:
                staff_id = self.context.user.user_id
                for booking_id, amount in values["legs"]:
                    booking = session.get(Booking, int(booking_id))
                    if booking is None:
                        raise ServiceError("That booking no longer exists.")
                    payment_service.record_payment(
                        session, booking, amount,
                        values["method"],
                        recorded_by=staff_id,
                        reference_no=values["reference_no"] or None,
                        note="Counter settlement",
                    )
        except Exception as exc:  # noqa: BLE001
            message, _field, _kind = describe(exc)
            toast(self, message, "error")
            return
        self._hide_overlay()
        self._request_load(full=False)
        toast(self, f"Settled across {len(values['legs'])} booking(s).")

    def _on_deactivate_button(self) -> None:
        email = self.selected_email()
        if email is None:
            toast(self, "Select a customer first.", "warn")
            return
        if not self.context.is_admin:
            toast(self, "That action is for administrators.", "error")
            return
        if self._selected_active() is False:
            if not confirm(self, f"Reactivate {email}?",
                           detail="They become bookable again.",
                           pref="behavior/confirm_deactivate"):
                return
            try:
                with self.context.session() as session:
                    customer = session.get(Users, self._resolve_id(email))
                    customer_service.reactivate_customer(session, customer)
            except Exception as exc:  # noqa: BLE001
                message, _field, _kind = describe(exc)
                toast(self, message, "error")
                return
            self._request_load(full=False)
            toast(self, f"{email} reactivated.")
            return
        if not confirm(
            self, f"Deactivate {email}?",
            detail="They stay in history but cannot rent.",
            destructive=True,
            pref="behavior/confirm_deactivate",
        ):
            return
        try:
            with self.context.session() as session:
                customer = session.get(Users, self._resolve_id(email))
                if customer is None:
                    raise ServiceError("That customer no longer exists.")
                customer_service.deactivate_customer(session, customer)
        except Exception as exc:  # noqa: BLE001
            message, _field, _kind = describe(exc)
            toast(self, message, "error")
            return
        self._request_load(full=False)
        toast(self, f"{email} deactivated.")

    def open_export(self) -> bool:
        from PySide6.QtGui import QDesktopServices

        email = self.selected_email()
        if email is None:
            toast(self, "Select a customer first.", "warn")
            return False
        user_id = self._resolve_id(email)
        if user_id is None:
            toast(self, "That customer no longer exists.", "error")
            return False
        try:
            from app.services.receipt_service import generate_statement_pdf

            with self.context.session() as session:
                customer = session.get(Users, user_id)
                path = generate_statement_pdf(session, customer)
        except Exception as exc:  # noqa: BLE001
            message, _field, _kind = describe(exc)
            toast(self, message, "error")
            return False
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        return True

    def refresh(self) -> None:
        super().refresh()
        # Async: shimmer shows synchronously so show_page returns fast
        # and the tab cuts instantly; rows land on the loaded signal.
        self._request_load(full=True)
        self._refresh_actions()

    def blocked_customers(self) -> list[str]:
        today = date.today()
        blocked = []
        with self.context.reading() as session:
            for customer in session.execute(
                select(Users).where(Users.role == "customer")
            ).scalars():
                try:
                    check_licence(session, customer, today)
                except Exception:  # noqa: BLE001 - any refusal means blocked
                    blocked.append(customer.full_name)
        return blocked


def build_customers_page(shell) -> CustomersPage:
    return CustomersPage(shell)
