from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.staff import theme
from app.staff.feedback import describe
from app.staff.widgets import blocking_error


class LoadState(Enum):
    LOADING = auto()
    LOADED = auto()
    EMPTY = auto()
    FAILED = auto()


@dataclass(frozen=True)
class Column:
    title: str
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    stretch: bool = False
    #: Warm-table presentation: ``text``, ``status``, ``report_status``,
    #: ``payment``, ``chip``, ``ratio``, ``inspection``, ``damage``,
    #: ``muted``, ``strong``, or ``money``.
    kind: str = "text"
    weight: QFont.Weight | None = None
    colour: str | None = None
    wrap: bool = False


ALIGN_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
ALIGN_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
ALIGN_CENTRE = Qt.AlignmentFlag.AlignCenter


class StatusPill(QLabel):
    def __init__(self, text: str, status: str | None, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName(theme.OBJ_STATUS)
        colour = theme.colour_for(status)
        self.setStyleSheet(
            f"QLabel#statusPill {{ background: {colour.name()}; color: #FFFFFF; }}"
        )
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)


def _status_tone(status: str | None) -> str:
    key = (status or "").strip().lower()
    if key in ("confirmed", "ongoing", "available", "paid"):
        return "ok"
    if key in (
        "pending",
        "reserved",
        "maintenance",
        "in_progress",
        "scheduled",
        "refunded",
    ):
        return "warn"
    if key in ("cancelled", "failed"):
        return "danger"
    return "neutral"


def _payment_tone(text: str) -> str:
    lowered = (text or "").strip().lower()
    if "unpaid" in lowered:
        return "danger"
    if "partial" in lowered:
        return "warn"
    return "neutral"


def _chip_tone(text: str) -> str:
    """Tone for a report chip whose label is already human-readable."""
    key = (text or "").strip().lower()
    if key.startswith("overdue") or key.startswith("expired"):
        return "danger"
    if key.startswith("expires") or key.startswith("due"):
        return "warn"
    if key in ("ok", "valid", "paid", "complete", "completed"):
        return "ok"
    return _status_tone(key)


def _ratio_tone(text: str) -> str:
    try:
        value = float((text or "").replace("%", "").replace(",", ""))
    except ValueError:
        return "neutral"
    if value > 100:
        return "danger"
    if value > 50:
        return "warn"
    return "ok"


class TableChip(QLabel):
    """22px warm-table chip (status or payment state).

    A fixed height is load-bearing: Qt drops the stylesheet's border radius
    when it exceeds half the label height, which left the old 19px chip with
    square corners. The QSS radius is half of this height, so the chip is a
    true pill at every DPI.
    """

    HEIGHT = 22

    def __init__(
        self,
        text: str,
        *,
        tone: str = "neutral",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("tableChip")
        self.setProperty("tone", tone)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )


