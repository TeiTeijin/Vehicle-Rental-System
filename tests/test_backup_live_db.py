"""The live -> local snapshot: a copy must round-trip faithfully, and a
failed run must never leave something that looks like a backup."""

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.database import Base
from app.models import Users, Vehicle, Vehicle_Category, Vehicle_Media
from scripts.backup_live_db import copy_database


def _seed_source(path) -> None:
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Users(
                full_name="Snapshot Tester",
                email="snapshot@example.com",
                password_hash="hash",
                address="1 Test St",
                license_number="LC0000001",
                license_expiry=date(2030, 1, 1),
                role="customer",
                is_active=True,
                must_change_password=False,
                created_at=datetime(2026, 1, 2, 9, 0),
            )
        )
        session.add(Vehicle_Category(category_name="Car", base_rate_multiplier="1.00"))
        session.commit()
    engine.dispose()


def test_a_snapshot_carries_every_table_and_the_rows_in_them(tmp_path):
    source = tmp_path / "source.db"
    _seed_source(source)

    out = tmp_path / "copy.db"
    counts = copy_database(f"sqlite:///{source}", out)

    assert counts["USERS"] == 1
    assert counts["VEHICLE_CATEGORY"] == 1
    assert counts["BOOKING"] == 0

    target = create_engine(f"sqlite:///{out}")
    with Session(target) as session:
        user = session.execute(select(Users)).scalar_one()
        assert user.email == "snapshot@example.com"
        assert user.license_expiry == date(2030, 1, 1)
    target.dispose()


def test_a_snapshot_strips_signed_media_urls(tmp_path):
    """The signed URLs carry the CarImages API key. A copied database that
    keeps them turns every backup into a plaintext credential file."""
    source = tmp_path / "source.db"
    engine = create_engine(f"sqlite:///{source}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        category = Vehicle_Category(
            category_name="Car", base_rate_multiplier="1.00"
        )
        session.add(category)
        session.flush()
        vehicle = Vehicle(
            category_id=category.category_id,
            make="BMW",
            model="3 Series",
            year=2022,
            plate_number="SNP-001",
            daily_rate="2500.00",
            mileage=1000,
            status="available",
            created_at=datetime(2026, 1, 2, 9, 0),
        )
        session.add(vehicle)
        session.flush()
        session.add(
            Vehicle_Media(
                vehicle_id=vehicle.vehicle_id,
                source="CarImages",
                view_angle="front34",
                image_url=(
                    "https://cdn.example/image.webp"
                    "?sig=abc&api_key=ci_live_secret"
                ),
                model_3d_url=(
                    "https://cdn.example/model.glb"
                    "?sig=def&api_key=ci_live_secret"
                ),
                is_watermarked=True,
                cached_at=datetime(2026, 1, 2, 10, 0),
            )
        )
        session.commit()
    engine.dispose()

    out = tmp_path / "copy.db"
    counts = copy_database(f"sqlite:///{source}", out)

    # Row counts survive so restore tests still see the same shape.
    assert counts["VEHICLE_MEDIA"] == 1

    target = create_engine(f"sqlite:///{out}")
    with Session(target) as session:
        media = session.execute(select(Vehicle_Media)).scalar_one()
        assert "api_key=" not in media.image_url
        assert "api_key=" not in media.model_3d_url
        assert "ci_live_secret" not in media.image_url
        assert "ci_live_secret" not in media.model_3d_url
    target.dispose()

    # The secret must never reach the file at all -- not even in an
    # overwritten page SQLite has only marked free.
    raw = out.read_bytes()
    assert b"api_key=" not in raw
    assert b"ci_live_secret" not in raw


def test_a_snapshot_replaces_the_previous_file(tmp_path):
    source = tmp_path / "source.db"
    _seed_source(source)

    out = tmp_path / "copy.db"
    out.write_bytes(b"stale nonsense from an old run")
    copy_database(f"sqlite:///{source}", out)

    target = create_engine(f"sqlite:///{out}")
    with Session(target) as session:
        assert session.execute(select(Users)).scalar_one().email == (
            "snapshot@example.com"
        )
    target.dispose()


def test_a_failed_snapshot_is_deleted_not_left_behind(tmp_path, monkeypatch):
    """Half a copy that opens in the app is worse than none: a clerk would
    reconcile against a snapshot missing yesterday's bookings."""
    import sys

    from scripts import backup_live_db

    monkeypatch.setattr(backup_live_db, "BASE_DIR", tmp_path)
    out = tmp_path / "backup.db"

    def explode(source_url, out_path):
        # Simulate dying mid-copy: the file exists but is incomplete.
        out_path.write_bytes(b"half a copy")
        raise RuntimeError("connection lost")

    monkeypatch.setattr(backup_live_db, "copy_database", explode)
    monkeypatch.setattr(sys, "argv", ["backup_live_db"])

    with pytest.raises(RuntimeError):
        backup_live_db.main()
    assert not out.exists()
