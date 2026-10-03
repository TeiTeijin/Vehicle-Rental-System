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
    as_admin,
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
    RevenueSparkline,
    SalesTargetChart,
    _monotone_path,
    _nice_ceiling,
)
from app.staff.metrics import (  # noqa: E402
    ACTIVITY_SHARE,
    CARD_GUTTER,
    CARD_RADIUS,
    CHANNELS_SHARE,
    MEDIUM_CONTENT_W,
    REFERENCE_H,
    REFERENCE_W,
    REVENUE_SHARE,
    ROW1_H,
    ROW2_H,
    TARGET_SHARE,
    TRANSACTIONS_SHARE,
    WIDE_CONTENT_W,
)
from app.staff.pages.dashboard import DashboardPage, _pace, pesos  # noqa: E402


#: Alias for the session-scoped app fixture imported from `test_staff_pages`.
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


def _rows_of(page) -> list[list]:
    """The laid-out cards, grouped into the grid's rows.

    Grouped by y rather than by index, so a test can assert about "the cards in
    the second row" without knowing how the shape placed them. Cards in the same
    row share a y and a height; sorting on x then gives reading order.
    """
    cards = sorted(page.grid.cards, key=lambda c: (c.y(), c.x()))
    rows: list[list] = []
    for card in cards:
        if rows and abs(card.y() - rows[-1][0].y()) <= 1:
            rows[-1].append(card)
        else:
            rows.append([card])
    for row in rows:
        row.sort(key=lambda c: c.x())
    return rows


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


