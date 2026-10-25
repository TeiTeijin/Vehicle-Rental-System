"""The live -> local snapshot: a copy must round-trip faithfully, and a
failed run must never leave something that looks like a backup."""

import os
import sqlite3
import stat
import subprocess
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.database import Base
from app.models import Users, Vehicle, Vehicle_Category, Vehicle_Media
from app.services.backup_service import (
    SnapshotError,
    create_sanitized_sqlite_snapshot,
)
from scripts.backup_live_db import copy_database


def _shared_dir(tmp_path):
    """A folder whose new files inherit a read ACE for the local Users group."""
    folder = tmp_path / "shared"
    folder.mkdir()
    if os.name == "nt":
        subprocess.run(
            [
                "icacls",
                str(folder),
                "/grant",
                "*S-1-5-32-545:(OI)(CI)R",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    return folder


def _assert_owner_only(path) -> None:
    """POSIX mode bits are the control on Unix; on Windows it is the ACL."""
    if os.name == "nt":
        acl = subprocess.run(
            ["icacls", str(path)], capture_output=True, text=True, check=True
        ).stdout
        assert os.environ["USERNAME"] in acl
        assert "BUILTIN\\Users" not in acl
        assert "Everyone" not in acl
    else:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


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


def _minimal_media_db(path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE VEHICLE_MEDIA "
        "(image_url TEXT, model_3d_url TEXT, source TEXT)"
    )
    connection.execute(
        "INSERT INTO VEHICLE_MEDIA VALUES (?, ?, ?)",
        (
            "https://cdn.example/image.webp?sig=abc&api_key=secret",
            "https://cdn.example/model.glb?sig=def&api_key=secret",
            "carimagesapi",
        ),
    )
    connection.commit()
    connection.close()


def test_the_settings_backup_helper_sanitizes_only_the_copy(tmp_path):
    source = tmp_path / "settings-source.db"
    _minimal_media_db(source)
    before = source.read_bytes()
    out = _shared_dir(tmp_path) / "settings-backup.db"

    create_sanitized_sqlite_snapshot(source, out)

    assert b"api_key=" not in out.read_bytes()
    assert b"secret" not in out.read_bytes()
    assert source.read_bytes() == before
    _assert_owner_only(out)

    connection = sqlite3.connect(out)
    try:
        rows = connection.execute(
            "SELECT image_url, model_3d_url, source FROM VEHICLE_MEDIA"
        ).fetchall()
    finally:
        connection.close()
    assert rows == [("", "", "carimagesapi")]


def test_an_unsanitizable_snapshot_is_deleted(tmp_path):
    """A table that lost its URL columns cannot be certified safe."""
    source = tmp_path / "not-a-rental.db"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE VEHICLE_MEDIA (source TEXT)")
    connection.commit()
    connection.close()

    out = tmp_path / "copy.db"
    with pytest.raises(SnapshotError, match="URL columns"):
        create_sanitized_sqlite_snapshot(source, out)
    assert not out.exists()


def test_a_snapshot_is_owner_only(tmp_path):
    source = tmp_path / "source.db"
    _seed_source(source)
    out = _shared_dir(tmp_path) / "copy.db"

    copy_database(f"sqlite:///{source}", out)

    _assert_owner_only(out)


def test_a_snapshot_refuses_to_overwrite_its_source(tmp_path):
    source = tmp_path / "source.db"
    _seed_source(source)
    before = source.read_bytes()

    with pytest.raises(ValueError, match="source"):
        copy_database(f"sqlite:///{source}", source)

    assert source.read_bytes() == before


def test_the_cli_refuses_an_out_path_that_is_the_source(tmp_path, monkeypatch):
    import sys

    from scripts import backup_live_db

    source = tmp_path / "source.db"
    _seed_source(source)
    before = source.read_bytes()
    monkeypatch.setattr(backup_live_db, "BASE_DIR", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backup_live_db",
            "--source-url",
            f"sqlite:///{source}",
            "--out",
            "source.db",
        ],
    )

    with pytest.raises(SystemExit):
        backup_live_db.main()

    assert source.read_bytes() == before
