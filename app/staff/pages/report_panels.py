"""Right-side report panels for the Reports page.

Each panel owns one question: its filter row, cream metric tiles, summary
caption, chart (when it helps), and table. Shared contract used by
`ReportsPage`:

- `key`: export filename stem.
- `set_range(from_date, to_date)` + `reload_async(controller)`: fetch runs
  on the pool thread (pure data: no widget may be touched there -- widgets
  are snapshotted into plain attributes first), then the render applies the
  summary/chart/rows back on the GUI thread.
- `show_error(message)` / `error_label`: the inverted-range path.
- `summary` / `table`: captions and tables the page (and tests) read.
- `export_data(from_date, to_date)` -> (title, summary, headers, rows).
  Exports always use the service's complete result, never the visible page.
"""
from __future__ import annotations

import time
from datetime import date

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.services import maintenance_service, report_service
from app.staff.cards import Caption
from app.staff.charts import HandDrawnBars, HandDrawnChart
from app.staff.metrics import TOOL_ROW_H
from app.staff.tables import ALIGN_LEFT, ALIGN_RIGHT, Column, LoadStateTable
from app.staff.ui_kit import MetricGrid
from app.utils.money import pesos

CHANNEL_FILTERS: tuple[tuple[str, str | None], ...] = (
    ("All channels", None),
    ("Walk-in", "walk_in"),
    ("Online", "online"),
)

GRANULARITIES: tuple[str, ...] = ("Day", "Week", "Month")

#: Reuse an identical utilization report for this long. Exports bypass it.
UTILIZATION_CACHE_SECONDS = 60

#: Report tiles keep their 20px value type when there is room, then step
#: down until the figure (cents included) fits the tile at the 900px window.
METRIC_MAX_PX = 20
METRIC_MIN_PX = 12
METRIC_TILE_HEIGHT = 76


