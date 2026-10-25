"""Tests for the staff query-count and slow-query helpers.

These are development/test utilities, not production behaviour. They must
attach and detach SQLAlchemy listeners cleanly so one test cannot leak a
listener into the next.
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine, text

from app.staff.query_metrics import (
    count_queries,
    log_slow_queries,
    slow_query_threshold_ms,
)


def _select_one(engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


class TestCountQueries:
    def test_counts_statements_inside_the_context(self):
        engine = create_engine("sqlite://", future=True)
        with count_queries(engine) as counter:
            _select_one(engine)
            assert counter.count == 1
            assert "SELECT 1" in counter.statements[0]
        engine.dispose()

    def test_detaches_after_the_context(self):
        engine = create_engine("sqlite://", future=True)
        with count_queries(engine) as counter:
            _select_one(engine)
        before = counter.count
        _select_one(engine)
        assert counter.count == before
        engine.dispose()


class TestSlowQueryLogging:
    def test_logs_a_statement_over_the_threshold(self, caplog):
        engine = create_engine("sqlite://", future=True)
        logger = logging.getLogger("test.staff.slow")
        with caplog.at_level(logging.WARNING, logger=logger.name):
            with log_slow_queries(engine, threshold_ms=0, logger=logger):
                _select_one(engine)
        assert any("slow query" in record.message.lower() for record in caplog.records)
        engine.dispose()

    def test_does_not_log_a_statement_under_the_threshold(self, caplog):
        engine = create_engine("sqlite://", future=True)
        logger = logging.getLogger("test.staff.slow.fast")
        with caplog.at_level(logging.WARNING, logger=logger.name):
            with log_slow_queries(engine, threshold_ms=60_000, logger=logger):
                _select_one(engine)
        assert not caplog.records
        engine.dispose()

    def test_detaches_after_the_context(self, caplog):
        engine = create_engine("sqlite://", future=True)
        logger = logging.getLogger("test.staff.slow.detach")
        with log_slow_queries(engine, threshold_ms=0, logger=logger):
            pass
        with caplog.at_level(logging.WARNING, logger=logger.name):
            _select_one(engine)
        assert not caplog.records
        engine.dispose()


class TestEnvironment:
    def test_reads_the_threshold_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("STAFF_SLOW_QUERY_MS", "250")
        assert slow_query_threshold_ms() == 250

    def test_is_disabled_when_the_environment_is_unset(self, monkeypatch):
        monkeypatch.delenv("STAFF_SLOW_QUERY_MS", raising=False)
        assert slow_query_threshold_ms() is None

    def test_rejects_a_non_numeric_threshold(self, monkeypatch):
        monkeypatch.setenv("STAFF_SLOW_QUERY_MS", "not-a-number")
        assert slow_query_threshold_ms() is None