def _pixel_positions(
    image: QImage, colour: str, tolerance: int = 26
) -> list[tuple[int, int]]:
    """Where the pixels near `colour` are, for tests about *where* a chart drew.

    A count proves a colour is on the canvas; it cannot prove the line sank on
    the day the week sank, or that the dot landed on the last day. The
    coordinates can.
    """
    target = QColor(colour)
    hits: list[tuple[int, int]] = []
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if (
                abs(pixel.red() - target.red()) <= tolerance
                and abs(pixel.green() - target.green()) <= tolerance
                and abs(pixel.blue() - target.blue()) <= tolerance
            ):
                hits.append((x, y))
    return hits


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
                # `intersects` counts shared edges, so one card is shrunk.
                assert not a.geometry().adjusted(0, 0, -1, -1).intersects(
                    b.geometry()
                ), f"cards {i} and {j} overlap: {a.geometry()} vs {b.geometry()}"

    def test_the_reference_width_gives_the_briefs_two_rows(self, shell, branch):
        """2 cards then 3, which is the layout in `dashboard_staff.txt`."""
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()
        _laid_out(page)

        assert page.grid.shape == "wide", page.grid.shape
        rows = _rows_of(page)
        assert [len(row) for row in rows] == [2, 3], [
            len(row) for row in rows
        ]

    def test_both_rows_start_on_the_same_x(self, shell, branch):
        """Row one and row two must share a leading edge.

        They are separate `QHBoxLayout`s now -- the brief's rows have different
        column counts, so they cannot be rows of one grid -- which means nothing
        forces them to line up. This is the assertion that keeps the grid from
        drifting into two ragged blocks.
        """
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()
        _laid_out(page)

        rows = _rows_of(page)
        assert rows[0][0].x() == rows[1][0].x(), (
            f"row one starts at {rows[0][0].x()}, row two at {rows[1][0].x()}"
        )

    def test_both_rows_fill_the_grid_width(self, shell, branch):
        """Neither row may leave a gap at the right edge.

        A stretched card that stops short is a card that has been given a share
        it did not use, which reads as a layout that did not finish.
        """
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()
        _laid_out(page)

        width = page.grid.width()
        for row in _rows_of(page):
            rightmost = row[-1].x() + row[-1].width()
            assert rightmost == width, (
                f"a row ends at {rightmost}, the grid is {width} wide"
            )

    def test_row_one_is_a_35_65_split(self, shell, branch):
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()
        _laid_out(page)

        row = _rows_of(page)[0]
        assert len(row) == 2
        total = row[0].width() + row[1].width() + CARD_GUTTER
        share = row[0].width() / total * 100
        assert abs(share - REVENUE_SHARE) < 1.5, (
            f"revenue card is {share:.1f}% of row one, expected "
            f"{REVENUE_SHARE}/{CHANNELS_SHARE}"
        )

    def test_row_two_is_a_30_38_32_split(self, shell, branch):
        """The three cards of row two, in the brief's order.

        The order matters as much as the shares: activity, transactions, target
        left to right, so the target card is the one that lines up with the right
        edge of the channels card above it.
        """
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()
        _laid_out(page)

        row = _rows_of(page)[1]
        assert len(row) == 3
        total = sum(card.width() for card in row) + CARD_GUTTER * 2
        for card, expected in zip(
            row, (ACTIVITY_SHARE, TRANSACTIONS_SHARE, TARGET_SHARE)
        ):
            share = card.width() / total * 100
            assert abs(share - expected) < 1.5, (
                f"a row-two card is {share:.1f}%, expected {expected}"
            )

    def test_the_gutter_holds_between_cards(self, shell, branch):
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()
        _laid_out(page)

        for row in _rows_of(page):
            for left, right in zip(row, row[1:]):
                gap = right.x() - (left.x() + left.width())
                assert gap == CARD_GUTTER, (
                    f"gutter is {gap}px, expected {CARD_GUTTER}"
                )

    def test_the_gutter_holds_between_rows(self, shell, branch):
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()
        _laid_out(page)

        rows = _rows_of(page)
        gap = rows[1][0].y() - (rows[0][0].y() + rows[0][0].height())
        assert gap == CARD_GUTTER, f"row gutter is {gap}px, expected {CARD_GUTTER}"

    def test_row_heights_meet_the_briefs_minimums(self, shell, branch):
        """230 for row one, 300 for row two -- as minimums, not fixed heights.

        A fixed height would leave a band of empty page under a tall window,
        which is the thing the "minimums, not heights" comment in `metrics`
        exists to prevent.
        """
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()
        _laid_out(page)

        rows = _rows_of(page)
        assert rows[0][0].height() >= ROW1_H, rows[0][0].height()
        assert rows[1][0].height() >= ROW2_H, rows[1][0].height()

    def test_a_taller_window_makes_the_rows_taller(self, shell, branch):
        """The growth is what makes "minimums" the right word for them."""
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()
        _laid_out(page)
        short = _rows_of(page)[0][0].height()

        _laid_out(page, REFERENCE_W, REFERENCE_H + 200)
        taller = _rows_of(page)[0][0].height()
        assert taller > short, f"{taller} is not taller than {short}"

    def test_big_figures_do_not_break_the_rows_shares(self, shell, branch):
        """A long figure must not widen the card holding it past its share.

        This one needs figures with digits in them. A card's minimum width comes
        from its children, so a peso figure of eight or nine characters claims
        more room than its neighbours -- the test branch's small numbers hide
        it, and the real demo data does not.

        The rows are no longer equal-width columns, so "every card the same width"
        is the wrong assertion now. What must hold is that each card keeps the
        proportion the brief gave it.
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

        for row, expected in zip(
            _rows_of(page),
            (
                (REVENUE_SHARE, CHANNELS_SHARE),
                (ACTIVITY_SHARE, TRANSACTIONS_SHARE, TARGET_SHARE),
            ),
        ):
            total = sum(card.width() for card in row) + CARD_GUTTER * (
                len(row) - 1
            )
            for card, share in zip(row, expected):
                actual = card.width() / total * 100
                assert abs(actual - share) < 1.5, (
                    f"a card is {actual:.1f}% of its row, expected {share}% -- "
                    "a long figure has widened it"
                )

    def test_the_grid_picks_its_shape_from_content_width(self, shell, branch):
        """The breakpoints are grid widths, not window widths.

        The sidebar takes a fixed 210 and the page margins 52, so a 1146px grid
        is a 1440px window. A test that passed window widths would pass at widths
        where the cards are still wide enough for the brief's layout.
        """
        grid = DashboardPage(shell, animate=False).grid
        assert grid.shape_for_width(WIDE_CONTENT_W) == "wide"
        assert grid.shape_for_width(WIDE_CONTENT_W - 1) == "medium"
        assert grid.shape_for_width(MEDIUM_CONTENT_W) == "medium"
        assert grid.shape_for_width(MEDIUM_CONTENT_W - 1) == "narrow"

    def test_medium_gives_the_target_a_row_of_its_own(self, shell, branch):
        """2 + 2 + 1. The sales curve needs the width more than the other three."""
        page = DashboardPage(shell, animate=False)
        page.grid.reflow_to(MEDIUM_CONTENT_W)
        page.refresh()
        _laid_out(page, 1000, REFERENCE_H)

        rows = _rows_of(page)
        assert [len(row) for row in rows] == [2, 2, 1], [len(row) for row in rows]
        assert rows[2][0] is page.target_card
        assert rows[2][0].width() == page.grid.width()

    def test_narrow_stacks_all_five(self, shell, branch):
        page = DashboardPage(shell, animate=False)
        page.grid.reflow_to(MEDIUM_CONTENT_W - 1)
        page.refresh()
        _laid_out(page, 800, REFERENCE_H)

        rows = _rows_of(page)
        assert [len(row) for row in rows] == [1, 1, 1, 1, 1], [
            len(row) for row in rows
        ]
        assert [row[0] for row in rows] == page.grid.cards

    def test_no_shape_overlaps_its_cards(self, shell, branch):
        """Overlap is the failure that matters, so it is checked at every width.

        Each shape is a different set of layouts, and the bugs are per-shape: a
        row that does not fit its cards only shows up at the width where it does
        not fit.
        """
        for width in (REFERENCE_W, 1100, 1000, 800, 640):
            page = DashboardPage(shell, animate=False)
            page.refresh()
            _laid_out(page, width, REFERENCE_H)
            cards = page.grid.cards
            for i, a in enumerate(cards):
                for b in cards[i + 1 :]:
                    assert not a.geometry().adjusted(0, 0, -1, -1).intersects(
                        b.geometry()
                    ), f"overlap at width {width}: {a.geometry()} vs {b.geometry()}"
            page.deleteLater()

    def test_a_resized_page_reflows_its_grid_on_its_own(self, shell, branch):
        """`reflow_to` is the test hook; `resizeEvent` is the real path.

        Resizing the *page* rather than the grid: the page's layout owns the
        grid's width, so resizing the grid directly would be undone by the next
        layout pass and the test would prove nothing.
        """
        page = _laid_out(DashboardPage(shell, animate=False))
        page.refresh()
        _laid_out(page)
        assert page.grid.shape == "wide"

        page.resize(1000, REFERENCE_H)
        QApplication.processEvents()
        QApplication.processEvents()
        assert page.grid.width() < WIDE_CONTENT_W, (
            f"the grid is {page.grid.width()} wide; the page never got narrow"
        )
        assert page.grid.shape == "medium", (
            f"resizeEvent left the grid {page.grid.shape}"
        )

        page.resize(800, REFERENCE_H)
        QApplication.processEvents()
        QApplication.processEvents()
        assert page.grid.shape == "narrow", (
            f"resizeEvent left the grid {page.grid.shape}"
        )

        page.resize(REFERENCE_W, REFERENCE_H)
        QApplication.processEvents()
        QApplication.processEvents()
        assert page.grid.shape == "wide"


class TestCardColours:
    """The dashboard's cards all share one palette.

    The brief mixed dark and light cards, but the working design settled on
    light cards across the grid -- so Order Activity and Recent Transactions must
    match Total Revenue, Rental Channels and Sales Target rather than sit dark
    between them.
    """

    def test_every_card_is_light(self, shell, branch):
        page = DashboardPage(shell, animate=False)
        page.refresh()
        assert page.revenue_card.dark is False
        assert page.channels_card.dark is False
        assert page.activity_card.dark is False
        assert page.transactions_card.dark is False
        assert page.target_card.dark is False


class TestLegendLayout:
    """The `Less ■■■■■ More` run must not draw a word over the swatches.

    The old code right-anchored the five swatches to the widget edge and then
    placed "More" at a hardcoded offset inside that span, so the label sat on
    top of the top shades. `_legend_positions` is the pure geometry behind the
    fix, tested here without a painter.
    """

    def test_the_words_sit_on_either_side_of_the_swatches(self):
        from app.staff.dashboard_charts import _legend_positions

        box, gap, label_gap = 14.0, 4.0, 8.0
        less_w, more_w = 24.0, 30.0
        less_x, swatch_xs, more_x = _legend_positions(
            320.0, less_w, more_w, box=box, gap=gap, label_gap=label_gap
        )

        assert len(swatch_xs) == 5
        assert swatch_xs == sorted(swatch_xs)
        assert swatch_xs[0] == pytest.approx(less_x + less_w + label_gap)
        assert more_x == pytest.approx(swatch_xs[-1] + box + label_gap)
        assert less_x + less_w <= swatch_xs[0]
        assert swatch_xs[-1] + box <= more_x

    def test_the_whole_run_is_anchored_to_the_right_edge(self):
        from app.staff.dashboard_charts import _legend_positions

        less_w, more_w = 24.0, 30.0
        _less_x, _swatches, more_x = _legend_positions(320.0, less_w, more_w)
        assert more_x + more_w == pytest.approx(320.0)

    def test_a_narrow_legend_is_clamped_rather_than_going_negative(self):
        from app.staff.dashboard_charts import _legend_positions

        less_x, _swatches, _more_x = _legend_positions(40.0, 24.0, 30.0)
        assert less_x == 0.0


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

    def test_the_revenue_pill_radius_is_eight(self):
        """The day picker reads as a pill, and the corner is a design choice.

        A QSS radius cannot be read back at runtime, so the block is pulled from
        the sheet and asserted directly. Checking the whole sheet would pass on
        any of the other 8px radii around it; this pins the picker itself.
        """
        css = theme.load_stylesheet()
        block = css.split("QComboBox#revenuePeriod")[1].split("}")[0]
        assert "border-radius: 8px" in block, block

    def test_the_gutter_appears_in_the_stylesheet_or_the_layout(self, qt_app):
        """Either the QSS or the layout owns the gap, never neither.

        The gap is a layout concern here, so this is really a guard that it did
        not move into the stylesheet and get out of sync with metrics.py.

        Both levels are checked: the gap between rows belongs to the vertical
        layout, and the gap between cards inside a row to each row layout, so
        setting one and forgetting the other leaves a grid with a seam down the
        middle of it.

        Takes `qt_app` because a `QWidget` constructed with no `QApplication`
        alive is a hard Qt abort, not a Python exception.
        """
        from app.staff.pages.dashboard import _CardGrid

        grid = _CardGrid()
        assert grid._rows.spacing() == CARD_GUTTER
        for index, layout in enumerate(grid._row_layouts):
            assert layout.spacing() == CARD_GUTTER, f"row {index} lost the gutter"


# --------------------------------------------------------------------------
# numbers
# --------------------------------------------------------------------------


class TestFiguresMatchTheService:
    def test_the_revenue_card_shows_the_year_to_date_total(self, shell, branch):
        from app.services import dashboard_service

        page = DashboardPage(shell, animate=False)
        page.refresh()

        with page.context.reading() as session:
            months = dashboard_service.month_sales(session, date.today().year)
            collected_today = dashboard_service.revenue_on(session, date.today())
        ytd = sum((m.total for m in months), Decimal("0.00"))
        assert page.revenue_value.text() == pesos(ytd)
        assert pesos(collected_today) in page.revenue_collections.text()

    def test_the_revenue_picker_shows_each_days_takings(self, shell, branch):
        """Picking a day swaps the collections line to that day's takings.

        The hero is the year-to-date total and does not move with the picker;
        the day the picker selects must re-read the same day the service would
        report, not a stale total.
        """
        from app.services import dashboard_service

        page = DashboardPage(shell, animate=False)
        page.refresh()

        combo = page.revenue_period
        assert combo.count() >= 2, "the picker offers no earlier days"
        assert combo.itemText(0) == "Today's Revenue"

        day = combo.itemData(1)
        combo.setCurrentIndex(1)
        QApplication.processEvents()

        with page.context.reading() as session:
            expected = dashboard_service.revenue_on(session, day)
        assert pesos(expected) in page.revenue_collections.text()

    def test_the_sparkline_shows_the_last_seven_days(self, shell, branch):
        """The sparkline is fixed to the week ending today, regardless of the
        day the picker drills into: it is the context *behind today*, so it must
        not follow the selection."""
        from app.services import dashboard_service

        page = DashboardPage(shell, animate=False)
        page.refresh()

        with page.context.reading() as session:
            figures = dashboard_service.collect(session)
        week = sorted(figures.revenue_days)[-7:]
        assert page.revenue_sparkline.points == [
            float(figures.revenue_days[day]) for day in week
        ]
        assert len(page.revenue_sparkline.points) == 7

    def test_the_channel_series_carry_the_real_totals(self, shell, branch):
        """The chart is the only place the figures live, so it is checked
        against a fresh read of the same window rather than against a tile."""
        page = DashboardPage(shell, animate=False)
        page.refresh()

        with page.context.reading() as session:
            expected = dashboard_service.collect(session).channels

        series = page.channels_curves.series
        assert [count for _label, count, _colour in series] == [
            expected.get(name) for name in ("walk_in", "online")
        ], f"the curve says {series}, the service says {expected.as_rows()}"

    def test_the_channel_labels_are_the_theme_labels(self, shell, branch):
        """Not `.title()`. 'gcash'.title() is 'Gcash' and 'walk_in' is 'Walk_In'."""
        page = DashboardPage(shell, animate=False)
        page.refresh()
        assert [label for label, _count, _colour in page.channels_curves.series] == [
            theme.CHANNEL_LABELS[name] for name in ("walk_in", "online")
        ]

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

    def test_the_target_pill_is_a_rounded_rect_not_a_stadium(self, shell, branch):
        page = DashboardPage(shell, animate=False)
        assert page.target_pill._radius == 8

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
        assert rows_each_pass >= 1, "the fixture branch has no payments to list"
        assert layout_item_count() == 4 * rows_each_pass + 1, layout_item_count()
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


class _Payment:
    """The fields `_transaction_row` reads, without a database row."""

    def __init__(self, customer, method, channel, amount, refunded=False):
        self.customer = customer
        self.method = method
        self.channel = channel
        self.amount = Decimal(amount)
        self.refunded = refunded


class TestTransactionRows:
    """The Recent Transactions row: a chip per channel and four lined columns."""

    def _fill_two(self, page):
        page._clear_transactions()
        page._transaction_row(
            _Payment("Ana Dela Cruz", "gcash", "online", "1250.00"), 0
        )
        page._transaction_row(
            _Payment("Marco Reyes", "cash", "walk_in", "480.00"), 1
        )

    def test_the_channel_pill_is_a_rounded_rect_not_a_stadium(
        self, shell, branch
    ):
        from app.staff.cards import Pill

        page = DashboardPage(shell, animate=False)
        self._fill_two(page)
        pill = page.transactions_list.itemAtPosition(0, 2).widget()
        assert isinstance(pill, Pill)
        assert pill._radius == 8, "the channel pill is the stadium default"

    def test_walk_in_and_online_take_different_marks(self, shell, branch):
        from app.staff import theme

        page = DashboardPage(shell, animate=False)
        self._fill_two(page)
        online = page.transactions_list.itemAtPosition(0, 2).widget()
        walk_in = page.transactions_list.itemAtPosition(1, 2).widget()
        assert online._border_colour == theme.CHANNEL_DOT_COLOURS["online"]
        assert walk_in._border_colour == theme.CHANNEL_DOT_COLOURS["walk_in"]
        assert online._dot_colour != walk_in._dot_colour
        assert online._border_colour == online._dot_colour
        assert walk_in._border_colour == walk_in._dot_colour

    def test_the_four_columns_line_up_across_rows(self, shell, branch):
        page = _laid_out(DashboardPage(shell, animate=False))
        self._fill_two(page)
        page.transactions_list.activate()
        QApplication.processEvents()
        cells = [
            [
                page.transactions_list.itemAtPosition(row, column).widget()
                for column in range(4)
            ]
            for row in (0, 1)
        ]
        name0, method0, channel0, amount0 = cells[0]
        _name1, method1, channel1, amount1 = cells[1]
        assert method0.x() == method1.x()
        assert channel0.x() == channel1.x()
        assert amount0.x() == amount1.x()
        assert name0.x() < method0.x() < channel0.x() < amount0.x()

    def test_the_method_keeps_its_full_label(self, shell, branch):
        page = DashboardPage(shell, animate=False)
        page._clear_transactions()
        page._transaction_row(
            _Payment("Ana Dela Cruz", "card", "online", "1250.00"), 0
        )
        method = page.transactions_list.itemAtPosition(0, 1).widget()
        assert method.text() == "Credit/Debit Card"

    def test_both_channel_marks_reach_the_pixels(self, shell, branch):
        """The dot and hairline are painted colour, not just set on a widget."""
        from app.staff import theme

        page = DashboardPage(shell, animate=False)
        self._fill_two(page)
        image = _render(page.transactions_card, 420, 320)
        assert _count(image, theme.CHANNEL_DOT_COLOURS["online"]) > 0, (
            "the online coffee mark was never drawn"
        )
        assert _count(image, theme.CHANNEL_DOT_COLOURS["walk_in"]) > 0, (
            "the walk-in caramel mark was never drawn"
        )
        assert _count(image, theme.SURFACE) > 0, "the chip lost its fill"


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


class TestRoles:
    """Everyone sees all five cards.

    This page used to hide the cards from staff and show only the operational
    strip. The brief asks for one screen for everyone, and every figure on it is
    an aggregate count rather than anything about a named customer, so there is
    nothing here for a counter member to be kept out of.
    """

    def test_staff_see_all_five_cards(self, shell, branch):
        as_staff(branch)
        page = DashboardPage(shell, animate=False)
        page.refresh()
        page.show()
        assert page.grid.isVisible() is True
        assert len(page.grid.cards) == 5

    def test_an_admin_sees_all_five_cards(self, shell, branch):
        page = DashboardPage(shell, animate=False)
        page.refresh()
        page.show()
        assert page.grid.isVisible() is True
        assert len(page.grid.cards) == 5

    def test_both_roles_get_the_same_figures(self, shell, branch):
        """Not just the same widgets -- the same numbers.

        The failure this guards against is a gate that hides the cards but keeps
        skipping their queries, so a staff member sees five cards of zeroes and
        nobody can say why.
        """
        as_staff(branch)
        staff_page = DashboardPage(shell, animate=False)
        staff_page.refresh()
        staff_revenue = staff_page.revenue_value.text()

        as_admin(branch)
        admin_page = DashboardPage(shell, animate=False)
        admin_page.refresh()

        assert admin_page.revenue_value.text() == staff_revenue, (
            f"staff see {staff_revenue}, admins see {admin_page.revenue_value.text()}"
        )

    def test_the_strip_is_gone(self, shell, branch):
        """Five more figures competing with the hero number is what it was.

        Its operational questions moved to the Today page, which is where a
        member of staff at the counter looks for them.
        """
        page = DashboardPage(shell, animate=False)
        assert not hasattr(page, "strip")
        assert not hasattr(page, "_strip_tiles")

    def test_a_refresh_runs_every_card_query(self, shell, branch, monkeypatch):
        """Counted, not asserted by eye -- this is the gate that used to exist."""
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
        """Both curves are on the canvas.

        Asserted as a *difference* against a tan-only control rather than as a
        pixel count. The strokes are gradients that ramp up to full saturation at
        the end dot, so the number of on-colour pixels depends on how light the
        colour is: TAN sits close to the cream card and its blended pixels still
        match within the tolerance, while BROWN only matches where the gradient
        has nearly reached full alpha. A fixed "> 200 pixels" threshold passed
        for one curve and failed for the other for that reason alone.

        The control makes the question the one that matters: does drawing the
        Online series put BROWN on the canvas that was not there before?
        """
        def rendered(series):
            widget = ChannelCurves()
            widget.set_series(series)
            widget.progress = 1.0
            return _render(widget, 560, 300)

        both = rendered(
            [
                ("Walk-in", 79, theme.TAN),
                ("Online", 82, theme.BROWN),
            ]
        )
        tan_only = rendered([("Walk-in", 79, theme.TAN)])

        assert _count(both, theme.TAN) > 200, "no tan curve was drawn"

        brown_both = _count(both, theme.BROWN)
        brown_without = _count(tan_only, theme.BROWN)
        assert brown_both > 50, f"only {brown_both} brown pixels were drawn"
        assert brown_both > brown_without * 2, (
            f"brown went from {brown_without} to {brown_both} when the Online "
            "series was added, which is not the shape of a second curve"
        )

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

    def test_the_revenue_sparkline_draws_a_brown_line(self, qt_app):
        """The week is a stroke, not a bar chart: it must leave a line behind."""
        widget = RevenueSparkline()
        widget.set_series(
            [Decimal("100"), Decimal("480"), Decimal("120"), Decimal("500"),
             Decimal("490"), Decimal("520"), Decimal("40")]
        )
        widget.progress = 1.0
        image = _render(widget, 340, 72)
        assert _count(image, theme.BROWN) > 80, "no sparkline was drawn"

    def test_the_sparkline_sits_on_the_floor_when_the_scale_starts_at_zero(
        self, qt_app
    ):
        """A week at 10% of its peak must dive, not hover mid-canvas.

        The whole reason the card keeps a red "↓ 92.08%" honest is that the wash
        behind it is scaled from zero: the quiet day lands near the floor, so the
        percent and the picture agree. Scaling to the data's own range would
        leave every wobble looking like a cliff.
        """
        def last_y(series):
            widget = RevenueSparkline()
            widget.set_series(series)
            widget.progress = 1.0
            image = _render(widget, 340, 80)
            hits = _pixel_positions(image, theme.BROWN)
            assert hits, "nothing was drawn"
            right = [y for x, y in hits if x > image.width() - 8]
            assert right, "the line never reached the last day"
            return max(right)

        flat = [Decimal("1000")] * 7
        drop = [Decimal("1000")] * 6 + [Decimal("100")]
        assert last_y(drop) > last_y(flat) + 20, (
            "the quiet day did not sink relative to a flat week"
        )

    def test_an_empty_or_flat_sparkline_does_not_divide_by_zero(self, qt_app):
        """No orders yet, and a week where every day is zero, both paint."""
        for series in ([], [Decimal("0")] * 7):
            widget = RevenueSparkline()
            widget.set_series(series)
            widget.progress = 1.0
            assert not _render(widget, 340, 72).isNull()

    def test_the_heatmap_paints_one_shade_per_activity_level(self, qt_app):
        from datetime import date as d

        today = d(2026, 9, 30)
        counts = {today - timedelta(days=i): i % 4 for i in range(112)}
        widget = ActivityHeatmap()
        widget.set_counts(counts, min(counts))
        widget.progress = 1.0
        image = _render(widget, 420, 150)

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

    def test_no_month_is_highlighted_until_the_pointer_arrives(self, qt_app):
        """At rest the card is the line and its dots; the chip is a hover."""
        months = [
            dashboard_service.MonthSales(
                month=date(2026, m, 1), label=date(2026, m, 1).strftime("%b"),
                total=Decimal("400000"),
            )
            for m in (1, 2, 3, 4, 5, 6)
        ]
        widget = SalesTargetChart()
        widget.set_months(months, Decimal("6000000"), Decimal("2400000"))
        widget.progress = 1.0

        assert _count(_render(widget, 560, 220), theme.INK) == 0, (
            "a month was highlighted with no pointer on the chart"
        )

        widget._hover_month = 2
        assert _count(_render(widget, 560, 220), theme.INK) > 0, (
            "the hover chip never drew"
        )


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

        # `hero.font` is the method; PySide returns the callable, not a QFont.
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


class TestHeroNumberFit:
    def test_a_long_value_shrinks_to_fit_its_card(self, qt_app):
        """The revenue card gives the figure ~292px. A 64px value does not fit,
        and a QLabel clips rather than wraps, so the figure must shrink instead
        of silently losing its leading digits."""
        from PySide6.QtGui import QFontMetrics

        from app.staff.cards import HeroNumber
        from app.staff.metrics import HERO_MIN_SIZE, HERO_SIZE

        hero = HeroNumber("₱98,765,432.10")
        hero.setFixedWidth(292)
        hero.show()
        qt_app.processEvents()
        metrics = QFontMetrics(hero.font())
        assert metrics.horizontalAdvance(hero.text()) <= hero.width()
        assert HERO_MIN_SIZE <= hero.font().pixelSize() < HERO_SIZE
        assert hero.text() == "₱98,765,432.10"

    def test_an_absurd_value_elides_and_keeps_the_full_tooltip(self, qt_app):
        from app.staff.cards import HeroNumber
        from app.staff.metrics import HERO_MIN_SIZE

        full = "₱" + "9" * 40 + ".00"
        hero = HeroNumber(full)
        hero.setFixedWidth(120)
        hero.show()
        qt_app.processEvents()
        assert hero.font().pixelSize() == HERO_MIN_SIZE
        assert hero.text().endswith("…")
        assert hero.toolTip() == full


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
