from datetime import date, timedelta

from app.database import SessionLocal
from app.models import Booking, Penalty, Users, Vehicle
from app.services import booking_service
from app.services.receipt_service import generate_receipt_pdf


def _print_all_ok() -> None:
    print("\n== ALL OK ==")


def main() -> None:
    session = SessionLocal()
    try:
        customer = session.query(Users).filter_by(email="carlo.cust@rental.ph").one()
        staff = session.query(Users).filter_by(email="juan.staff@rental.ph").one()
        vehicle = session.query(Vehicle).filter_by(plate_number="TOY1234").one()

        print("== 1. Create booking ==")
        start = date(2026, 12, 1)
        end = date(2026, 12, 5)
        booking = booking_service.create_booking(session, customer, vehicle, start, end)
        session.commit()
        print(f"  Booking {booking.booking_id}: {booking.status}, "
              f"{start} -> {end}, Php {booking.total_cost}")

        print("\n== 2. Check overlap is blocked ==")
        try:
            booking_service.create_booking(session, customer, vehicle, date(2026, 12, 3), date(2026, 12, 4))
            print("  FAIL: overlapping booking was allowed")
        except ValueError as exc:
            print(f"  OK blocked: {exc}")

        print("\n== 3. Check-in (pre-rental inspection) ==")
        report = booking_service.check_in(session, booking, staff, mileage_reading=42500)
        session.commit()
        print(f"  Inspection {report.inspection_id}: type={report.inspection_type}, "
              f"mileage={report.mileage_reading}, fuel={report.fuel_level}, "
              f"booking status={booking.status}")

        print("\n== 4. Check-out with late return + damage ==")
        late_return = end + timedelta(days=2)
        booking_service.check_out(
            session,
            booking,
            staff,
            actual_return_date=late_return,
            mileage_reading=42900,
            fuel_level="half",
            damage_notes="Light scratch on bumper.",
            late_penalty_per_day=1500.0,
        )
        session.commit()

        penalties = session.query(Penalty).filter_by(booking_id=booking.booking_id).all()
        print(f"  Booking status: {booking.status}, actual return: {booking.actual_return_date}")
        for p in penalties:
            print(f"  Penalty: {p.penalty_type} - Php {p.amount} ({p.description})")
        assert any(p.penalty_type == "late_return" for p in penalties)
        assert any(p.penalty_type == "damage" for p in penalties)

        print("\n== 5. Generate PDF receipt ==")
        filepath = generate_receipt_pdf(session, booking)
        session.commit()
        print(f"  Receipt saved: {filepath}")

        print("\n== 6. Verify final state ==")
        booking = session.query(Booking).filter_by(booking_id=booking.booking_id).one()
        total = float(booking.total_cost) + sum(float(p.amount) for p in penalties)
        print(f"  Booking status: {booking.status}")
        print(f"  Base cost: Php {booking.total_cost}")
        print(f"  Penalties: {len(penalties)}")
        print(f"  Grand total: Php {total:.2f}")

        _print_all_ok()
    finally:
        session.close()


if __name__ == "__main__":
    main()