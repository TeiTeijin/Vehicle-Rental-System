from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
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


class LoadStateTable(QWidget):
    def __init__(
        self,
        columns: Sequence[Column],
        loader: Callable[[], Iterable[Sequence[object]]],
        *,
        empty_message: str = "Nothing here yet.",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self._loader = loader
        self._empty_message = empty_message
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
        error_column.addWidget(self._error)
        error_column.addWidget(self._error_detail)
        error_column.addLayout(error_row)
        error_column.addStretch(1)

        self._error_panel = QWidget(self)
        self._error_panel.setLayout(error_column)

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self._table)       # 0 LOADED
        self._stack.addWidget(self._placeholder)  # 1 EMPTY
        self._stack.addWidget(self._error_panel)  # 2 FAILED

        self.load()

    # -- construction -----------------------------------------------------

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(0, len(self._columns), self)
        table.setHorizontalHeaderLabels([c.title for c in self._columns])
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setWordWrap(False)

        header = table.horizontalHeader()
        header.setHighlightSections(False)
        for index, column in enumerate(self._columns):
            if column.stretch:
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(
                    index, QHeaderView.ResizeMode.ResizeToContents
                )
        return table

    # -- loading ----------------------------------------------------------

    def load(self) -> LoadState:
        self.state = LoadState.LOADING
        self._sync_stack()
        try:
            rows = list(self._loader())
        except Exception as error:  # noqa: BLE001 - this is the whole point
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

        self.last_error = None
        self._populate(rows)
        self.state = LoadState.LOADED if rows else LoadState.EMPTY
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
                for c, value in enumerate(row[: len(self._columns)]):
                    self._table.setItem(r, c, self._make_item(r, c, value))
        finally:
            self._table.setUpdatesEnabled(True)

    def _make_item(self, row: int, column: int, value) -> QTableWidgetItem:
        column_spec = self._columns[column]
        if column_spec.title.lower() in ("status", "state") and isinstance(value, str):
            pill = StatusPill(value.replace("_", " ").title(), value)
            self._table.setCellWidget(row, column, pill)
            return QTableWidgetItem("")
        return QTableWidgetItem("" if value is None else str(value))

    def _sync_stack(self) -> None:
        index = {
            LoadState.LOADED: 0,
            LoadState.EMPTY: 1,
            LoadState.FAILED: 2,
            LoadState.LOADING: 0,
        }[self.state]
        self._stack.setCurrentIndex(index)
        self._placeholder.setVisible(self.state is LoadState.EMPTY)
        self._error_panel.setVisible(self.state is LoadState.FAILED)
        if self.state is LoadState.FAILED:
            self._retry.setFocus()

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
