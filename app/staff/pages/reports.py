"""Period reports: one question per report, printable or exportable.

The left list picks the report; the right side shows its filters, summary
row, chart (when it helps), and table. All figures come from
`report_service` (one function per metric, shared with the Dashboard), so
the numbers here and there cannot disagree. Read-only: nothing here writes.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt

from app.services import export_service, report_service
from app.staff.feedback import describe
from app.staff.metrics import CARD_GUTTER, PAGE_PANEL_PAD, SIDEBAR_GAP, TOOL_ROW_H
from app.staff.pages.base import StaffPage
from app.staff.section_row import ActiveRow
from app.staff.ui_kit import WarmPanel
from app.staff.widgets import toast

PRESETS: tuple[str, ...] = (
    "Custom",
    "Today",
    "This week",
    "Last 7 days",
    "Last 30 days",
    "This month",
    "Last month",
    "This year",
)

DEFAULT_PRESET = "Last 30 days"

CHANNEL_FILTERS: tuple[tuple[str, str | None], ...] = (
    ("All channels", None),
    ("Walk-in", "walk_in"),
    ("Online", "online"),
)

GRANULARITIES: tuple[str, ...] = ("Day", "Week", "Month")


def _preset_range(preset: str, today: date) -> tuple[date, date] | None:
    if preset == "Today":
        return today, today
    if preset == "This week":
        return today - timedelta(days=today.weekday()), today
    if preset == "Last 7 days":
        return today - timedelta(days=6), today
    if preset == "Last 30 days":
        return today - timedelta(days=29), today
    if preset == "This month":
        return today.replace(day=1), today
    if preset == "Last month":
        first = today.replace(day=1)
        end = first - timedelta(days=1)
        return end.replace(day=1), end
    if preset == "This year":
        return today.replace(month=1, day=1), today
    return None


class ReportsPage(StaffPage):
    HEADER = False

    #: Below this page width the single-row toolbar's fixed-width controls
    #: would overlap, so the note and exports move to a second row.
    TOOLS_WRAP_BELOW = 1120

    def __init__(self, shell) -> None:
        super().__init__(
            shell,
            "Reports",
            "One question per report, over any period.",
        )
        from PySide6.QtWidgets import (
            QHBoxLayout,
            QSizePolicy,
            QStackedWidget,
            QVBoxLayout,
        )

        today = date.today()
        start, end = _preset_range(DEFAULT_PRESET, today)
        self._from_date: date = start
        self._to_date: date = end
        self._report: str = "revenue"

        # -- two warm cards on the cream page: tools, then the workspace ----
        self.layout().setContentsMargins(SIDEBAR_GAP, 0, SIDEBAR_GAP, 0)
        self.body.setSpacing(CARD_GUTTER)

        tools = WarmPanel(self, padding=PAGE_PANEL_PAD)
        tools.body.setSpacing(0)
        self._tools_row = self._tool_row()
        tools.body.addLayout(self._tools_row)
        self.body.addWidget(tools)
        self._tools_panel = tools

        data = WarmPanel(self, padding=PAGE_PANEL_PAD)
        data.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        data.body.setSpacing(0)
        data_layout = QHBoxLayout()
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setSpacing(20)
        self._list_host = QVBoxLayout()
        self._list_host.setContentsMargins(0, 0, 0, 0)
        self._list_host.setSpacing(2)
        self._rows: dict[str, ActiveRow] = {}
        for key, label in (
            ("revenue", "Revenue"),
            ("bookings", "Bookings"),
            ("utilization", "Utilization"),
            ("performance", "Performance"),
            ("maintenance", "Shop costs"),
            ("receivables", "Receivables"),
            ("customers", "Customers"),
            ("staff", "Staff takings"),
        ):
            row = ActiveRow(label, parent=self, variant="reportNav")
            row.setFixedHeight(44)
            row.setObjectName("reportRow")
            row.clicked.connect(lambda _c=False, k=key: self._pick_report(k))
            self._rows[key] = row
            self._list_host.addWidget(row)
        self._list_host.addStretch(1)
        list_wrap, _ = self._wrap_layout(self._list_host)
        list_wrap.setFixedWidth(200)
        list_wrap.setObjectName("reportListHost")
        data_layout.addWidget(list_wrap, 0, Qt.AlignmentFlag.AlignTop)
        self._stack = QStackedWidget(data)
        self._stack.setObjectName("reportStack")
        from app.staff.pages.report_panels import (
            BookingsPanel,
            CustomersPanel,
            MaintenanceCostPanel,
            PerformancePanel,
            ReceivablesPanel,
            RevenuePanel,
            StaffPanel,
            UtilizationPanel,
        )

        self._panels = {
            "revenue": RevenuePanel(self.context, self._stack),
            "bookings": BookingsPanel(self.context, self._stack),
            "utilization": UtilizationPanel(self.context, self._stack),
            "performance": PerformancePanel(self.context, self._stack),
            "maintenance": MaintenanceCostPanel(self.context, self._stack),
            "receivables": ReceivablesPanel(self.context, self._stack),
            "customers": CustomersPanel(self.context, self._stack),
            "staff": StaffPanel(self.context, self._stack),
        }
        for panel in self._panels.values():
            self._stack.addWidget(panel)

        # -- shared async machinery (one pool, shimmer-then-rows) ------------
        from app.staff.async_load import AsyncTable
        from app.staff.workers import loader_pool

        self._pool = loader_pool()
        self._async = {
            key: AsyncTable(self._pool, panel.table, self)
            for key, panel in self._panels.items()
        }
        for panel in self._panels.values():
            panel.reload_requested = self._reload
        data_layout.addWidget(self._stack, 1)
        data.body.addLayout(data_layout)
        self.body.addWidget(data, 1)
        self._data_panel = data
        self._rows["revenue"].set_active(True)
        self._reload()

    @staticmethod
    def _wrap_layout(layout):
        from PySide6.QtWidgets import QWidget

        wrap = QWidget()
        wrap.setLayout(layout)
        return wrap, layout

    # -- tools ------------------------------------------------------------

    def _tool_row(self):
        """Preset, dates, export note, and exports; wraps at narrow widths.

        At the 1440px reference everything shares one row. At the app's
        900px minimum the fixed-width controls alone exceed the panel, so
        the note and the two export buttons move to a second row rather than
        being squeezed underneath the combo and date fields.
        """
        from PySide6.QtCore import QDate
        from PySide6.QtWidgets import (
            QComboBox,
            QDateEdit,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QVBoxLayout,
        )

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        second = QHBoxLayout()
        second.setContentsMargins(0, 0, 0, 0)
        second.setSpacing(8)
        outer.addLayout(row)
        outer.addLayout(second)
        self._tools_primary = row
        self._tools_secondary = second
        self._tools_compact = None

        self.preset_combo = QComboBox(self)
        self.preset_combo.setObjectName("reportPreset")
        self.preset_combo.addItems(list(PRESETS))
        self.preset_combo.setCurrentText(DEFAULT_PRESET)
        self.preset_combo.setFixedHeight(TOOL_ROW_H)
        self.preset_combo.setFixedWidth(142)
        self.preset_combo.currentIndexChanged.connect(self._on_preset)
        self.from_label = self._field_label("From")
        self.to_label = self._field_label("To")
        self.from_edit = QDateEdit(self)
        self.from_edit.setObjectName("reportDate")
        self.from_edit.setCalendarPopup(True)
        self.from_edit.setDisplayFormat("dd MMM yyyy")
        self.from_edit.setDate(
            QDate(self._from_date.year, self._from_date.month, self._from_date.day)
        )
        self.from_edit.setProperty("compact", True)
        self.from_edit.setFixedHeight(TOOL_ROW_H)
        self.from_edit.setFixedWidth(140)
        self.from_edit.dateChanged.connect(self._on_dates)
        self.to_edit = QDateEdit(self)
        self.to_edit.setObjectName("reportDate")
        self.to_edit.setCalendarPopup(True)
        self.to_edit.setDisplayFormat("dd MMM yyyy")
        self.to_edit.setDate(
            QDate(self._to_date.year, self._to_date.month, self._to_date.day)
        )
        self.to_edit.setProperty("compact", True)
        self.to_edit.setFixedHeight(TOOL_ROW_H)
        self.to_edit.setFixedWidth(140)
        self.to_edit.dateChanged.connect(self._on_dates)
        self.export_hint = QLabel(
            "Every export carries the period and who ran it.", self
        )
        self.export_hint.setObjectName("pageSubtitle")

        self.pdf_button = QPushButton("Export PDF", self)
        self.pdf_button.setObjectName("reportExportPdf")
        self.pdf_button.setFixedHeight(TOOL_ROW_H)
        self.pdf_button.setMinimumWidth(112)
        self.pdf_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pdf_button.clicked.connect(lambda: self._export("pdf"))

        self.csv_button = QPushButton("Export CSV", self)
        self.csv_button.setObjectName("reportExportCsv")
        self.csv_button.setFixedHeight(TOOL_ROW_H)
        self.csv_button.setMinimumWidth(112)
        self.csv_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.csv_button.clicked.connect(lambda: self._export("csv"))

        self._arrange_tools(self._tools_must_wrap())
        return outer

    def _tools_must_wrap(self) -> bool:
        return self.width() < self.TOOLS_WRAP_BELOW

    def _arrange_tools(self, wrap: bool) -> None:
        """Place the same controls in one row or in filters + actions rows."""
        for layout in (self._tools_primary, self._tools_secondary):
            while layout.count():
                layout.takeAt(0)
        row = self._tools_primary
        row.addWidget(self.preset_combo)
        row.addWidget(self.from_label)
        row.addWidget(self.from_edit)
        row.addWidget(self.to_label)
        row.addWidget(self.to_edit)
        row.addStretch(1)
        if wrap:
            second = self._tools_secondary
            second.addWidget(self.export_hint)
            second.addStretch(1)
            second.addWidget(self.pdf_button)
            second.addWidget(self.csv_button)
        else:
            row.addWidget(self.export_hint)
            row.addSpacing(8)
            row.addWidget(self.pdf_button)
            row.addWidget(self.csv_button)
        self._tools_compact = wrap
        row.invalidate()
        self._tools_secondary.invalidate()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        if getattr(self, "_tools_primary", None) is None:
            return
        wrap = self._tools_must_wrap()
        if wrap != self._tools_compact:
            self._arrange_tools(wrap)

    def _field_label(self, text: str):
        from PySide6.QtWidgets import QLabel

        label = QLabel(text, self)
        label.setObjectName("fieldLabel")
        return label

    # -- report switching -------------------------------------------------

    def _pick_report(self, key: str) -> None:
        self._report = key
        for name, row in self._rows.items():
            row.set_active(name == key)
        self._stack.setCurrentWidget(self._panels[key])
        self._reload()

    def _current_panel(self):
        return self._panels[self._report]

    # -- loading ----------------------------------------------------------

    def _period_label(self) -> str:
        return f"{self._from_date:%d %b %Y} → {self._to_date:%d %b %Y}"

    def _reload(self) -> None:
        """Shimmer now, rows land on the pool signal. Returns instantly so
        tab switches and preset changes never stall on the queries."""
        panel = self._current_panel()
        if self._to_date < self._from_date:
            panel.show_error("The start date must not be after the end date.")
            return
        panel.set_range(self._from_date, self._to_date)
        panel.reload_async(self._async[self._report])

    def _export(self, kind: str) -> bool:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        panel = self._current_panel()
        title, summary, headers, rows = panel.export_data(self._from_date, self._to_date)
        if title is None:
            return False
        try:
            generated = (
                f"Generated by {self.context.display_name} on "
                f"{datetime.now():%d %b %Y %H:%M}."
            )
            if kind == "csv":
                path = export_service.write_csv(
                    export_service.export_path(panel.key, "csv"), headers, rows
                )
            else:
                path = export_service.write_pdf(
                    export_service.export_path(panel.key, "pdf"),
                    title=title,
                    period=self._period_label(),
                    generated_by=generated,
                    summary=summary,
                    headers=headers,
                    rows=rows,
                )
        except Exception as exc:  # noqa: BLE001
            message, _field, _kind = describe(exc)
            toast(self, message, "error")
            return False
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        toast(self, f"Saved {path.name}.")
        return True

    def _set_dates(self, start: date, end: date) -> None:
        from PySide6.QtCore import QDate

        self._from_date, self._to_date = start, end
        for edit, day in ((self.from_edit, start), (self.to_edit, end)):
            blocked = edit.blockSignals(True)
            try:
                edit.setDate(QDate(day.year, day.month, day.day))
            finally:
                edit.blockSignals(blocked)

    def _on_preset(self) -> None:
        preset = self.preset_combo.currentText()
        if preset == "Custom":
            return
        start_end = _preset_range(preset, date.today())
        if start_end is None:
            return
        self._set_dates(*start_end)
        self._reload()

    def _on_dates(self) -> None:
        self._from_date = self.from_edit.date().toPython()
        self._to_date = self.to_edit.date().toPython()
        blocked = self.preset_combo.blockSignals(True)
        try:
            self.preset_combo.setCurrentText("Custom")
        finally:
            self.preset_combo.blockSignals(blocked)
        self._reload()

    def refresh(self) -> None:
        super().refresh()
        self._set_dates(self._from_date, self._to_date)
        self._reload()

    # -- test seams -------------------------------------------------------

    @property
    def summary(self):
        """The visible report's summary caption (tests read this)."""
        return self._current_panel().summary

    @property
    def table(self):
        """The visible report's table (tests read this)."""
        return self._current_panel().table

    @property
    def _error(self):
        return self._current_panel().error_label


def build_reports_page(shell) -> ReportsPage:
    return ReportsPage(shell)
