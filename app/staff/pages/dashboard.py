from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from sqlalchemy import func, select

from app.services import dashboard_service
from app.staff import theme
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
from app.staff.pages.base import StaffPage
from app.utils.money import ZERO


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


class DashboardPage(StaffPage):
    PANEL = True

    def __init__(self, shell, *, animate: bool = True) -> None:
        super().__init__(shell, "Dashboard", "The branch at a glance.")

        #: Set False to land every value immediately.
        self.animate = animate

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

        self._first_load = True
        self._animations_pending = False

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

        self.revenue_value = HeroNumber(
            "₱0.00", size=HERO_SIZE, colour=theme.TEXT
        )
        self.revenue_trend = TrendLabel()

        # The picker's selection is the day shown; today is the default.
        self.revenue_period = QComboBox()
        self.revenue_period.setObjectName("revenuePeriod")
        self.revenue_period.setCursor(Qt.CursorShape.PointingHandCursor)
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

        body = self._card_header(
            card,
            "Recent Transactions",
            dark=False,
            buttons=(
                IconButton(
                    "search",
                    colour=theme.MUTED,
                    background=theme.SURFACE,
                    tooltip="Search transactions",
                ),
                IconButton(
                    "sliders",
                    colour=theme.MUTED,
                    background=theme.SURFACE,
                    tooltip="Filter",
                ),
            ),
        )
        body.addLayout(self.transactions_list, 1)
        card.layout().addLayout(body)
        return card

    def _build_target_card(self) -> Card:
        card = Card(dark=False)
        card.setMinimumHeight(ROW2_H)

        self.target_value = HeroNumber(
            "₱0", size=TARGET_SIZE, colour=theme.TEXT
        )
        self.target_pill = Pill(
            "0% of target",
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

    def refresh(self) -> None:
        super().refresh()

        today = date.today()
        with self.context.reading() as session:
            figures = dashboard_service.collect(
                session,
                today=today,
                weeks=dashboard_service.HEATMAP_WEEKS,
                recent=5,
                monthly=True,
            )

        self._fill_revenue(figures)
        self._fill_channels(figures)
        self._fill_activity(figures)
        self._fill_transactions(figures)
        self._fill_target(figures)

        self._first_load = False

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
        self._clear_transactions()

        rows = list(figures.recent)
        if not rows:
            empty = QLabel("No payments recorded yet.")
            font = QFont("Inter")
            font.setPixelSize(13)
            empty.setFont(font)
            empty.setStyleSheet(
                f"color: {theme.MUTED}; background: transparent;"
            )
            self.transactions_list.addWidget(empty, 0, 0, 1, 4)
            self._transaction_rows.append(empty)
            return

        for row, entry in enumerate(rows):
            self._transaction_row(entry, row)
        # A trailing spacer holds the rows at the top; `_clear_transactions` drains it.
        self.transactions_list.addItem(
            QSpacerItem(
                0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
            ),
            len(rows),
            0,
            1,
            4,
        )

    def _transaction_row(self, entry, row: int) -> None:
        name = ElidedLabel(
            entry.customer,
            colour=theme.TEXT,
            size=14,
            weight=QFont.Weight.Medium,
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

        amount = QLabel(_signed_pesos(entry.amount, entry.refunded))
        amount_font = QFont("Inter")
        amount_font.setPixelSize(15)
        amount_font.setWeight(QFont.Weight.DemiBold)
        amount_font.setFeature(QFont.Tag("tnum"), 1)
        amount.setFont(amount_font)
        amount.setStyleSheet(
            "background: transparent; color: "
            f"{theme.DANGER if entry.refunded else theme.TEXT};"
        )
        amount.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        self.transactions_list.addWidget(name, row, 0, align)
        self.transactions_list.addWidget(method, row, 1, align)
        self.transactions_list.addWidget(channel, row, 2, align)
        self.transactions_list.addWidget(amount, row, 3)
        self.transactions_list.setRowMinimumHeight(row, 46)
        self._transaction_rows.append(name)

    def _fill_target(self, figures) -> None:
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


def build_dashboard_page(shell, *, animate: bool = True) -> DashboardPage:
    return DashboardPage(shell, animate=animate)
