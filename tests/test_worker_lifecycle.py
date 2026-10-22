"""Worker pool lifecycle: pools are shared, teardown is bounded and safe."""


def test_shared_pools_are_reused():
    from app.staff.workers import loader_pool, media_pool
    assert loader_pool() is loader_pool()
    assert media_pool() is media_pool()
    assert loader_pool() is not media_pool()


def test_shutdown_pools_is_bounded_when_a_worker_is_blocked():
    """shutdown_pools must return quickly even with a worker mid-flight.

    A worker holds a thread past waitForDone's timeout; the call still has
    to return. Restore the pool's thread count afterwards so the (now
    released) worker's slot cannot slow the rest of the suite.
    """
    import threading
    import time

    from PySide6.QtCore import QRunnable

    from app.staff.workers import loader_pool, shutdown_pools

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class _BlockedWorker(QRunnable):
        def run(self) -> None:
            started.set()
            release.wait(timeout=5.0)
            finished.set()

    pool = loader_pool()
    saved_threads = pool.maxThreadCount()
    runnable = _BlockedWorker()  # keep a reference for the whole test
    pool.start(runnable)
    assert started.wait(timeout=5.0), "worker never started"
    try:
        began = time.monotonic()
        shutdown_pools(timeout_ms=100)
        elapsed = time.monotonic() - began
        assert elapsed < 2.0, f"shutdown blocked for {elapsed:.2f}s on a live worker"
    finally:
        release.set()
        assert finished.wait(timeout=5.0), "blocked worker never finished"
        pool.setMaxThreadCount(saved_threads)


def test_emit_safely_passes_live_signals_through(qapp):
    from app.staff.workers import emit_safely

    from PySide6.QtCore import QObject, Signal

    class _Owner(QObject):
        fired = Signal(int)

    owner = _Owner()
    seen: list[int] = []
    owner.fired.connect(seen.append)
    emit_safely(owner.fired, 7)
    assert seen == [7], "a live signal must still be emitted"


def test_emit_safely_survives_a_deleted_signal_owner(qapp):
    """A worker may outlive the page whose signals object it reports to."""
    from PySide6.QtCore import QObject, Signal

    from app.staff.workers import emit_safely

    class _Owner(QObject):
        fired = Signal(int)

    owner = _Owner()
    bound = owner.fired  # captured while the owner is alive
    owner.deleteLater()
    qapp.processEvents()
    emit_safely(bound, 7)  # must not raise
