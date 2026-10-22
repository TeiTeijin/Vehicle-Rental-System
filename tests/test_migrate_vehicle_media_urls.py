"""Tests for the vehicle-media URL widening migration.

The CarImages signed URL carries an API key plus a signature and routinely
runs past the old ``VARCHAR(255)``. This builds the pre-widening table by
hand, runs the migration against a throwaway SQLite file, and checks both
the column type and that a 400-character URL survives.

Never touches a real database.
"""

from __future__ import annotations

import importlib
import sys

import pytest
from sqlalchemy import create_engine, inspect, text

OLD_VEHICLE_MEDIA = """
CREATE TABLE VEHICLE_MEDIA (
    media_id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL,
    source VARCHAR(255) NOT NULL,
    view_angle VARCHAR(255) NOT NULL,
    image_url VARCHAR(255) NOT NULL,
    model_3d_url VARCHAR(255) NOT NULL,
    is_watermarked BOOLEAN,
    cached_at DATETIME NOT NULL
)
"""

LONG_URL = "https://carimagesapi.com/v1/image?key=abc&sig=" + "f" * 380


def _seed_old(conn) -> None:
    conn.execute(text(OLD_VEHICLE_MEDIA))
    conn.execute(
        text(
            "INSERT INTO VEHICLE_MEDIA "
            "(vehicle_id, source, view_angle, image_url, model_3d_url, "
            " is_watermarked, cached_at) "
            "VALUES (1, 'carimagesapi', 'front34', 'https://old.test/a.jpg', "
            "        '', 1, '2026-01-01 09:00:00')"
        )
    )


@pytest.fixture
def old_db(tmp_path):
    db_file = tmp_path / "legacy_media.db"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", future=True)
    with engine.begin() as conn:
        _seed_old(conn)

    migration = importlib.import_module("scripts.migrate_vehicle_media_urls")

    def run(*args):
        sys.argv = ["migrate_vehicle_media_urls", "--database", str(db_file), *args]
        try:
            migration.main()
        finally:
            sys.argv = ["pytest"]
        return engine

    try:
        yield engine, run
    finally:
        migration.engine = None
        engine.dispose()


def columns(engine, table="VEHICLE_MEDIA") -> dict[str, dict]:
    return {c["name"]: c for c in inspect(engine).get_columns(table)}


def _type_name(column: dict) -> str:
    return type(column["type"]).__name__.upper()


class TestWidensColumns:
    def test_image_url_becomes_text(self, old_db):
        engine, run = old_db
        run()
        assert _type_name(columns(engine)["image_url"]) == "TEXT"

    def test_model_3d_url_becomes_text(self, old_db):
        engine, run = old_db
        run()
        assert _type_name(columns(engine)["model_3d_url"]) == "TEXT"

    def test_existing_rows_survive_and_keep_their_values(self, old_db):
        engine, run = old_db
        run()
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT image_url, model_3d_url FROM VEHICLE_MEDIA")
            ).one()
        assert row.image_url == "https://old.test/a.jpg"
        assert row.model_3d_url == ""

    def test_a_400_character_signed_url_can_be_stored(self, old_db):
        engine, run = old_db
        run()
        assert len(LONG_URL) > 400
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE VEHICLE_MEDIA SET image_url = :url, model_3d_url = :url "
                    "WHERE media_id = 1"
                ),
                {"url": LONG_URL},
            )
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT image_url FROM VEHICLE_MEDIA")).scalar_one()
                == LONG_URL
            )


class TestIdempotent:
    def test_running_twice_is_harmless(self, old_db):
        engine, run = old_db
        run()
        before = columns(engine)
        run()
        after = columns(engine)
        assert {name: _type_name(col) for name, col in after.items()} == {
            name: _type_name(col) for name, col in before.items()
        }

    def test_dry_run_changes_nothing(self, old_db):
        engine, run = old_db
        before = columns(engine)
        run("--dry-run")
        assert _type_name(columns(engine)["image_url"]) == _type_name(before["image_url"])
