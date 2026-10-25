"""Media, cache, and CarImages failure behaviour.

These tests run fully offline. They deliberately shadow the global
``offline_media`` fixture so media services can be exercised directly, while
still replacing every HTTP entry point with a test double.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import requests

from app.services import api_cache, carimages_client as ci, image_cache
from app.services import media_service, vehicle_photos
from app.services.vehicle_photos import VehicleRef


@pytest.fixture(autouse=True)
def offline_media(monkeypatch, tmp_path):
    """Redirect both disk caches and make an accidental network call loud."""
    monkeypatch.setattr(image_cache, "CACHE_DIR", tmp_path / "image-cache")
    monkeypatch.setattr(api_cache, "CACHE_DIR", tmp_path / "api-cache")

    def _blocked(*args, **kwargs):
        raise AssertionError(f"unexpected network call: {args!r} {kwargs!r}")

    monkeypatch.setattr(ci.requests, "get", _blocked)
    monkeypatch.setattr(ci.requests, "post", _blocked)
    monkeypatch.setattr(vehicle_photos.requests, "get", _blocked)
    yield


class _Response:
    def __init__(self, status_code=200, payload=None, content=b"", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class TestApiCacheKeys:
    def test_slug_collisions_are_disambiguated(self):
        # Both slugify to the same text; the digest must keep them apart.
        assert api_cache._key(
            "A-B", "C", 2020, "car-image"
        ) != api_cache._key("A", "B-C", 2020, "car-image")

    def test_path_refuses_a_hand_built_escape(self, monkeypatch):
        monkeypatch.setattr(api_cache, "_key", lambda *a, **k: "../escape")
        with pytest.raises(ValueError):
            api_cache._path("Toyota", "Vios", 2022, "car-image")

    def test_old_record_version_is_invalidated(self):
        legacy = api_cache.CACHE_DIR / "car-image-toyota-vios-2022.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(
            json.dumps({"version": 1, "payload": {"views": {"front": {}}}}),
            encoding="utf-8",
        )

        assert api_cache.load("Toyota", "Vios", 2022, kind="car-image") is None

    def test_cached_payload_round_trips(self):
        payload = {"views": {"front": {"webp": "https://cdn/x.webp"}}}
        api_cache.store("Toyota", "Vios", 2022, payload, kind="car-image")
        assert api_cache.load("Toyota", "Vios", 2022, kind="car-image") == payload

    def test_stats_and_clear_count_both_caches(self):
        api_cache.store("Toyota", "Vios", 2022, {"views": {}}, kind="car-image")
        image_cache.store("Toyota", "Vios", 2022, b"photo", "https://x/y")

        assert api_cache.stats()[0] == 1
        assert image_cache.stats()[0] == 1
        assert api_cache.clear() == 1
        assert image_cache.clear() == 1
        assert api_cache.stats() == (0, 0)
        assert image_cache.stats() == (0, 0)


class TestImageCacheCompatibility:
    def test_variants_have_separate_entries(self):
        image_cache.store(
            "Toyota", "Vios", 2022, b"front", "https://x/f", variant="front"
        )
        image_cache.store(
            "Toyota", "Vios", 2022, b"left", "https://x/l", variant="left"
        )
        image_cache.store(
            "Toyota", "Vios", 2022, b"right", "https://x/r", variant="right"
        )

        assert image_cache.load("Toyota", "Vios", 2022, "front").data == b"front"
        assert image_cache.load("Toyota", "Vios", 2022, "left").data == b"left"
        assert image_cache.load("Toyota", "Vios", 2022, "right").data == b"right"

    def test_store_failure_keeps_the_previous_entry(self, monkeypatch):
        image_cache.store("Toyota", "Vios", 2022, b"good", "https://x/good")
        real_write = image_cache._atomic_write

        def _fail_metadata(target, data):
            if target.suffix == image_cache._META_SUFFIX:
                raise OSError("disk full")
            return real_write(target, data)

        monkeypatch.setattr(image_cache, "_atomic_write", _fail_metadata)
        image_cache.store("Toyota", "Vios", 2022, b"new", "https://x/new")

        cached = image_cache.load("Toyota", "Vios", 2022)
        assert cached is not None
        assert cached.data == b"good"

    def test_transient_read_error_does_not_delete_the_entry(self, monkeypatch):
        image_cache.store("Toyota", "Vios", 2022, b"good", "https://x/good")
        original_read = Path.read_bytes

        def _permission_error(self):
            raise PermissionError("file is busy")

        monkeypatch.setattr(Path, "read_bytes", _permission_error)
        assert image_cache.load("Toyota", "Vios", 2022) is None
        monkeypatch.setattr(Path, "read_bytes", original_read)

        assert image_cache.load("Toyota", "Vios", 2022) is not None

    def test_old_slug_only_key_is_ignored_but_cleared(self):
        # Before the digest was added the key was just the slug.
        legacy_bytes = image_cache.CACHE_DIR / "toyota-vios-2022.img"
        legacy_meta = image_cache.CACHE_DIR / "toyota-vios-2022.json"
        legacy_bytes.parent.mkdir(parents=True)
        legacy_bytes.write_bytes(b"old")
        legacy_meta.write_text(
            json.dumps({"url": "https://x/old"}), encoding="utf-8"
        )

        assert image_cache.load("Toyota", "Vios", 2022) is None
        assert image_cache.stats()[0] == 1
        assert image_cache.clear() == 1
        assert not legacy_bytes.exists()

    def test_media_metadata_contains_no_customer_fields(self, session):
        image_cache.store("Toyota", "Vios", 2022, b"photo", "https://x/y")
        _, meta_path = image_cache._paths(
            image_cache._identity("Toyota", "Vios", 2022)
        )
        sidecar = json.loads(meta_path.read_text(encoding="utf-8"))
        assert set(sidecar) == {"make", "model", "year", "variant", "url"}

        api_cache.store(
            "Toyota", "Vios", 2022, {"views": {}}, kind="car-image"
        )
        record = json.loads(
            (
                api_cache.CACHE_DIR
                / f"{api_cache._key('Toyota', 'Vios', 2022, 'car-image')}.json"
            ).read_text(encoding="utf-8")
        )
        assert set(record) == {"version", "make", "model", "year", "kind", "payload"}
        assert not any(
            word in json.dumps(record).lower()
            for word in ("customer", "email", "phone", "licence", "license")
        )


class TestMediaServiceResilience:
    def _stale_row(self, session, vehicle):
        from app.models import Vehicle_Media

        row = Vehicle_Media(
            vehicle_id=vehicle.vehicle_id,
            source="carimagesapi",
            view_angle="front34",
            image_url="https://carimagesapi.com/old.webp",
            model_3d_url="",
            is_watermarked=True,
            cached_at=datetime.now() - timedelta(days=2),
        )
        session.add(row)
        session.flush()
        return row

    def test_offline_api_keeps_the_last_known_media(
        self, session, vehicle, monkeypatch
    ):
        row = self._stale_row(session, vehicle)

        def _offline(*args, **kwargs):
            raise requests.ConnectionError("offline")

        monkeypatch.setattr(ci, "get_signed_image_url", _offline)
        rows = media_service.get_or_fetch_media(session, vehicle)

        assert [r.media_id for r in rows] == [row.media_id]
        assert session.get(type(row), row.media_id) is not None

    def test_force_refresh_replaces_rows_only_after_a_successful_fetch(
        self, session, vehicle, monkeypatch
    ):
        old = self._stale_row(session, vehicle)
        monkeypatch.setattr(
            ci,
            "get_signed_image_url",
            lambda *a, **k: "https://carimagesapi.com/new.webp",
        )

        rows = media_service.get_or_fetch_media(session, vehicle, force=True)

        assert len(rows) == 1
        assert rows[0].image_url.endswith("/new.webp")
        assert old.image_url.endswith("/old.webp")


class TestCredentialBearingRequests:
    def test_follow_refuses_an_untrusted_endpoint(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            ci.requests, "get", lambda *a, **k: calls.append((a, k))
        )

        assert (
            ci.follow_model_redirect("https://evil.example/model?redirect=1")
            is None
        )
        assert calls == []

    def test_follow_refuses_an_untrusted_redirect_target(self, monkeypatch):
        monkeypatch.setattr(
            ci.requests,
            "get",
            lambda *a, **k: _Response(
                status_code=302,
                headers={"location": "https://evil.example/x.glb"},
            ),
        )

        assert (
            ci.follow_model_redirect(
                "https://carimagesapi.com/api/v1/vehicles/x/y/g/model?redirect=1"
            )
            is None
        )

    def test_vehicle_photo_download_does_not_send_the_secret_to_the_cdn(
        self, monkeypatch
    ):
        seen = {}

        def _fake_get(url, **kwargs):
            seen.update(kwargs)
            return _Response(status_code=200, content=b"image")

        monkeypatch.setattr(vehicle_photos.requests, "get", _fake_get)
        assert (
            vehicle_photos._download(
                "https://cdn.carimagesapi.com/x.webp", with_key=True
            )
            == b"image"
        )
        assert "X-Api-Secret" not in (seen.get("headers") or {})
        assert not seen.get("params")

    def test_vehicle_photo_download_refuses_an_unknown_host(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            vehicle_photos.requests, "get", lambda *a, **k: calls.append((a, k))
        )

        assert vehicle_photos._download("https://evil.example/x.webp", with_key=True) is None
        assert calls == []

    def test_model_glb_can_download_from_the_cdn_without_a_secret(
        self, monkeypatch
    ):
        seen = {}

        def _fake_get(url, **kwargs):
            seen.update(kwargs)
            return _Response(status_code=200, content=b"glb")

        monkeypatch.setattr(ci.requests, "get", _fake_get)
        assert ci.download_model_glb("https://cdn.carimagesapi.com/x.glb") == b"glb"
        assert "X-Api-Secret" not in (seen.get("headers") or {})

    def test_ping_is_false_when_the_api_cannot_be_reached(self, monkeypatch):
        monkeypatch.setattr(
            ci,
            "resolve_generation_slug",
            lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("down")),
        )
        assert ci.ping() is False

        monkeypatch.setattr(ci, "resolve_generation_slug", lambda *a, **k: "gen")
        assert ci.ping() is True


class TestModelPathCollision:
    def test_slug_collisions_get_different_glb_files(self):
        first = VehicleRef(make="A-B", model="C", year=2020)
        second = VehicleRef(make="A", model="B-C", year=2020)

        first_path = vehicle_photos._glb_path(first)
        second_path = vehicle_photos._glb_path(second)

        assert first_path != second_path
        assert first_path.parent == image_cache.CACHE_DIR.resolve()
        assert second_path.parent == image_cache.CACHE_DIR.resolve()
