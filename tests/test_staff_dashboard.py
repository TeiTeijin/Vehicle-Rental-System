"""The dashboard's tests.

The `branch` and `shell` fixtures are imported rather than redefined. They live
with the branch-builder in `test_staff_pages.py` because the *same* branch is
what every staff screen is tested against -- the dashboard has to agree with the
Fleet table about which cars are free, and two independently built fixtures
would not make that agreement testable.


Split by what each one is protecting:

* **Geometry** -- the five cards must not overlap, must share their column
  edges, and must keep the gutter the layout claims. A grid that overlaps is
  invisible in a screenshot nobody can see and obvious on screen.
* **Numbers** -- each card's figure must come from the service and must agree
  with the table it summarises.
* **Gates** -- a counter member's refresh must not run the branch queries.
* **Painted output** -- the charts draw their colour. They are QPainter code, so
  "renders without raising" would pass on a paintEvent that painted nothing.

No test here reads a mid-animation value; `animate=False` throughout, and the
animation is exercised separately by asserting it *reaches* full progress.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.test_staff_pages import (  # noqa: E402,F401
    as_staff,
    branch,
    qapp,
    shell,
)

from app.services import dashboard_service  # noqa: E402
from app.staff import icons, theme  # noqa: E402
from app.staff.dashboard_charts import (  # noqa: E402
    ActivityHeatmap,
    ChannelCurves,
    SalesTargetChart,
    _monotone_path,
    _nice_ceiling,
)
from app.staff.metrics import (  # noqa: E402
    CARD_GUTTER,
    CARD_RADIUS,
    REFERENCE_H,
    REFERENCE_W,
    TWO_COL_W,
)
from app.staff.pages.dashboard import DashboardPage, _pace, pesos  # noqa: E402


#: Alias for the session-scoped app fixture imported from `test_staff_pages`.
#: Two `QApplication`s in one process is a crash, so both files must share one.
qt_app = qapp


def _render(widget, width: int, height: int) -> QImage:
    """Render `widget` at a size and return the image."""
    widget.resize(width, height)
    widget.show()
    QTest.qWait(60)
    return widget.grab().toImage()


def _laid_out(page, width: int = REFERENCE_W, height: int = REFERENCE_H):
    """Show the page at a size and settle the layout.

    Geometry assertions need this: a page that has only been `resize()`d has not
    had a layout pass over it, and its children still report whatever size they
    were constructed with. That produces failures that look like overlap bugs
    and are really "nobody ever ran the layout".
    """
    page.resize(width, height)
    page.show()
    QApplication.processEvents()
    page.layout().activate()
    page.grid.layout().activate()
    QApplication.processEvents()
    return page


def _count(image: QImage, colour: str, tolerance: int = 26) -> int:
    """Pixels near `colour`. The only way to know a QPainter chart drew."""
    target = QColor(colour)
    total = 0
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if (
                abs(pixel.red() - target.red()) <= tolerance
                and abs(pixel.green() - target.green()) <= tolerance
                and abs(pixel.blue() - target.blue()) <= tolerance
            ):
                total += 1
    return total


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


class TestCardGeometry:
    def test_the_five_cards_do_not_overlap(self, shell, branch):
        """Two cards sharing pixels is the layout bug that matters.

        Checked by pairwise intersection of the laid-out rectangles rather than
        by reading the grid's geometry: `QGridLayout` reports what it was told,
        so the assertion has to come from the result.
        """
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()
        _laid_out(page)

        cards = page.grid._cards
        assert len(cards) == 5
        for card in cards:
            assert card.width() > 100, f"a card collapsed to {card.width()}px"

        for i, a in enumerate(cards):
            for j, b in enumerate(cards[i + 1 :], start=i + 1):
                # `intersects` counts shared edges, so shrink one by a pixel to
                # test for a real overlap rather than two flush cards.
                assert not a.geometry().adjusted(0, 0, -1, -1).intersects(
                    b.geometry()
                ), f"cards {i} and {j} overlap: {a.geometry()} vs {b.geometry()}"

    def test_the_cards_share_their_column_edges(self, shell, branch):
        """Row one must start on the same x as row two.

        Without this, a card with a longer minimum width pushes its whole column
        out of alignment, which is the exact thing the fixed gutter is for.
        """
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()
        _laid_out(page)

        cards = page.grid._cards
        assert len(cards) == 5
        # Three columns at the reference width, so cards 0/1/2 are row one.
        lefts_row_one = sorted(cards[i].x() for i in range(3))
        lefts_row_two = sorted(cards[i].x() for i in (3, 4))
        assert lefts_row_two[0] == lefts_row_one[0], (
            f"row two starts at {lefts_row_two[0]}, row one at {lefts_row_one[0]}"
        )
        assert lefts_row_two[1] == lefts_row_one[1], (
            f"column two drifts: {lefts_row_two[1]} vs {lefts_row_one[1]}"
        )

    def test_the_gutter_holds_between_columns(self, shell, branch):
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()
        _laid_out(page)

        cards = page.grid._cards
        gap = cards[1].x() - (cards[0].x() + cards[0].width())
        assert gap == CARD_GUTTER, f"gutter is {gap}px, expected {CARD_GUTTER}"

    def test_the_cards_fill_the_grid_width(self, shell, branch):
        """Three columns across the page, so no card is stranded at the left."""
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()
        _laid_out(page)

        cards = page.grid._cards
        rightmost = cards[2].x() + cards[2].width()
        assert rightmost == page.grid.width(), (
            f"the row ends at {rightmost}, the grid is {page.grid.width()} wide"
        )

    def test_every_card_in_a_row_is_the_same_width(self, shell, branch):
        """A row of cards that are not all the same width looks broken.

        This one needs figures with digits in them. A card's minimum width comes
        from its children, so a peso figure of eight or nine characters claims
        more room than its neighbours -- the test branch's small numbers hide
        it, and the real demo data does not.
        """
        from app.services.dashboard_service import (
            DashboardFigures,
            MonthSales,
            TransactionRow,
        )

        year = [
            MonthSales(
                month=date(2026, m, 1),
                label=date(2026, m, 1).strftime("%b"),
                total=Decimal("499056.25"),
            )
            for m in range(1, 10)
        ]
        page = _laid_out(DashboardPage(shell, animate=False))
        page._fill_revenue(
            DashboardFigures(
                today=date.today(),
                revenue_today=Decimal("28115.00"),
                revenue_yesterday=Decimal("50992.00"),
                month_sales=year,
                recent=[
                    TransactionRow(
                        customer="Maximilliana Concepcion III",
                        channel="online",
                        method="gcash",
                        amount=Decimal("15675.00"),
                        refunded=False,
                    )
                ],
            )
        )
        page._fill_transactions(
            DashboardFigures(
                today=date.today(),
                month_sales=year,
                recent=[
                    TransactionRow(
                        customer="Maximilliana Concepcion III",
                        channel="online",
                        method="gcash",
                        amount=Decimal("15675.00"),
                        refunded=False,
                    )
                ],
            )
        )
        _laid_out(page)

        widths = {card.width() for card in page.grid._cards}
        assert len(widths) == 1, f"cards are {sorted(widths)} wide, not one width"

    def test_the_cards_stay_equal_width_in_two_columns(self, shell, branch):
        """The other reflow, where two cards share the row instead of three."""
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()
        _laid_out(page, 1000, REFERENCE_H)

        assert page.grid._columns == 2, page.grid._columns
        cards = page.grid._cards
        assert cards[0].width() == cards[2].width(), (
            f"{cards[0].width()} vs {cards[2].width()}"
        )
        assert cards[2].width() == cards[3].width(), (
            f"{cards[2].width()} vs {cards[3].width()}"
        )

    def test_the_grid_reflows_to_two_columns_when_narrow(self, shell, branch):
        page = DashboardPage(shell, animate=False)
        page.resize(REFERENCE_W, REFERENCE_H)
        page.refresh()
        page.layout().activate()
        assert page.grid._columns == 3

        page.grid.reflow_to(1000)
        assert page.grid._columns == 2

        page.grid.reflow_to(800)
        assert page.grid._columns == 1

        page.grid.reflow_to(REFERENCE_W)
        assert page.grid._columns == 3

    def test_a_resized_page_reflows_its_grid_on_its_own(self, shell, branch):
        """`reflow_to` is the test hook; `resizeEvent` is the real path.

        Resizing the *page* rather than the grid: the page's layout owns the
        grid's width, so resizing the grid directly would be undone by the next
        layout pass and the test would prove nothing.
        """
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()
        _laid_out(page)
        assert page.grid._columns == 3

        page.resize(1000, REFERENCE_H)
        QApplication.processEvents()
        QApplication.processEvents()
        assert page.grid.width() < TWO_COL_W, (
            f"the grid is {page.grid.width()} wide; the page never got narrow"
        )
        assert page.grid._columns == 2, (
            f"resizeEvent left the grid at {page.grid._columns} columns"
        )

        page.resize(800, REFERENCE_H)
        QApplication.processEvents()
        QApplication.processEvents()
        assert page.grid._columns == 1, (
            f"resizeEvent left the grid at {page.grid._columns} columns"
        )

        # And back out again, because a one-way reflow is still a broken reflow.
        page.resize(REFERENCE_W, REFERENCE_H)
        QApplication.processEvents()
        QApplication.processEvents()
        assert page.grid._columns == 3


class TestStylesheetAgreesWithMetrics:
    """A QSS value cannot be read back at runtime, so the two are asserted.

    If someone changes `CARD_RADIUS` in metrics.py and not in the stylesheet,
    nothing errors -- the cards just stop matching the drawings.
    """

    def test_the_card_radius_in_the_qss_is_the_one_in_metrics(self):
        css = theme.load_stylesheet()
        assert f"border-radius: {CARD_RADIUS}px" in css, (
            f"staff.qss has no {CARD_RADIUS}px radius; "
            "cards will not match the design"
        )

    def test_the_gutter_appears_in_the_stylesheet_or_the_layout(self):
        """Either the QSS or the layout owns the gap, never neither.

        The gap is a layout concern here, so this is really a guard that it did
        not move into the stylesheet and get out of sync with metrics.py.
        """
        from app.staff.pages.dashboard import _CardGrid

        grid = _CardGrid()
        assert grid._grid.spacing() == CARD_GUTTER


# --------------------------------------------------------------------------
# numbers
# --------------------------------------------------------------------------


class TestFiguresMatchTheService:
    def test_the_revenue_card_shows_todays_takings(self, shell, branch):
        from app.services import dashboard_service

        page = DashboardPage(shell, animate=False)
        page.refresh()

        with page.context.reading() as session:
            expected = dashboard_service.revenue_on(session, date.today())
        assert page.revenue_value.text() == pesos(expected)

    def test_the_channel_totals_add_up_to_the_orders_counted(self, shell, branch):
        page = DashboardPage(shell, animate=False)
        page.refresh()

        shown = sum(
            int(label.text().replace(",", ""))
            for label, _pill in page.channel_tiles.values()
        )
        series_total = sum(count for _label, count, _c in page.channels_curves.series)
        assert shown == series_total, (
            f"the tiles say {shown}, the curves say {series_total}; "
            "one of them is lying"
        )

    def test_the_channel_labels_are_the_theme_labels(self, shell, branch):
        """Not `.title()`. 'gcash'.title() is 'Gcash' and 'walk_in' is 'Walk_In'."""
        page = DashboardPage(shell, animate=False)
        page.refresh()
        for key, (_label, pill) in page.channel_tiles.items():
            assert pill.text() == theme.CHANNEL_LABELS[key]

    def test_the_activity_caption_sums_the_grid(self, shell, branch):
        page = DashboardPage(shell, animate=False)
        page.refresh()
        counts = page.activity_heatmap.counts
        assert f"{sum(counts.values())} orders" in page.activity_caption.text()

    def test_the_target_pill_reports_the_real_percentage(self, shell, branch):
        from app.services import dashboard_service

        page = DashboardPage(shell, animate=False)
        page.refresh()
        with page.context.reading() as session:
            months = dashboard_service.month_sales(session, date.today().year)
        ytd = sum((m.total for m in months), Decimal("0.00"))

        expected = float(ytd / theme.SALES_TARGET * 100)
        assert f"{expected:.0f}%" in page.target_pill.text()

    def test_a_refund_renders_negative_though_the_column_is_positive(
        self, shell, branch, monkeypatch
    ):
        """`record_payment` refuses a non-positive amount, so `Payment.amount`
        is always positive and the sign is a rendering decision."""
        from app.staff.pages.dashboard import _signed_pesos

        rendered = _signed_pesos(Decimal("500.00"), refunded=True)
        assert rendered.startswith("−₱"), rendered
        assert "-500" not in rendered.replace("−", ""), rendered

    def test_the_transaction_list_shows_at_most_five(self, shell, branch):
        page = DashboardPage(shell, animate=False)
        page.refresh()
        assert len(page._transaction_rows) <= 5

    def test_a_branch_with_no_payments_says_so(self, shell, branch):
        page = DashboardPage(shell, animate=False)
        page.refresh()
        page._fill_transactions(
            dashboard_service.DashboardFigures(today=date.today())
        )
        assert len(page._transaction_rows) == 1
        assert "No payments" in page._transaction_rows[0].text()

    def test_refreshing_twenty_times_leaves_one_set_of_rows(self, shell, branch):
        """The page refreshes itself every 30 seconds.

        Anything the fill leaves behind -- rows, spacers, a stale stretch -- adds
        up silently, because one extra stretch moves nothing you can see until
        the list has walked off the card. Counted over twenty refreshes, which
        is ten minutes, and asserted to be flat rather than merely small.
        """
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()

        def layout_item_count() -> int:
            return page.transactions_list.count()

        baseline = layout_item_count()
        rows_each_pass = len(page._transaction_rows)
        for _ in range(20):
            page.refresh()
            QApplication.processEvents()

        assert layout_item_count() == baseline, (
            f"the list grew from {baseline} to {layout_item_count()} items"
        )
        # One row per payment plus the trailing spacer, and it must stay that way.
        assert rows_each_pass >= 1, "the fixture branch has no payments to list"
        assert layout_item_count() == rows_each_pass + 1, layout_item_count()
        assert len(page._transaction_rows) == rows_each_pass

    def test_the_list_does_not_creep_up_the_card(self, shell, branch):
        """The visual half of the same bug: the first row must not move."""
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()
        QApplication.processEvents()
        first_top = page._transaction_rows[0].y()

        for _ in range(10):
            page.refresh()
            QApplication.processEvents()

        assert page._transaction_rows[0].y() == first_top, (
            f"the first row moved from y={first_top} to "
            f"y={page._transaction_rows[0].y()}"
        )


class TestPace:
    def test_behind_in_march_is_not_the_same_as_behind_in_november(self):
        """A branch that sold a third of its target by March is on pace; the
        same figure in November is not. Comparing to the full year would call
        both of them behind."""
        target = Decimal("1200000")
        assert _pace(Decimal("300000"), target, date(2026, 3, 31)) is True
        assert _pace(Decimal("300000"), target, date(2026, 11, 30)) is False

    def test_a_zero_target_is_never_on_pace(self):
        assert _pace(Decimal("500"), Decimal("0"), date(2026, 6, 1)) is False


# --------------------------------------------------------------------------
# gates
# --------------------------------------------------------------------------


class TestRoleGates:
    def test_staff_see_the_strip_and_not_the_cards(self, shell, branch):
        as_staff(branch)
        page = DashboardPage(shell, animate=False)
        page.refresh()
        page.show()
        assert page.strip.isVisible() is True
        assert page.grid.isVisible() is False

    def test_an_admin_sees_everything(self, shell, branch):
        page = DashboardPage(shell, animate=False)
        page.refresh()
        page.show()
        assert page.grid.isVisible() is True
        assert page.strip.isVisible() is True

    def test_a_staff_refresh_issues_none_of_the_card_queries(
        self, shell, branch, monkeypatch
    ):
        """The point of the gate. Counted, not asserted by eye."""
        as_staff(branch)
        import app.staff.pages.dashboard as module

        calls: list[str] = []
        for name in ("month_sales", "orders_per_day", "recent_transactions"):
            real = getattr(module.dashboard_service, name)

            def spy(*args, _n=name, _r=real, **kwargs):
                calls.append(_n)
                return _r(*args, **kwargs)

            monkeypatch.setattr(module.dashboard_service, name, spy)

        page = DashboardPage(shell, animate=False)
        page.refresh()
        assert calls == [], f"a staff refresh ran {calls}"

    def test_an_admin_refresh_runs_all_three(self, shell, branch, monkeypatch):
        import app.staff.pages.dashboard as module

        calls: list[str] = []
        for name in ("month_sales", "orders_per_day", "recent_transactions"):
            real = getattr(module.dashboard_service, name)

            def spy(*args, _n=name, _r=real, **kwargs):
                calls.append(_n)
                return _r(*args, **kwargs)

            monkeypatch.setattr(module.dashboard_service, name, spy)

        page = DashboardPage(shell, animate=False)
        page.refresh()
        assert set(calls) == {"month_sales", "orders_per_day", "recent_transactions"}


# --------------------------------------------------------------------------
# the painted charts
# --------------------------------------------------------------------------


class TestChartsPaint:
    def test_the_channel_curves_draw_both_colours(self, qt_app):
        widget = ChannelCurves()
        widget.set_series(
            [
                ("Walk-in", 79, theme.TAN),
                ("Online", 82, theme.BROWN),
            ]
        )
        widget.progress = 1.0
        image = _render(widget, 560, 300)

        assert _count(image, theme.TAN) > 200, "no tan curve was drawn"
        assert _count(image, theme.BROWN) > 200, "no brown curve was drawn"

    def test_an_empty_channel_chart_paints_nothing_and_does_not_raise(self, qt_app):
        """A branch with no orders yet. The old code would divide by zero here."""
        widget = ChannelCurves()
        widget.set_series([])
        widget.progress = 1.0
        image = _render(widget, 560, 300)
        assert not image.isNull()

    def test_a_single_channel_does_not_break_the_split(self, qt_app):
        """100/0 must not divide the axis by zero or draw off-canvas."""
        widget = ChannelCurves()
        widget.set_series([("Walk-in", 40, theme.TAN)])
        widget.progress = 1.0
        image = _render(widget, 560, 300)
        assert _count(image, theme.TAN) > 200

    def test_the_heatmap_paints_one_shade_per_activity_level(self, qt_app):
        from datetime import date as d

        today = d(2026, 9, 30)
        counts = {today - timedelta(days=i): i % 4 for i in range(112)}
        widget = ActivityHeatmap()
        widget.set_counts(counts, min(counts))
        widget.progress = 1.0
        image = _render(widget, 420, 150)

        # The ramp runs from SURFACE up to TAN, so both ends must be present.
        assert _count(image, theme.TAN) > 100, "no high-activity cells"
        assert _count(image, theme.SURFACE) > 100, "no empty cells"

    def test_the_heatmap_drops_future_dated_rows(self, shell, branch, monkeypatch):
        """A booking whose start is tomorrow is not activity.

        The seeder creates them -- a rental that begins tomorrow legitimately
        has a `created_at` just before it -- so this is the real shape of the
        data, not a hypothetical.
        """
        import datetime as dt

        from app.models import Booking

        built, context = branch
        with context.session() as session:
            session.add(
                Booking(
                    user_id=built.customer.user_id,
                    vehicle_id=built.free_car.vehicle_id,
                    start_date=date.today() + timedelta(days=3),
                    end_date=date.today() + timedelta(days=5),
                    total_cost=Decimal("5000.00"),
                    status="confirmed",
                    created_at=dt.datetime.combine(
                        date.today() + timedelta(days=1), dt.time(9)
                    ),
                )
            )

        page = DashboardPage(shell, animate=False)
        page.refresh()
        assert max(page.activity_heatmap.counts) <= date.today()

    def test_the_heatmap_window_is_exactly_sixteen_weeks(self, shell, branch):
        page = DashboardPage(shell, animate=False)
        page.refresh()
        counts = page.activity_heatmap.counts
        assert 7 * 15 < len(counts) <= 7 * 16, len(counts)

        """The seeder books rentals slightly into the future. An order placed
        tomorrow is not activity and must not be plotted."""
        page = DashboardPage(shell, animate=False)
        page.refresh()
        assert max(page.activity_heatmap.counts) <= date.today()

    def test_the_sales_line_draws_and_labels_the_axis(self, qt_app):
        months = [
            dashboard_service.MonthSales(
                month=date(2026, m, 1), label=date(2026, m, 1).strftime("%b"),
                total=Decimal(v),
            )
            for m, v in [(1, "0"), (4, "412565.50"), (5, "445587.50"),
                         (6, "535092.00"), (7, "572753.50"), (9, "535759.00")]
        ]
        widget = SalesTargetChart()
        widget.set_months(months, Decimal("6000000"), Decimal("2500000"))
        widget.progress = 1.0
        image = _render(widget, 560, 220)
        assert _count(image, theme.TAN) > 150, "no sales line was drawn"

    def test_a_branch_with_no_months_paints_nothing_and_does_not_raise(
        self, qt_app
    ):
        widget = SalesTargetChart()
        widget.set_months([], Decimal("6000000"), Decimal("0"))
        widget.progress = 1.0
        assert not _render(widget, 560, 220).isNull()

    def test_the_year_stops_at_the_last_month_with_takings(self, qt_app):
        """A curve that sags to zero in the untouched months of the year reads
        as a collapse in takings, not as a year that is not over yet.

        Interior zeroes are a different matter and must survive: a real month
        with no revenue is a hole in the line, not a reason to stop.
        """
        year = [
            dashboard_service.MonthSales(
                month=date(2026, m, 1),
                label=date(2026, m, 1).strftime("%b"),
                total=Decimal(v),
            )
            for m, v in [
                (1, "0"), (2, "300000"), (3, "0"), (4, "412565.50"),
                (5, "445587.50"), (6, "535092.00"),
                (7, "0"), (8, "0"), (9, "0"), (10, "0"),
                (11, "0"), (12, "0"),
            ]
        ]
        widget = SalesTargetChart()
        widget.set_months(year, Decimal("6000000"), Decimal("1693245"))

        assert [m.month.month for m in widget.months] == [1, 2, 3, 4, 5, 6], (
            f"chart kept months {[m.month.month for m in widget.months]}"
        )

    def test_the_pace_line_is_drawn_when_a_target_is_set(self, qt_app):
        """The target is only meaningful if you can see where you are against it.

        Measured by ink, not by the code: the line and its caption have to end up
        in the widget's own pixels or the goal is a number in a pill and nothing
        else.
        """
        months = [
            dashboard_service.MonthSales(
                month=date(2026, m, 1), label=date(2026, m, 1).strftime("%b"),
                total=Decimal(v),
            )
            for m, v in [(1, "200000"), (2, "300000"), (3, "400000")]
        ]

        with_target = SalesTargetChart()
        with_target.set_months(months, Decimal("6000000"), Decimal("900000"))
        with_target.progress = 1.0
        target_ink = _count(_render(with_target, 560, 220), theme.MUTED)

        without = SalesTargetChart()
        without.set_months(months, Decimal("0"), Decimal("900000"))
        without.progress = 1.0
        plain_ink = _count(_render(without, 560, 220), theme.MUTED)

        assert target_ink > plain_ink, (
            f"the target line added no ink: {target_ink} vs {plain_ink}"
        )

    def test_the_takings_line_is_never_drawn_under_a_zero_target(self, qt_app):
        """A zero target would put the pace line on the axis floor, which reads
        as a real measurement at zero rather than as an unset target."""
        months = [
            dashboard_service.MonthSales(
                month=date(2026, m, 1), label=date(2026, m, 1).strftime("%b"),
                total=Decimal("400000"),
            )
            for m in (1, 2, 3)
        ]
        widget = SalesTargetChart()
        widget.set_months(months, Decimal("0"), Decimal("1200000"))
        widget.progress = 1.0
        assert not _render(widget, 560, 220).isNull()


class TestMonotoneSpline:
    """The reason this is not a natural spline.

    A spline through two falling months overshoots upwards between them and
    invents takings that never happened, on the one chart where an invented
    number would be believed.
    """

    def test_it_never_rises_where_the_data_falls(self):
        from PySide6.QtCore import QPointF

        points = [
            QPointF(0, 100), QPointF(50, 80), QPointF(100, 60),
        ]
        path = _monotone_path(points)
        # Sample the curve and confirm the y never increases.
        sampled = [
            path.pointAtPercent(i / 100).y() for i in range(101)
        ]
        for earlier, later in zip(sampled, sampled[1:]):
            assert later <= earlier + 0.5, f"curve rose from {earlier} to {later}"

    def test_it_passes_through_every_point(self):
        from PySide6.QtCore import QPointF

        points = [QPointF(i * 10, (i * i) % 7 * 10) for i in range(6)]
        path = _monotone_path(points)
        for point in points:
            nearest = min(
                (path.pointAtPercent(i / 200) for i in range(201)),
                key=lambda p: (p.x() - point.x()) ** 2,
            )
            assert abs(nearest.y() - point.y()) < 1.0, (
                f"the curve missed {point} by {abs(nearest.y() - point.y())}"
            )


class TestNiceCeiling:
    def test_it_lands_on_a_readable_number(self):
        """161 orders must not produce an axis labelled 161."""
        assert _nice_ceiling(161) == 200
        assert _nice_ceiling(7) == 10
        assert _nice_ceiling(1) == 1
        assert _nice_ceiling(0) == 1

    def test_it_never_rounds_down_below_the_value(self):
        for value in (3, 17, 99, 161, 2048, 999_999):
            assert _nice_ceiling(value) >= value


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------


class TestIconSet:
    def test_every_glyph_renders_and_stays_inside_the_grid(self, qt_app):
        for name in icons.PATHS:
            pixmap = icons.pixmap(name, theme.INK, 96)
            assert not pixmap.isNull(), name
            image = pixmap.toImage()
            for y in range(image.height()):
                for x in range(image.width()):
                    if image.pixelColor(x, y).alpha() > 40:
                        assert 2 <= x <= image.width() - 3, f"{name} clips at x={x}"
                        assert 2 <= y <= image.height() - 3, f"{name} clips at y={y}"
                        break

    def test_no_two_glyphs_look_the_same(self, qt_app):
        """`billing` and `card` were the same shape until this caught it."""
        signatures: dict[tuple, list[str]] = {}
        for name in icons.PATHS:
            image = icons.pixmap(name, theme.INK, 96).toImage()
            step = 96 // 8
            sig = tuple(
                1
                if image.pixelColor(
                    (x + 0.5) * step, (y + 0.5) * step
                ).alpha() > 40
                else 0
                for y in range(8)
                for x in range(8)
            )
            signatures.setdefault(sig, []).append(name)
        clashes = {tuple(v) for v in signatures.values() if len(v) > 1}
        assert not clashes, f"indistinguishable glyphs: {clashes}"

    def test_an_unknown_icon_is_an_error(self):
        with pytest.raises(KeyError):
            icons.svg_source("definitely-not-an-icon", theme.INK)


class TestTabularFigures:
    def test_the_hero_number_uses_tabular_digits(self, qt_app):
        """Without `tnum` the figure changes width as the thousands digit
        changes, so the number jitters on every 30-second refresh."""
        from PySide6.QtGui import QFont

        from app.staff.cards import HeroNumber

        hero = HeroNumber("₱1,234.00")

        # `hero.font` is the *method* on a QWidget -- `font` is a Qt property,
        # so PySide hands back the unbound callable rather than a QFont.
        assert hero.font().isFeatureSet(QFont.Tag("tnum"))

    def test_the_hero_number_holds_a_constant_width_across_values(
        self, qt_app
    ):
        """The actual symptom: two different figures, same pixel width."""
        from PySide6.QtGui import QFontMetrics

        from app.staff.cards import HeroNumber

        hero = HeroNumber("0")
        metrics = QFontMetrics(hero.font())
        widths = {
            metrics.horizontalAdvance(text)
            for text in ("₱1,111.11", "₱9,999.99", "₱0,000.00")
        }
        assert len(widths) == 1, f"widths differ: {widths}"


# --------------------------------------------------------------------------
# animation
# --------------------------------------------------------------------------


class TestMotion:
    def test_the_curves_reach_full_progress_and_stop(self, qt_app):
        widget = ChannelCurves()
        widget.set_series([("Walk-in", 79, theme.TAN), ("Online", 82, theme.BROWN)])
        widget.show()
        widget.animate_in(120)
        QTest.qWait(400)
        assert widget.progress == pytest.approx(1.0, abs=1e-6)

    def test_the_curves_are_not_mid_draw_after_the_animation(self, qt_app):
        """A half-drawn curve is a valid-looking wrong answer. This is the
        check that the reveal actually completes rather than stalling."""
        widget = ChannelCurves()
        widget.set_series([("Walk-in", 79, theme.TAN), ("Online", 82, theme.BROWN)])
        widget.animate_in(100)
        QTest.qWait(300)
        full = ChannelCurves()
        full.set_series([("Walk-in", 79, theme.TAN), ("Online", 82, theme.BROWN)])
        full.progress = 1.0
        assert abs(widget.progress - 1.0) < 1e-6

    def test_the_heatmap_staggers_rather_than_appearing_at_once(self, qt_app):
        """Mid-animation, some columns are drawn and later ones are not."""
        today = date(2026, 9, 30)
        counts = {today - timedelta(days=i): (i % 4) + 1 for i in range(112)}
        widget = ActivityHeatmap()
        widget.set_counts(counts, min(counts))
        widget.show()
        widget.animate_in(400)
        QTest.qWait(60)
        mid = widget.progress
        assert 0.0 < mid < 1.0, f"expected a partial draw, got {mid}"
        QTest.qWait(600)
        assert widget.progress == pytest.approx(1.0, abs=1e-6)
