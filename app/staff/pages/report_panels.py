"""Right-side report panels for the Reports page.

Each panel owns one question: its filter row, summary caption, chart (when
it helps), and table. Shared contract used by `ReportsPage`:

- `key`: export filename stem.
- `set_range(from_date, to_date)` + `reload_async(controller)`: fetch runs
  on the pool thread (pure data: no widget may be touched there -- widgets
  are snapshotted into plain attributes first), then the render applies the
  summary/chart/rows back on the GUI thread.
- `show_error(message)` / `error_label`: the inverted-range path.
- `summary` / `table`: captions and tables the page (and tests) read.
- `export_data(from_date, to_date)` -> (title, summary, headers, rows).
"""
from __future__ import annotations

import time
from datetime import date

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.services import report_service
from app.staff.cards import Caption
from app.staff.charts import HandDrawnBars, HandDrawnChart
from app.staff.metrics import TOOL_ROW_H
from app.staff.tables import ALIGN_LEFT, ALIGN_RIGHT, Column, LoadStateTable
from app.utils.money import pesos

CHANNEL_FILTERS: tuple[tuple[str, str | None], ...] = (
    ("All channels", None),
    ("Walk-in", "walk_in"),
    ("Online", "online"),
)

GRANULARITIES: tuple[str, ...] = ("Day", "Week", "Month")

#: Reuse an identical utilization report for this long. Exports bypass it.
UTILIZATION_CACHE_SECONDS = 60


