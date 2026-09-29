import os
from collections import namedtuple
from datetime import date, datetime

from app.domain.availability import AvailabilityChecker
from app.domain.rental_calculator import RentalCalculator
from app.domain.vehicle_types import Car, Motorcycle

Booking = namedtuple("Booking", ["start_date", "end_date"])


def test_rate_polymorphism() -> None:
    car = Car("Toyota", "Vios", 2022, "2500.00")
    bike = Motorcycle("Honda", "Click 125i", 2023, "2500.00")

    car_cost = car.calculate_rate(5)
    bike_cost = bike.calculate_rate(5)

    print(f"Car 5 days  @2500/day = {car_cost}")
    print(f"Moto 5 days @2500/day = {bike_cost}")

    assert car_cost == 12500, f"expected 12500, got {car_cost}"
    assert bike_cost == 8750, f"expected 8750, got {bike_cost}"
    assert car_cost != bike_cost, "polymorphism failed: both costs identical"

    long_car = Car("Toyota", "Camry", 2020, "3000.00")
    print(f"Car 7+ days with discount = {long_car.calculate_rate(7)}")
    assert long_car.calculate_rate(7) == 18900, "long-term discount failed"


def test_rental_calculator() -> None:
    car = Car("Toyota", "Vios", 2022, "2500.00")
    start = date(2026, 9, 1)
    end = date(2026, 9, 6)
    total = RentalCalculator.total_cost(car, start, end)
    print(f"Rental 2026-09-01..06 = {total}")
    assert total == 12500, f"expected 12500, got {total}"

    try:
        RentalCalculator.total_cost(car, end, start)
        raise AssertionError("negative range should have raised")
    except ValueError:
        pass


def test_availability() -> None:
    existing = [Booking(date(2026, 9, 1), date(2026, 9, 5))]

    overlap = AvailabilityChecker.is_available(None, date(2026, 9, 4), date(2026, 9, 8), existing)
    print(f"Overlapping request available? {overlap}")
    assert overlap is False, "overlap must be rejected"

    clear = AvailabilityChecker.is_available(None, date(2026, 10, 1), date(2026, 10, 4), existing)
    print(f"Clear request available? {clear}")
    assert clear is True, "clear slot must be accepted"

    conf = AvailabilityChecker.find_conflict(date(2026, 9, 4), date(2026, 9, 9), existing)
    assert conf is not None, "a genuine overlap (4-8 Sep vs 1-5 Sep) must be a conflict"

    # Edge-touch is NOT a conflict: bookings are half-open [start, end), so a
    # car back on the 5th can be re-rented on the 5th. This used to assert the
    # opposite, which made same-day turnaround impossible.
    touch = AvailabilityChecker.find_conflict(date(2026, 9, 5), date(2026, 9, 8), existing)
    assert touch is None, "edge-touch must be allowed (half-open booking range)"


def test_database_round_trip() -> None:
    from sqlalchemy import func

    from app.database import SessionLocal
    from app.models import Booking, Vehicle
    from app.utils.security import verify_password
    from scripts.seed_data import VEHICLES

    session = SessionLocal()
    try:
        total_vehicles = session.query(func.count(Vehicle.vehicle_id)).scalar()
        assert total_vehicles == len(VEHICLES), (
            f"expected {len(VEHICLES)} vehicles from the seed list, found {total_vehicles}"
        )

        booking = session.query(Booking).filter_by(status="completed").first()
        assert booking is not None, "no completed booking seeded"
        print(f"Completed booking #{booking.booking_id}: {booking.vehicle.make} {booking.vehicle.model}, "
              f"paid={sum(p.amount for p in booking.payments if p.status == 'paid')}, "
              f"penalties={len(booking.penalties)}, inspections={len(booking.inspections)}")

        assert booking.vehicle.model == "Vios"
        assert booking.payments, "expected at least one payment on the completed booking"
        assert any(p.status == "paid" for p in booking.payments)
        assert booking.penalties, "expected at least one penalty on completed booking"

        from app.models import Users

        # seed_data.py generates a random password unless SEED_PASSWORD_CUSTOMER
        # is exported, so a hardcoded literal only passed by coincidence of a
        # previous manual export. Assert the hash is a real bcrypt hash instead,
        # and only verify the plaintext when the seed password is known.
        customer = session.query(Users).filter_by(role="customer").one()
        assert customer.password_hash.startswith("$2"), "customer password is not bcrypt-hashed"

        seed_password = os.getenv("SEED_PASSWORD_CUSTOMER")
        if seed_password:
            assert verify_password(seed_password, customer.password_hash), (
                "customer password does not match SEED_PASSWORD_CUSTOMER"
            )
            print("Password hash/bcrypt check: OK (matched SEED_PASSWORD_CUSTOMER)")
        else:
            print(
                "Password hash/bcrypt check: format OK "
                "(plaintext not verified; set SEED_PASSWORD_CUSTOMER to check it)"
            )
    finally:
        session.close()


if __name__ == "__main__":
    test_rate_polymorphism()
    test_rental_calculator()
    test_availability()
    test_database_round_trip()
    print("\nALL TESTS PASSED")