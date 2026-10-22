"""Shared worker pools for the staff app.

Every page used to own its ``QThreadPool``. A parented pool is destroyed with
its page -- and its destructor waits on every running worker, so one worker
blocked in a slow network call hung the whole teardown. These pools live for
the app instead (never parented to a widget) and ``shutdown_pools`` gives
process/pytest teardown a bounded stop: drop queued work, then wait at most
``timeout_ms``.

Pools are split by the kind of work they run:

- ``loader_pool``: list/table reloads (short SQLite reads).
- ``media_pool``: media resolvers, photo downloads, and CarImages probes
  (slow, externally flaky, must never hold up a list reload).
"""
from __future__ import annotations

from PySide6.QtCore import QThreadPool

_MAX_LOADER_THREADS = 2
_MAX_MEDIA_THREADS = 2

_loader_pool: QThreadPool | None = None
_media_pool: QThreadPool | None = None


def loader_pool() -> QThreadPool:
    """The app-wide pool for table/list load workers."""
    global _loader_pool
    if _loader_pool is None:
        _loader_pool = QThreadPool()
        _loader_pool.setMaxThreadCount(_MAX_LOADER_THREADS)
    return _loader_pool


def media_pool() -> QThreadPool:
    """The app-wide pool for media workers (resolvers, downloads, probes)."""
    global _media_pool
    if _media_pool is None:
        _media_pool = QThreadPool()
        _media_pool.setMaxThreadCount(_MAX_MEDIA_THREADS)
    return _media_pool


def shutdown_pools(timeout_ms: int = 2000) -> None:
    """Stop both pools without ever blocking forever.

    ``clear`` drops queued-and-unstarted work; ``waitForDone`` waits at most
    ``timeout_ms``. A worker still running past the timeout is abandoned by
    the call (the thread ends on its own) -- the caller never hangs on a
    stuck network request.
    """
    for pool in (_loader_pool, _media_pool):
        if pool is None:
            continue
        pool.clear()
        pool.waitForDone(timeout_ms)
