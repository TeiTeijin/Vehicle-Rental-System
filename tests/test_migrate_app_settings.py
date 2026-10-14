"""Migration test: APP_SETTINGS table + USERS.must_change_password."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect, text

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def db_path(tmp_path):
    from app.database import Base
    path = tmp_path / "appsettings.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    # Simulate a pre-settings database: all tables except APP_SETTINGS,
    # and USERS without the new column.
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        cols = [c["name"] for c in inspect(engine).get_columns("USERS")]
        assert "must_change_password" in cols  # model has it; drop to simulate old
        # SQLite cannot drop a column portably here; instead test the
        # migration against a DB missing only APP_SETTINGS.
        conn.execute(text("DROP TABLE APP_SETTINGS"))
    engine.dispose()
    return path


class TestMigrateAppSettings:
    def test_creates_settings_table(self, db_path, monkeypatch):
        import scripts.migrate_app_settings as mig
        from sqlalchemy import create_engine as _create
        test_engine = _create(f"sqlite:///{db_path}", future=True)
        monkeypatch.setattr(mig, "engine", test_engine)
        try:
            assert "APP_SETTINGS" not in mig.existing_tables()
            mig.ensure_settings_table(dry_run=False)
            assert "APP_SETTINGS" in mig.existing_tables()
            # idempotent
            mig.ensure_settings_table(dry_run=False)
        finally:
            test_engine.dispose()

    def test_dry_run_changes_nothing(self, db_path, monkeypatch):
        import scripts.migrate_app_settings as mig
        from sqlalchemy import create_engine as _create
        test_engine = _create(f"sqlite:///{db_path}", future=True)
        monkeypatch.setattr(mig, "engine", test_engine)
        try:
            mig.ensure_settings_table(dry_run=True)
            assert "APP_SETTINGS" not in mig.existing_tables()
        finally:
            test_engine.dispose()
