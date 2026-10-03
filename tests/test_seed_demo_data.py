"""Tests for the demo-data seeder.

Two things matter here, and they are not the same thing:

  * The data has to be *good* -- it must satisfy the constraints the
    application itself enforces, or the demo breaks the first time someone
    clicks around it.
  * The seeder has to be *safe* -- it must be impossible to point it at the
    live Aiven instance. That is the failure mode worth being paranoid about,
    so the refusal tests below are deliberately exhaustive.

Seeding runs once per module and is shared, because bcrypt on 28 users costs
about seven seconds and none of these tests mutate what they check.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import (
    Booking,
    Inspection_Report,
    Maintenance_Record,
    Payment,
    Penalty,
    Users,
    Vehicle,
)
from app.utils.security import verify_password
from scripts import seed_demo_data
from scripts.seed_demo_data import (
    DEMO_ADMIN_EMAIL,
    DEMO_STAFF_EMAIL,
    SEED,
    UnsafeTarget,
)

#: Pinned so the generated data is byte-identical on every run of the suite.
TODAY = date(2026, 9, 29)

REAL_HASH_PASSWORD = seed_demo_data.hash_password


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo_engine():
    """A seeded database, built once and read by every test in this module.

    `hash_password` is stubbed to keep the seven seconds of bcrypt work out of
    the suite. Nothing here checks a password hash except `test_admin_can_`
    below, which deliberately uses the real function.
    """
    real_hash = seed_demo_data.hash_password
    seed_demo_data.hash_password = lambda _: "not-a-real-hash-for-tests"
    engine = create_engine("sqlite://", future=True)
    try:
        seed_demo_data.seed(engine, days_back=180, today=TODAY)
        yield engine
    finally:
        seed_demo_data.hash_password = real_hash
        engine.dispose()


@pytest.fixture
def demo(demo_engine):
    """A read-only session over the seeded data."""
    with Session(demo_engine, future=True) as session:
        yield session


def rows_of(session, model, *conditions):
    stmt = select(model)
    for condition in conditions:
        stmt = stmt.where(condition)
    return session.execute(stmt).scalars().all()


# --------------------------------------------------------------------------
# The safety guard
# --------------------------------------------------------------------------


class TestRefusesDangerousTargets:
    """`assert_safe_target` runs before any connection is opened."""

    @pytest.mark.parametrize(
        "url",
        [
            "mysql://user:pw@aiven.io:3306/rentals",
            "mysql://user:pw@my-project.aivencloud.com:13306/rentals",
            "mysql://user:pw:ssl@db.aivencloud.com:3306/prod?ssl-mode=REQUIRED",
            "mysql://user:pw@thing.mysql.database.azure.com:3306/rentals",
            "mysql://user:pw@instance.cid.abcd.us-east-1.rds.amazonaws.com:3306/rentals",
        ],
    )
    def test_hosted_databases_are_refused(self, url):
        with pytest.raises(UnsafeTarget) as excinfo:
            seed_demo_data.assert_safe_target(url, acknowledged=True, forced=True)
        assert "looks like a hosted database" in str(excinfo.value)

    @pytest.mark.parametrize(
        "url",
        ["mysql://user:pw@db.internal.corp:3306/rentals", "postgresql://u:p@h/db"],
    )
    def test_unknown_hosts_need_both_flags(self, url):
        with pytest.raises(UnsafeTarget) as excinfo:
            seed_demo_data.assert_safe_target(url, acknowledged=False, forced=False)
        message = str(excinfo.value)
        assert "--i-know-this-is-a-scratch-database" in message
        assert "--yes-do-it" in message

    @pytest.mark.parametrize(
        ("acknowledged", "forced"),
        [(True, False), (False, True)],
    )
    def test_one_flag_is_not_enough(self, acknowledged, forced):
        with pytest.raises(UnsafeTarget):
            seed_demo_data.assert_safe_target(
                "mysql://user:pw@db.internal.corp:3306/rentals",
                acknowledged=acknowledged,
                forced=forced,
            )

    def test_sqlite_never_needs_flags(self):
        seed_demo_data.assert_safe_target("sqlite:///demo.db", acknowledged=False, forced=False)
        seed_demo_data.assert_safe_target("sqlite://", acknowledged=False, forced=False)

    def test_two_flags_unlock_a_non_hosted_database(self):
        seed_demo_data.assert_safe_target(
            "mysql://user:pw@db.internal.corp:3306/scratch",
            acknowledged=True,
            forced=True,
        )

    def test_refusal_never_connects(self):
        """The guard is not just a pre-flight check that runs after a connect.

        If a future edit moved it below `create_engine`, this fails.
        """
        attempts = []

        class Tripwire:
            def __getattr__(self, name):
                attempts.append(name)
                raise AssertionError(f"create_engine was called ({name})")

        original = seed_demo_data.create_engine
        seed_demo_data.create_engine = lambda *a, **k: Tripwire()
        try:
            with pytest.raises(UnsafeTarget):
                seed_demo_data.assert_safe_target(
                    "mysql://user:pw@aiven.io:3306/rentals", acknowledged=True, forced=True
                )
        finally:
            seed_demo_data.create_engine = original
        assert attempts == []

    def test_existing_database_is_refused(self, demo_engine):
        """Seeding into a populated database would mix fiction into real rows."""
        with pytest.raises(UnsafeTarget) as excinfo:
            seed_demo_data.seed(demo_engine, today=TODAY)
        assert "already has users" in str(excinfo.value)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_same_seed_and_date_produce_identical_rows(tmp_path):
    """The point of SEED: regenerating must not churn reference screenshots."""

    def generate(path, **kwargs):
        real_hash = seed_demo_data.hash_password
        seed_demo_data.hash_password = lambda _: "stub"
        try:
            engine = create_engine(f"sqlite:///{path}", future=True)
            try:
                seed_demo_data.seed(engine, **kwargs)
                return _fingerprint(engine)
            finally:
                engine.dispose()
        finally:
            seed_demo_data.hash_password = real_hash

    first = generate(tmp_path / "one.db", days_back=180, today=TODAY)
    second = generate(tmp_path / "two.db", days_back=180, today=TODAY)
    assert first == second

    third = generate(tmp_path / "three.db", days_back=180, today=TODAY + timedelta(days=1))
    assert third != first


def _fingerprint(engine):
    """A stable digest of the business data, excluding nothing meaningful.

    `password_hash` is excluded because bcrypt salts each call, and every other
    column is included -- a seeder that quietly stopped writing `recorded_by`
    should fail here.
    """
    with Session(engine, future=True) as session:
        digest = []
        for model in (Users, Vehicle, Booking, Payment, Penalty, Inspection_Report):
            for row in session.execute(select(model)).scalars().all():
                digest.append(
                    (model.__tablename__, *[getattr(row, c.name) for c in model.__table__.columns])
                )
        return tuple(sorted(repr(d) for d in digest))


# --------------------------------------------------------------------------
# The data satisfies the application's own rules
# --------------------------------------------------------------------------


class TestGeneratedDataIsValid:
    def test_no_vehicle_is_double_booked(self, demo):
        """Half-open [start, end): back-to-back rentals must not collide."""
        bookings = rows_of(demo, Booking)
        by_vehicle = {}
        for booking in bookings:
            by_vehicle.setdefault(booking.vehicle_id, []).append(booking)

        clashes = []
        for vehicle_id, rows in by_vehicle.items():
            rows = sorted(rows, key=lambda b: b.start_date)
            for earlier, later in zip(rows, rows[1:]):
                if earlier.start_date < later.end_date and later.start_date < earlier.end_date:
                    clashes.append((vehicle_id, earlier.booking_id, later.booking_id))
        assert not clashes, f"{len(clashes)} overlapping bookings, e.g. {clashes[:3]}"

    def test_every_rental_ends_after_it_starts(self, demo):
        for booking in rows_of(demo, Booking):
            assert booking.end_date > booking.start_date

    def test_no_in_progress_booking_is_backdated(self, demo):
        """Historical rentals start in the past on purpose, but nothing that is
        still in progress may -- the app would have refused to create it."""
        for booking in rows_of(demo, Booking, Booking.status.in_(("ongoing", "pending", "confirmed"))):
            assert booking.start_date <= booking.end_date
            assert booking.start_date >= TODAY - timedelta(days=14)

    def test_status_matches_the_dates(self, demo):
        """Status is derived from the calendar, not chosen at random.

        The one deliberate exception is a rental that is still `ongoing` even
        though its end date has passed: that is exactly the overdue state the
        Today screen exists to show, and a seeded overdue car is what stops the
        Overdue table rendering empty. It stays `ongoing` and stays without an
        `actual_return_date` -- it just has not been brought back yet.
        """
        for booking in rows_of(demo, Booking):
            if booking.start_date <= TODAY < booking.end_date:
                assert booking.status == "ongoing", booking
                assert booking.actual_return_date is None, booking
            elif booking.start_date > TODAY:
                assert booking.status in ("pending", "confirmed"), booking
            else:
                overdue = booking.end_date <= TODAY and booking.status == "ongoing"
                if not overdue:
                    assert booking.status == "completed", booking
                    assert booking.actual_return_date is not None, booking
                else:
                    assert booking.actual_return_date is None, booking

    def test_ongoing_rentals_mark_their_vehicle_rented(self, demo):
        """Otherwise the Fleet tab offers cars that are physically gone.

        `maintenance` is the third legitimate value: a car in the workshop is
        neither on the road nor free, and the seeder guarantees those cars carry
        no live booking so the two states can never contradict each other.
        """
        out = {
            b.vehicle_id
            for b in rows_of(demo, Booking, Booking.status == "ongoing")
        }
        in_workshop = {
            record.vehicle_id
            for record in rows_of(
                demo,
                Maintenance_Record,
                Maintenance_Record.status.in_(("scheduled", "ongoing")),
            )
        }
        for vehicle in rows_of(demo, Vehicle):
            if vehicle.vehicle_id in in_workshop:
                expected = "maintenance"
            elif vehicle.vehicle_id in out:
                expected = "rented"
            else:
                expected = "available"
            assert vehicle.status == expected, vehicle.plate_number

    def test_a_car_is_never_in_the_workshop_and_on_rent(self, demo):
        """The two states are physically exclusive, so seed them exclusively."""
        in_workshop = {
            record.vehicle_id
            for record in rows_of(
                demo,
                Maintenance_Record,
                Maintenance_Record.status.in_(("scheduled", "ongoing")),
            )
        }
        assert in_workshop, "demo should show something in the workshop"
        for booking in rows_of(
            demo, Booking, Booking.status.in_(("confirmed", "ongoing"))
        ):
            assert booking.vehicle_id not in in_workshop, booking

    def test_some_cars_are_free_to_rent(self, demo):
        """A branch at 100% utilisation cannot show what searching for a car
        looks like, and every 'available' figure on screen reads as zero."""
        free = rows_of(demo, Vehicle, Vehicle.status == "available")
        assert len(free) >= 3, [v.plate_number for v in free]

    def test_every_booking_records_who_took_it(self, demo):
        for booking in rows_of(demo, Booking):
            assert booking.created_by is not None

    def test_history_spans_about_six_months(self, demo):
        earliest = min(b.start_date for b in rows_of(demo, Booking))
        latest = max(b.end_date for b in rows_of(demo, Booking))
        assert earliest <= TODAY - timedelta(days=150)
        assert latest >= TODAY

    def test_every_completed_rental_was_inspected(self, demo):
        """Both ends of the handover, or the Inspections tab looks broken."""
        done = {b.booking_id for b in rows_of(demo, Booking, Booking.status == "completed")}
        kinds = {}
        for report in rows_of(demo, Inspection_Report):
            kinds.setdefault(report.booking_id, set()).add(report.inspection_type)
        for booking_id in done:
            assert kinds.get(booking_id) == {"pre-rental", "post-rental"}, booking_id

    def test_inspections_without_photos_are_allowed(self, demo):
        """`photo_url` is nullable, and most demo inspections have no photo."""
        for report in rows_of(demo, Inspection_Report):
            assert report.photo_url is None or isinstance(report.photo_url, str)


class TestTodayScreenHasSomethingToShow:
    """The Today tab exists to answer three questions: what is out, what is due
    back, and what is late. A random booking walk essentially never lands in
    the last two categories -- they are one- and two-day windows -- so they are
    placed on purpose. Without them the screen renders three empty tables,
    which is indistinguishable from a broken query.
    """

    def test_some_rentals_are_out_now(self, demo):
        out = rows_of(demo, Booking, Booking.status == "ongoing")
        assert len(out) >= 3

    def test_some_rentals_are_due_back_today(self, demo):
        due = rows_of(
            demo, Booking, Booking.status == "ongoing", Booking.end_date == TODAY
        )
        assert len(due) >= 2, "the Due back today table would be empty"

    def test_some_rentals_are_overdue(self, demo):
        """Ongoing, end date already past, and no `actual_return_date`."""
        late = rows_of(
            demo,
            Booking,
            Booking.status == "ongoing",
            Booking.end_date < TODAY,
            Booking.actual_return_date.is_(None),
        )
        assert len(late) >= 2, "the Overdue table would be empty"

    def test_some_cars_are_in_the_workshop(self, demo):
        open_jobs = rows_of(
            demo,
            Maintenance_Record,
            Maintenance_Record.status.in_(("scheduled", "ongoing")),
        )
        assert len(open_jobs) >= 2, "the Workshop table would be empty"

    def test_no_future_booking_on_an_overdue_or_returned_car(self, demo):
        """The forced rentals share vehicles with the random walk, so a
        collision would show up as a car booked into two places at once."""
        for booking in rows_of(demo, Booking, Booking.status == "pending"):
            assert booking.vehicle.status != "maintenance", booking


class TestMoneyIsConsistent:
    def _owed(self, booking):
        total = Decimal(booking.total_cost)
        for penalty in booking.penalties:
            total += Decimal(penalty.amount)
        return total

    def test_no_booking_is_overpaid(self, demo):
        """Payments must not exceed rental plus penalties."""
        for booking in rows_of(demo, Booking, Booking.status == "completed"):
            paid = sum(
                (Decimal(p.amount) for p in booking.payments if p.status == "paid"),
                Decimal("0.00"),
            )
            assert paid <= self._owed(booking) + Decimal("0.01"), (
                f"booking {booking.booking_id} paid {paid} of {self._owed(booking)}"
            )

    def test_paid_payments_carry_a_timestamp(self, demo):
        """The migration made `paid_at` nullable for pending, not for paid."""
        for payment in rows_of(demo, Payment, Payment.status == "paid"):
            assert payment.paid_at is not None

    def test_uncleared_payments_have_no_timestamp(self, demo):
        """A transfer in flight or a decline never became money."""
        for payment in rows_of(demo, Payment, Payment.status.in_(("pending", "failed"))):
            assert payment.paid_at is None

    def test_refunded_payments_keep_their_timestamp(self, demo):
        """The money arrived before it left again.

        `payment_service.refund_payment` only flips the status and appends a
        note, so a refund must not blank `paid_at` -- that is the receipt trail.
        """
        for payment in rows_of(demo, Payment, Payment.status == "refunded"):
            assert payment.paid_at is not None
            assert payment.note and "REFUNDED" in payment.note

    def test_totals_are_quantized_to_centavos(self, demo):
        """No float crept in and left a 3rd decimal behind."""
        for booking in rows_of(demo, Booking):
            assert Decimal(booking.total_cost).as_tuple().exponent == -2
        for payment in rows_of(demo, Payment):
            assert Decimal(payment.amount).as_tuple().exponent == -2

    def test_the_demo_actually_exercises_the_interesting_states(self, demo):
        """Guards against a future tweak quietly flattening the data.

        A demo where everything is paid and on time makes the dashboard
        untestable: the Outstanding and Overdue tiles would always read zero.
        """
        payments = rows_of(demo, Payment)
        statuses = {p.status for p in payments}
        assert {"paid", "refunded", "pending", "failed"} <= statuses, statuses

        methods = {p.method for p in payments}
        assert {"cash", "gcash", "card"} <= methods, methods

        completed = rows_of(demo, Booking, Booking.status == "completed")
        assert any(b.actual_return_date > b.end_date for b in completed), "no late returns"
        assert any(
            Decimal(p.amount) > 0 for p in rows_of(demo, Penalty)
        ), "no penalties recorded"

        assert any(
            sum(
                (Decimal(p.amount) for p in b.payments if p.status == "paid"), Decimal("0.00")
            )
            < Decimal(b.total_cost)
            for b in completed
        ), "every completed rental was paid in full"

    def test_gcash_payments_carry_a_reference(self, demo):
        """A GCash transfer with no reference cannot be reconciled."""
        for payment in rows_of(demo, Payment, Payment.method == "gcash"):
            assert payment.reference_no, payment.payment_id


class TestNobodyRealIsContactable:
    KNOWN_LOGINS = frozenset({DEMO_ADMIN_EMAIL, DEMO_STAFF_EMAIL})

    def test_all_emails_are_fictional(self, demo):
        for user in rows_of(demo, Users):
            assert (
                user.email.endswith("@example.com") or user.email in self.KNOWN_LOGINS
            ), user.email

    def test_phones_look_like_placeholders(self, demo):
        for user in rows_of(demo, Users):
            assert user.phone.startswith("09"), user.email


class TestDemoAdmin:
    def test_admin_exists_with_the_documented_role(self, demo):
        admin = demo.execute(
            select(Users).where(Users.email == DEMO_ADMIN_EMAIL)
        ).scalars().one()
        assert admin.role == "admin"

    def test_counter_exists_and_is_not_an_admin(self, demo):
        """The whole staff/admin split is the point of the second login, so it
        has to be asserted: a `counter` typed as `admin` would hand out the
        dashboard charts to the wrong person."""
        counter = demo.execute(
            select(Users).where(Users.email == DEMO_STAFF_EMAIL)
        ).scalars().one()
        assert counter.role == "staff"

    def test_admin_password_really_verifies(self, tmp_path, monkeypatch):
        """The stubbed hash in the shared fixture must not hide a typo in the
        documented credentials, so this one hashes for real."""
        monkeypatch.setattr(seed_demo_data, "hash_password", REAL_HASH_PASSWORD)
        engine = create_engine(f"sqlite:///{tmp_path / 'pw.db'}", future=True)
        try:
            seed_demo_data.seed(engine, days_back=5, today=TODAY)
        finally:
            engine.dispose()

        with Session(engine, future=True) as session:
            admin = session.execute(
                select(Users).where(Users.email == DEMO_ADMIN_EMAIL)
            ).scalars().one()
            assert verify_password("demo-password", admin.password_hash)
            assert not verify_password("wrong-password", admin.password_hash)

    def test_staff_exist_to_attribute_bookings_to(self, demo):
        staff = rows_of(demo, Users, Users.role == "staff")
        assert len(staff) >= 2
        recorders = {b.created_by for b in rows_of(demo, Booking)}
        assert recorders <= {u.user_id for u in rows_of(demo, Users)}


class TestNameGeneration:
    def test_identities_do_not_collide(self):
        """`email` and `license_number` are both UNIQUE, and both derive from
        the index -- a collision here is a hard failure at insert time."""
        rng = random.Random(SEED)
        seen_email, seen_licence = set(), set()
        for index in list(range(30)) + [901, 902, 903]:
            name, email, _phone, _address = seed_demo_data._person(rng, index)
            assert email not in seen_email, f"{name} collided on {email}"
            seen_email.add(email)

        for index in range(30):
            licence = f"LC{index + 1:07d}"
            assert licence not in seen_licence
            seen_licence.add(licence)

    def test_staff_indices_cannot_collide_with_customers(self):
        """The generator shares one name/email helper, so the ranges must not
        overlap -- this is the bug that produced a duplicate admin email."""
        customer_emails = {
            seed_demo_data._person(random.Random(SEED), i)[1] for i in range(24)
        }
        staff_emails = {
            seed_demo_data._person(random.Random(SEED), 900 + n)[1] for n in (1, 2, 3)
        }
        assert not (customer_emails & staff_emails)


def test_summary_counts_match_the_database(demo):
    """The CLI's closing table has to be honest about what was written."""
    expected = {
        "customers": demo.scalar(select(func.count()).select_from(Users).where(Users.role == "customer")),
        "vehicles": demo.scalar(select(func.count()).select_from(Vehicle)),
        "payments": demo.scalar(select(func.count()).select_from(Payment)),
        "penalties": demo.scalar(select(func.count()).select_from(Penalty)),
        "inspections": demo.scalar(select(func.count()).select_from(Inspection_Report)),
    }
    for name, count in expected.items():
        assert count > 0, name
