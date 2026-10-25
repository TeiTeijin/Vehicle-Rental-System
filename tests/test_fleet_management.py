"""Tests for retire/bulk-rate/stats in fleet_service."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.database import Base  # noqa: E402
from app.models import Booking, Maintenance_Record, Vehicle, Vehicle_Category  # noqa: E402
from app.services import fleet_service  # noqa: E402
from app.services.errors import StateError  # noqa: E402
from app.staff.context import (  # noqa: E402
    DatabaseSelection,
    DatabaseTarget,
    StaffContext,
    StaffUser,
)


@pytest.fixture
def ctx(tmp_path):
    path = tmp_path / "fleet_mgmt.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    engine.dispose()
    context = StaffContext(
        DatabaseSelection(url=f"sqlite:///{path}", target=DatabaseTarget.LIVE, label="T")
    )
    context.sign_in(StaffUser(1, "Admin", "admin@example.com", "admin"))
    with context.session() as session:
        category = Vehicle_Category(category_name="Car", base_rate_multiplier=1.0)
        session.add(category)
        session.flush()
        car = Vehicle(
            category_id=category.category_id,
            make="Toyota",
            model="Vios",
            year=2022,
            plate_number="TST-001",
            daily_rate=Decimal("1500.00"),
            mileage=1000,
            status="available",
            created_at=datetime.now(),
        )
        session.add(car)
        session.flush()
        car_id = car.vehicle_id
    yield context, car_id
    context.dispose()


def _vehicle(context, car_id):
    """Detached row with loaded columns intact: expunged before reading()
    rolls back (which would otherwise expire every attribute). Only column
    attributes are touched afterwards -- never lazy relationships."""
    with context.reading() as session:
        car = session.get(Vehicle, car_id)
        session.expunge(car)
        return car


class TestRetire:
    def test_retire_sets_status_and_date(self, ctx):
        context, car_id = ctx
        with context.session() as session:
            car = session.get(Vehicle, car_id)
            fleet_service.retire_vehicle(session, car)
        car = _vehicle(context, car_id)
        assert car.status == "retired"
        assert car.retired_at == date.today()

    def test_retired_is_not_rentable(self, ctx):
        context, car_id = ctx
        with context.session() as session:
            car = session.get(Vehicle, car_id)
            fleet_service.retire_vehicle(session, car)
        assert fleet_service.is_rentable(_vehicle(context, car_id)) is False

    def test_retire_refused_with_active_booking(self, ctx):
        context, car_id = ctx
        from app.models import Users

        with context.session() as session:
            user = Users(
                full_name="Renter", email="r@example.com", password_hash="x",
                address="a", license_number="L1",
                license_expiry=date(2030, 1, 1), role="customer",
                created_at=datetime.now(),
            )
            session.add(user)
            session.flush()
            session.add(
                Booking(
                    user_id=user.user_id, vehicle_id=car_id,
                    start_date=date.today(), end_date=date.today() + timedelta(days=2),
                    total_cost=Decimal("3000.00"), status="confirmed",
                    created_at=datetime.now(),
                )
            )
        with context.session() as session:
            car = session.get(Vehicle, car_id)
            with pytest.raises(StateError):
                fleet_service.retire_vehicle(session, car)

    def test_unretire_returns_to_service(self, ctx):
        context, car_id = ctx
        with context.session() as session:
            car = session.get(Vehicle, car_id)
            fleet_service.retire_vehicle(session, car)
            fleet_service.unretire_vehicle(session, car)
        car = _vehicle(context, car_id)
        assert car.status == "available"
        assert car.retired_at is None


class TestStatusPolicy:
    def test_maintenance_and_retired_always_block_a_booking(self):
        assert fleet_service.ALWAYS_BLOCKING_STATUSES == (
            "maintenance",
            "retired",
        )

    def test_is_rentable_still_means_handover_ready(self):
        assert fleet_service.is_rentable(Vehicle(status="available")) is True
        assert fleet_service.is_rentable(Vehicle(status="rented")) is False
        assert fleet_service.is_rentable(Vehicle(status="reserved")) is False
        assert fleet_service.is_rentable(Vehicle(status="maintenance")) is False
        assert fleet_service.is_rentable(Vehicle(status="retired")) is False


class TestBulkRate:
    def test_bulk_writes_rate_and_logs(self, ctx):
        context, car_id = ctx
        with context.session() as session:
            result = fleet_service.bulk_set_rate(
                session, [car_id], Decimal("1700.00"), changed_by=1, note="category"
            )
        assert result == {"updated": 1, "skipped": 0, "errors": []}
        car = _vehicle(context, car_id)
        assert car.daily_rate == Decimal("1700.00")
        from app.models import Vehicle_Rate_Log

        with context.reading() as session:
            logs = session.query(Vehicle_Rate_Log).all()
            assert len(logs) == 1
            assert logs[0].is_bulk is True
            assert logs[0].old_rate == Decimal("1500.00")

    def test_only_at_rate_protects_exceptions(self, ctx):
        context, car_id = ctx
        with context.session() as session:
            result = fleet_service.bulk_set_rate(
                session, [car_id], Decimal("1700.00"), only_at_rate=Decimal("9999.00")
            )
        assert result["updated"] == 0
        assert result["skipped"] == 1
        assert _vehicle(context, car_id).daily_rate == Decimal("1500.00")

    def test_preview_counts(self, ctx):
        context, car_id = ctx
        with context.reading() as session:
            preview = fleet_service.preview_bulk_rate(session, [car_id, 999999])
        assert preview["count"] == 1
        assert preview["mode"] == "1,500.00"


class TestBulkStatus:
    def test_bulk_status_moves_rows(self, ctx):
        context, car_id = ctx
        with context.session() as session:
            result = fleet_service.bulk_set_status(session, [car_id], "maintenance")
        assert result["updated"] == 1
        assert _vehicle(context, car_id).status == "maintenance"

    def test_bulk_status_collects_errors(self, ctx):
        context, car_id = ctx
        with context.session() as session:
            result = fleet_service.bulk_set_status(session, [car_id, 999999], "maintenance")
        assert result["updated"] == 1
        assert len(result["errors"]) == 1


class TestStats:
    def test_list_vehicles_can_skip_maintenance_records(self, ctx):
        context, _car_id = ctx
        with context.reading() as session:
            vehicles = fleet_service.list_vehicles(
                session, with_maintenance=False
            )
            assert vehicles
            assert "maintenance_records" not in vehicles[0].__dict__

    def test_utilization_math(self, ctx):
        context, car_id = ctx
        today = date.today()
        with context.session() as session:
            from app.models import Users

            user = Users(
                full_name="Renter", email="r@example.com", password_hash="x",
                address="a", license_number="L1",
                license_expiry=date(2030, 1, 1), role="customer",
                created_at=datetime.now(),
            )
            session.add(user)
            session.flush()
            session.add(
                Booking(
                    user_id=user.user_id, vehicle_id=car_id,
                    start_date=today - timedelta(days=4), end_date=today - timedelta(days=2),
                    total_cost=Decimal("4500.00"), status="completed",
                    created_at=datetime.now(),
                )
            )
            session.add(
                Maintenance_Record(
                    vehicle_id=car_id, description="Oil",
                    cost=Decimal("500.00"),
                    start_date=today - timedelta(days=9), end_date=today - timedelta(days=8),
                    status="completed",
                )
            )
        with context.reading() as session:
            car = session.get(Vehicle, car_id)
            stats = fleet_service.vehicle_stats(session, car, window_days=30)
        # completed maintenance is not blocking -> available stays 30
        assert stats["available_days"] == 30
        assert stats["rented_days"] == 2
        assert stats["times_rented"] == 1
        assert stats["total_revenue"] == Decimal("4500.00")
        assert abs(stats["utilization"] - (2 / 30)) < 1e-9


class TestModelUrl:
    def test_follow_sends_secret_once_without_following(self, ctx, monkeypatch):
        from app.services import carimages_client as ci

        # The harness keeps the CarImages keys empty; this test asserts the
        # auth plumbing itself, so it gets placeholder keys here and only
        # here. The request stays fully faked -- no network is reachable.
        monkeypatch.setattr(ci, "CI_API_KEY", "test-key")
        monkeypatch.setattr(ci, "CI_API_SECRET", "test-secret")

        seen = {}

        class _Resp:
            status_code = 302
            headers = {"location": "https://cdn.carimagesapi.com/v/x.glb?sig=abc"}

        def _fake_get(url, **kwargs):
            seen["url"] = url
            seen["headers"] = kwargs.get("headers")
            seen["params"] = kwargs.get("params")
            seen["redirects"] = kwargs.get("allow_redirects")
            return _Resp()

        monkeypatch.setattr(ci.requests, "get", _fake_get)
        resolved = ci.follow_model_redirect(
            "https://carimagesapi.com/api/v1/vehicles/ford/explorer/g/model?redirect=1"
        )
        assert resolved == "https://cdn.carimagesapi.com/v/x.glb?sig=abc"
        assert seen["redirects"] is False
        assert seen["headers"]["X-Api-Secret"] == ci.CI_API_SECRET
        assert seen["params"]["api_key"] == ci.CI_API_KEY

    def test_follow_returns_none_when_not_a_redirect(self, ctx, monkeypatch):
        from app.services import carimages_client as ci

        class _Resp:
            status_code = 200
            headers = {}

        monkeypatch.setattr(ci.requests, "get", lambda *a, **k: _Resp())
        assert ci.follow_model_redirect("https://example.test/x.glb") is None

    def test_playable_passes_cdn_through(self, ctx):
        from app.models import Vehicle_Media
        from app.services import media_service

        context, car_id = ctx
        with context.session() as session:
            row = Vehicle_Media(
                vehicle_id=car_id, source="carimagesapi", view_angle="model_3d",
                image_url="http://x/1.webp",
                model_3d_url="https://cdn.carimagesapi.com/v/x.glb?sig=abc",
                is_watermarked=True, cached_at=datetime.now(),
            )
            session.add(row)
            session.flush()
            assert (
                media_service.playable_model_url(session, row)
                == "https://cdn.carimagesapi.com/v/x.glb?sig=abc"
            )

    def test_playable_resolves_and_persists_endpoint(self, ctx, monkeypatch):
        from app.models import Vehicle_Media
        from app.services import media_service

        context, car_id = ctx
        monkeypatch.setattr(
            media_service.ci, "follow_model_redirect",
            lambda url: "https://cdn.carimagesapi.com/v/x.glb?sig=abc",
        )
        with context.session() as session:
            row = Vehicle_Media(
                vehicle_id=car_id, source="carimagesapi", view_angle="model_3d",
                image_url="http://x/1.webp",
                model_3d_url="https://carimagesapi.com/api/v1/vehicles/ford/explorer/g/model?redirect=1",
                is_watermarked=True, cached_at=datetime.now(),
            )
            session.add(row)
            session.flush()
            media_id = row.media_id
            assert (
                media_service.playable_model_url(session, row)
                == "https://cdn.carimagesapi.com/v/x.glb?sig=abc"
            )
        with context.reading() as session:
            stored = session.get(Vehicle_Media, media_id)
            assert stored.model_3d_url == "https://cdn.carimagesapi.com/v/x.glb?sig=abc"

    def test_viewer_script_builds_renderer_before_loop(self):
        import json

        from app.staff import fleet_panels

        html = fleet_panels._THREE_HTML
        assert html.index("new THREE.WebGLRenderer") < html.index("requestAnimationFrame(tick)")
        assert "Loading 3D" in html
        # The exact failure seen live: doubled braces silently killed the
        # import map ("Failed to parse import map") and every JS block.
        block = html.split('<script type="importmap">', 1)[1].split("</script>", 1)[0]
        assert json.loads(block)["imports"]["three"].startswith("https://")
        assert "{{" not in html
        assert "__URL__" in html
