"""Regression tests for the dashboard reliability and controls fixes.

These cover the five reported failures:

* the revenue-period picker clipped its own label after an async fill;
* the Recent Transactions search and filter buttons were decorative;
* the page header's More button did nothing on every page;
* the cards showed zeroes (indistinguishable from real data) while loading;
* hovering a non-clickable chart promised a click with a pointing hand.

They also cover the customer-name tooltip that was missing from long names.
"""

from __future__ import annotations

import threading
from datetime import date, timedelta
from decimal import Decimal

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QLineEdit,
)

from tests.test_staff_pages import (  # noqa: E402,F401
    as_admin,
    as_staff,
    branch,
    qapp,
    shell,
)

from app.services import dashboard_service  # noqa: E402
from app.services.dashboard_service import (  # noqa: E402
    DashboardFigures,
    MonthSales,
    TransactionRow,
)
from app.staff.dashboard_charts import ActivityHeatmap, SalesTargetChart  # noqa: E402
from app.staff.pages.base import StaffPage  # noqa: E402
from app.staff.pages.dashboard import DashboardPage  # noqa: E402


def _page(shell) -> DashboardPage:
    page = DashboardPage(shell, animate=False)
    page.resize(1440, 900)
    page.show()
    QApplication.processEvents()
    return page


def _transaction(
    customer: str,
    method: str,
    channel: str | None,
    amount: str,
    *,
    refunded: bool = False,
    status: str = "paid",
) -> TransactionRow:
    return TransactionRow(
        customer=customer,
        channel=channel,
        method=method,
        amount=Decimal(amount),
        refunded=refunded,
        status=status,
    )


#: Seven rows, so "at most five rendered" is observable, with one example of
#: every method, channel, and status the filter offers.
ROWS = [
    _transaction("Ana Reyes", "cash", "walk_in", "480.00"),
    _transaction("Marco Reyes", "gcash", "online", "1250.00"),
    _transaction("Pending Pia", "gcash", "online", "500.00", status="pending"),
    _transaction(
        "Refunded Rita", "card", "online", "900.00",
        refunded=True, status="refunded",
    ),
    _transaction("Cash Carl", "cash", "walk_in", "300.00"),
    _transaction("Card Cora", "card", "walk_in", "1200.00"),
    _transaction("Ana Lopez", "gcash", "walk_in", "750.00"),
]


def _fill_transactions(page: DashboardPage, rows=ROWS) -> None:
    page._fill_transactions(
        DashboardFigures(today=date.today(), recent=list(rows))
    )


def _visible_customers(page: DashboardPage) -> list[str]:
    return [
        row.full_text()
        for row in page._transaction_rows
        if hasattr(row, "full_text")
    ]


def _mouse_move(widget, x: float, y: float) -> None:
    local = QPointF(x, y)
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        local,
        local,
        local,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mouseMoveEvent(event)


# --------------------------------------------------------------------------
# the clipped revenue-day picker
# --------------------------------------------------------------------------


class TestRevenuePeriodSizing:
    def test_the_picker_sizes_to_its_contents_after_an_async_fill(
        self, shell, branch
    ):
        """The control used to be measured once, while empty, and then clipped.

        An async fill ran after the first show, so the longest label
        ("Yesterday's Revenue") was drawn under the arrow.
        """
        page = _page(shell)
        assert (
            page.revenue_period.sizeAdjustPolicy()
            == QComboBox.SizeAdjustPolicy.AdjustToContents
        )

        with page.context.reading() as session:
            figures = dashboard_service.collect(
                session, today=date.today(), recent=0, monthly=True
            )
        page._load_gen = 1
        page._on_async_loaded(1, figures)
        QApplication.processEvents()
        page.layout().activate()
        QApplication.processEvents()

        labels = [
            page.revenue_period.itemText(index)
            for index in range(page.revenue_period.count())
        ]
        longest = max(labels, key=len)
        needed = page.revenue_period.fontMetrics().horizontalAdvance(longest)
        assert page.revenue_period.width() >= needed, (
            f"the picker is {page.revenue_period.width()}px wide but "
            f"{longest!r} needs {needed}px"
        )
        assert page.revenue_period.width() >= page.revenue_period.sizeHint().width()


# --------------------------------------------------------------------------
# the recent-transactions search and filter
# --------------------------------------------------------------------------