class _ReportMetricGrid(MetricGrid):
    """Metric grid whose values shrink to fit instead of silently clipping.

    `MetricGrid` gives every report value ``wordWrap`` and a 20px font, but a
    wrapped label's minimum width is the longest unbreakable run -- a peso
    amount has none -- so at the app's 900px minimum the tile clips the last
    digits. Fit to the width the layout actually gives the label; if even the
    smallest size cannot fit one line, allow two lines and grow the tile just
    enough for them. The 1440px layout still gets the full 20px type.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Texts land one at a time from render_report; fitting on the next
        # event turn measures after the layout has distributed the new widths.
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self._fit_values)

    def set_value(self, label: str, text: str) -> None:
        super().set_value(label, text)
        self._fit_timer.start()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._fit_timer.start()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self._fit_timer.start()

    def _fit_values(self) -> None:
        for row in self.rows:
            for tile in row.tiles.values():
                self._fit_tile(tile)

    def _fit_tile(self, tile) -> None:
        label = tile.value_label
        text = label.text()
        width = label.width()
        if width <= 1:
            return
        base = QFont(label.font())
        chosen = METRIC_MIN_PX
        for size in range(METRIC_MAX_PX, METRIC_MIN_PX - 1, -1):
            candidate = QFont(base)
            candidate.setPixelSize(size)
            if QFontMetrics(candidate).horizontalAdvance(text) <= width - 2:
                chosen = size
                break

        font = QFont(base)
        font.setPixelSize(chosen)
        fits_one_line = (
            QFontMetrics(font).horizontalAdvance(text) <= width - 2
        )
        if fits_one_line:
            if label.wordWrap():
                label.setWordWrap(False)
            if label.minimumHeight():
                label.setMinimumHeight(0)
            if tile.minimumHeight() != METRIC_TILE_HEIGHT:
                tile.setMinimumHeight(METRIC_TILE_HEIGHT)
        else:
            if not label.wordWrap():
                label.setWordWrap(True)
            needed = QFontMetrics(font).boundingRect(
                QRect(0, 0, width, 10_000),
                Qt.TextFlag.TextWordWrap,
                text,
            ).height()
            label.setMinimumHeight(needed)
            tile.setMinimumHeight(
                max(METRIC_TILE_HEIGHT, 8 + 16 + 1 + needed + 8)
            )

        if getattr(label, "_report_font_px", None) != chosen:
            label.setStyleSheet(f"font-size: {chosen}px;")
            label._report_font_px = chosen


def _stacked_vehicle(label: str) -> str:
    """Vehicle labels read model first, plate second, like the references."""
    plate, _, rest = (label or "").partition(" ")
    return f"{rest}\n{plate}" if plate and rest else (label or "-")


class _PanelBase(QWidget):
    key = "report"

    #: The reference shows ten rows, then a footer that reveals the rest.
    PAGE_SIZE = 10

    def __init__(self, context, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        # Transparent host: the white data box shows through everywhere
        # except the data tables (see the reportPanelHost rule in staff.qss).
        self.setObjectName("reportPanelHost")
        #: Set by the page: re-runs the visible report (used by filter
        #: controls, which must not load tables directly).
        self.reload_requested = lambda: None
        self._from: date | None = None
        self._to: date | None = None
        self._body = QVBoxLayout(self)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(10)

        self.summary = Caption("", parent=self)
        self.summary.setObjectName("reportSummary")
        self.error_label = QLabel("", self)
        self.error_label.setObjectName("fieldError")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)

        #: Panels without a metric strip (Utilization) leave this as None.
        self.metrics: MetricGrid | None = None
        self.metric_tiles: dict = {}
        self._all_rows: list = []
        self.total_row_count = 0
        self._page_size = self.PAGE_SIZE
        self._count_unit = ""

    def _filter_row(self) -> QHBoxLayout:
        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(8)
        return filters

    def _combo(self, items: list[str]) -> QComboBox:
        combo = QComboBox(self)
        combo.setObjectName("reportFilter")
        combo.addItems(items)
        combo.setFixedHeight(TOOL_ROW_H)
        return combo

    def _channel_combo(self, channel: str | None) -> QComboBox:
        combo = self._combo([label for label, _code in CHANNEL_FILTERS])
        wanted = next(
            i for i, (_label, code) in enumerate(CHANNEL_FILTERS)
            if code == channel
        )
        combo.setCurrentIndex(wanted)
        combo.currentIndexChanged.connect(lambda _i: self._reload_requested())
        return combo

    def _reload_requested(self) -> None:
        self.reload_requested()

    def _add_metrics(self, rows: list[list[tuple[str, str, bool]]]) -> None:
        """Build the cream metric rows; the first tile is the headline."""
        self.metrics = _ReportMetricGrid(self)
        for spec in rows:
            row = self.metrics.row()
            for label, value, highlight in spec:
                row.add(label, value, highlight=highlight)
        self.metric_tiles = self.metrics.tiles
        self._body.addWidget(self.metrics)

    def _warm_table(
        self,
        columns: list[Column],
        *,
        empty_message: str,
    ) -> LoadStateTable:
        return LoadStateTable(
            columns,
            lambda: [],
            empty_message=empty_message,
            shimmer_rows=8,
            variant="warm",
            row_height=56,
        )

    def _mount(
        self,
        table: LoadStateTable,
        *,
        chart: QWidget | None = None,
    ) -> None:
        """Add the shared caption/chart/table/footer rhythm."""
        self.table = table
        self._body.addWidget(self.summary)
        self._body.addWidget(self.error_label)

        if chart is not None:
            card = QFrame(self)
            card.setObjectName("reportChartCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(8, 8, 8, 8)
            card_layout.setSpacing(0)
            chart.setMinimumHeight(240)
            card_layout.addWidget(chart)
            self.chart_card = card
            self._body.addWidget(card)

        # The table hugs exactly the rows on its page. A stretch after it
        # keeps the footer at the card's bottom when the viewport is taller,
        # while a taller page (chart + ten rows) scrolls instead of squeezing
        # the last rows out of sight behind the hidden scrollbar.
        self.table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.table.setFixedHeight(44 + self.PAGE_SIZE * 56)
        self._body.addWidget(self.table)

        footer = QWidget(self)
        footer.setObjectName("reportTableFooter")
        footer_row = QHBoxLayout(footer)
        footer_row.setContentsMargins(4, 8, 4, 0)
        footer_row.setSpacing(12)
        self.footer_count = QLabel("", footer)
        self.footer_count.setObjectName("reportFooterCount")
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
        self._body.addWidget(footer)
        # A taller viewport leaves the card's white space below the footer
        # rather than stretching the table or pushing its count off-screen.
        self._body.addStretch(1)

    # -- paging ----------------------------------------------------------

    def _count_caption(self, showing: int) -> str:
        total = self.total_row_count
        if total == 0:
            return "Showing 0"
        if total > self.PAGE_SIZE:
            return f"1–{showing} of {total}{self._count_unit}"
        return f"Showing {total}"

    def _render_page(self) -> None:
        shown = self._all_rows[: self._page_size]
        self.table.set_rows(shown)
        # Reserve the full height of the rows on this page. The table's
        # scrollbar is deliberately hidden, so letting the layout squeeze it
        # would silently clip the bottom rows instead of scrolling the page.
        self.table.setFixedHeight(44 + max(1, len(shown)) * 56)
        self.footer_count.setText(self._count_caption(len(shown)))
        self.load_more_button.setVisible(len(shown) < self.total_row_count)

    def _show_report(self, data) -> None:
        """Apply the fetched report, then display its first page."""
        self._all_rows = list(self.render_report(data))
        self.total_row_count = len(self._all_rows)
        self._page_size = self.PAGE_SIZE
        self._render_page()

    def _load_more(self) -> None:
        if self._page_size >= self.total_row_count:
            return
        self._page_size = min(
            self.total_row_count, self._page_size + self.PAGE_SIZE
        )
        self._render_page()

    # -- async contract -------------------------------------------------

    def set_range(self, from_date: date, to_date: date) -> None:
        self._from, self._to = from_date, to_date

    def snapshot_filters(self) -> None:
        """Read widgets into plain attributes (GUI thread). Most panels
        snapshot incrementally in their control handlers; override only
        when a control has no handler of its own."""

    def fetch_report(self):
        """Pure data for the range. Runs on the pool thread: touch no
        widget here, only plain attributes snapshotted beforehand."""
        raise NotImplementedError

    def render_report(self, data) -> list:
        """Apply summary/chart on the GUI thread; return every table row."""
        raise NotImplementedError

    def reload_async(self, controller) -> None:
        self.snapshot_filters()
        self._clear_error()
        self._page_size = self.PAGE_SIZE
        self._all_rows = []
        self.total_row_count = 0
        self.footer_count.clear()
        self.load_more_button.hide()
        controller.request(self.fetch_report, on_done=self._show_report)

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(True)

    def _clear_error(self) -> None:
        self.error_label.setVisible(False)


class RevenuePanel(_PanelBase):
    """Money received, split rental vs penalties, by period and channel."""

    key = "revenue"

    def __init__(self, context, parent=None) -> None:
        super().__init__(context, parent)
        self._granularity = "day"
        self._channel: str | None = None
        filters = self._filter_row()
        self.granularity_combo = self._combo(list(GRANULARITIES))
        self.granularity_combo.setFixedWidth(100)
        self.granularity_combo.currentIndexChanged.connect(
            lambda _i: self._on_granularity()
        )
        filters.addWidget(self.granularity_combo)
        self.channel_combo = self._channel_combo(self._channel)
        self.channel_combo.setFixedWidth(150)
        filters.addWidget(self.channel_combo)
        filters.addStretch(1)
        self._body.addLayout(filters)
        self._add_metrics([
            [
                ("Revenue", "—", True),
                ("Rental", "—", False),
                ("Penalties", "—", False),
            ]
        ])
        self.chart = HandDrawnChart("Revenue", [], parent=self)
        self.chart.setObjectName("reportChart")
        self._mount(
            self._warm_table(
                [
                    Column("Period", ALIGN_LEFT, kind="strong"),
                    Column("Rental", ALIGN_RIGHT, kind="money"),
                    Column("Penalties", ALIGN_RIGHT, kind="money"),
                    Column("Total", ALIGN_RIGHT, kind="money"),
                ],
                empty_message="No revenue in this period.",
            ),
            chart=self.chart,
        )

    def _on_granularity(self) -> None:
        self._granularity = self.granularity_combo.currentText().lower()
        self._reload_requested()

    def snapshot_filters(self) -> None:
        self._granularity = self.granularity_combo.currentText().lower()
        _label, self._channel = CHANNEL_FILTERS[self.channel_combo.currentIndex()]

    def fetch_report(self):
        with self._context.reading() as session:
            return report_service.revenue_report(
                session, self._from, self._to,
                granularity=self._granularity, channel=self._channel,
            )

    def render_report(self, report) -> list:
        self._count_unit = " days" if report.granularity == "day" else ""
        self.metrics.set_value("Revenue", pesos(report.total))
        self.metrics.set_value("Rental", pesos(report.rental))
        self.metrics.set_value("Penalties", pesos(report.penalties))
        self.summary.setText(
            f"Revenue {pesos(report.total)} = rental {pesos(report.rental)} + "
            f"penalties {pesos(report.penalties)}."
        )
        self.chart.set_values(
            [float(b.total) for b in report.buckets],
            [b.label for b in report.buckets],
        )
        return [
            (b.label, f"{b.rental:,.2f}", f"{b.penalties:,.2f}", f"{b.total:,.2f}")
            for b in report.buckets
        ]

    def export_data(self, from_date: date, to_date: date):
        with self._context.reading() as session:
            report = report_service.revenue_report(
                session, from_date, to_date,
                granularity=self._granularity, channel=self._channel,
            )
        summary = [
            f"Revenue {pesos(report.total)} = rental {pesos(report.rental)} "
            f"+ penalties {pesos(report.penalties)}.",
            "By method: "
            + (
                ", ".join(
                    f"{method} {pesos(part.total)} "
                    f"(rental {pesos(part.rental)}, penalties {pesos(part.penalties)})"
                    for method, part in sorted(report.methods.items())
                )
                or "—"
            ),
            "By channel: "
            + (
                ", ".join(
                    f"{channel} {pesos(part.total)}"
                    for channel, part in sorted(report.channels.items())
                )
                or "—"
            ),
        ]
        return (
            "Revenue report",
            summary,
            ["Period", "Rental", "Penalties", "Total"],
            [
                (b.label, f"{b.rental:,.2f}", f"{b.penalties:,.2f}", f"{b.total:,.2f}")
                for b in report.buckets
            ],
        )


class BookingsPanel(_PanelBase):
    """What was booked: counts, averages, and the rows behind them."""

    key = "bookings"

    def __init__(self, context, parent=None) -> None:
        super().__init__(context, parent)
        self._channel: str | None = None
        filters = self._filter_row()
        self.channel_combo = self._channel_combo(self._channel)
        self.channel_combo.setFixedWidth(150)
        filters.addWidget(self.channel_combo)
        filters.addStretch(1)
        self._body.addLayout(filters)
        self._add_metrics([
            [
                ("Bookings", "—", True),
                ("Completed", "—", False),
                ("Ongoing", "—", False),
                ("Confirmed", "—", False),
            ],
            [
                ("Pending", "—", False),
                ("Avg length", "—", False),
                ("Per booking", "—", False),
            ],
        ])
        self._mount(
            self._warm_table(
                [
                    Column("Ref", ALIGN_LEFT, kind="muted"),
                    Column("Customer", ALIGN_LEFT, kind="strong"),
                    Column("Vehicle", ALIGN_LEFT, kind="strong", wrap=True),
                    Column("Dates", ALIGN_LEFT),
                    Column("Status", ALIGN_LEFT, kind="report_status"),
                    Column("Channel", ALIGN_LEFT, kind="muted"),
                    Column("Total", ALIGN_RIGHT, kind="money"),
                ],
                empty_message="No bookings in this period.",
            )
        )

    def snapshot_filters(self) -> None:
        _label, self._channel = CHANNEL_FILTERS[self.channel_combo.currentIndex()]

    def fetch_report(self):
        with self._context.reading() as session:
            return report_service.bookings_report(
                session, self._from, self._to, channel=self._channel
            )

    def render_report(self, report) -> list:
        by_status = report.by_status or {}
        self.metrics.set_value("Bookings", str(report.total))
        self.metrics.set_value(
            "Completed", str(by_status.get("completed", 0))
        )
        self.metrics.set_value("Ongoing", str(by_status.get("ongoing", 0)))
        self.metrics.set_value(
            "Confirmed", str(by_status.get("confirmed", 0))
        )
        self.metrics.set_value("Pending", str(by_status.get("pending", 0)))
        self.metrics.set_value("Avg length", f"{report.avg_days:.1f} days")
        self.metrics.set_value("Per booking", pesos(report.avg_value))
        status_text = ", ".join(
            f"{status} {count}" for status, count in sorted(by_status.items())
        ) or "—"
        self.summary.setText(
            f"Bookings {report.total} ({status_text}) · "
            f"walk-in {report.walk_in}, online {report.online}, "
            f"cancelled {report.cancelled} · "
            f"avg {report.avg_days} day(s), {pesos(report.avg_value)} per booking."
        )
        return [
            (
                row.ref, row.customer, _stacked_vehicle(row.vehicle), row.dates,
                row.status, row.channel, f"{row.total:,.2f}",
            )
            for row in report.rows
        ]

    def export_data(self, from_date: date, to_date: date):
        with self._context.reading() as session:
            report = report_service.bookings_report(
                session, from_date, to_date, channel=self._channel
            )
        summary = [
            f"Bookings {report.total} · walk-in {report.walk_in}, "
            f"online {report.online}, cancelled {report.cancelled} · "
            f"avg {report.avg_days} day(s), {pesos(report.avg_value)} per booking."
        ]
        return (
            "Bookings report",
            summary,
            ["Ref", "Customer", "Vehicle", "Dates", "Status", "Channel", "Total"],
            [
                (
                    row.ref, row.customer, row.vehicle, row.dates,
                    row.status, row.channel, f"{row.total:,.2f}",
                )
                for row in report.rows
            ],
        )


class UtilizationPanel(_PanelBase):
    """Rented days ÷ available days per vehicle, most used first."""

    key = "utilization"

    def __init__(self, context, parent=None) -> None:
        super().__init__(context, parent)
        self._category: str | None = None
        self._cache_key = None
        self._cache_rows = None
        self._cache_at = 0.0
        filters = self._filter_row()
        with context.reading() as session:
            categories = report_service.vehicle_categories(session)
        self.category_combo = self._combo(["All categories", *categories])
        self.category_combo.setFixedWidth(170)
        self.category_combo.currentIndexChanged.connect(
            lambda _i: self._reload_requested()
        )
        filters.addWidget(self.category_combo)
        filters.addStretch(1)
        self._body.addLayout(filters)
        self.chart = HandDrawnBars("Utilization %", [], parent=self)
        self.chart.setObjectName("reportChart")
        self._mount(
            self._warm_table(
                [
                    Column("Vehicle", ALIGN_LEFT, kind="strong", wrap=True),
                    Column("Category", ALIGN_LEFT, kind="muted"),
                    Column("Rented", ALIGN_RIGHT),
                    Column("Available", ALIGN_RIGHT),
                    Column("Use %", ALIGN_RIGHT, kind="money"),
                    Column("Revenue", ALIGN_RIGHT, kind="money"),
                    Column("Shop cost", ALIGN_RIGHT, kind="money"),
                ],
                empty_message="No vehicles in scope.",
            ),
            chart=self.chart,
        )

    def snapshot_filters(self) -> None:
        index = self.category_combo.currentIndex()
        self._category = None if index == 0 else self.category_combo.currentText()

    def fetch_report(self):
        key = (self._from, self._to, self._category)
        now = time.monotonic()
        if (
            key == self._cache_key
            and now - self._cache_at < UTILIZATION_CACHE_SECONDS
        ):
            return self._cache_rows
        with self._context.reading() as session:
            rows = report_service.utilization_report(
                session, self._from, self._to, category=self._category
            )
        self._cache_key = key
        self._cache_rows = rows
        self._cache_at = now
        return rows

    @staticmethod
    def _use_text(row) -> str:
        return f"{row.utilization * 100:.0f}%" if row.utilization is not None else "—"

    def render_report(self, rows) -> list:
        used = sum(1 for r in rows if r.utilization is not None)
        self.summary.setText(
            f"{len(rows)} vehicle(s) · {used} with available days in range."
        )
        self.chart.set_pairs([
            (r.plate, (r.utilization or 0.0) * 100.0) for r in rows[:12]
        ])
        return [
            (
                _stacked_vehicle(r.label), r.category, str(r.rented_days),
                str(r.available_days), self._use_text(r),
                f"{r.revenue:,.2f}", f"{r.shop_cost:,.2f}",
            )
            for r in rows
        ]

    def export_data(self, from_date: date, to_date: date):
        with self._context.reading() as session:
            rows = report_service.utilization_report(
                session, from_date, to_date, category=self._category
            )
        return (
            "Fleet utilization report",
            [f"{len(rows)} vehicle(s), most used first."],
            ["Vehicle", "Category", "Rented days", "Available days",
             "Use %", "Revenue", "Shop cost"],
            [
                (
                    r.label, r.category, str(r.rented_days),
                    str(r.available_days), self._use_text(r),
                    f"{r.revenue:,.2f}", f"{r.shop_cost:,.2f}",
                )
                for r in rows
            ],
        )


class PerformancePanel(_PanelBase):
    """Which categories and models earn the most."""

    key = "performance"

    def __init__(self, context, parent=None) -> None:
        super().__init__(context, parent)
        self._add_metrics([
            [
                ("Top model", "—", True),
                ("Top revenue", "—", False),
                ("Models", "—", False),
            ]
        ])
        self.chart = HandDrawnBars("Revenue by model (top 10)", [], parent=self)
        self.chart.setObjectName("reportChart")
        self._mount(
            self._warm_table(
                [
                    Column("Category", ALIGN_LEFT, kind="muted"),
                    Column("Model", ALIGN_LEFT, kind="strong"),
                    Column("Revenue", ALIGN_RIGHT, kind="money"),
                    Column("Rentals", ALIGN_RIGHT),
                ],
                empty_message="No revenue in this period.",
            ),
            chart=self.chart,
        )

    def fetch_report(self):
        with self._context.reading() as session:
            return report_service.category_report(session, self._from, self._to)

    def render_report(self, rows) -> list:
        top = rows[0] if rows else None
        self.metrics.set_value("Top model", top.model if top else "—")
        self.metrics.set_value(
            "Top revenue", pesos(top.revenue) if top else pesos(0)
        )
        self.metrics.set_value("Models", str(len(rows)))
        self.summary.setText(
            f"{len(rows)} model(s). "
            + (
                f"Top: {top.category} {top.model} at {pesos(top.revenue)}."
                if top
                else "Nothing earned in range."
            )
        )
        self.chart.set_pairs([(r.model, float(r.revenue)) for r in rows[:12]])
        return [
            (r.category, r.model, f"{r.revenue:,.2f}", str(r.rentals))
            for r in rows
        ]

    def export_data(self, from_date: date, to_date: date):
        with self._context.reading() as session:
            rows = report_service.category_report(session, from_date, to_date)
        return (
            "Category and model performance report",
            [f"{len(rows)} model(s), richest first."],
            ["Category", "Model", "Revenue", "Rentals"],
            [
                (r.category, r.model, f"{r.revenue:,.2f}", str(r.rentals))
                for r in rows
            ],
        )


class MaintenanceCostPanel(_PanelBase):
    """Shop spend by vehicle against its revenue."""

    key = "maintenance_cost"

    def __init__(self, context, parent=None) -> None:
        super().__init__(context, parent)
        self._add_metrics([
            [
                ("Spent", "—", True),
                ("Inspection / registration", "—", False),
                ("Routine", "—", False),
            ]
        ])
        self.chart = HandDrawnBars("Shop spend", [], parent=self)
        self.chart.setObjectName("reportChart")
        self._mount(
            self._warm_table(
                [
                    Column("Vehicle", ALIGN_LEFT, kind="strong", wrap=True),
                    Column("Spend", ALIGN_RIGHT, kind="money"),
                    Column("Revenue", ALIGN_RIGHT, kind="money"),
                    Column("Cost vs revenue", ALIGN_RIGHT, kind="ratio"),
                ],
                empty_message="No completed spend in this period.",
            ),
            chart=self.chart,
        )

    def fetch_report(self):
        with self._context.reading() as session:
            return report_service.maintenance_cost_report(
                session, self._from, self._to
            )

    @staticmethod
    def _ratio_text(row) -> str:
        return f"{row.ratio * 100:.0f}%" if row.ratio is not None else "—"

    def render_report(self, report) -> list:
        by_type = report.by_type or {}
        self.metrics.set_value("Spent", pesos(report.spend))
        self.metrics.set_value(
            "Inspection / registration",
            pesos(by_type.get("inspection_registration", report_service.ZERO)),
        )
        self.metrics.set_value(
            "Routine", pesos(by_type.get("routine", report_service.ZERO))
        )
        type_text = ", ".join(
            f"{maintenance_service.type_label(t)} {pesos(a)}"
            for t, a in sorted(by_type.items())
        ) or "—"
        self.summary.setText(f"Spent {pesos(report.spend)} · {type_text}.")
        self.chart.set_pairs([(r.plate, float(r.spend)) for r in report.rows[:12]])
        return [
            (
                _stacked_vehicle(r.label), f"{r.spend:,.2f}",
                f"{r.revenue:,.2f}", self._ratio_text(r),
            )
            for r in report.rows
        ]

    def export_data(self, from_date: date, to_date: date):
        with self._context.reading() as session:
            report = report_service.maintenance_cost_report(
                session, from_date, to_date
            )
        return (
            "Maintenance cost report",
            [f"Spent {pesos(report.spend)} in range."],
            ["Vehicle", "Spend", "Revenue", "Cost vs revenue"],
            [
                (
                    r.label, f"{r.spend:,.2f}", f"{r.revenue:,.2f}",
                    self._ratio_text(r),
                )
                for r in report.rows
            ],
        )


class ReceivablesPanel(_PanelBase):
    """Who still owes, oldest debt first; penalties charged vs collected."""

    key = "receivables"

    def __init__(self, context, parent=None) -> None:
        super().__init__(context, parent)
        from app.staff import theme

        self._overdue_only = False
        filters = self._filter_row()
        self.overdue_check = QCheckBox("Overdue only", self)
        self.overdue_check.setObjectName("reportOverdue")
        overdue_font = QFont("Inter")
        overdue_font.setPixelSize(12)
        overdue_font.setWeight(QFont.Weight.DemiBold)
        self.overdue_check.setFont(overdue_font)
        self.overdue_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.overdue_check.setFixedHeight(TOOL_ROW_H)
        self.overdue_check.toggled.connect(lambda _on: self._reload_requested())
        filters.addWidget(self.overdue_check)
        filters.addStretch(1)
        self._body.addLayout(filters)
        self._add_metrics([
            [
                ("Owed", "—", True),
                ("Bookings", "—", False),
                ("Penalties charged", "—", False),
                ("Collected", "—", False),
            ]
        ])
        self._mount(
            self._warm_table(
                [
                    Column("Ref", ALIGN_LEFT, kind="muted"),
                    Column("Customer", ALIGN_LEFT, kind="strong"),
                    Column("Vehicle", ALIGN_LEFT, kind="strong", wrap=True),
                    Column("Due", ALIGN_LEFT),
                    Column("Age", ALIGN_LEFT, kind="chip"),
                    Column("Owed", ALIGN_RIGHT, kind="money", colour=theme.DANGER),
                    Column("Penalties", ALIGN_RIGHT, kind="money"),
                    Column("Collected", ALIGN_RIGHT, kind="money"),
                ],
                empty_message="Nothing outstanding.",
            )
        )

    def snapshot_filters(self) -> None:
        self._overdue_only = self.overdue_check.isChecked()

    def fetch_report(self):
        with self._context.reading() as session:
            return report_service.receivables_report(
                session, self._to, overdue_only=self._overdue_only
            )

    @staticmethod
    def _age_text(row) -> str:
        if row.age_days < 0:
            return f"Due in {abs(row.age_days)}d"
        if row.age_days == 0:
            return "Due today"
        return f"Overdue {row.age_days}d"

    def render_report(self, rows) -> list:
        owed = sum((r.owed for r in rows), report_service.ZERO)
        charged = sum((r.penalties_charged for r in rows), report_service.ZERO)
        collected = sum((r.penalties_collected for r in rows), report_service.ZERO)
        self.metrics.set_value("Owed", pesos(owed))
        self.metrics.set_value("Bookings", str(len(rows)))
        self.metrics.set_value("Penalties charged", pesos(charged))
        self.metrics.set_value("Collected", pesos(collected))
        self.summary.setText(
            f"Owed {pesos(owed)} across {len(rows)} booking(s) · "
            f"penalties charged {pesos(charged)}, collected {pesos(collected)}."
        )
        return [
            (
                r.ref, r.customer, _stacked_vehicle(r.vehicle),
                r.due.strftime("%d %b %Y") if r.due else "-",
                self._age_text(r), f"{r.owed:,.2f}",
                f"{r.penalties_charged:,.2f}", f"{r.penalties_collected:,.2f}",
            )
            for r in rows
        ]

    def export_data(self, from_date: date, to_date: date):
        with self._context.reading() as session:
            rows = report_service.receivables_report(
                session, to_date, overdue_only=self._overdue_only
            )
        return (
            "Receivables report",
            [f"{len(rows)} open balance(s), oldest first."],
            ["Ref", "Customer", "Vehicle", "Due", "Age", "Owed",
             "Penalties", "Collected"],
            [
                (
                    r.ref, r.customer, r.vehicle,
                    r.due.strftime("%d %b %Y") if r.due else "-",
                    self._age_text(r), f"{r.owed:,.2f}",
                    f"{r.penalties_charged:,.2f}",
                    f"{r.penalties_collected:,.2f}",
                )
                for r in rows
            ],
        )


class CustomersPanel(_PanelBase):
    """New and repeat faces, top spenders, licence horizon."""

    key = "customers"

    def __init__(self, context, parent=None) -> None:
        super().__init__(context, parent)
        self._add_metrics([
            [
                ("Repeat customers", "—", True),
                ("New", "—", False),
                ("Top customer", "—", False),
                ("Licence issues", "—", False),
            ]
        ])
        self._mount(
            self._warm_table(
                [
                    Column("Customer", ALIGN_LEFT, kind="strong"),
                    Column("Bookings", ALIGN_RIGHT),
                    Column("Spent", ALIGN_RIGHT, kind="money"),
                    Column("Licence", ALIGN_LEFT, kind="chip"),
                ],
                empty_message="No customer activity in this period.",
            )
        )

    def fetch_report(self):
        with self._context.reading() as session:
            return report_service.customers_report(session, self._from, self._to)

    def render_report(self, report) -> list:
        new_names = ", ".join(report.new_names) or "—"
        issues = ", ".join(report.licence_issues) or "None"
        self.metrics.set_value("Repeat customers", str(report.repeat_count))
        self.metrics.set_value("New", f"{report.new_count} · {new_names}")
        self.metrics.set_value(
            "Top customer",
            f"{report.top_spender} · {pesos(report.top_spent)}",
        )
        self.metrics.set_value("Licence issues", issues)
        self.summary.setText(
            f"New {report.new_count} ({new_names}) · "
            f"repeat {report.repeat_count} · "
            f"top {report.top_spender} at {pesos(report.top_spent)} · "
            f"licence issues: {issues}."
        )
        return [
            (r.customer, str(r.bookings), f"{r.spent:,.2f}", r.licence)
            for r in report.rows
        ]

    def export_data(self, from_date: date, to_date: date):
        with self._context.reading() as session:
            report = report_service.customers_report(session, from_date, to_date)
        return (
            "Customer report",
            [f"New {report.new_count} · repeat {report.repeat_count}."],
            ["Customer", "Bookings", "Spent", "Licence"],
            [
                (r.customer, str(r.bookings), f"{r.spent:,.2f}", r.licence)
                for r in report.rows
            ],
        )


class StaffPanel(_PanelBase):
    """Money taken per cashier per day: the end-of-day close."""

    key = "staff"

    def __init__(self, context, parent=None) -> None:
        super().__init__(context, parent)
        self._add_metrics([
            [
                ("Taken", "—", True),
                ("Rental", "—", False),
                ("Deposits", "—", False),
                ("Transactions", "—", False),
            ]
        ])
        self._mount(
            self._warm_table(
                [
                    Column("Date", ALIGN_LEFT, kind="strong"),
                    Column("Staff", ALIGN_LEFT, kind="muted"),
                    Column("Transactions", ALIGN_RIGHT),
                    Column("Rental", ALIGN_RIGHT, kind="money"),
                    Column("Deposits", ALIGN_RIGHT, kind="money"),
                    Column("Total", ALIGN_RIGHT, kind="money"),
                ],
                empty_message="No takings in this period.",
            )
        )

    def fetch_report(self):
        with self._context.reading() as session:
            return report_service.staff_report(session, self._from, self._to)

    def render_report(self, report) -> list:
        self.metrics.set_value("Taken", pesos(report.total))
        self.metrics.set_value("Rental", pesos(report.rental))
        self.metrics.set_value("Deposits", pesos(report.deposits))
        self.metrics.set_value("Transactions", str(report.transactions))
        self.summary.setText(
            f"Taken {pesos(report.total)} = rental {pesos(report.rental)} + "
            f"deposits {pesos(report.deposits)} across {report.transactions} "
            "transaction(s)."
        )
        return [
            (
                r.day.strftime("%d %b %Y"), r.staff, str(r.transactions),
                f"{r.rental:,.2f}", f"{r.deposits:,.2f}", f"{r.total:,.2f}",
            )
            for r in report.rows
        ]

    def export_data(self, from_date: date, to_date: date):
        with self._context.reading() as session:
            report = report_service.staff_report(session, from_date, to_date)
        return (
            "Staff takings report",
            [f"Taken {pesos(report.total)} across {report.transactions} transaction(s)."],
            ["Date", "Staff", "Transactions", "Rental", "Deposits", "Total"],
            [
                (
                    r.day.strftime("%d %b %Y"), r.staff, str(r.transactions),
                    f"{r.rental:,.2f}", f"{r.deposits:,.2f}", f"{r.total:,.2f}",
                )
                for r in report.rows
            ],
        )
