"""One async-loading pattern for every table that reloads after its page.

A single-thread pool runs the fetch off the GUI thread; the fetch must
open its own session (all loaders do, via `context.reading()`), and only
plain tuples may cross the signal -- never ORM rows, never widgets. Each
request bumps a generation; stale results are dropped, so rapid filter
typing collapses into the latest reload. This is the Bookings machinery,
factored out so Payments, Inspections, Maintenance, and Reports share it.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from app.staff.workers import emit_safely


class TableLoadSignals(QObject):
    loaded = Signal(int, object)
    failed = Signal(int, object)


class TableLoadWorker(QRunnable):
    """Run one fetch off the GUI thread, forwarding plain rows only."""

    def __init__(self, fetch: Callable[[], object], gen: int, signals: TableLoadSignals) -> None:
        super().__init__()
        self._fetch = fetch
        self._gen = gen
        # Bound once here, while the owning page is alive: a deleted owner
        # raises the moment its attributes are touched, and the worker can
        # outlive the page that started it.
        self._loaded = signals.loaded
        self._failed = signals.failed

    def run(self) -> None:
        try:
            result = self._fetch()
        except Exception as exc:  # noqa: BLE001 - forwarded to the GUI slot
            emit_safely(self._failed, self._gen, exc)
        else:
            emit_safely(self._loaded, self._gen, result)


class AsyncTable:
    """Drives one LoadStateTable: shimmer now, rows on the loaded signal.

    `request(fetch, on_done=None)`: shows the shimmer and returns at once.
    On landing, the table is populated (unless `on_done` takes the payload
    itself, e.g. panels that also repaint a summary/chart) and `on_done`
    runs on the GUI thread. Failures surface the table's failed state.
    """

    def __init__(self, pool: QThreadPool, table, parent=None) -> None:
        self._pool = pool
        self._table = table
        self._signals = TableLoadSignals(parent)
        self._signals.loaded.connect(self._on_loaded)
        self._signals.failed.connect(self._on_failed)
        self.load_gen = 0
        self.done_gen = 0
        self._on_done = None

    def request(self, fetch: Callable[[], object], *, on_done=None) -> None:
        self.load_gen += 1
        self._on_done = on_done
        self._table.show_loading()
        self._pool.start(TableLoadWorker(fetch, self.load_gen, self._signals))

    def _on_loaded(self, gen: int, result: object) -> None:
        if gen != self.load_gen:
            return
        if self._on_done is not None:
            self._on_done(result)
        else:
            self._table.set_rows(result)
        self.done_gen = gen

    def _on_failed(self, gen: int, exc: BaseException) -> None:
        if gen != self.load_gen:
            return
        self._table.set_failed(exc)
        self.done_gen = gen
