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
#: NewRental's pool ran on Qt's default (6 threads) before the pools were
#: shared; 6 keeps that ceiling now that every media worker is in one pool.
_MAX_MEDIA_THREADS = 6

# The two module globals below are load-bearing.
#
# The pools have no parent, so they are only ever destroyed when the
# interpreter dies -- and ~QThreadPool waits for every thread that is still
# running. If the last reference to a pool were dropped earlier (globals
# cleared, module reloaded, references reassigned), that wait would run at
# interpreter teardown and reintroduce the original hang. Never clear or
# reassign these while a worker may still be running; `shutdown_pools` is
# the only sanctioned stop, and it stays bounded.
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


def emit_safely(signal, *args) -> None:
    """Emit a signal whose owner (a page's QObject) may already be gone.

    Workers started on the shared pools can outlive the page that enqueued
    them, so they may emit into a deleted QObject, which raises
    ``RuntimeError: Signal source has been deleted``. That is harmless -- the
    page is gone and nobody is listening -- so it is dropped here and only
    here. Anything else still propagates.
    """
    try:
        signal.emit(*args)
    except RuntimeError:
        pass

