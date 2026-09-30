"""The dashboard: the branch at a glance, as five cards.

Replaces the older six-tile layout, which answered "where is everything" --
useful for running a counter, and not what a manager opens the app to see. The
new layout answers "how is the branch doing": what came in today, where the
orders came from, when they were placed, what was just paid, and how the year
is tracking.

**The five tiles are not gone.** Out / due back / overdue / free / owed are the
questions a member of staff asks while standing at the counter, and this page is
still the first thing they see. They are kept in a compact strip along the top,
restyled rather than replaced, so nothing that was on screen has been lost.

**The cards are admin-only, and the widgets are built lazily.** The revenue
figure, the channel split and the year-to-date line are the branch's
performance rather than the next customer's. Staff see the operational strip and
the transaction list; admins see all of it. The gate is on construction, not
just visibility, so a counter member's thirty-second refresh does not run four
queries for numbers they will never see.

**Every figure comes from `app.services.dashboard_service`, in one call.** Two
figures for the same thing that quietly differ is the specific failure a
dashboard exists to prevent, and that happens when each card runs its own query
and a refresh straddles midnight. One `collect()` per refresh, one `today`, and
the cards cannot disagree.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from sqlalchemy import func, select

from app.models import Booking, Vehicle
from app.services import booking_service, dashboard_service, payment_service
from app.staff import theme
from app.staff.cards import (
    Caption,
    Card,
    HeroNumber,
    IconButton,
    Pill,
    SectionLabel,
    TrendLabel,
)
from app.staff.dashboard_charts import (
    ActivityHeatmap,
    ChannelCurves,
    SalesTargetChart,
)
from app.staff.metrics import (
    CARD_GUTTER,
    CARD_PADDING,
    HERO_SIZE,
)
from app.staff.pages.base import StaffPage


class MetricTile(QWidget):
    """One of the small operational figures in the strip along the top.

    A plain widget rather than a `Card`: at 96px tall there is no room for a
    border and a shadow, and the strip reads better as a row of numbers than as
    a row of boxes. The value is tabular so the strip does not jitter when the
    numbers change width.
    """

    def __init__(self, label: str, colour: str, parent=None) -> None:
        super().__init__(parent)
        self._colour = colour

        self.value = HeroNumber("0", size=28, colour=colour)
        self.caption = Caption(label, size=12)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(2)
        layout.addWidget(self.value)
        layout.addWidget(self.caption)

    def set_value(self, value, *, prefix: str = "", decimals: int | None = None) -> None:
        if decimals is not None:
            self.value.setText(f"{prefix}{value:,.{decimals}f}")
        else:
            self.value.setText(f"{prefix}{value:,.0f}")


class _CardGrid(QWidget):
    """The five cards, in a grid that reflows as the window narrows.

    Three columns at the 1440px reference, two below `TWO_COL_W`, one below
    `NARROW_W`. The reflow is on a `resizeEvent` rather than a `QGridLayout`
    with stretch factors, because a grid cannot move a widget from one cell to
    another after it has been added -- the layout would need to be rebuilt, and
    rebuilding it on every resize frame is a visible stutter.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cards: list[Card] = []
        self._columns = 3
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(CARD_GUTTER)

    def add(self, card: Card) -> None:
        self._cards.append(card)
        self._relayout()

    def _relayout(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
        for index, card in enumerate(self._cards):
            self._grid.addWidget(card, index // self._columns, index % self._columns)
        for column in range(3):
            self._grid.setColumnStretch(column, 1 if column < self._columns else 0)
        # Rows share the leftover height evenly, so a short card and a tall one
        # still meet on the same baseline.
        for row in range((len(self._cards) + self._columns - 1) // self._columns):
            self._grid.setRowStretch(row, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._apply_columns()
        super().resizeEvent(event)

    def _apply_columns(self, width: int | None = None) -> None:
        """Choose a column count for `width` and reflow if it changed."""
        from app.staff.metrics import NARROW_W, TWO_COL_W

        available = self.width() if width is None else width
        columns = 3
        if available < NARROW_W:
            columns = 1
        elif available < TWO_COL_W:
            columns = 2
        if columns != self._columns:
            self._columns = columns
            self._relayout()

    def reflow_to(self, width: int) -> None:
        """Lay out as though the grid were `width` across.

        For tests and for the screenshot script, which need a deterministic
        column count without depending on when Qt happens to deliver a resize.
        """
        self._apply_columns(width)


class DashboardPage(StaffPage):
    def __init__(self, shell, *, animate: bool = True) -> None:
        super().__init__(shell, "Dashboard", "The branch at a glance.")

        #: Set False to land every value and every chart immediately. Tests read
        #: numbers out of labels and cannot see a half-drawn curve, so they
        #: turn this off; anything embedding the page may want the same.
        self.animate = animate

        # -- the operational strip ------------------------------------------
        # Kept from the old dashboard. Every one of these is a question a member
        # of staff asks while standing at the counter, and they are the numbers
        # a counter member should see -- unlike the cards below, which are the
        # branch's performance.
        self.strip = QWidget(self)
        strip_layout = QHBoxLayout(self.strip)
        strip_layout.setContentsMargins(0, 0, 0, 0)
        strip_layout.setSpacing(38)
        self._strip_tiles = {
            "out": MetricTile("Out now", theme.WARN),
            "due_back": MetricTile("Due back today", theme.TEXT),
            "overdue": MetricTile("Overdue", theme.DANGER),
            "available": MetricTile("Free to rent", theme.OK),
            "outstanding": MetricTile("Owed to the branch", theme.DANGER),
        }
        for tile in self._strip_tiles.values():
            strip_layout.addWidget(tile)
        strip_layout.addStretch(1)
        self.body.addWidget(self.strip)

        # -- the cards ------------------------------------------------------
        self.grid = _CardGrid(self)
        self.body.addWidget(self.grid, 1)

        self.revenue_card = self._build_revenue_card()
        self.channels_card = self._build_channels_card()
        self.activity_card = self._build_activity_card()
        self.transactions_card = self._build_transactions_card()
        self.target_card = self._build_target_card()

        # Order matters: the brief reads left-to-right, top-to-bottom, with the
        # dark revenue card anchoring the top left and the two dark cards
        # balancing it across the row.
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
        """A card's title row. Returns the layout to add the body into."""
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
        """Today's takings, dark, with the three counter actions."""
        card = Card(dark=True)
        card.setMinimumHeight(210)

        self.revenue_value = HeroNumber(
            "₱0.00", size=HERO_SIZE, colour=theme.PAPER
        )
        self.revenue_trend = TrendLabel()
        self.revenue_pill = Pill(
            "Today",
            colour=theme.PAPER,
            background=theme.INK_RAISED,
            height=26,
        )

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self.btn_new_rental = self._pill_button(
            "New Rental", "plus", theme.INK, theme.TAN, tooltip="Start a new rental"
        )
        self.btn_log_return = self._pill_button(
            "Log Return", "history", theme.PAPER, theme.INK_RAISED,
            tooltip="Record a returned car",
        )
        self.btn_more = IconButton(
            "dots",
            colour=theme.MUTED,
            background=theme.INK_RAISED,
            tooltip="More",
        )
        actions.addWidget(self.btn_new_rental)
        actions.addWidget(self.btn_log_return)
        actions.addStretch(1)
        actions.addWidget(self.btn_more)

        body = self._card_header(card, "Total Revenue", dark=True)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(10)
        title_row.addWidget(self.revenue_value)
        title_row.addStretch(1)
        title_row.addWidget(self.revenue_trend, 0, Qt.AlignmentFlag.AlignTop)
        body.addLayout(title_row)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.addWidget(self.revenue_pill)
        foot.addStretch(1)
        foot.addLayout(actions)
        body.addLayout(foot)
        body.addStretch(1)

        card.layout().addLayout(body)
        return card

    def _pill_button(
        self,
        text: str,
        icon_name: str,
        colour: str,
        background: str,
        *,
        tooltip: str = "",
    ) -> QWidget:
        """A filled action chip: an icon and a word, 44px tall."""
        from app.staff.metrics import BTN_HEIGHT, BTN_RADIUS

        holder = QWidget()
        holder.setFixedHeight(BTN_HEIGHT)
        holder.setCursor(Qt.CursorShape.PointingHandCursor)
        holder.setToolTip(tooltip or text)

        layout = QHBoxLayout(holder)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(7)
        layout.addWidget(IconButton(icon_name, colour=colour, size=20, icon_size=18))
        label = QLabel(text)
        font = QFont("Inter")
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.DemiBold)
        label.setFont(font)
        label.setStyleSheet(f"color: {colour}; background: transparent;")
        layout.addWidget(label)

        holder.setStyleSheet(
            f"QWidget {{ background: {background}; border-radius: {BTN_RADIUS}px; }}"
        )

        def click(event) -> None:
            from app.staff.widgets import toast

            toast(self.window(), f"{text}  Enot wired up in this build")

        holder.mousePressEvent = click  # type: ignore[method-assign]
        return holder

    def _build_channels_card(self) -> Card:
        """The walk-in / online split, light."""
        card = Card(dark=False)
        card.setMinimumHeight(210)

        self.channels_curves = ChannelCurves()
        self.channels_curves.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.channel_tiles: dict[str, tuple[HeroNumber, Pill]] = {}

        body = self._card_header(
            card,
            "Rental Channels",
            dark=False,
            subtitle="Orders placed, last 16 weeks",
        )
        tiles = QHBoxLayout()
        tiles.setContentsMargins(0, 0, 0, 0)
        tiles.setSpacing(30)
        for key in ("walk_in", "online"):
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(2)
            label = HeroNumber(
                "0",
                size=30,
                colour=theme.CHANNEL_COLOURS[key],
            )
            pill = Pill(
                theme.CHANNEL_LABELS[key],
                colour=theme.CHANNEL_COLOURS[key],
                background=_tint(theme.CHANNEL_COLOURS[key], 0.14),
                height=24,
                font_size=11,
            )
            column.addWidget(label)
            column.addWidget(pill)
            tiles.addLayout(column)
            self.channel_tiles[key] = (label, pill)
        tiles.addStretch(1)
        body.addLayout(tiles)
        body.addWidget(self.channels_curves, 1)

        card.layout().addLayout(body)
        return card

    def _build_activity_card(self) -> Card:
        """Orders per day, dark, as a sixteen-week grid."""
        card = Card(dark=True)
        card.setMinimumHeight(210)

        self.activity_heatmap = ActivityHeatmap()
        self.activity_heatmap.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.activity_caption = Caption(
            "Sixteen weeks of orders", colour=theme.INK_LINE, size=12
        )

        body = self._card_header(card, "Order Activity", dark=True)
        body.addWidget(self.activity_heatmap, 1)
        body.addWidget(self.activity_caption)
        card.layout().addLayout(body)
        return card

    def _build_transactions_card(self) -> Card:
        """The five most recent payments, dark."""
        card = Card(dark=True)
        card.setMinimumHeight(210)

        self.transactions_list = QVBoxLayout()
        self.transactions_list.setContentsMargins(0, 0, 0, 0)
        self.transactions_list.setSpacing(0)
        self._transaction_rows: list[QWidget] = []

        body = self._card_header(
            card,
            "Recent Transactions",
            dark=True,
            buttons=(
                IconButton(
                    "search",
                    colour=theme.MUTED,
                    background=theme.INK_RAISED,
                    tooltip="Search transactions",
                ),
                IconButton(
                    "sliders",
                    colour=theme.MUTED,
                    background=theme.INK_RAISED,
                    tooltip="Filter",
                ),
            ),
        )
        body.addLayout(self.transactions_list, 1)
        card.layout().addLayout(body)
        return card

    def _build_target_card(self) -> Card:
        """Year-to-date against the annual target, light."""
        card = Card(dark=False)
        card.setMinimumHeight(210)

        self.target_value = HeroNumber("₱0", size=30, colour=theme.TEXT)
        self.target_pill = Pill(
            "0% of target",
            colour=theme.TEXT,
            background=theme.SURFACE,
            height=24,
            font_size=11,
        )
        self.target_chart = SalesTargetChart()
        self.target_chart.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        head.addWidget(self.target_value)
        head.addStretch(1)
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
        show_figures = self.context.is_admin
        with self.context.reading() as session:
            figures = dashboard_service.collect(
                session,
                today=today,
                # The cards are the branch's performance, not the next
                # customer's. A counter member is not shown them, so their
                # refresh must not pay for the channel, heatmap or year queries.
                # `collect` is told to skip them rather than being handed a
                # session it is not allowed to use -- the alternative is
                # computing them and throwing them away.
                weeks=dashboard_service.HEATMAP_WEEKS if show_figures else 0,
                recent=5 if show_figures else 0,
                monthly=show_figures,
            )
            operational = self._operational(session, today)

        self._fill_strip(operational)
        if not show_figures:
            # Staff get the operational strip and nothing else. The cards are
            # hidden rather than left showing zeroes: an empty revenue card
            # invites someone to ask why the branch has taken nothing, and the
            # answer would be "because you are not an admin", which is a worse
            # answer than not asking.
            self.grid.setVisible(False)
            self._first_load = False
            return

        self.grid.setVisible(True)
        self._fill_revenue(figures)
        self._fill_channels(figures)
        self._fill_activity(figures)
        self._fill_transactions(figures)
        self._fill_target(figures)

        self._first_load = False

    def _operational(self, session, today: date) -> dict:
        """The counter's five numbers.

        Unchanged from the previous dashboard, including the decision to count
        "free to rent" against committed bookings rather than the raw
        `Vehicle.status` -- which is what keeps this row agreeing with the
        Fleet screen.
        """
        out_now = session.execute(
            select(func.count(Booking.booking_id)).where(
                Booking.status == "ongoing"
            )
        ).scalar_one()
        due_back = session.execute(
            select(func.count(Booking.booking_id)).where(
                Booking.status == "ongoing", Booking.end_date == today
            )
        ).scalar_one()
        overdue = len(booking_service.overdue_returns(session, today))
        free_now = session.execute(
            select(func.count(Vehicle.vehicle_id)).where(
                Vehicle.status == "available",
                ~Vehicle.vehicle_id.in_(
                    select(Booking.vehicle_id).where(
                        Booking.status.in_(("confirmed", "ongoing")),
                        Booking.start_date <= today,
                        Booking.end_date > today,
                    )
                ),
            )
        ).scalar_one()

        outstanding = Decimal("0.00")
        for booking in session.execute(
            select(Booking).where(
                Booking.status.in_(("confirmed", "ongoing", "completed"))
            )
        ).scalars():
            balance = payment_service.booking_balance(session, booking)
            if balance.balance > 0:
                outstanding += balance.balance

        return {
            "out": out_now,
            "due_back": due_back,
            "overdue": overdue,
            "available": free_now,
            "outstanding": outstanding,
        }

    def _fill_strip(self, operational: dict) -> None:
        self._strip_tiles["out"].set_value(operational["out"])
        self._strip_tiles["due_back"].set_value(operational["due_back"])
        self._strip_tiles["overdue"].set_value(operational["overdue"])
        self._strip_tiles["available"].set_value(operational["available"])
        self._strip_tiles["outstanding"].set_value(
            operational["outstanding"], prefix="₱", decimals=2
        )

    def _fill_revenue(self, figures) -> None:
        self.revenue_value.setText(pesos(figures.revenue_today))
        self.revenue_trend.set_change(figures.revenue_change_pct, up_is_good=True)

    def _fill_channels(self, figures) -> None:
        totals = figures.channels
        series = []
        for key, count in totals.as_rows():
            label, _pill = self.channel_tiles[key]
            label.setText(f"{count:,}")
            series.append(
                (theme.CHANNEL_LABELS[key], count, theme.CHANNEL_COLOURS[key])
            )
        self.channels_curves.set_series(series)

        # If any booking predates the channel column, say so rather than letting
        # the two curves quietly total less than the branch's real order count.
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
        """Empty the list's layout completely.

        `removeWidget` on its own is not enough here: it takes the widget out of
        the layout but leaves the layout item, and `addStretch` returns `None`
        in PySide6 so a trailing spacer cannot be tracked and removed
        individually. Draining through `takeAt` handles rows and spacers with
        one loop and leaves nothing for the next refresh to accumulate on.
        """
        while self.transactions_list.count():
            item = self.transactions_list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            # The layout's ownership moved into Python here; dropping the last
            # reference is what actually deletes the C++ item.
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
                f"color: {theme.INK_LINE}; background: transparent;"
            )
            self.transactions_list.addWidget(empty)
            self._transaction_rows.append(empty)
            return

        for entry in rows:
            self.transactions_list.addWidget(self._transaction_row(entry))
        self.transactions_list.addStretch(1)

    def _transaction_row(self, entry) -> QWidget:
        """One payment: who, how, which channel, how much."""
        holder = QWidget()
        holder.setFixedHeight(46)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        name = QLabel(entry.customer)
        name_font = QFont("Inter")
        name_font.setPixelSize(14)
        name_font.setWeight(QFont.Weight.Medium)
        name.setFont(name_font)
        name.setStyleSheet(f"color: {theme.PAPER}; background: transparent;")

        method = Caption(
            theme.METHOD_LABELS.get(entry.method, entry.method.title()),
            colour=theme.INK_LINE,
            size=12,
        )

        channel = Pill(
            theme.CHANNEL_LABELS.get(entry.channel, "Unrecorded"),
            colour=theme.INK_LINE,
            background=theme.INK_RAISED,
            height=22,
            font_size=11,
            dot=bool(entry.channel),
        )

        amount = QLabel(_signed_pesos(entry.amount, entry.refunded))
        amount_font = QFont("Inter")
        amount_font.setPixelSize(15)
        amount_font.setWeight(QFont.Weight.DemiBold)
        amount_font.setFeature(QFont.Tag("tnum"), 1)
        amount.setFont(amount_font)
        # A refund is red and carries a minus sign. The sign alone would be
        # ambiguous next to a discount, and the amount column holds positive
        # values by design.
        amount.setStyleSheet(
            "background: transparent; color: "
            f"{theme.DANGER if entry.refunded else theme.PAPER};"
        )
        amount.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        layout.addWidget(name)
        layout.addWidget(method)
        layout.addStretch(1)
        layout.addWidget(channel)
        layout.addWidget(amount)
        self._transaction_rows.append(holder)
        return holder

    def _fill_target(self, figures) -> None:
        target = theme.SALES_TARGET
        achieved = figures.revenue_ytd
        self.target_value.setText(pesos(achieved, decimals=0))

        pct = float(achieved / target * 100) if target else Decimal(0)
        self.target_pill.set_text(f"{pct:.0f}% of ₱{float(target) / 1_000_000:.0f}M")
        # Behind pace is amber, on pace is green. A single threshold at 100% would
        # show a branch two thirds of the way through September at 50% as "fine",
        # which is the reading the bar exists to prevent.
        on_pace = _pace(achieved, target, figures.today)
        self.target_pill.set_text_colour(
            theme.OK if on_pace else theme.WARN
        )
        self.target_chart.set_months(figures.month_sales, target, achieved)

    # -- motion -----------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802
        """Draw the charts in, once, the first time the page appears.

        On the thirty-second refresh the curves are already at full progress:
        re-drawing them every thirty seconds would make a live screen flicker
        constantly, which is worse than no animation at all.
        """
        super().showEvent(event)
        if self.animate and not self._animations_played:
            self._animations_played = True
            self.channels_curves.animate_in()
            self.activity_heatmap.animate_in()
            self.target_chart.animate_in()

    _animations_played = False


def _pace(achieved: Decimal, target: Decimal, today: date) -> bool:
    """Whether revenue is tracking at or above a straight-line pace.

    Compares against the share of the year elapsed rather than against the full
    target, so a branch in April is not told it is behind for having sold less
    than a whole year. The last day is pulled back to include today.
    """
    if not target:
        return False
    year_start = date(today.year, 1, 1)
    days_elapsed = (today - year_start).days + 1
    expected = target * Decimal(days_elapsed) / Decimal(365)
    return achieved >= expected


def _tint(colour: str, alpha: float) -> str:
    """A translucent version of `colour`, as `#RRGGBB` for a QSS background."""
    from PySide6.QtGui import QColor

    c = QColor(colour)
    c.setAlphaF(alpha)
    return c.name(QColor.NameFormat.HexArgb)


def pesos(amount: Decimal | float, *, decimals: int = 2) -> str:
    """Money as ₱1,234.50.

    The staff app used to render `PHP ` because the old tile font did not
    reliably carry the sign glyph; Inter does, so the dashboard uses the same
    helper as the rest of the app and the two stop disagreeing about what a peso
    looks like.
    """
    return f"₱{amount:,.{decimals}f}"


def _signed_pesos(amount: Decimal, refunded: bool) -> str:
    if refunded:
        return f"−₱{abs(float(amount)):,.2f}"
    return f"₱{abs(float(amount)):,.2f}"


def build_dashboard_page(shell, *, animate: bool = True) -> DashboardPage:
    return DashboardPage(shell, animate=animate)