class TestTransactionSearch:
    def test_the_search_field_is_toggled_by_the_search_icon(self, shell, branch):
        page = _page(shell)
        assert isinstance(page.transaction_search, QLineEdit)
        assert page.transaction_search.isHidden() is True

        page.transaction_search_button.clicked.emit()
        QApplication.processEvents()
        assert page.transaction_search.isHidden() is False

        page.transaction_search_button.clicked.emit()
        QApplication.processEvents()
        assert page.transaction_search.isHidden() is True

    def test_a_query_narrows_the_visible_rows(self, shell, branch):
        page = _page(shell)
        _fill_transactions(page)
        page.set_transaction_query("ana")
        QApplication.processEvents()
        assert _visible_customers(page) == ["Ana Reyes", "Ana Lopez"]

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("gcash", ["Marco Reyes", "Pending Pia", "Ana Lopez"]),
            ("walk-in", ["Ana Reyes", "Cash Carl", "Card Cora", "Ana Lopez"]),
            ("pending", ["Pending Pia"]),
            ("refunded", ["Refunded Rita"]),
            ("1,250", ["Marco Reyes"]),
            ("credit/debit", ["Refunded Rita", "Card Cora"]),
        ],
    )
    def test_search_matches_customer_method_channel_status_and_amount(
        self, shell, branch, query, expected
    ):
        page = _page(shell)
        _fill_transactions(page)
        page.set_transaction_query(query)
        assert _visible_customers(page) == expected

    def test_at_most_five_matches_are_rendered(self, shell, branch):
        page = _page(shell)
        _fill_transactions(page)
        assert len(page._transaction_rows) == 5
        assert _visible_customers(page) == [
            "Ana Reyes",
            "Marco Reyes",
            "Pending Pia",
            "Refunded Rita",
            "Cash Carl",
        ]

    def test_a_query_with_no_match_says_so(self, shell, branch):
        page = _page(shell)
        _fill_transactions(page)
        page.set_transaction_query("nobody-by-that-name")
        assert len(page._transaction_rows) == 1
        assert "No matching" in page._transaction_rows[0].text()


class TestTransactionFilter:
    def test_the_filter_menu_offers_the_six_choices(self, shell, branch):
        page = _page(shell)
        labels = [action.text() for action in page.transaction_filter.actions()]
        assert labels == ["All", "Cash", "Card", "GCash", "Refunded", "Pending"]

    def test_the_filter_icon_opens_the_menu(self, shell, branch):
        page = _page(shell)
        page.transaction_filter_button.clicked.emit()
        QApplication.processEvents()
        assert page.transaction_filter.isVisible() is True
        page.transaction_filter.hide()

    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("all", [
                "Ana Reyes", "Marco Reyes", "Pending Pia",
                "Refunded Rita", "Cash Carl",
            ]),
            ("cash", ["Ana Reyes", "Cash Carl"]),
            ("card", ["Refunded Rita", "Card Cora"]),
            ("gcash", ["Marco Reyes", "Pending Pia", "Ana Lopez"]),
            ("refunded", ["Refunded Rita"]),
            ("pending", ["Pending Pia"]),
        ],
    )
    def test_a_filter_applies_its_method_or_status(
        self, shell, branch, key, expected
    ):
        page = _page(shell)
        _fill_transactions(page)
        page.set_transaction_filter(key)
        assert _visible_customers(page) == expected

    def test_choosing_a_menu_action_applies_that_filter(self, shell, branch):
        page = _page(shell)
        _fill_transactions(page)
        action = next(
            action
            for action in page.transaction_filter.actions()
            if action.text() == "GCash"
        )
        action.trigger()
        QApplication.processEvents()
        assert _visible_customers(page) == [
            "Marco Reyes", "Pending Pia", "Ana Lopez",
        ]
        assert action.isChecked() is True

    def test_search_and_filter_combine(self, shell, branch):
        page = _page(shell)
        _fill_transactions(page)
        page.set_transaction_filter("gcash")
        page.set_transaction_query("pending")
        assert _visible_customers(page) == ["Pending Pia"]


class TestCustomerTooltip:
    def test_each_transaction_row_carries_the_full_customer_name(
        self, shell, branch
    ):
        page = _page(shell)
        _fill_transactions(page)
        assert page._transaction_rows[0].toolTip() == "Ana Reyes"


# --------------------------------------------------------------------------
# the page header's overflow action
# --------------------------------------------------------------------------


