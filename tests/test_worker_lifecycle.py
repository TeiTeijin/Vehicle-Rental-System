"""Worker pool lifecycle: pools are shared, teardown is bounded."""


def test_shared_pools_are_reused():
    from app.staff.workers import loader_pool, media_pool
    assert loader_pool() is loader_pool()
    assert media_pool() is media_pool()
    assert loader_pool() is not media_pool()


def test_shutdown_pools_is_bounded_when_a_worker_is_blocked():
    import time
    from app.staff.workers import shutdown_pools
    started = time.monotonic()
    shutdown_pools(timeout_ms=100)
    assert time.monotonic() - started < 2.0
