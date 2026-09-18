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

    conf = AvailabilityChecker.find_conflict(date(2026, 9, 5), date(2026, 9, 9), existing)
    assert conf is not None, "edge-touch must count as conflict"


def test_database_round_trip() -> None:
    from sqlalchemy import func

    from app.database import SessionLocal
    from app.models import Booking, Vehicle
    from app.utils.security import verify_password

    session = SessionLocal()
    try:
        total_vehicles = session.query(func.count(Vehicle.vehicle_id)).scalar()
        assert total_vehicles == 5, f"expected 5 vehicles, found {total_vehicles}"

        booking = session.query(Booking).filter_by(status="completed").first()
        assert booking is not None, "no completed booking seeded"
        print(f"Completed booking #{booking.booking_id}: {booking.vehicle.make} {booking.vehicle.model}, "
              f"paid={booking.payment.amount if booking.payment else None}, "
              f"penalties={len(booking.penalties)}, inspections={len(booking.inspections)}")

        assert booking.vehicle.model == "Vios"
        assert booking.payment is not None
        assert booking.penalties, "expected at least one penalty on completed booking"

        from app.models import Users

        assert verify_password("customer123", session.query(Users).filter_by(role="customer").one().password_hash)
        print("Password hash/bcrypt check: OK")
    finally:
        session.close()


if __name__ == "__main__":
    test_rate_polymorphism()
    test_rental_calculator()
    test_availability()
    test_database_round_trip()
    print("\nALL TESTS PASSED")