class _PanelBase(QWidget):
    key = "report"

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
        self._body.setSpacing(8)

        self.summary = Caption("", parent=self)
        self.error_label = QLabel("", self)
        self.error_label.setObjectName("fieldError")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)

    def _combo(self, items: list[str]) -> QComboBox:
        combo = QComboBox(self)
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
        """Apply summary/chart on the GUI thread; return the table rows."""
        raise NotImplementedError

    def reload_async(self, controller) -> None:
        self.snapshot_filters()
        self._clear_error()
        controller.request(
            self.fetch_report,
            on_done=lambda data: self.table.set_rows(self.render_report(data)),
        )

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
        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(8)
        self.granularity_combo = self._combo(list(GRANULARITIES))
        self.granularity_combo.currentIndexChanged.connect(
            lambda _i: self._on_granularity()
        )
        filters.addWidget(self.granularity_combo)
        self.channel_combo = self._channel_combo(self._channel)
        filters.addWidget(self.channel_combo)
        filters.addStretch(1)
        self._body.addLayout(filters)
        self._body.addWidget(self.summary)
        self._body.addWidget(self.error_label)
        self.chart = HandDrawnChart("Revenue", [], parent=self)
        self.chart.setObjectName("reportChart")
        self._body.addWidget(self.chart)
        self.table = LoadStateTable(
            [
                Column("Period", ALIGN_LEFT),
                Column("Rental", ALIGN_RIGHT),
                Column("Penalties", ALIGN_RIGHT),
                Column("Total", ALIGN_RIGHT),
            ],
            lambda: [],
            empty_message="No revenue in this period.",
            shimmer_rows=8,
        )
        self._body.addWidget(self.table, 1)

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
        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(8)
        self.channel_combo = self._channel_combo(self._channel)
        filters.addWidget(self.channel_combo)
        filters.addStretch(1)
        self._body.addLayout(filters)
        self._body.addWidget(self.summary)
        self._body.addWidget(self.error_label)
        self.table = LoadStateTable(
            [
                Column("Ref", ALIGN_LEFT),
                Column("Customer", ALIGN_LEFT),
                Column("Vehicle", ALIGN_LEFT),
                Column("Dates", ALIGN_LEFT),
                Column("Status"),
                Column("Channel", ALIGN_LEFT),
                Column("Total", ALIGN_RIGHT),
            ],
            lambda: [],
            empty_message="No bookings in this period.",
            shimmer_rows=8,
        )
        self._body.addWidget(self.table, 1)

    def snapshot_filters(self) -> None:
        _label, self._channel = CHANNEL_FILTERS[self.channel_combo.currentIndex()]

    def fetch_report(self):
        with self._context.reading() as session:
            return report_service.bookings_report(
                session, self._from, self._to, channel=self._channel
            )

    def render_report(self, report) -> list:
        by_status = ", ".join(
            f"{status} {count}" for status, count in sorted(report.by_status.items())
        ) or "—"
        self.summary.setText(
            f"Bookings {report.total} ({by_status}) · "
            f"walk-in {report.walk_in}, online {report.online}, "
            f"cancelled {report.cancelled} · "
            f"avg {report.avg_days} day(s), {pesos(report.avg_value)} per booking."
        )
        return [
            (
                row.ref, row.customer, row.vehicle, row.dates,
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
        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(8)
        with context.reading() as session:
            categories = report_service.vehicle_categories(session)
        self.category_combo = self._combo(["All categories", *categories])
        self.category_combo.currentIndexChanged.connect(
            lambda _i: self._reload_requested()
        )
        filters.addWidget(self.category_combo)
        filters.addStretch(1)
        self._body.addLayout(filters)
        self._body.addWidget(self.summary)
        self._body.addWidget(self.error_label)
        self.chart = HandDrawnBars("Utilization %", [], parent=self)
        self.chart.setObjectName("reportChart")
        self._body.addWidget(self.chart)
        self.table = LoadStateTable(
            [
                Column("Vehicle", ALIGN_LEFT),
                Column("Category", ALIGN_LEFT),
                Column("Rented", ALIGN_RIGHT),
                Column("Available", ALIGN_RIGHT),
                Column("Use %", ALIGN_RIGHT),
                Column("Revenue", ALIGN_RIGHT),
                Column("Shop cost", ALIGN_RIGHT),
            ],
            lambda: [],
            empty_message="No vehicles in scope.",
            shimmer_rows=8,
        )
        self._body.addWidget(self.table, 1)

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
                r.label, r.category, str(r.rented_days), str(r.available_days),
                self._use_text(r), f"{r.revenue:,.2f}", f"{r.shop_cost:,.2f}",
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
        self._body.addWidget(self.summary)
        self._body.addWidget(self.error_label)
        self.chart = HandDrawnBars("Revenue by model", [], parent=self)
        self.chart.setObjectName("reportChart")
        self._body.addWidget(self.chart)
        self.table = LoadStateTable(
            [
                Column("Category", ALIGN_LEFT),
                Column("Model", ALIGN_LEFT),
                Column("Revenue", ALIGN_RIGHT),
                Column("Rentals", ALIGN_RIGHT),
            ],
            lambda: [],
            empty_message="No revenue in this period.",
            shimmer_rows=8,
        )
        self._body.addWidget(self.table, 1)

    def fetch_report(self):
        with self._context.reading() as session:
            return report_service.category_report(session, self._from, self._to)

    def render_report(self, rows) -> list:
        top = rows[0] if rows else None
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
        self._body.addWidget(self.summary)
        self._body.addWidget(self.error_label)
        self.chart = HandDrawnBars("Shop spend", [], parent=self)
        self.chart.setObjectName("reportChart")
        self._body.addWidget(self.chart)
        self.table = LoadStateTable(
            [
                Column("Vehicle", ALIGN_LEFT),
                Column("Spend", ALIGN_RIGHT),
                Column("Revenue", ALIGN_RIGHT),
                Column("Cost vs revenue", ALIGN_RIGHT),
            ],
            lambda: [],
            empty_message="No completed spend in this period.",
            shimmer_rows=8,
        )
        self._body.addWidget(self.table, 1)

    def fetch_report(self):
        with self._context.reading() as session:
            return report_service.maintenance_cost_report(
                session, self._from, self._to
            )

    @staticmethod
    def _ratio_text(row) -> str:
        return f"{row.ratio * 100:.0f}%" if row.ratio is not None else "—"

    def render_report(self, report) -> list:
        by_type = ", ".join(
            f"{t} {pesos(a)}" for t, a in sorted(report.by_type.items())
        ) or "—"
        self.summary.setText(f"Spent {pesos(report.spend)} · {by_type}.")
        self.chart.set_pairs([(r.plate, float(r.spend)) for r in report.rows[:12]])
        return [
            (
                r.label, f"{r.spend:,.2f}", f"{r.revenue:,.2f}",
                self._ratio_text(r),
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
        from PySide6.QtGui import QFont

        from app.staff import theme

        self._overdue_only = False
        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(8)
        self.overdue_check = QCheckBox("Overdue only", self)
        overdue_font = QFont("Inter")
        overdue_font.setPixelSize(12)
        overdue_font.setWeight(QFont.Weight.DemiBold)
        self.overdue_check.setFont(overdue_font)
        self.overdue_check.setStyleSheet(
            f"color: {theme.MUTED}; background: transparent;"
        )
        self.overdue_check.toggled.connect(lambda _on: self._reload_requested())
        filters.addWidget(self.overdue_check)
        filters.addStretch(1)
        self._body.addLayout(filters)
        self._body.addWidget(self.summary)
        self._body.addWidget(self.error_label)
        self.table = LoadStateTable(
            [
                Column("Ref", ALIGN_LEFT),
                Column("Customer", ALIGN_LEFT),
                Column("Vehicle", ALIGN_LEFT),
                Column("Due", ALIGN_LEFT),
                Column("Age", ALIGN_RIGHT),
                Column("Owed", ALIGN_RIGHT),
                Column("Penalties", ALIGN_RIGHT),
                Column("Collected", ALIGN_RIGHT),
            ],
            lambda: [],
            empty_message="Nothing outstanding.",
            shimmer_rows=8,
        )
        self._body.addWidget(self.table, 1)

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
            return f"due in {abs(row.age_days)}d"
        if row.age_days == 0:
            return "due today"
        return f"OVERDUE {row.age_days}d"

    def render_report(self, rows) -> list:
        owed = sum((r.owed for r in rows), report_service.ZERO)
        charged = sum((r.penalties_charged for r in rows), report_service.ZERO)
        collected = sum((r.penalties_collected for r in rows), report_service.ZERO)
        self.summary.setText(
            f"Owed {pesos(owed)} across {len(rows)} booking(s) · "
            f"penalties charged {pesos(charged)}, collected {pesos(collected)}."
        )
        return [
            (
                r.ref, r.customer, r.vehicle,
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
        self._body.addWidget(self.summary)
        self._body.addWidget(self.error_label)
        self.table = LoadStateTable(
            [
                Column("Customer", ALIGN_LEFT),
                Column("Bookings", ALIGN_RIGHT),
                Column("Spent", ALIGN_RIGHT),
                Column("Licence", ALIGN_LEFT),
            ],
            lambda: [],
            empty_message="No customer activity in this period.",
            shimmer_rows=8,
        )
        self._body.addWidget(self.table, 1)

    def fetch_report(self):
        with self._context.reading() as session:
            return report_service.customers_report(session, self._from, self._to)

    def render_report(self, report) -> list:
        new_names = ", ".join(report.new_names) or "—"
        issues = ", ".join(report.licence_issues) or "none"
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
        self._body.addWidget(self.summary)
        self._body.addWidget(self.error_label)
        self.table = LoadStateTable(
            [
                Column("Date", ALIGN_LEFT),
                Column("Staff", ALIGN_LEFT),
                Column("Transactions", ALIGN_RIGHT),
                Column("Rental", ALIGN_RIGHT),
                Column("Deposits", ALIGN_RIGHT),
                Column("Total", ALIGN_RIGHT),
            ],
            lambda: [],
            empty_message="No takings in this period.",
            shimmer_rows=8,
        )
        self._body.addWidget(self.table, 1)

    def fetch_report(self):
        with self._context.reading() as session:
            return report_service.staff_report(session, self._from, self._to)

    def render_report(self, report) -> list:
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
