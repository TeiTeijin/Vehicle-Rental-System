import os
import secrets
from datetime import date, datetime

from sqlalchemy import func

from app.database import get_session
from app.models import (
    Booking,
    Inspection_Report,
    Maintenance_Record,
    Payment,
    Penalty,
    Users,
    Vehicle,
    Vehicle_Category,
)
from app.utils.security import hash_password

CATEGORIES = [
    {"name": "Car", "multiplier": "1.00"},
    {"name": "Motorcycle", "multiplier": "0.70"},
]

VEHICLES = [
    {"make": "Toyota", "model": "Vios", "year": 2022, "plate": "TOY1234", "daily_rate": "2500.00", "mileage": 42000, "category": "Car"},
    {"make": "Honda", "model": "Civic", "year": 2021, "plate": "CIV1234", "daily_rate": "3000.00", "mileage": 38000, "category": "Car"},
    {"make": "Toyota", "model": "Camry", "year": 2020, "plate": "CAM1234", "daily_rate": "3500.00", "mileage": 51000, "category": "Car"},
    {"make": "Honda", "model": "Click 125i", "year": 2023, "plate": "CLK1234", "daily_rate": "800.00", "mileage": 12000, "category": "Motorcycle"},
    {"make": "Yamaha", "model": "NMAX 155", "year": 2022, "plate": "NMA1234", "daily_rate": "1000.00", "mileage": 15000, "category": "Motorcycle"},
]

USERS = [
    {
        "full_name": "Juan Dela Cruz",
        "email": "juan.staff@rental.ph",
        "phone": "09171234567",
        "role": "staff",
        "address": "123 Quezon Blvd, Manila",
        "license": "D03-1234-567",
        "license_expiry": date(2028, 1, 15),
    },
    {
        "full_name": "Maria Santos",
        "email": "maria.admin@rental.ph",
        "phone": "09179876543",
        "role": "admin",
        "address": "456 Luna St, Makati",
        "license": "D01-7654-321",
        "license_expiry": date(2027, 6, 30),
    },
    {
        "full_name": "Carlo Reyes",
        "email": "carlo.cust@rental.ph",
        "phone": "09175551234",
        "role": "customer",
        "address": "789 Mabini Ave, Pasig",
        "license": "D02-5555-000",
        "license_expiry": date(2029, 3, 22),
    },
]

_PASSWORD_ENV = {
    "staff": "SEED_PASSWORD_STAFF",
    "admin": "SEED_PASSWORD_ADMIN",
    "customer": "SEED_PASSWORD_CUSTOMER",
}


def _seed_password(role: str) -> str:
    env_name = _PASSWORD_ENV[role]
    value = os.getenv(env_name)
    if value:
        return value
    generated = secrets.token_urlsafe(16)
    print(f"[seed] {env_name} not set; generated password: {generated}")
    return generated


def seed_categories_and_vehicles(session) -> None:
    if session.query(func.count(Vehicle_Category.category_id)).scalar():
        return

    category_by_name = {}
    for cat in CATEGORIES:
        row = Vehicle_Category(category_name=cat["name"], base_rate_multiplier=cat["multiplier"])
        session.add(row)
        session.flush()
        category_by_name[cat["name"]] = row

    for v in VEHICLES:
        session.add(
            Vehicle(
                category_id=category_by_name[v["category"]].category_id,
                make=v["make"],
                model=v["model"],
                year=v["year"],
                plate_number=v["plate"],
                daily_rate=v["daily_rate"],
                mileage=v["mileage"],
                status="available",
                created_at=datetime.now(),
            )
        )

    session.flush()


def seed_users(session) -> None:
    if session.query(func.count(Users.user_id)).scalar():
        return

    for u in USERS:
        session.add(
            Users(
                full_name=u["full_name"],
                email=u["email"],
                phone=u["phone"],
                password_hash=hash_password(_seed_password(u["role"])),
                address=u["address"],
                license_number=u["license"],
                license_expiry=u["license_expiry"],
                role=u["role"],
                created_at=datetime.now(),
            )
        )

    session.flush()


def seed_bookings_and_children(session) -> None:
    if session.query(func.count(Booking.booking_id)).scalar():
        return

    customer = session.query(Users).filter_by(email="carlo.cust@rental.ph").one()
    staff = session.query(Users).filter_by(email="juan.staff@rental.ph").one()
    vios = session.query(Vehicle).filter_by(plate_number="TOY1234").one()
    civic = session.query(Vehicle).filter_by(plate_number="CIV1234").one()

    now = datetime.now()

    completed = Booking(
        user_id=customer.user_id,
        vehicle_id=vios.vehicle_id,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 5),
        actual_return_date=date(2026, 9, 6),
        total_cost="12500.00",
        status="completed",
        created_at=now,
    )
    session.add(completed)
    session.flush()

    session.add(
        Payment(
            booking_id=completed.booking_id,
            amount="12500.00",
            method="gcash",
            status="paid",
            paid_at=now,
        )
    )
    session.add(
        Penalty(
            booking_id=completed.booking_id,
            penalty_type="late_return",
            amount="1000.00",
            description="Returned 1 day after the scheduled return date.",
            created_at=now,
        )
    )
    session.add(
        Inspection_Report(
            booking_id=completed.booking_id,
            inspected_by=staff.user_id,
            inspection_type="pre-rental",
            mileage_reading=42100,
            fuel_level="full",
            damage_notes=None,
            photo_url="media/inspections/vios-pre.png",
            inspected_at=now,
        )
    )
    session.add(
        Inspection_Report(
            booking_id=completed.booking_id,
            inspected_by=staff.user_id,
            inspection_type="post-rental",
            mileage_reading=42320,
            fuel_level="half",
            damage_notes="Light scratch on front bumper.",
            photo_url="media/inspections/vios-post.png",
            inspected_at=now,
        )
    )

    pending = Booking(
        user_id=customer.user_id,
        vehicle_id=civic.vehicle_id,
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 4),
        actual_return_date=None,
        total_cost="12000.00",
        status="pending",
        created_at=now,
    )
    session.add(pending)

    camry = session.query(Vehicle).filter_by(plate_number="CAM1234").one()
    session.add(
        Maintenance_Record(
            vehicle_id=camry.vehicle_id,
            description="Oil change and brake pad replacement.",
            cost="4500.00",
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 3),
            status="scheduled",
        )
    )


def main() -> None:
    with get_session() as session:
        seed_categories_and_vehicles(session)
        seed_users(session)
        seed_bookings_and_children(session)

    print("Seed complete. Tables populated.")


if __name__ == "__main__":
    main()