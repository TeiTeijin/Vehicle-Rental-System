"""Security hardening for local images, media URLs, and customer documents.

Task 16. Three independent attack surfaces, one file:

- cache filenames are built from staff-typed make/model/year, so a value
  containing ``/`` or ``..`` must never place a file outside the cache;
- CarImages signed URLs are longer than the old ``VARCHAR(255)`` column;
- licence-photo paths were stored absolute, leaking the host layout and
  breaking as soon as the folder moves.

Plus the permission sweep for local secrets. Everything is exercised on
throwaway SQLite and ``tmp_path`` files; no live database and no network.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.services import customer_service, image_cache  # noqa: E402
from app.services.errors import ValidationError  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent


class TestCacheKeyTraversal:
    def test_cache_key_cannot_escape_the_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(image_cache, "CACHE_DIR", tmp_path / "cache")
        image_cache.store("../../evil", "x", 2020, b"data", "https://example.test/x")
        assert not (tmp_path.parent / "evil-x-2020.img").exists()
        assert list((tmp_path / "cache").glob("*.img"))

    def test_identity_has_no_separators_or_traversal(self):
        key = image_cache._identity("../../evil", "..\\x", 2020)
        assert "/" not in key
        assert "\\" not in key
        assert ".." not in key
        assert key == key.lower()

    def test_identity_is_stable_and_normalised(self):
        assert image_cache._identity("Toyota", "Fortuner", 2023) == image_cache._identity(
            "  toyota ", "FORTUNER", 2023
        )

    def test_identity_disambiguates_by_digest(self):
        assert image_cache._identity("../a", "x", 2020) != image_cache._identity(
            "../b", "x", 2020
        )

    def test_paths_refuses_to_leave_the_cache_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(image_cache, "CACHE_DIR", tmp_path / "cache")
        with pytest.raises(ValueError):
            image_cache._paths("../escape")

    def test_store_load_still_round_trips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(image_cache, "CACHE_DIR", tmp_path / "cache")
        image_cache.store("Toyota", "Vios", 2022, b"ok", "https://example.test/x")
        assert image_cache.load("Toyota", "Vios", 2022).data == b"ok"
        assert image_cache.load("toyota", "  vios ", 2022) is not None


class TestMediaUrlWidth:
    def test_model_allows_a_signed_url_longer_than_255(self):
        from app.models import Vehicle_Media

        column = Vehicle_Media.__table__.c.image_url
        length = getattr(column.type, "length", None)
        assert length is None or length >= 512, "image_url is still narrow"
        model_column = Vehicle_Media.__table__.c.model_3d_url
        model_length = getattr(model_column.type, "length", None)
        assert model_length is None or model_length >= 512

    def test_row_round_trips_a_400_character_url(self, session, vehicle):
        from app.models import Vehicle_Media

        url = "https://cdn.example.test/image?token=" + "a" * 400
        assert len(url) > 400
        row = Vehicle_Media(
            vehicle_id=vehicle.vehicle_id,
            source="carimagesapi",
            view_angle="front34",
            image_url=url,
            model_3d_url=url,
            is_watermarked=True,
            cached_at=datetime.now(),
        )
        session.add(row)
        session.flush()
        loaded = session.get(Vehicle_Media, row.media_id)
        assert loaded.image_url == url
        assert loaded.model_3d_url == url


class TestCustomerDocuments:
    def test_store_returns_a_relative_path(self, tmp_path, monkeypatch):
        from app import config

        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        src = tmp_path / "front.jpg"
        src.write_bytes(b"jpeg-bytes")

        stored = customer_service.store_licence_doc("D03-1234-567", "front", str(src))

        assert stored.startswith("customer_docs/")
        filename = stored.split("customer_docs/", 1)[1]
        assert "/" not in filename and "\\" not in filename

    def test_resolve_doc_path_stays_inside_base_dir(self, tmp_path, monkeypatch):
        from app import config

        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        src = tmp_path / "back.png"
        src.write_bytes(b"png-bytes")
        stored = customer_service.store_licence_doc("LIC123456", "back", str(src))

        resolved = customer_service.resolve_doc_path(stored)
        assert resolved.is_file()
        assert resolved.resolve().is_relative_to(tmp_path.resolve())
        assert resolved.parent == (tmp_path / "customer_docs").resolve()

    def test_resolve_keeps_legacy_absolute_paths_inside_base_dir(
        self, tmp_path, monkeypatch
    ):
        from app import config

        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        legacy = tmp_path / "customer_docs" / "OLD_front.jpg"
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b"jpeg")

        assert customer_service.resolve_doc_path(str(legacy)) == legacy.resolve()

    def test_resolve_refuses_traversal(self, tmp_path, monkeypatch):
        from app import config

        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        with pytest.raises(ValidationError):
            customer_service.resolve_doc_path("../../etc/passwd")

    def test_resolve_refuses_absolute_paths_outside_base_dir(self, tmp_path, monkeypatch):
        from app import config

        monkeypatch.setattr(config, "BASE_DIR", tmp_path / "the-app")
        with pytest.raises(ValidationError):
            customer_service.resolve_doc_path("/etc/passwd")

    def test_gitignore_ignores_customer_docs(self):
        text = (BASE_DIR / ".gitignore").read_text(encoding="utf-8")
        assert "customer_docs/" in text


class TestFieldLengths:
    """Services refuse input the MySQL columns cannot hold, instead of
    letting MySQL truncate it or raise an opaque 1406."""

    def test_overlong_licence_is_refused(self, session):
        with pytest.raises(ValidationError) as caught:
            customer_service.create_customer(
                session,
                full_name="Over Long",
                email="overlong@example.com",
                phone="09171234567",
                address="1 Street",
                license_number="L" * (customer_service.MAX_LICENCE_LENGTH + 1),
                license_expiry=date(2030, 1, 1),
            )
        assert caught.value.field == "license_number"

    def test_overlong_name_is_refused(self, session):
        with pytest.raises(ValidationError) as caught:
            customer_service.create_customer(
                session,
                full_name="N" * (customer_service.MAX_TEXT_LENGTH + 1),
                email="longname@example.com",
                phone="09171234567",
                address="1 Street",
                license_number="LICLONGNAME",
                license_expiry=date(2030, 1, 1),
            )
        assert caught.value.field == "full_name"

    def test_overlong_note_is_refused(self, session, customer):
        with pytest.raises(ValidationError) as caught:
            customer_service.add_note(
                session, customer, "x" * (customer_service.MAX_NOTE_LENGTH + 1)
            )
        assert caught.value.field == "note"

    def test_licence_at_the_limit_is_accepted(self, session):
        limit = customer_service.MAX_LICENCE_LENGTH
        created = customer_service.create_customer(
            session,
            full_name="At Limit",
            email="atlimit@example.com",
            phone="09171234567",
            address="1 Street",
            license_number="L" * limit,
            license_expiry=date(2030, 1, 1),
        )
        assert len(created.license_number) == limit


class TestSecureLocalFiles:
    def test_existing_secrets_become_0600(self, tmp_path):
        from scripts import secure_local_files

        env = tmp_path / ".env"
        env.write_text("SECRET=1\n")
        ca = tmp_path / "ca.pem"
        ca.write_text("cert\n")
        os.chmod(ca, 0o644)

        results = {name: status for name, status, _path in secure_local_files.secure(tmp_path)}

        assert (env.stat().st_mode & 0o777) == 0o600
        assert (ca.stat().st_mode & 0o777) == 0o600
        assert results[".env"] == "secured"
        assert results["ca.pem"] == "secured"

    def test_missing_files_are_reported_not_raised(self, tmp_path):
        from scripts import secure_local_files

        results = {name: status for name, status, _path in secure_local_files.secure(tmp_path)}

        assert results["backup.db"] == "missing"
        assert results["demo.db"] == "missing"
        assert not list(tmp_path.iterdir())

    def test_secure_is_idempotent(self, tmp_path):
        from scripts import secure_local_files

        (tmp_path / ".env").write_text("SECRET=1\n")
        first = secure_local_files.secure(tmp_path)
        second = secure_local_files.secure(tmp_path)
        assert [status for _n, status, _p in first] == [
            status for _n, status, _p in second
        ]
        assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600

    def test_main_never_fails_on_missing_files(self, tmp_path, monkeypatch):
        from scripts import secure_local_files

        monkeypatch.setattr(
            secure_local_files, "DEFAULT_ROOT", tmp_path, raising=False
        )
        monkeypatch.setattr(
            "sys.argv", ["secure_local_files", "--root", str(tmp_path)]
        )
        assert secure_local_files.main() == 0
