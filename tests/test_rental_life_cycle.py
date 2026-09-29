"""End-to-end check of a rental's whole life, against a real SQLite file.

The other suites run in memory on a schema built by `create_all`, which is
fast but means every test sees a database that only ever existed for the
duration of that test. This one uses a file on disk, so the tables have to
survive being created, written, and read back across several sessions, and a
flush/expire mistake shows up as a failure rather than passing silently.

What this is for is the *sequence*: book, confirm, deposit, collect, return
late with damage, settle, re-book the same dates, print the receipt. Each step
assumes the previous one left the data in a state the next can trust, and it
is the ordering that no single-function test can check.
"""

from datetime import date, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Booking, Users, Vehicle, Vehicle_Category
from app.services import booking_service, payment_service
from app.services.receipt_service import generate_receipt_pdf, receipt_rows

START = date(2026, 9, 1)
END = date(2026, 9, 5)


def test_a_full_rental_life(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'life.db').as_posix()}", future=True)
    Base.metadata.create_all(engine)

    with Session(engine, future=True) as session:
        category = Vehicle_Category(category_name="Car", base_rate_multiplier=1.00)
        session.add(category)
        session.flush()

        customer = Users(
            full_name="Ana Reyes",
            email="ana@example.com",
            phone="09171234567",
            password_hash="x",
            address="1 Main St",
            license_number="LIC-0001",
            license_expiry=date(2027, 1, 1),
            role="customer",
            created_at=date(2026, 1, 1),
        )
        staff = Users(
            full_name="Rico Diaz",
            email="rico@example.com",
            password_hash="x",
            address="2 Main St",
            license_number="LIC-0002",
            license_expiry=date(2027, 1, 1),
            role="staff",
            created_at=date(2026, 1, 1),
        )
        vehicle = Vehicle(
            category_id=category.category_id,
            make="Toyota",
            model="Vios",
            year=2022,
            plate_number="LIF-001",
            daily_rate="2500.00",
            mileage=40000,
            status="available",
            created_at=date(2026, 1, 1),
        )
        session.add_all([customer, staff, vehicle])
        session.flush()

        # 1. Book it. A future booking must not change what the car is doing
        #    today, so the vehicle is still `available` afterwards.
        booking = booking_service.create_booking(
            session, customer, vehicle, START, END, created_by=staff.user_id
        )
        assert booking.status == "pending"
        assert booking.total_cost == 10000       # 4 days x 2,500
        assert vehicle.status == "available"
        assert booking_service.is_reserved_between(session, vehicle, START, END)

        # 2. Confirm, and take a deposit.
        booking_service.confirm_booking(session, booking)
        payment_service.record_payment(
            session, booking, 4000, "gcash", recorded_by=staff.user_id, reference_no="GC1"
        )
        assert payment_service.booking_balance(session, booking).balance == 6000

        # 3. Hand over the keys.
        booking_service.check_in(session, booking, staff, 41000, "full")
        assert booking.status == "ongoing"
        assert vehicle.status == "rented"

        # 4. Return it a day late, half a tank down, with a scratched bumper.
        booking_service.check_out(
            session, booking, staff, END + timedelta(days=1), 41500, "half",
            damage_notes="Scratched bumper", damage_charge=1500,
            require_settlement=False,
        )
        assert booking.status == "completed"
        assert vehicle.status == "available"
        assert vehicle.mileage == 41500

        # 5. The bill: 10,000 rental + 1,000 late + 1,500 damage, less 4,000 paid.
        balance = payment_service.booking_balance(session, booking)
        assert (balance.total_due, balance.amount_paid, balance.balance) == (
            12500, 4000, 8500
        )

        # 6. Settle it.
        payment_service.record_payment(
            session, booking, 8500, "cash", recorded_by=staff.user_id
        )
        assert payment_service.booking_balance(session, booking).is_settled

        # 7. The dates are free again.
        assert booking_service.is_reserved_between(session, vehicle, START, END) is False
        second = booking_service.create_booking(session, customer, vehicle, START, END)
        assert second.status == "pending"
        assert second.booking_id != booking.booking_id

        # 8. The receipt reads as settled and names the damage.
        text = "\n".join(r.text for r in receipt_rows(session, booking))
        assert "PAID IN FULL" in text
        assert "Scratched bumper" in text
        path = generate_receipt_pdf(session, booking, dest_dir=tmp_path)
        with open(path, "rb") as fh:
            assert fh.read(4) == b"%PDF"

        session.commit()

        # Plain values, because the ORM objects expire when the session closes
        # and cannot be touched from the verification block below.
        staff_id = staff.user_id
        vehicle_id = vehicle.vehicle_id

    # Everything above is gone once the session closes unless it was really
    # written, so a fresh session reading it back is the actual assertion that
    # the rows landed.
    with Session(engine, future=True) as verify:
        stored = verify.execute(
            select(Booking).order_by(Booking.booking_id)
        ).scalars().all()
        assert len(stored) == 2
        assert stored[0].status == "completed"
        assert stored[0].actual_return_date == END + timedelta(days=1)
        assert stored[0].created_by == staff_id
        assert len(stored[0].payments) == 2
        assert len(stored[0].penalties) == 2

        reread = verify.get(Vehicle, vehicle_id)
        assert reread.status == "available"
        assert reread.mileage == 41500

    engine.dispose()
