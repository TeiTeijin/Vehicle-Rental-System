"""Small SQLAlchemy instrumentation helpers for staff performance work.

`count_queries` is for tests: it records every statement executed on an engine
and detaches cleanly. `log_slow_queries` is for development: it warns about
statements slower than a caller-supplied threshold. Neither changes query
results or connection behaviour.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

from sqlalchemy import Engine, event

_SLOW_START = "_staff_slow_query_started"
_DEFAULT_LOGGER = "app.staff.slow_query"


@dataclass
class QueryCounter:
    statements: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.statements)


@contextmanager
def count_queries(engine: Engine) -> Iterator[QueryCounter]:
    """Record SQL statements executed on `engine` while the context is open."""
    counter = QueryCounter()

    def before_cursor_execute(
        _conn, _cursor, statement, _params, _context, _many
    ) -> None:
        counter.statements.append(statement)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)


@contextmanager
def log_slow_queries(
    engine: Engine,
    *,
    threshold_ms: float,
    logger: logging.Logger | None = None,
) -> Iterator[None]:
    """Log statements slower than `threshold_ms` while the context is open."""
    log = logger or logging.getLogger(_DEFAULT_LOGGER)

    def before_cursor_execute(
        conn, _cursor, _statement, _params, _context, _many
    ) -> None:
        conn.info[_SLOW_START] = time.perf_counter()

    def after_cursor_execute(
        conn, _cursor, statement, _params, _context, _many
    ) -> None:
        started = conn.info.pop(_SLOW_START, None)
        if started is None:
            return
        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms >= threshold_ms:
            log.warning(
                "slow query %.1f ms (threshold %.1f ms): %s",
                elapsed_ms,
                threshold_ms,
                " ".join(statement.split())[:500],
            )

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    event.listen(engine, "after_cursor_execute", after_cursor_execute)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
        event.remove(engine, "after_cursor_execute", after_cursor_execute)


def slow_query_threshold_ms() -> int | None:
    """Read `STAFF_SLOW_QUERY_MS`; return None when logging is disabled."""
    raw = os.environ.get("STAFF_SLOW_QUERY_MS")
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None
