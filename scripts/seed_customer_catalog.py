"""Seed what the customer app needs on top of the counter's database.

The vehicle catalog is NOT seeded: screens 1-4 read the vehicles the counter
already maintains, and photos come from the CarImages API. This script only
fills the gaps the online flow needs:

* BRANCH  -- the three pickup/return counters and their opening hours
* a demo shopper, with two bookings, so the calendar has something to grey out
  and the Bookings tab has something to list (opt-in, see --demo-customer)
* --remove-demo deletes that shopper again

Idempotent by design -- re-running refreshes the branch hours and never
duplicates a branch, customer or booking.

    python -m scripts.migrate_customer_booking     # BRANCH + BOOKING columns
    python -m scripts.seed_customer_catalog        # branches
    python -m scripts.seed_customer_catalog --demo-customer
"""

from __future__ import annotations

import argparse
import os
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import Booking, Branch, Payment, Users, Vehicle, Vehicle_Category
from app.services import booking_service
from app.services.errors import ServiceError
from app.utils.security import hash_password

#: Demo shopper for the customer app. Only created with --demo-customer.
DEMO_EMAIL = "demo@rentwheels.local"
DEMO_PASSWORD_ENV = "SEED_PASSWORD_CUSTOMER"
DEMO_PASSWORD_FALLBACK = "demo-password"

#: (name, address, open, close)
BRANCHES = [
    ("Makati City", "Ayala Avenue", time(7, 0), time(21, 0)),
    ("Quezon City", "North Avenue", time(6, 0), time(22, 0)),
    ("Pasay City", "Macapagal Boulevard", time(8, 0), time(20, 0)),
]

#: Demo bookings, as (days from today, length in days for the *past* one, paid
#: method). The upcoming one lands on a car with a photo, the completed one on
#: the next free car, so the list filters show two different buckets.
DEMO_UPCOMING = (6, 3, "card")
DEMO_COMPLETED = (-21, 4, "gcash")

#: Cars tried first for the demo bookings; anything available is accepted.
PREFERRED_MODELS = ("Innova", "Fortuner", "Vios", "City", "Xpander", "Mirage")


def seed_branches(session: Session) -> None:
    print("=== branches ===")
    for name, address, opens, closes in BRANCHES:
        row = (
            session.query(Branch)
            .filter(Branch.name == name, Branch.address == address)
            .one_or_none()
        )
        if row is None:
            session.add(
                Branch(
                    name=name,
                    address=address,
                    open_time=opens,
                    close_time=closes,
                    is_active=True,
                    created_at=datetime.now(),
                )
            )
            print(f"  + branch {name} \u2014 {address}")
        else:
            row.open_time, row.close_time, row.is_active = opens, closes, True
            print(f"  = branch {name} \u2014 {address} (hours refreshed)")
    session.flush()


def demo_customer(session: Session) -> Users | None:
    return session.query(Users).filter_by(email=DEMO_EMAIL).one_or_none()


def seed_demo_customer(session: Session) -> Users:
    print("=== demo shopper ===")
    password = os.getenv(DEMO_PASSWORD_ENV) or DEMO_PASSWORD_FALLBACK
    row = demo_customer(session)
    if row is not None:
        print(f"  = {DEMO_EMAIL} already exists")
        return row
    row = Users(
        full_name="Demo Customer",
        email=DEMO_EMAIL,
        phone="09171234567",
        password_hash=hash_password(password),
        address="1 Ayala Avenue, Makati City",
        license_number="DEMO-00001",
        license_expiry=date(date.today().year + 5, 12, 31),
        guarantor_first_name="Rosa",
        guarantor_last_name="Santos",
        guarantor_mobile="09181234567",
        emergency_contact="Rosa Santos - 09181234567",
        role="customer",
        # Signing up through the app is the only way a row gets this stamp.
        source="online",
        is_active=True,
        created_at=datetime.now(),
    )
    session.add(row)
    session.flush()
    print(f"  + {DEMO_EMAIL} (password: {password})")
    return row


def _candidate_vehicles(session: Session, taken: set[int]) -> list[Vehicle]:
    """Available cars, the nice-looking ones first."""
    rows = (
        session.query(Vehicle)
        .join(Vehicle_Category, Vehicle.category_id == Vehicle_Category.category_id)
        .filter(
            Vehicle_Category.category_name == "Car",
            Vehicle.status == "available",
        )
        .order_by(Vehicle.vehicle_id)
        .all()
    )
    preferred = [
        v for v in rows if any(model in v.model for model in PREFERRED_MODELS)
    ]
    rest = [v for v in rows if v not in preferred]
    return [v for v in preferred + rest if v.vehicle_id not in taken][:8]