class ChipCell(QWidget):
    """Auto-width chip inside a full-width table cell."""

    def __init__(
        self,
        chip: QLabel,
        *,
        align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # The blanket cream QWidget rule would paint a cream block behind
        # every chip in a white card. This host is a layout shim only.
        self.setObjectName("tableChipCell")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        if align & Qt.AlignmentFlag.AlignRight:
            layout.addStretch(1)
        layout.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
        if not (align & Qt.AlignmentFlag.AlignRight):
            layout.addStretch(1)
        self._chip = chip

    def text(self) -> str:
        return self._chip.text()


class LoadStateTable(QWidget):
    def __init__(
        self,
        columns: Sequence[Column],
        loader: Callable[[], Iterable[Sequence[object]]],
        *,
        empty_message: str = "Nothing here yet.",
        parent: QWidget | None = None,
        shimmer_rows: int = 0,
        variant: str = "default",
        row_height: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(theme.OBJ_TABLE_HOST)
        self._loader = loader
        self._empty_message = empty_message
        self._variant = variant
        self._row_height = row_height or (56 if variant == "warm" else None)
        self.state = LoadState.LOADING
        self.last_error: BaseException | None = None
        #: Retry presses; surfaced for tests.
        self.retry_count = 0

        self._columns = list(columns)

        self._table = self._build_table()
        self._placeholder = QLabel(empty_message, self)
        self._placeholder.setObjectName(theme.OBJ_EMPTY)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)

        self._error = QLabel("", self)
        self._error.setObjectName(theme.OBJ_ERROR)
        self._error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error.setWordWrap(True)
        # Hug the text instead of spanning the panel end to end: the
        # white error box sizes to its content and stays centred.
        self._error.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred
        )

        self._error_detail = QLabel("", self)
        self._error_detail.setObjectName("errorDetail")
        self._error_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_detail.setWordWrap(True)

        self._retry = QPushButton("Try again", self)
        self._retry.setObjectName("retryButton")
        self._retry.clicked.connect(self.retry)

        error_row = QHBoxLayout()
        error_row.setContentsMargins(0, 0, 0, 0)
        error_row.addStretch(1)
        error_row.addWidget(self._retry)
        error_row.addStretch(1)

        error_column = QVBoxLayout()
        error_column.setContentsMargins(0, 0, 0, 0)
        error_column.setSpacing(8)
        error_column.addStretch(1)
        error_column.addWidget(self._error, 0, Qt.AlignmentFlag.AlignHCenter)
        error_column.addWidget(self._error_detail)
        error_column.addLayout(error_row)
        error_column.addStretch(1)

        self._error_panel = QWidget(self)
        self._error_panel.setObjectName(theme.OBJ_TABLE_HOST)
        self._error_panel.setLayout(error_column)

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self._table)       # 0 LOADED
        self._stack.addWidget(self._placeholder)  # 1 EMPTY
        self._stack.addWidget(self._error_panel)  # 2 FAILED
        #: Shimmer shown while an async reload is in flight (index 3).
        #: Built only when asked: without it LOADING shows the table, exactly
        #: as before, so existing synchronous users are untouched.
        self._shimmer = (
            self._build_shimmer(shimmer_rows) if shimmer_rows > 0 else None
        )
        if self._shimmer is not None:
            self._stack.addWidget(self._shimmer)

        self.load()

    def set_loader(self, loader: Callable[[], Iterable[Sequence[object]]]) -> None:
        self._loader = loader

    # -- construction -----------------------------------------------------

    def _build_table(self) -> QTableWidget:
        warm = self._variant == "warm"
        table = QTableWidget(0, len(self._columns), self)
        table.setHorizontalHeaderLabels([c.title for c in self._columns])
        table.setAlternatingRowColors(not warm)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setWordWrap(warm)
        # No bar widgets at all for a cleaner look (same pattern as the
        # sidebar): wheel / keys / scrollToItem keep working.
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        if warm:
            table.setProperty("variant", "warm")
            if self._row_height:
                table.verticalHeader().setDefaultSectionSize(self._row_height)

        header = table.horizontalHeader()
        header.setHighlightSections(False)
        if warm:
            header.setObjectName("warmHeader")
            header.setFixedHeight(44)
        # Last column absorbs leftover viewport width so no empty gap
        # shows on the right when content is narrower than the panel.
        header.setStretchLastSection(True)
        for index, column in enumerate(self._columns):
            if column.stretch:
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(
                    index, QHeaderView.ResizeMode.ResizeToContents
                )
            item = table.horizontalHeaderItem(index)
            if item is not None:
                item.setTextAlignment(column.align)
        return table

    # -- loading ----------------------------------------------------------

    def load(self) -> LoadState:
        self.state = LoadState.LOADING
        self._sync_stack()
        try:
            rows = list(self._loader())
        except Exception as error:  # noqa: BLE001 - this is the whole point
            return self.set_failed(error)

        self.last_error = None
        self.set_rows(rows)
        return self.state

    def show_loading(self) -> None:
        """Enter LOADING for an async reload; rows land via set_rows."""
        self.state = LoadState.LOADING
        self._sync_stack()

    def set_rows(self, rows) -> LoadState:
        self.last_error = None
        rows = list(rows)
        self._populate(rows)
        self.state = LoadState.LOADED if rows else LoadState.EMPTY
        self._sync_stack()
        return self.state

    def set_failed(self, error: BaseException) -> LoadState:
        self.last_error = error
        self.state = LoadState.FAILED
        message, _field, _kind = describe(error)
        self._error.setText(f"Could not load this list.\n{message}")
        self._error_detail.setText(
            "Nothing has been changed. Retrying is safe."
            if not isinstance(error, KeyboardInterrupt)
            else ""
        )
        self._table.setRowCount(0)
        self._sync_stack()
        return self.state

    def retry(self) -> LoadState:
        self.retry_count += 1
        return self.load()

    def _populate(self, rows: list[Sequence[object]]) -> None:
        self._table.setUpdatesEnabled(False)
        try:
            self._table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                if self._row_height:
                    self._table.setRowHeight(r, self._row_height)
                for c, value in enumerate(row[: len(self._columns)]):
                    self._table.setItem(r, c, self._make_item(r, c, value))
        finally:
            self._table.setUpdatesEnabled(True)

    def _chip_item(
        self,
        row: int,
        column: int,
        column_spec: Column,
        text: str,
        *,
        tone: str,
    ) -> QTableWidgetItem:
        """Chip in a cell widget plus an invisible item for column sizing."""
        chip = TableChip(text, tone=tone)
        self._table.setCellWidget(
            row, column, ChipCell(chip, align=column_spec.align)
        )
        # ResizeToContents measures the item, not the cell widget. Keep the
        # label in the (invisible) item so the chip gets its width.
        placeholder = QTableWidgetItem(f"{text}  ")
        placeholder.setForeground(QBrush(Qt.GlobalColor.transparent))
        placeholder.setTextAlignment(column_spec.align)
        return placeholder

    def _make_item(self, row: int, column: int, value) -> QTableWidgetItem:
        column_spec = self._columns[column]
        warm = self._variant == "warm"
        if not warm and column_spec.title.lower() in ("status", "state") and isinstance(value, str):
            pill = StatusPill(theme.status_label(value), value)
            self._table.setCellWidget(row, column, pill)
            return QTableWidgetItem("")
        if warm and column_spec.kind in (
            "status", "report_status", "payment", "chip", "ratio"
        ):
            raw = "" if value is None else str(value)
            if column_spec.kind in ("status", "report_status"):
                text = theme.status_label(raw)
                if (
                    column_spec.kind == "report_status"
                    and raw.strip().lower() in ("ongoing", "in_progress")
                ):
                    # The report references paint an active rental blue.
                    tone = "info"
                else:
                    tone = _status_tone(raw)
            elif column_spec.kind == "payment":
                text = raw
                tone = _payment_tone(raw)
            elif column_spec.kind == "ratio":
                text = raw
                tone = _ratio_tone(raw)
            else:
                text = raw
                tone = _chip_tone(raw)
            chip = TableChip(text, tone=tone)
            self._table.setCellWidget(
                row, column, ChipCell(chip, align=column_spec.align)
            )
            # ResizeToContents measures the item, not the cell widget. Keep
            # ResizeToContents measures the item, not the cell widget. Keep
            # the label in the (invisible) item so the chip gets its width;
            # the trailing pad pays for the chip's 10px side padding + border.
            placeholder = QTableWidgetItem(f"{text}      ")
            placeholder.setForeground(QBrush(Qt.GlobalColor.transparent))
            placeholder.setTextAlignment(column_spec.align)
            return placeholder
        if warm and column_spec.kind == "kind":
            # Ledger kinds read as outlined pills, not filled status chips:
            # "Rental"/"Deposit" are categories, not states.
            text = "" if value is None else str(value)
            chip = TableChip(text, tone="outline")
            self._table.setCellWidget(
                row, column, ChipCell(chip, align=column_spec.align)
            )
            placeholder = QTableWidgetItem(f"{text}      ")
            placeholder.setForeground(QBrush(Qt.GlobalColor.transparent))
            placeholder.setTextAlignment(column_spec.align)
            return placeholder
        if warm and column_spec.kind == "inspection":
            text = "" if value is None else str(value)
            lowered = text.strip().lower()
            if lowered.startswith("pre"):
                tone = "pre"
            elif lowered.startswith("post"):
                tone = "post"
            else:
                tone = "neutral"
            return self._chip_item(
                row, column, column_spec, text, tone=tone
            )
        if warm and column_spec.kind == "damage":
            text = "" if value is None else str(value)
            clean = text.strip().lower()
            if not clean or clean in ("-", "none", "none recorded"):
                # No damage is ordinary text, not a chip: a neutral sentence
                # reads calmer than a pill and keeps the red chips meaningful.
                item = QTableWidgetItem(text or "None recorded")
                item.setTextAlignment(column_spec.align)
                item.setForeground(QBrush(QColor("#7D7265")))
                return item
            return self._chip_item(
                row, column, column_spec, text, tone="danger"
            )

        item = QTableWidgetItem("" if value is None else str(value))
        item.setTextAlignment(column_spec.align)
        kind = column_spec.kind
        if warm:
            font = item.font()
            if column_spec.weight is not None:
                font.setWeight(column_spec.weight)
            elif kind == "strong":
                font.setWeight(QFont.Weight.DemiBold)
            elif kind == "money":
                font.setWeight(QFont.Weight.DemiBold)
            item.setFont(font)
            colour = column_spec.colour
            if colour is None:
                colour = "#7D7265" if kind == "muted" else "#171717"
            item.setForeground(QBrush(QColor(colour)))
        return item

    def _sync_stack(self) -> None:
        if self.state is LoadState.LOADING and self._shimmer is not None:
            index = 3
        else:
            index = {
                LoadState.LOADED: 0,
                LoadState.EMPTY: 1,
                LoadState.FAILED: 2,
                LoadState.LOADING: 0,
            }[self.state]
        self._stack.setCurrentIndex(index)
        self._set_busy(self.state is LoadState.LOADING)
        self._placeholder.setVisible(self.state is LoadState.EMPTY)
        self._error_panel.setVisible(self.state is LoadState.FAILED)
        if self.state is LoadState.FAILED:
            self._retry.setFocus()

    def _build_shimmer(self, rows: int):
        from PySide6.QtWidgets import QSizePolicy, QVBoxLayout

        from app.staff.metrics import TOOL_ROW_H
        from app.widgets.skeleton import Skeleton

        # Same language as the Bookings/Fleet/Customers skeleton: a header
        # mimic plus full-width row bars at the tools-row rhythm, so every
        # shimmering table in the app reads as one design.
        host = QWidget(self)
        host.setObjectName(theme.OBJ_TABLE_HOST)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._shimmer_bars: list = []
        for _ in range(rows + 1):
            bar = Skeleton(host)
            bar.setFixedHeight(TOOL_ROW_H)
            bar.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            layout.addWidget(bar)
            self._shimmer_bars.append(bar)
        layout.addStretch(1)
        return host

    def _set_busy(self, busy: bool) -> None:
        for bar in getattr(self, "_shimmer_bars", []):
            bar.set_busy(busy)

    # -- access -----------------------------------------------------------

    @property
    def table(self) -> QTableWidget:
        return self._table

    def row_count(self) -> int:
        return self._table.rowCount()

    def cell_text(self, row: int, column: int) -> str:
        widget = self._table.cellWidget(row, column)
        if isinstance(widget, QLabel):
            return widget.text()
        if widget is not None and callable(getattr(widget, "text", None)):
            return widget.text()
        item = self._table.item(row, column)
        return item.text() if item is not None else ""

    def set_placeholder(self, message: str) -> None:
        self._empty_message = message
        self._placeholder.setText(message)

    def reload_or_report(self, parent: QWidget | None = None) -> LoadState:
        state = self.retry()
        if state is LoadState.FAILED and parent is not None:
            blocking_error(parent, self.last_error, title="Still not loading")
        return state

    def paint_status_colours(self) -> None:
        brush = QBrush(QColor(theme.TEXT))
        for row in range(self._table.rowCount()):
            for column in range(self._table.columnCount()):
                item = self._table.item(row, column)
                if item is not None:
                    item.setForeground(brush)
