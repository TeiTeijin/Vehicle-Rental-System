from datetime import date, datetime

from sqlalchemy.orm import Session

from app.domain.availability import AvailabilityChecker
from app.domain.rental_calculator import RentalCalculator
from app.domain.vehicle_types import Car, Motorcycle
from app.models import Booking, Inspection_Report, Penalty, Vehicle


def _to_domain_vehicle(vehicle: Vehicle) -> Car | Motorcycle:
    category = getattr(vehicle, "category", None)
    if category and category.category_name == "Motorcycle":
        return Motorcycle(vehicle.make, vehicle.model, vehicle.year, vehicle.daily_rate)
    return Car(vehicle.make, vehicle.model, vehicle.year, vehicle.daily_rate)


def create_booking(
    session: Session,
    user,
    vehicle: Vehicle,
    start_date: date,
    end_date: date,
) -> Booking:
    if end_date <= start_date:
        raise ValueError("end_date must be after start_date")

    active = session.query(Booking).filter(
        Booking.vehicle_id == vehicle.vehicle_id,
        Booking.status.in_(["pending", "confirmed", "ongoing"]),
    ).all()

    if not AvailabilityChecker.is_available(vehicle, start_date, end_date, active):
        raise ValueError("Vehicle is not available for the requested dates")

    total_cost = RentalCalculator.total_cost(_to_domain_vehicle(vehicle), start_date, end_date)

    booking = Booking(
        user_id=user.user_id,
        vehicle_id=vehicle.vehicle_id,
        start_date=start_date,
        end_date=end_date,
        actual_return_date=None,
        total_cost=str(total_cost),
        status="pending",
        created_at=datetime.now(),
    )
    session.add(booking)
    session.flush()
    return booking


def check_in(
    session: Session,
    booking: Booking,
    inspector,
    mileage_reading: int,
    fuel_level: str = "full",
) -> Inspection_Report:
    if booking.status != "pending":
        raise ValueError(f"Booking must be pending to check in (current: {booking.status})")

    booking.status = "ongoing"

    report = Inspection_Report(
        booking_id=booking.booking_id,
        inspected_by=inspector.user_id,
        inspection_type="pre-rental",
        mileage_reading=mileage_reading,
        fuel_level=fuel_level,
        damage_notes=None,
        photo_url="",
        inspected_at=datetime.now(),
    )
    session.add(report)
    session.flush()
    return report


def check_out(
    session: Session,
    booking: Booking,
    inspector,
    actual_return_date: date,
    mileage_reading: int,
    fuel_level: str,
    damage_notes: str | None = None,
    photo_url: str = "",
    late_penalty_per_day: float = 1000.00,
) -> Inspection_Report:
    if booking.status != "ongoing":
        raise ValueError(f"Booking must be ongoing to check out (current: {booking.status})")

    booking.status = "completed"
    booking.actual_return_date = actual_return_date

    report = Inspection_Report(
        booking_id=booking.booking_id,
        inspected_by=inspector.user_id,
        inspection_type="post-rental",
        mileage_reading=mileage_reading,
        fuel_level=fuel_level,
        damage_notes=damage_notes,
        photo_url=photo_url,
        inspected_at=datetime.now(),
    )
    session.add(report)

    if actual_return_date > booking.end_date:
        days_late = (actual_return_date - booking.end_date).days
        apply_penalty(
            session,
            booking,
            penalty_type="late_return",
            amount=late_penalty_per_day * days_late,
            description=(
                f"Returned {days_late} day(s) after the scheduled return date."
            ),
        )

    if damage_notes:
        apply_penalty(
            session,
            booking,
            penalty_type="damage",
            amount=0.0,
            description=damage_notes,
        )

    session.flush()
    return report


def apply_penalty(
    session: Session,
    booking: Booking,
    penalty_type: str,
    amount: float,
    description: str,
) -> Penalty:
    if penalty_type not in ("late_return", "damage", "cleaning", "other"):
        raise ValueError(f"Unknown penalty type: {penalty_type}")

    penalty = Penalty(
        booking_id=booking.booking_id,
        penalty_type=penalty_type,
        amount=f"{amount:.2f}",
        description=description,
        created_at=datetime.now(),
    )
    session.add(penalty)
    session.flush()
    return penalty