class TestOverflowActions:
    def test_the_default_page_has_no_overflow_actions(self, shell):
        page = StaffPage(shell, "Plain page", "")
        assert page.overflow_actions() == []
        assert page.overflow_button.isHidden() is True

    def test_the_dashboard_offers_a_refresh_action(self, shell, branch):
        page = _page(shell)
        actions = page.overflow_actions()
        assert [label for label, _callback in actions] == ["Refresh dashboard"]
        assert callable(actions[0][1])
        assert page.overflow_button.isHidden() is False
        assert [
            action.text() for action in page.overflow_menu.actions()
        ] == ["Refresh dashboard"]

    def test_the_more_button_opens_the_menu(self, shell, branch):
        page = _page(shell)
        page.overflow_button.clicked.emit()
        QApplication.processEvents()
        assert page.overflow_menu is not None
        assert page.overflow_menu.isVisible() is True
        page.overflow_menu.hide()

    def test_the_refresh_action_reloads_the_dashboard(
        self, shell, branch, monkeypatch
    ):
        page = _page(shell)
        calls = []
        monkeypatch.setattr(
            page, "refresh_async", lambda *args, **kwargs: calls.append((args, kwargs))
        )
        page.overflow_menu.actions()[0].trigger()
        assert calls, "the header action did not call refresh_async"


# --------------------------------------------------------------------------
# loading, failure, and retry
# --------------------------------------------------------------------------


class TestLoadingAndFailure:
    def test_a_page_that_has_not_loaded_yet_does_not_show_zeroed_cards(
        self, shell, branch
    ):
        page = DashboardPage(shell, animate=False)
        assert page.load_state == "loading"
        assert page.revenue_value.text() != "₱0.00"
        assert page.target_value.text() != "₱0"
        assert page.status_banner.isHidden() is False
        assert "loading" in page.status_label.text().lower()

    def test_a_successful_refresh_hides_the_loading_banner(self, shell, branch):
        page = DashboardPage(shell, animate=False)
        page.refresh()
        assert page.load_state == "loaded"
        assert page.status_banner.isHidden() is True

    def test_an_async_failure_shows_the_error_and_a_retry_button(
        self, shell, branch, monkeypatch
    ):
        page = DashboardPage(shell, animate=False)
        page._load_gen = 4
        page._on_async_failed(4, RuntimeError("database is down"))
        QApplication.processEvents()

        assert page.load_state == "failed"
        assert page.status_banner.isHidden() is False
        assert "could not load" in page.status_label.text().lower()
        assert page.retry_button.isHidden() is False

        calls = []
        monkeypatch.setattr(
            page, "refresh_async", lambda *args, **kwargs: calls.append(kwargs)
        )
        page.retry_button.click()
        assert calls, "Try again did not reload the dashboard"

    def test_an_async_refresh_reports_loading_until_the_figures_land(
        self, shell, branch, monkeypatch
    ):
        import app.staff.pages.dashboard as module
        from app.staff.workers import loader_pool

        started = threading.Event()
        release = threading.Event()
        real = module.dashboard_service.collect

        def slow_collect(*args, **kwargs):
            started.set()
            release.wait(5)
            return real(*args, **kwargs)

        monkeypatch.setattr(module.dashboard_service, "collect", slow_collect)
        page = DashboardPage(shell, animate=False)
        try:
            page.refresh_async()
            assert started.wait(2)
            assert page.load_state == "loading"
            assert page.status_banner.isHidden() is False
        finally:
            release.set()
            loader_pool().waitForDone(5000)
        QTest.qWait(50)
        assert page.load_state == "loaded"
        assert page.status_banner.isHidden() is True


class TestRecentLimit:
    def test_the_dashboard_reads_fifty_recent_transactions(
        self, shell, branch, monkeypatch
    ):
        seen = []
        real = dashboard_service.collect

        def spy(*args, **kwargs):
            seen.append(kwargs.get("recent"))
            return real(*args, **kwargs)

        monkeypatch.setattr(dashboard_service, "collect", spy)
        page = DashboardPage(shell, animate=False)
        page.refresh()
        assert seen == [50]


# --------------------------------------------------------------------------
# the misleading pointing-hand cursor
# --------------------------------------------------------------------------


class TestChartCursors:
    def test_hovering_the_heatmap_keeps_the_arrow_cursor(self, qapp):
        today = date.today()
        widget = ActivityHeatmap()
        widget.set_counts({today: 3}, today)
        widget.progress = 1.0
        widget.resize(420, 180)
        widget.show()
        QApplication.processEvents()

        _mouse_move(widget, 40, 20)
        assert widget.cursor().shape() == Qt.CursorShape.ArrowCursor

    def test_hovering_the_sales_chart_keeps_the_arrow_cursor(self, qapp):
        months = [
            MonthSales(
                month=date(2026, number, 1),
                label=date(2026, number, 1).strftime("%b"),
                total=Decimal("400000"),
            )
            for number in (1, 2, 3, 4, 5, 6)
        ]
        widget = SalesTargetChart()
        widget.set_months(months, Decimal("6000000"), Decimal("2400000"))
        widget.progress = 1.0
        widget.resize(560, 220)
        widget.show()
        QApplication.processEvents()

        _mouse_move(widget, 50, 100)
        assert widget.cursor().shape() == Qt.CursorShape.ArrowCursor
