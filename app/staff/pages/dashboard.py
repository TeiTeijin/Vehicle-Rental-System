from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
import time

from PySide6.QtCore import QObject, QPoint, QRunnable, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from sqlalchemy import func, select

from app.services import dashboard_service
from app.staff import prefs, theme
from app.staff.cards import (
    Caption,
    Card,
    ElidedLabel,
    HeroNumber,
    IconButton,
    Pill,
    SectionLabel,
    TrendLabel,
)
from app.staff.dashboard_charts import (
    ActivityHeatmap,
    ChannelCurves,
    RevenueSparkline,
    SalesTargetChart,
)
from app.staff.metrics import (
    ACTIVITY_SHARE,
    CARD_GUTTER,
    CARD_PADDING,
    CHANNELS_SHARE,
    HERO_SIZE,
    MEDIUM_CONTENT_W,
    REVENUE_SHARE,
    ROW1_H,
    ROW2_H,
    TARGET_SHARE,
    TRANSACTIONS_SHARE,
    TARGET_SIZE,
    WIDE_CONTENT_W,
)
from app.staff.feedback import describe
from app.staff.pages.base import StaffPage
from app.staff.workers import emit_safely, loader_pool
from app.utils.money import ZERO

#: Filter-menu order and keys for the Recent Transactions card.
TRANSACTION_FILTERS = (
    ("all", "All"),
    ("cash", "Cash"),
    ("card", "Card"),
    ("gcash", "GCash"),
    ("refunded", "Refunded"),
    ("pending", "Pending"),
)

_FILTER_KEYS = {key for key, _label in TRANSACTION_FILTERS}


class _CardGrid(QWidget):
    WIDE = "wide"
    MEDIUM = "medium"
    NARROW = "narrow"

    #: Most rows any shape uses; narrow is five, one card per row.
    MAX_ROWS = 5

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cards: list[Card] = []
        self._shape: str | None = None
        #: One per row any shape can use; created once, never rebuilt.
        self._row_layouts = [
            self._make_row_layout() for _ in range(self.MAX_ROWS)
        ]
        self._rows = QVBoxLayout(self)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(CARD_GUTTER)
        for layout in self._row_layouts:
            self._rows.addLayout(layout)

    @staticmethod
    def _make_row_layout() -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(CARD_GUTTER)
        return layout

    def add(self, card: Card) -> None:
        self._cards.append(card)
        self._shape = None
        self._relayout()

    @property
    def cards(self) -> list[Card]:
        return list(self._cards)

    def shape_for_width(self, width: int) -> str:
        if width >= WIDE_CONTENT_W:
            return self.WIDE
        if width >= MEDIUM_CONTENT_W:
            return self.MEDIUM
        return self.NARROW

    @property
    def shape(self) -> str | None:
        return self._shape

    def _rows_for(self, shape: str) -> list[list[tuple[int, int]]]:
        if shape == self.WIDE:
            return [
                # Row 1: 35 / 65.
                [(0, REVENUE_SHARE), (1, CHANNELS_SHARE)],
                # Row 2: 30 / 38 / 32.
                [(2, ACTIVITY_SHARE), (3, TRANSACTIONS_SHARE), (4, TARGET_SHARE)],
            ]
        if shape == self.MEDIUM:
            return [
                [(0, REVENUE_SHARE), (1, CHANNELS_SHARE)],
                # Three here is a third of 760; the heatmap cannot hold 16 weeks.
                [(2, 50), (3, 50)],
                # The target takes a row of its own.
                [(4, 100)],
            ]
        return [[(index, 100)] for index in range(len(self._cards))]

    def _empty(self, layout: QHBoxLayout) -> None:
        while layout.count():
            layout.takeAt(0)

    def _relayout(self) -> None:
        if not self._cards:
            return
        shape = self._shape_for_current_width()
        rows = self._rows_for(shape)

        for layout in self._row_layouts:
            self._empty(layout)

        for index, row in enumerate(rows):
            layout = self._row_layouts[index]
            for card_index, share in row:
                if card_index >= len(self._cards):
                    continue
                layout.addWidget(self._cards[card_index], share)

        # Emptied rather than hidden, so a card is in one layout only.
        for layout in self._row_layouts[len(rows) :]:
            self._empty(layout)

        self._shape = shape

    def _shape_for_current_width(self) -> str:
        return self.shape_for_width(self.width())

    def resizeEvent(self, event) -> None:  # noqa: N802
        new_shape = self._shape_for_current_width()
        if new_shape != self._shape:
            self._relayout()
        super().resizeEvent(event)

    def reflow_to(self, width: int) -> None:
        shape = self.shape_for_width(width)
        if shape != self._shape:
            self._shape = shape
            self._relayout()


