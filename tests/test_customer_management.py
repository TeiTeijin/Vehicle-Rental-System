"""Tests for customer eligibility, duplicates, flags, notes, deactivate."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.database import Base  # noqa: E402
from app.models import Booking, Penalty, Users, Vehicle, Vehicle_Category  # noqa: E402
from app.services import customer_service, payment_service  # noqa: E402
from app.services.errors import ConflictError, ValidationError  # noqa: E402
from app.staff.context import (  # noqa: E402
    DatabaseSelection,
    DatabaseTarget,
    StaffContext,
    StaffUser,
)


@pytest.fixture
def ctx(tmp_path):
    path = tmp_path / "cust_mgmt.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    engine.dispose()
    context = StaffContext(
        DatabaseSelection(url=f"sqlite:///{path}", target=DatabaseTarget.LIVE, label="T")
    )
    context.sign_in(StaffUser(1, "Admin", "admin@example.com", "admin"))
    with context.session() as session:
        customer = Users(
            full_name="Rita Reyes", email="rita@example.com",
            password_hash="x", address="1 Street", phone="09171234567",
            license_number="LIC0000001", license_expiry=date(2030, 1, 1),
            role="customer", birthdate=date(1995, 5, 5),
            source="walk_in", is_active=True,
            created_at=datetime.now(),
        )
        session.add(customer)
        session.flush()
        customer_id = customer.user_id
    yield context, customer_id
    context.dispose()


def _customer(context, customer_id):
    with context.reading() as session:
        customer = session.get(Users, customer_id)
        session.expunge(customer)
        return customer


class TestEligibility:
    def test_clean_customer_passes(self, ctx):
        context, customer_id = ctx
        today = date.today()
        with context.reading() as session:
            customer = session.get(Users, customer_id)
            verdict = customer_service.eligibility_for(
                session, customer, today, today + timedelta(days=2)
            )
        assert verdict["blocked"] is False
        assert verdict["flagged"] is False

    def test_expired_licence_blocks(self, ctx):
        context, customer_id = ctx
        today = date.today()
        with context.session() as session:
            customer = session.get(Users, customer_id)
            customer.license_expiry = today - timedelta(days=1)
        with context.reading() as session:
            customer = session.get(Users, customer_id)
            verdict = customer_service.eligibility_for(
                session, customer, today, today + timedelta(days=2)
            )
        assert verdict["blocked"] is True
        assert any("expired" in reason.lower() for reason in verdict["reasons"])

    def test_expiry_before_return_blocks(self, ctx):
        context, customer_id = ctx
        today = date.today()
        with context.session() as session:
            customer = session.get(Users, customer_id)
            customer.license_expiry = today + timedelta(days=1)
        with context.reading() as session:
            customer = session.get(Users, customer_id)
            verdict = customer_service.eligibility_for(
                session, customer, today, today + timedelta(days=5)
            )
        assert verdict["blocked"] is True

    def test_missing_birthdate_blocks_once(self, ctx):
        context, customer_id = ctx
        today = date.today()
        with context.session() as session:
            customer = session.get(Users, customer_id)
            customer.birthdate = None
        with context.reading() as session:
            customer = session.get(Users, customer_id)
            verdict = customer_service.eligibility_for(
                session, customer, today, today + timedelta(days=2)
            )
        assert verdict["blocked"] is True
        assert verdict["needs_dob"] is True
        # Quick fix clears it: each legacy customer is blocked exactly once.
        with context.session() as session:
            customer = session.get(Users, customer_id)
            customer_service.update_customer(session, customer, birthdate=date(1990, 1, 1))
        with context.reading() as session:
            customer = session.get(Users, customer_id)
            verdict = customer_service.eligibility_for(
                session, customer, today, today + timedelta(days=2)
            )
        assert verdict["blocked"] is False

    def test_underage_blocks(self, ctx):
        context, customer_id = ctx
        today = date.today()
        with context.session() as session:
            customer = session.get(Users, customer_id)
            customer.birthdate = today - timedelta(days=365 * 18)
        with context.reading() as session:
            customer = session.get(Users, customer_id)
            verdict = customer_service.eligibility_for(
                session, customer, today, today + timedelta(days=2)
            )
        assert verdict["blocked"] is True

    def test_flag_warns_instead_of_blocking(self, ctx):
        context, customer_id = ctx
        today = date.today()
        with context.session() as session:
            customer = session.get(Users, customer_id)
            customer_service.set_flag(session, customer, "Unpaid penalty", staff_id=1)
        with context.reading() as session:
            customer = session.get(Users, customer_id)
            verdict = customer_service.eligibility_for(
                session, customer, today, today + timedelta(days=2)
            )
        assert verdict["blocked"] is False
        assert verdict["flagged"] is True
        assert "Unpaid penalty" in (verdict["flag_reason"] or "")

    def test_expiry_window_comes_from_settings(self, ctx):
        from contextlib import contextmanager
        from types import SimpleNamespace

        from app.services import settings_service
        from app.staff.pages.customers import CustomerFilter, _customer_rows

        context, customer_id = ctx
        today = date.today()
        with context.session() as session:
            customer = session.get(Users, customer_id)
            customer.license_expiry = today + timedelta(days=60)
            settings_service.set_value(
                session, "licence_expiring_days", "90"
            )

        @contextmanager
        def reading():
            with context.reading() as session:
                yield session

        rows = _customer_rows(
            SimpleNamespace(reading=reading), CustomerFilter()
        )
        row = next(r for r in rows if r[0] == "Rita Reyes")
        assert row[5] == "Soon"

        with context.session() as session:
            settings_service.set_value(
                session, "licence_expiring_days", "30"
            )
        rows = _customer_rows(
            SimpleNamespace(reading=reading), CustomerFilter()
        )
        row = next(r for r in rows if r[0] == "Rita Reyes")
        assert row[5] == "-"

    def test_deactivated_blocks(self, ctx):
        context, customer_id = ctx
        today = date.today()
        with context.session() as session:
            customer = session.get(Users, customer_id)
            customer_service.deactivate_customer(session, customer)
        with context.reading() as session:
            customer = session.get(Users, customer_id)
            verdict = customer_service.eligibility_for(
                session, customer, today, today + timedelta(days=2)
            )
        assert verdict["blocked"] is True


class TestAggregateBalances:
    def test_maps_match_single_customer_balance(
        self, session, customer, customer_factory, vehicle, booking_factory
    ):
        booking = booking_factory(
            user=customer,
            vehicle=vehicle,
            status="confirmed",
            total_cost="3000.00",
        )
        session.add(
            Penalty(
                booking_id=booking.booking_id,
                penalty_type="late_return",
                amount=Decimal("500.00"),
                description="Late",
                created_at=datetime.now(),
            )
        )
        payment_service.record_payment(
            session, booking, Decimal("1000.00"), "cash"
        )

        owed = customer_service.outstanding_by_customer(session)
        assert owed[customer.user_id] == Decimal("2500.00")
        assert owed[customer.user_id] == customer_service.outstanding_for(
            session, customer
        )

        done_customer = customer_factory()
        booking_factory(
            user=done_customer, vehicle=vehicle, status="completed"
        )
        open_map = customer_service.open_bookings_by_customer(session)
        assert open_map[customer.user_id] is True
        assert open_map.get(done_customer.user_id, False) is False


class TestDuplicates:
    def test_phone_match_warns(self, ctx):
        context, customer_id = ctx
        with context.reading() as session:
            hits = customer_service.find_duplicates(
                session, phone="+63 917-123-4567", exclude_id=999999
            )
            ids = [c.user_id for c in hits]
        assert ids == [customer_id]

    def test_licence_match_warns(self, ctx):
        context, customer_id = ctx
        with context.reading() as session:
            hits = customer_service.find_duplicates(
                session, license_number=" lic0000001 ", exclude_id=999999
            )
            ids = [c.user_id for c in hits]
        assert ids == [customer_id]

    def test_self_excluded(self, ctx):
        context, customer_id = ctx
        with context.reading() as session:
            assert customer_service.find_duplicates(
                session, phone="09171234567", license_number="LIC0000001",
                exclude_id=customer_id,
            ) == []


class TestNotes:
    def test_add_and_list_with_authorship(self, ctx):
        context, customer_id = ctx
        with context.session() as session:
            customer = session.get(Users, customer_id)
            customer_service.add_note(
                session, customer, "Prefers automatic.", author_id=1
            )
        with context.reading() as session:
            customer = session.get(Users, customer_id)
            notes = customer_service.list_notes(session, customer)
            snapshot = [(n.note, n.author_id) for n in notes]
        assert len(snapshot) == 1
        assert snapshot[0] == ("Prefers automatic.", 1)

    def test_empty_note_refused(self, ctx):
        context, customer_id = ctx
        with context.session() as session:
            customer = session.get(Users, customer_id)
            with pytest.raises(ValidationError):
                customer_service.add_note(session, customer, "  ")


class TestDeactivate:
    def test_deactivate_blocks_licence_check(self, ctx):
        from app.services.booking_service import check_licence

        context, customer_id = ctx
        with context.session() as session:
            customer = session.get(Users, customer_id)
            customer_service.deactivate_customer(session, customer)
        with context.reading() as session:
            customer = session.get(Users, customer_id)
            assert customer.is_active is False
            with pytest.raises(ConflictError):
                check_licence(session, customer, date.today())
        with context.session() as session:
            customer = session.get(Users, customer_id)
            customer_service.reactivate_customer(session, customer)
        with context.reading() as session:
            assert session.get(Users, customer_id).is_active is True


class TestSettleAllocation:
    def test_covers_selected_then_cascades(self):
        legs = customer_service.allocate_settlement(
            [(1, Decimal("100.00")), (2, Decimal("50.00"))],
            1, Decimal("120.00"),
        )
        assert legs == [(1, Decimal("100.00")), (2, Decimal("20.00"))]

    def test_partial_stays_on_the_pick(self):
        legs = customer_service.allocate_settlement(
            [(1, Decimal("100.00")), (2, Decimal("50.00"))],
            2, Decimal("30.00"),
        )
        assert legs == [(2, Decimal("30.00"))]

    def test_overpayment_refused(self):
        with pytest.raises(ValidationError):
            customer_service.allocate_settlement(
                [(1, Decimal("100.00"))], 1, Decimal("101.00")
            )


class TestStatement:
    def test_statement_pdf_builds(self, ctx, tmp_path):
        from app.services import receipt_service

        context, customer_id = ctx
        with context.session() as session:
            customer = session.get(Users, customer_id)
            path = receipt_service.generate_statement_pdf(
                session, customer, dest_dir=tmp_path
            )
        assert path.endswith(".pdf")
        assert list(tmp_path.glob("statement_*.pdf"))