def _book(
    session: Session, customer: Users, branch: Branch, vehicle: Vehicle,
    offset: int, length_days: int, method: str,
) -> Booking:
    """One booking, made through the same service the app uses."""
    pickup = datetime.combine(
        date.today() + timedelta(days=offset), branch.open_time
    ) + timedelta(hours=2)
    drop_off = pickup + timedelta(days=length_days) - timedelta(hours=1)
    return booking_service.create_online_booking(
        session,
        user=customer,
        vehicle=vehicle,
        pickup_dt=pickup,
        return_dt=drop_off,
        pickup_branch=branch,
        return_branch=branch,
        payment_method=method,
        card_last4="4242" if method == "card" else None,
        payment_reference=f"DEMO-{vehicle.plate_number}",
    )


def _complete(session: Session, booking: Booking) -> None:
    """Close a demo booking the way the counter does at return."""
    booking.status = "completed"
    booking.actual_return_date = booking.return_dt.date() if booking.return_dt else None
    session.flush()


def seed_demo_bookings(session: Session, customer: Users) -> None:
    print("=== demo bookings ===")
    if session.query(func.count(Booking.booking_id)).filter(
        Booking.user_id == customer.user_id
    ).scalar():
        print(f"  = {DEMO_EMAIL} already has bookings, nothing to seed")
        return

    branch = (
        session.execute(
            select(Branch).where(Branch.is_active.is_(True)).order_by(Branch.name)
        )
        .scalars()
        .first()
    )
    if branch is None:
        print("  ! no active branch -- run this again after the branch step")
        return

    wanted = (
        ("upcoming", DEMO_UPCOMING),
        ("completed", DEMO_COMPLETED),
    )
    taken: set[int] = set()
    for label, (offset, length, method) in wanted:
        for vehicle in _candidate_vehicles(session, taken):
            try:
                booking = _book(
                    session, customer, branch, vehicle, offset, length, method
                )
            except ServiceError:
                # Busy that week (or outside opening hours): try the next car.
                session.rollback()
                continue
            taken.add(vehicle.vehicle_id)
            if label == "completed":
                _complete(session, booking)
            print(
                f"  + {label:<9} {booking.reference_code} "
                f"{vehicle.make} {vehicle.model} {vehicle.year} "
                f"({booking.start_date} - {booking.end_date}, {method})"
            )
            break
        else:
            print(f"  ! no free vehicle for the {label} booking")


def remove_demo(session: Session) -> None:
    print("=== remove demo shopper ===")
    row = demo_customer(session)
    if row is None:
        print(f"  = {DEMO_EMAIL} is not there")
        return
    booking_ids = [
        booking_id
        for (booking_id,) in session.query(Booking.booking_id).filter(
            Booking.user_id == row.user_id
        )
    ]
    if booking_ids:
        session.query(Payment).filter(Payment.booking_id.in_(booking_ids)).delete(
            synchronize_session=False
        )
        session.query(Booking).filter(Booking.booking_id.in_(booking_ids)).delete(
            synchronize_session=False
        )
    session.delete(row)
    session.flush()
    print(f"  - {DEMO_EMAIL} and {len(booking_ids)} booking(s) deleted")


def report(session: Session) -> None:
    print()
    print("=== catalog ===")
    print(f"  branches:  {session.query(func.count(Branch.branch_id)).scalar()}")
    print(
        f"  vehicles:  {session.query(func.count(Vehicle.vehicle_id)).scalar()} "
        "(the counter's catalog, read as is)"
    )
    print(f"  customers: {session.query(func.count(Users.user_id)).scalar()}")
    print(f"  bookings:  {session.query(func.count(Booking.booking_id)).scalar()}")
    print()
    print("  run the app:  python -m app.ui.main_window")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--demo-customer",
        action="store_true",
        help=f"also create {DEMO_EMAIL} with two bookings",
    )
    parser.add_argument(
        "--remove-demo",
        action="store_true",
        help=f"delete {DEMO_EMAIL} and its bookings instead",
    )
    args = parser.parse_args()

    with get_session() as session:
        if args.remove_demo:
            remove_demo(session)
        else:
            seed_branches(session)
            if args.demo_customer:
                # Committed before the bookings: a booking that clashes on one
                # car rolls the session back and tries the next one, and the
                # shopper must survive that.
                customer = seed_demo_customer(session)
                session.commit()
                seed_demo_bookings(session, customer)
        report(session)


if __name__ == "__main__":
    main()