class _DashboardLoadSignals(QObject):
    loaded = Signal(int, object)
    failed = Signal(int, object)


class _DashboardLoader(QRunnable):
    """Collect dashboard figures off the GUI thread."""

    def __init__(self, context, gen: int, signals: _DashboardLoadSignals) -> None:
        super().__init__()
        self._context = context
        self._gen = gen
        self._loaded = signals.loaded
        self._failed = signals.failed

    def run(self) -> None:
        try:
            with self._context.reading() as session:
                figures = dashboard_service.collect(
                    session,
                    today=date.today(),
                    weeks=dashboard_service.HEATMAP_WEEKS,
                    recent=dashboard_service.RECENT_TRANSACTIONS,
                    monthly=True,
                )
        except Exception as exc:  # noqa: BLE001 - forwarded to the GUI slot
            emit_safely(self._failed, self._gen, exc)
        else:
            emit_safely(self._loaded, self._gen, figures)


class DashboardPage(StaffPage):
    PANEL = True

    def __init__(self, shell, *, animate: bool = True) -> None:
        super().__init__(shell, "Dashboard", "The branch at a glance.")

        #: Set False to land every value immediately.
        self.animate = animate

        #: Nothing is read until `refresh_async`/`refresh` returns figures.
        self._load_state = "loading"
        self._recent_transactions: list = []
        self._transaction_query = ""
        self._transaction_filter_key = "all"

        # -- the banner -----------------------------------------------------
        self.status_banner = self._build_status_banner()
        self.body.addWidget(self.status_banner)

        # -- the cards ------------------------------------------------------
        self.grid = _CardGrid(self)
        self.body.addWidget(self.grid, 1)

        self.revenue_card = self._build_revenue_card()
        self.channels_card = self._build_channels_card()
        self.activity_card = self._build_activity_card()
        self.transactions_card = self._build_transactions_card()
        self.target_card = self._build_target_card()

        for card in (
            self.revenue_card,
            self.channels_card,
            self.activity_card,
            self.transactions_card,
            self.target_card,
        ):
            self.grid.add(card)

        self._render_transactions_message("Loading transactions…")
        self._show_status("Loading dashboard…", error=False)

        self._first_load = True
        self._animations_pending = False
        self._pool = loader_pool()
        self._load_signals = _DashboardLoadSignals(self)
        self._load_signals.loaded.connect(self._on_async_loaded)
        self._load_signals.failed.connect(self._on_async_failed)
        self._load_gen = 0
        self._done_gen = 0
        self._last_figures = None
        self._last_loaded_at = 0.0
        self._load_error = None
        # Safe here: every attribute the action list reads now exists.
        self._sync_overflow()

    # -- loading and error state -------------------------------------------

    def _build_status_banner(self) -> QFrame:
        banner = QFrame(self)
        banner.setObjectName("dashboardStatus")
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        font = QFont("Inter")
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.Medium)
        self.status_label.setFont(font)
        self.status_label.setStyleSheet("background: transparent;")

        self.retry_button = QPushButton("Try again")
        self.retry_button.setObjectName("retryButton")
        self.retry_button.clicked.connect(self.retry_load)
        self.retry_button.hide()

        layout.addWidget(self.status_label, 1)
        layout.addWidget(self.retry_button)
        banner.hide()
        return banner

    @property
    def load_state(self) -> str:
        """``loading``, ``loaded``, or ``failed`` -- for tests and the banner."""
        return self._load_state

    def _show_status(self, message: str, *, error: bool) -> None:
        border = theme.DANGER if error else theme.BORDER
        colour = theme.DANGER if error else theme.MUTED
        self.status_banner.setStyleSheet(
            f"QFrame#dashboardStatus {{ background: {theme.PAPER}; "
            f"border: 1px solid {border}; border-radius: 12px; }}"
        )
        self.status_label.setStyleSheet(
            f"color: {colour}; background: transparent;"
        )
        self.status_label.setText(message)
        self.retry_button.setVisible(error)
        self.status_banner.show()

    def _begin_loading(self) -> None:
        was_failed = self._load_state == "failed"
        self._load_state = "loading"
        # A refresh over existing data needs no banner: the cards stay useful.
        if self._last_figures is None or was_failed:
            self._show_status("Loading dashboard…", error=False)
            if self._last_figures is None:
                self._render_transactions_message("Loading transactions…")

    def retry_load(self) -> None:
        self._load_error = None
        # Bypass the refresh TTL without discarding the figures still on screen.
        self._last_loaded_at = 0.0
        self.refresh_async()

    # -- card construction -------------------------------------------------

    def _card_header(
        self,
        card: Card,
        title: str,
        *,
        dark: bool,
        subtitle: str = "",
        buttons: tuple = (),
    ) -> QVBoxLayout:
        body = QVBoxLayout()
        body.setContentsMargins(CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING)
        body.setSpacing(14)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        head.addWidget(
            SectionLabel(
                title,
                colour=theme.PAPER if dark else theme.TEXT,
            )
        )
        head.addStretch(1)
        for button in buttons:
            head.addWidget(button)
        body.addLayout(head)

        if subtitle:
            body.addWidget(
                Caption(
                    subtitle,
                    colour=theme.INK_LINE if dark else theme.MUTED,
                )
            )
        return body

    def _build_revenue_card(self) -> Card:
        card = Card(dark=False)
        # A minimum, not a fixed height: the row grows with the window.
        card.setMinimumHeight(ROW1_H)

        # A dash, not ₱0.00: no figures have been read yet, and a zero that
        # looks like a measurement is worse than an honest placeholder.
        self.revenue_value = HeroNumber(
            "—", size=HERO_SIZE, colour=theme.TEXT
        )
        self.revenue_trend = TrendLabel()

        # The picker's selection is the day shown; today is the default.
        self.revenue_period = QComboBox()
        self.revenue_period.setObjectName("revenuePeriod")
        self.revenue_period.setCursor(Qt.CursorShape.PointingHandCursor)
        # The control is filled asynchronously, so it must keep sizing itself.
        # The default policy measures once, while empty, and then clips the
        # longer day labels added later.
        self.revenue_period.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.revenue_period.currentIndexChanged.connect(
            self._on_revenue_day_changed
        )
        self._revenue_days: dict[date, Decimal] = {}
        self._revenue_day: date | None = None
        self._today: date = date.today()

        #: The selected day's takings, shown under the year-to-date hero.
        self.revenue_collections = QLabel()
        self.revenue_collections.setTextFormat(Qt.TextFormat.RichText)
        self.revenue_collections.setWordWrap(True)
        self.revenue_collections.setStyleSheet("background: transparent;")

        body = self._card_header(card, "Total Revenue", dark=False)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(10)
        title_row.addWidget(self.revenue_value, 1)
        body.addLayout(title_row)

        collections_row = QHBoxLayout()
        collections_row.setContentsMargins(0, 0, 0, 0)
        collections_row.setSpacing(10)
        collections_row.addWidget(self.revenue_collections, 1)
        collections_row.addWidget(self.revenue_trend, 0, Qt.AlignmentFlag.AlignTop)
        body.addLayout(collections_row)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.addWidget(self.revenue_period)
        foot.addStretch(1)
        body.addLayout(foot)

        self.revenue_sparkline = RevenueSparkline()
        body.addWidget(self.revenue_sparkline, 1)

        card.layout().addLayout(body)
        return card

    def _on_revenue_day_changed(self, index: int) -> None:
        day = self.revenue_period.itemData(index)
        if day is not None:
            self._show_revenue_day(day)

    def _show_revenue_day(self, day: date) -> None:
        self._revenue_day = day
        amount = self._revenue_days.get(day, ZERO)
        self.revenue_collections.setText(
            f'<span style="color:{theme.MUTED}">{self._collections_label(day)}:'
            f'</span> <span style="color:{theme.TEXT}; font-weight:600;">'
            f"{pesos(amount)}</span>"
        )
        yesterday = self._revenue_days.get(day - timedelta(days=1))
        self.revenue_trend.set_change(
            self._change_pct(amount, yesterday),
            up_is_good=True,
        )

    def _collections_label(self, day: date) -> str:
        if day == self._today:
            return "Today"
        if day == self._today - timedelta(days=1):
            return "Yesterday"
        return day.strftime("%b %d")

    @staticmethod
    def _change_pct(
        current: Decimal | None, previous: Decimal | None
    ) -> float | None:
        if not previous:
            return None
        return float(((current or ZERO) - previous) / previous * 100)

    @staticmethod
    def _day_label(day: date, today: date) -> str:
        if day == today:
            return "Today's Revenue"
        if day == today - timedelta(days=1):
            return "Yesterday's Revenue"
        return day.strftime("%a, %b %d")

    def _build_channels_card(self) -> Card:
        card = Card(dark=False)
        card.setMinimumHeight(ROW1_H)

        self.channels_curves = ChannelCurves()
        self.channels_curves.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        body = self._card_header(
            card,
            "Rental Channels",
            dark=False,
            subtitle="Orders placed, last 16 weeks",
        )
        body.addWidget(self.channels_curves, 1)

        card.layout().addLayout(body)
        return card

    def _build_activity_card(self) -> Card:
        card = Card(dark=False)
        # Taller than row 1: each holds a chart or a list that needs the height.
        card.setMinimumHeight(ROW2_H)

        self.activity_heatmap = ActivityHeatmap()
        self.activity_heatmap.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.activity_caption = Caption(
            "Sixteen weeks of orders", colour=theme.MUTED, size=12
        )

        body = self._card_header(card, "Order Activity", dark=False)
        body.addWidget(self.activity_heatmap, 1)
        body.addWidget(self.activity_caption)
        card.layout().addLayout(body)
        return card

    def _build_transactions_card(self) -> Card:
        card = Card(dark=False)
        card.setMinimumHeight(ROW2_H)

        # One shared grid so the four columns line up across rows.
        self.transactions_list = QGridLayout()
        self.transactions_list.setContentsMargins(0, 0, 0, 0)
        self.transactions_list.setHorizontalSpacing(12)
        self.transactions_list.setVerticalSpacing(0)
        self.transactions_list.setColumnStretch(0, 1)
        self._transaction_rows: list[QWidget] = []

        self.transaction_search = QLineEdit()
        self.transaction_search.setPlaceholderText("Search transactions")
        self.transaction_search.setClearButtonEnabled(True)
        self.transaction_search.setAccessibleName("Search transactions")
        self.transaction_search.textChanged.connect(
            self._on_transaction_query_changed
        )
        self.transaction_search.hide()

        self.transaction_search_button = IconButton(
            "search",
            colour=theme.MUTED,
            background=theme.SURFACE,
            tooltip="Search transactions",
        )
        self.transaction_search_button.clicked.connect(
            self.toggle_transaction_search
        )

        self.transaction_filter_button = IconButton(
            "sliders",
            colour=theme.MUTED,
            background=theme.SURFACE,
            tooltip="Filter transactions",
        )
        self.transaction_filter_button.clicked.connect(
            self._show_transaction_filter_menu
        )
        self.transaction_filter = QMenu(self)
        self._filter_actions = {}
        for key, label in TRANSACTION_FILTERS:
            action = self.transaction_filter.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, filter_key=key: self.set_transaction_filter(
                    filter_key
                )
            )
            self._filter_actions[key] = action
        self._filter_actions["all"].setChecked(True)

        body = self._card_header(
            card,
            "Recent Transactions",
            dark=False,
            buttons=(
                self.transaction_search_button,
                self.transaction_filter_button,
            ),
        )
        body.addWidget(self.transaction_search)
        body.addLayout(self.transactions_list, 1)
        card.layout().addLayout(body)
        return card

    def toggle_transaction_search(self) -> None:
        showing = self.transaction_search.isHidden()
        self.transaction_search.setVisible(showing)
        self.transaction_search_button.set_checked(showing)
        if showing:
            self.transaction_search.setFocus()
        else:
            # A hidden query would keep filtering invisibly.
            self.set_transaction_query("")

    def _show_transaction_filter_menu(self) -> None:
        anchor = self.transaction_filter_button.mapToGlobal(
            QPoint(0, self.transaction_filter_button.height())
        )
        self.transaction_filter.popup(anchor)

    def _on_transaction_query_changed(self, text: str) -> None:
        self._transaction_query = text
        self._apply_transaction_view()

    def set_transaction_query(self, text: str) -> None:
        """Test hook: narrow the loaded transactions by free text."""
        text = text or ""
        self._transaction_query = text
        if self.transaction_search.text() != text:
            self.transaction_search.setText(text)
        else:
            self._apply_transaction_view()

    def set_transaction_filter(self, key: str) -> None:
        """Test hook: apply one of All, Cash, Card, GCash, Refunded, Pending."""
        normalised = (key or "all").strip().lower()
        if normalised not in _FILTER_KEYS:
            normalised = "all"
        self._transaction_filter_key = normalised
        for filter_key, action in self._filter_actions.items():
            action.setChecked(filter_key == normalised)
        self._apply_transaction_view()

    def _build_target_card(self) -> Card:
        card = Card(dark=False)
        card.setMinimumHeight(ROW2_H)

        self.target_value = HeroNumber(
            "—", size=TARGET_SIZE, colour=theme.TEXT
        )
        self.target_pill = Pill(
            "—",
            colour=theme.TEXT,
            background=theme.SURFACE,
            height=24,
            font_size=11,
            radius=8,
        )
        self.target_chart = SalesTargetChart()
        self.target_chart.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        head.addWidget(self.target_value, 1)
        head.addWidget(self.target_pill, 0, Qt.AlignmentFlag.AlignTop)

        body = self._card_header(card, "Sales Target", dark=False)
        body.addLayout(head)
        body.addWidget(self.target_chart, 1)
        card.layout().addLayout(body)
        return card

    # -- data -------------------------------------------------------------

    def overflow_actions(self) -> list[tuple[str, Callable[[], None]]]:
        return [("Refresh dashboard", self.refresh_async)]

    def refresh(self) -> None:
        """Synchronous refresh, kept for tests and explicit reloads."""
        super().refresh()
        today = date.today()
        with self.context.reading() as session:
            figures = dashboard_service.collect(
                session,
                today=today,
                weeks=dashboard_service.HEATMAP_WEEKS,
                recent=dashboard_service.RECENT_TRANSACTIONS,
                monthly=True,
            )
        self._apply_figures(figures)

    def refresh_async(self, *, force: bool = False) -> None:
        """Refresh without blocking the GUI thread.

        The shell uses this for navigation and the auto-refresh timer. A
        recent result is reused for the configured refresh interval; the retry
        button passes ``force`` so the failed read is actually repeated.
        """
        super().refresh()
        ttl_ms = prefs.dashboard_refresh_ms()
        if (
            not force
            and self._last_figures is not None
            and ttl_ms > 0
            and (time.monotonic() - self._last_loaded_at) * 1000 < ttl_ms
        ):
            self._apply_figures(self._last_figures)
            return
        self._begin_loading()
        self._load_gen += 1
        self._pool.start(
            _DashboardLoader(self.context, self._load_gen, self._load_signals)
        )

    def _on_async_loaded(self, gen: int, figures) -> None:
        if gen != self._load_gen:
            return
        self._apply_figures(figures)
        self._done_gen = gen

    def _on_async_failed(self, gen: int, exc: BaseException) -> None:
        if gen != self._load_gen:
            return
        self._load_error = exc
        self._done_gen = gen
        self._load_state = "failed"
        message, _field, _kind = describe(exc)
        self._show_status(
            f"Could not load the dashboard. {message}", error=True
        )
        if self._last_figures is None:
            # Never leave a "loading" row behind after the load has failed.
            self._render_transactions_message("Could not load transactions.")

    def _apply_figures(self, figures) -> None:
        self._fill_revenue(figures)
        self._fill_channels(figures)
        self._fill_activity(figures)
        self._fill_transactions(figures)
        self._fill_target(figures)
        self._load_state = "loaded"
        self._load_error = None
        self.status_banner.hide()
        self._first_load = False
        self._last_figures = figures
        self._last_loaded_at = time.monotonic()
        self._done_gen = self._load_gen

    def _fill_revenue(self, figures) -> None:
        days = dict(figures.revenue_days)
        if not days:
            # A hand-built `DashboardFigures` carries only the single figure.
            days = {figures.today: figures.revenue_today}
        self._revenue_days = days
        self._today = figures.today

        # The seven days ending today, oldest first; span is fixed to the week.
        week = sorted(days)[-7:]
        self.revenue_sparkline.set_series([days[day] for day in week])

        # The hero is the year-to-date figure; the picker lands on the line below.
        self.revenue_value.setText(pesos(figures.revenue_ytd))

        self.revenue_period.blockSignals(True)
        self.revenue_period.clear()
        for day in sorted(days, reverse=True):
            self.revenue_period.addItem(self._day_label(day, figures.today), day)
        # Keep the selected day across a refresh; else fall back to today.
        target = self._revenue_day if self._revenue_day in days else figures.today
        index = self.revenue_period.findData(target)
        if index < 0:
            index = 0
        self.revenue_period.setCurrentIndex(index)
        self.revenue_period.blockSignals(False)
        # Re-measure after the async fill; the longest label is known now.
        self.revenue_period.adjustSize()
        self._show_revenue_day(target)

    def _fill_channels(self, figures) -> None:
        totals = figures.channels
        series = [
            (theme.CHANNEL_LABELS[key], count, theme.CHANNEL_COLOURS[key])
            for key, count in totals.as_rows()
        ]
        self.channels_curves.set_series(series)

        # Say so if any booking predates the channel column.
        if totals.unattributed:
            self.channels_curves.setToolTip(
                f"{totals.unattributed} booking(s) predate channel tracking "
                "and are not in either curve."
            )
        else:
            self.channels_curves.setToolTip("")

    def _fill_activity(self, figures) -> None:
        counts = figures.orders_per_day
        if not counts:
            return
        self.activity_heatmap.set_counts(counts, min(counts))
        self.activity_caption.setText(
            f"{sum(counts.values())} orders over the last {len(counts)} days"
        )

    def _clear_transactions(self) -> None:
        while self.transactions_list.count():
            item = self.transactions_list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Reparent only; `deleteLater()` here is a double free waiting to happen.
                widget.setParent(None)
            # Ownership moved into Python; dropping the reference deletes it.
            del item
        self._transaction_rows.clear()

    def _fill_transactions(self, figures) -> None:
        # Fetch more than the card can show (the service reads 50) so the
        # search and filter controls have something to narrow.
        self._recent_transactions = list(figures.recent)
        self._apply_transaction_view()

    def _render_transactions_message(self, text: str) -> None:
        self._clear_transactions()
        empty = QLabel(text)
        font = QFont("Inter")
        font.setPixelSize(13)
        empty.setFont(font)
        empty.setStyleSheet(
            f"color: {theme.MUTED}; background: transparent;"
        )
        self.transactions_list.addWidget(empty, 0, 0, 1, 4)
        self._transaction_rows.append(empty)

    def _apply_transaction_view(self) -> None:
        self._clear_transactions()

        rows = list(self._recent_transactions)
        if not rows:
            if self._load_state == "loading":
                message = "Loading transactions…"
            elif self._load_state == "failed":
                message = "Could not load transactions."
            else:
                message = "No payments recorded yet."
            self._render_transactions_message(message)
            return

        query = (self._transaction_query or "").strip().lower()
        matches = [
            entry
            for entry in rows
            if self._transaction_matches(entry, query)
        ]
        if not matches:
            self._render_transactions_message("No matching transactions.")
            return

        shown = matches[: dashboard_service.RECENT_VISIBLE]
        for row, entry in enumerate(shown):
            self._transaction_row(entry, row)
        # A trailing spacer holds the rows at the top; `_clear_transactions` drains it.
        self.transactions_list.addItem(
            QSpacerItem(
                0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
            ),
            len(shown),
            0,
            1,
            4,
        )

    def _transaction_matches(self, entry, query: str) -> bool:
        key = self._transaction_filter_key
        status = (getattr(entry, "status", "") or "").strip().lower()

        if key in {"cash", "card", "gcash"} and entry.method != key:
            return False
        if key == "pending" and status != "pending":
            return False
        if key == "refunded" and not (
            entry.refunded or status == "refunded"
        ):
            return False
        if not query:
            return True

        amount = abs(Decimal(str(entry.amount)))
        method_label = theme.METHOD_LABELS.get(
            entry.method, entry.method.title()
        )
        channel_label = theme.CHANNEL_LABELS.get(entry.channel, "Unrecorded")
        haystack = " ".join(
            (
                entry.customer or "",
                entry.method or "",
                method_label,
                entry.channel or "",
                channel_label,
                status,
                theme.status_label(status),
                f"{amount:.2f}",
                f"{amount:,.2f}",
                pesos(amount),
                _signed_pesos(amount, entry.refunded),
                "refunded" if entry.refunded else "",
            )
        ).lower()
        return all(token in haystack for token in query.split())

    def _transaction_row(self, entry, row: int) -> None:
        name = ElidedLabel(
            entry.customer,
            colour=theme.TEXT,
            size=14,
            weight=QFont.Weight.Medium,
            tooltip_full=True,
        )

        method = Caption(
            theme.METHOD_LABELS.get(entry.method, entry.method.title()),
            colour=theme.MUTED,
            size=12,
        )

        mark = theme.CHANNEL_DOT_COLOURS.get(entry.channel, theme.MUTED)
        channel = Pill(
            theme.CHANNEL_LABELS.get(entry.channel, "Unrecorded"),
            colour=theme.TEXT,
            background=theme.SURFACE,
            dot=bool(entry.channel),
            dot_colour=mark,
            border_colour=mark,
            height=22,
            font_size=11,
            radius=8,
        )

        status = (getattr(entry, "status", "paid") or "").strip().lower()
        amount_colour = _amount_colour(status, entry.refunded)
        amount = QLabel(_signed_pesos(entry.amount, entry.refunded))
        amount_font = QFont("Inter")
        amount_font.setPixelSize(15)
        amount_font.setWeight(QFont.Weight.DemiBold)
        amount_font.setFeature(QFont.Tag("tnum"), 1)
        amount.setFont(amount_font)
        amount.setStyleSheet(
            f"background: transparent; color: {amount_colour};"
        )
        amount.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        # The amount and its status caption share the last column, so a
        # pending transfer never reads as the same thing as received cash.
        amount_cell = QWidget()
        amount_cell.setStyleSheet("background: transparent;")
        cell = QVBoxLayout(amount_cell)
        cell.setContentsMargins(0, 0, 0, 0)
        cell.setSpacing(0)
        cell.addStretch(1)
        cell.addWidget(amount, 0, Qt.AlignmentFlag.AlignRight)
        if status != "paid":
            caption = Caption(
                theme.status_label(status), colour=amount_colour, size=11
            )
            caption.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            cell.addWidget(caption, 0, Qt.AlignmentFlag.AlignRight)
        cell.addStretch(1)

        align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        self.transactions_list.addWidget(name, row, 0, align)
        self.transactions_list.addWidget(method, row, 1, align)
        self.transactions_list.addWidget(channel, row, 2, align)
        self.transactions_list.addWidget(amount_cell, row, 3)
        self.transactions_list.setRowMinimumHeight(row, 46)
        self._transaction_rows.append(name)

    def _fill_target(self, figures) -> None:
        try:
            from app.services import settings_service as _settings
            with self.context.reading() as _session:
                target = _settings.get_money(_session, "sales_target")
        except Exception:
            target = theme.SALES_TARGET
        achieved = figures.revenue_ytd
        self.target_value.setText(pesos(achieved, decimals=0))

        pct = float(achieved / target * 100) if target else Decimal(0)
        self.target_pill.set_text(f"{pct:.0f}% of ₱{float(target) / 1_000_000:.0f}M")
        # Behind pace is amber, on pace is green.
        on_pace = _pace(achieved, target, figures.today)
        self.target_pill.set_text_colour(
            theme.OK if on_pace else theme.WARN
        )
        self.target_chart.set_months(figures.month_sales, target, achieved)

    # -- motion -----------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        try:
            from app.staff import prefs as _prefs
            if bool(_prefs.get("appearance/reduce_motion")):
                self._animations_played = True
                return
        except Exception:
            pass
        if self.animate and not self._animations_played:
            self._animations_played = True
            self.channels_curves.animate_in()
            self.activity_heatmap.animate_in()
            self.target_chart.animate_in()
            self.revenue_sparkline.animate_in()

    _animations_played = False


def _pace(achieved: Decimal, target: Decimal, today: date) -> bool:
    if not target:
        return False
    year_start = date(today.year, 1, 1)
    days_elapsed = (today - year_start).days + 1
    expected = target * Decimal(days_elapsed) / Decimal(365)
    return achieved >= expected


def pesos(amount: Decimal | float, *, decimals: int = 2) -> str:
    return f"₱{amount:,.{decimals}f}"


def _signed_pesos(amount: Decimal, refunded: bool) -> str:
    if refunded:
        return f"−₱{abs(float(amount)):,.2f}"
    return f"₱{abs(float(amount)):,.2f}"


def _amount_colour(status: str, refunded: bool) -> str:
    """Only a paid row is drawn in the received-cash colour."""
    if refunded or status == "failed":
        return theme.DANGER
    if status == "pending":
        return theme.WARN
    return theme.TEXT


def build_dashboard_page(shell, *, animate: bool = True) -> DashboardPage:
    return DashboardPage(shell, animate=animate)
