from __future__ import annotations

import argparse
import random
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
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
from app.utils.money import CENT
from app.utils.security import hash_password

#: Fixed so the demo is reproducible. Changing this changes every screenshot.
SEED = 20260929

DEMO_ADMIN_EMAIL = "admin@rentwheels.local"

DEMO_STAFF_EMAIL = "counter@rentwheels.local"

#: How many cars sit in the workshop in the demo. They are held out of the
#: random booking walk, so a car is never both in the workshop and out on rent.
WORKSHOP_CARS = 2

#: The online share of new bookings today, and how far it has risen over the
#: 180-day window. `days_ago / 180` runs 1 -> 0 as history approaches the
#: present, so the ramp climbs towards the present. See `_channel`.
_ONLINE_SHARE_NOW = 0.45
_ONLINE_RAMP = 0.18

TODAYS_PLANS = [
    (2, 4),    # overdue by two days
    (5, 3),    # overdue by five days
    (0, 5),    # due back today
    (0, 2),    # due back today, short rental
    (-9, 12),  # still out, long rental
]

UNBOOKED_CARS = 3

#: Hostnames that must never be a demo target, whatever the flags say. The
#: Aiven project this was built against is the obvious one; the pattern catches
#: other Aiven projects and the usual MySQL cloud providers too.
FORBIDDEN_HOST_MARKERS = (
    "aiven.io",
    "aivencloud.com",
    "mysql.database.azure.com",
    "rds.amazonaws.com",
    "rds.amazonaws.com.cn",
)

FIRST_NAMES = [
    "Ana", "Ben", "Carla", "Diego", "Elena", "Fran", "Grace", "Hector",
    "Ivy", "Jonas", "Kyla", "Luis", "Mara", "Nico", "Olivia", "Paolo",
    "Quinn", "Rosa", "Sofia", "Tomas", "Ursula", "Vince", "Wendy", "Yves",
]
LAST_NAMES = [
    "Reyes", "Santos", "Cruz", "Bautista", "Ocampo", "Garcia", "Mendoza",
    "Torres", "Ramos", "Delgado", "Navarro", "Salazar", "Aquino", "Rivera",
    "Padilla", "Domingo", "Cabrera", "Evangelista", "Lazaro", "Manalo",
]
STREETS = [
    "Mabini St", "Rizal Ave", "Bonifacio St", "Del Pilar St", "Luna St",
    "Aguinaldo St", "Katipunan Ave", "Osmeña Blvd", "Quezon Ave", "Sumulong Hwy",
]

#: (make, model, year, plate, rate, category, seats, transmission, fuel, body,
#:  vehicle_class, engine_cc)
VEHICLE_SPECS = [
    # (make, model, year, plate, rate, category, seats, transmission, fuel, body, class, cc)
    ("Toyota", "Vios", 2022, "DEM-101", "2500.00", "Car", 5, "Automatic", "Petrol", "Sedan", "small", None),
    ("Honda", "Civic", 2021, "DEM-102", "3000.00", "Car", 5, "Manual", "Petrol", "Sedan", "small", None),
    ("Toyota", "Camry", 2020, "DEM-103", "3500.00", "Car", 5, "Automatic", "Hybrid", "Sedan", "medium", None),
    ("Toyota", "Fortuner", 2023, "DEM-104", "5500.00", "Car", 7, "Automatic", "Diesel", "SUV", "suv", None),
    ("Mitsubishi", "Xpander", 2022, "DEM-105", "3800.00", "Car", 7, "Automatic", "Petrol", "MPV", "van", None),
    ("Mazda", "CX-5", 2023, "DEM-106", "5200.00", "Car", 5, "Automatic", "Petrol", "SUV", "suv", None),
    ("Nissan", "Altima", 2021, "DEM-107", "3300.00", "Car", 5, "Automatic", "Petrol", "Sedan", "medium", None),
    ("Ford", "Explorer", 2022, "DEM-108", "5800.00", "Car", 7, "Automatic", "Petrol", "SUV", "suv", None),
    ("Toyota", "Innova", 2023, "DEM-109", "4000.00", "Car", 7, "Automatic", "Diesel", "MPV", "van", None),
    ("Honda", "City", 2022, "DEM-110", "2700.00", "Car", 5, "Automatic", "Petrol", "Sedan", "small", None),
    ("Isuzu", "D-Max", 2022, "DEM-111", "4800.00", "Car", 5, "Manual", "Diesel", "Pickup", "pickup", None),
    ("Mitsubishi", "Mirage", 2021, "DEM-112", "2300.00", "Car", 4, "Automatic", "Petrol", "Hatchback", "small", None),
    # The two classes with no body_style value. A pickup-bodied light truck is a
    # `Pickup` to the eye and a `truck` to a customer hiring one for a move, which is
    # exactly the distinction body_style cannot express.
    ("Isuzu", "F-Series", 2023, "DEM-113", "6200.00", "Car", 5, "Manual", "Diesel", "Pickup", "truck", None),
    ("Toyota", "Hilux", 2023, "DEM-114", "5900.00", "Car", 5, "Manual", "Diesel", "Pickup", "truck", None),
    ("Honda", "Click 125i", 2023, "DEM-201", "800.00", "Motorcycle", 2, "Automatic", "Petrol", "Underbone", "motorcycle", 125),
    ("Yamaha", "NMAX 155", 2022, "DEM-202", "1000.00", "Motorcycle", 2, "Automatic", "Petrol", "Scooter", "motorcycle", 155),
    ("Kawasaki", "Ninja 400", 2023, "DEM-203", "1400.00", "Motorcycle", 2, "Manual", "Petrol", "Underbone", "motorcycle", 400),
    ("Honda", "PCX 150", 2023, "DEM-204", "1200.00", "Motorcycle", 2, "Automatic", "Petrol", "Scooter", "motorcycle", 150),
    # FZ-S carries no number in its name, so the migration cannot infer its
    # displacement; written out here.
    ("Yamaha", "FZ-S", 2022, "DEM-205", "1100.00", "Motorcycle", 2, "Manual", "Petrol", "Underbone", "motorcycle", 250),
    ("Kymco", "P200", 2023, "DEM-206", "950.00", "Motorcycle", 2, "Automatic", "Petrol", "Scooter", "motorcycle", 200),
    ("Kawasaki", "Moto 600", 2023, "DEM-207", "1600.00", "Motorcycle", 2, "Manual", "Petrol", "Underbone", "motorcycle", 600),
]

#: `MaintenanceRecord.status` is an Enum column. `in_progress` is not one of its
#: values -- the open state is `ongoing` -- and writing it anyway raises a
#: LookupError on read, not on insert, so a typo here would surface much later.
MAINTENANCE_OPEN_STATUS = "ongoing"
MAINTENANCE_SCHEDULED_STATUS = "scheduled"
MAINTENANCE_OPEN_STATES = (MAINTENANCE_SCHEDULED_STATUS, MAINTENANCE_OPEN_STATUS)

MAINTENANCE_JOBS = [
    ("Oil and filter change", "3000.00"),
    ("Brake pad replacement", "4500.00"),
    ("Tire rotation", "1500.00"),
    ("Air conditioning service", "3500.00"),
    ("Battery replacement", "5500.00"),
    ("Suspension check", "4000.00"),
    ("Wheel alignment", "2000.00"),
    ("Transmission oil change", "5000.00"),
]

PENALTY_REASONS = {
    "late_return": [
        "Returned after the scheduled return date.",
        "Late return -- customer held the vehicle over the end of the rental.",
        "Returned a day late; the next booking had to wait.",
    ],
    "damage": [
        "Scratched rear bumper.",
        "Kerb scratch on the nearside front alloy.",
        "Small dent on the rear quarter panel.",
        "Windscreen chip, not in the pre-rental report.",
    ],
    "cleaning": [
        "Interior returned with significant mud.",
        "Vehicle returned without refuelling; cleaning surcharge applied.",
    ],
}

FUEL_LEVELS = ("empty", "quarter", "half", "three_quarter", "full")
DAMAGE_NOTES = [
    "Pre-existing scuff on the left mirror housing.",
    "Small chip above the rear wiper.",
    None,
    None,
    None,
]


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


class UnsafeTarget(Exception):
    pass


def assert_safe_target(url: str, *, acknowledged: bool, forced: bool) -> None:
    lowered = url.lower()

    for marker in FORBIDDEN_HOST_MARKERS:
        if marker in lowered:
            raise UnsafeTarget(
                f"Refusing to seed: {marker} looks like a hosted database.\n"
                "  Demo data is for a scratch database only. Point this at a\n"
                "  local SQLite file instead:\n"
                "      python -m scripts.seed_demo_data --database demo.db"
            )

    is_sqlite = lowered.startswith("sqlite")
    if is_sqlite:
        return
    if acknowledged and forced:
        return

    needed = []
    if not acknowledged:
        needed.append("--i-know-this-is-a-scratch-database")
    if not forced:
        needed.append("--yes-do-it")
    raise UnsafeTarget(
        "Refusing to seed a non-SQLite database without both "
        f"{' and '.join(needed)}.\n"
        f"  Target: {url}\n"
        "  Demo data is fictional. Writing it into a real database would put\n"
        "  invented bookings into the branch's actual history."
    )


# --------------------------------------------------------------------------
# Name and number generation
# --------------------------------------------------------------------------


def _person(rng: random.Random, index: int) -> tuple[str, str, str, str]:
    first = FIRST_NAMES[index % len(FIRST_NAMES)]
    last = LAST_NAMES[(index * 7 + 3) % len(LAST_NAMES)]
    name = f"{first} {last}"
    # The index is in both, so two people never collide on the unique columns.
    email = f"{first.lower()}.{last.lower().replace(' ', '')}{index}@example.com"
    phone = f"09{rng.randint(10, 99)}{rng.randint(1000000, 9999999)}"
    address = f"{rng.randint(1, 240)} {STREETS[index % len(STREETS)]}, Quezon City"
    return name, email, phone, address


def _staff_member(rng: random.Random, index: int, role: str) -> Users:
    name, email, phone, address = _person(rng, index)
    return Users(
        full_name=name,
        # Staff get example.com addresses too, so nothing in the demo points at
        # a real person's inbox.
        email=email,
        phone=phone,
        password_hash=hash_password("demo-password"),
        address=address,
        # NOT NULL and unique in the schema, including for staff.
        license_number=f"DL{index:06d}",
        license_expiry=date(2030, 12, 31),
        role=role,
        created_at=datetime(2026, 1, 2, 8, 0),
    )


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


def _make_customers(
    session: Session, rng: random.Random, count: int, today: date
) -> list[Users]:
    customers = []
    for i in range(count):
        name, email, phone, address = _person(rng, i)
        if i % 17 == 3:
            expiry = today + timedelta(days=rng.randint(5, 60))   # expiring soon
        elif i % 23 == 7:
            expiry = today - timedelta(days=rng.randint(1, 90))   # already lapsed
        else:
            expiry = today + timedelta(days=rng.randint(200, 1100))
        customers.append(
            Users(
                full_name=name,
                email=email,
                phone=phone,
                password_hash=hash_password("demo-password"),
                address=address,
                license_number=f"LC{i + 1:07d}",
                license_expiry=expiry,
                role="customer",
                created_at=datetime(2026, 1, 5, 9, 0),
            )
        )
    session.add_all(customers)
    session.flush()
    return customers


def _make_vehicles(session: Session, categories: dict[str, Vehicle_Category]) -> list[Vehicle]:
    vehicles = []
    for (
        make,
        model,
        year,
        plate,
        rate,
        category,
        seats,
        transmission,
        fuel,
        body,
        vehicle_class,
        engine_cc,
    ) in VEHICLE_SPECS:
        vehicles.append(
            Vehicle(
                category_id=categories[category].category_id,
                make=make,
                model=model,
                year=year,
                plate_number=plate,
                daily_rate=rate,
                # Spread of odometers, so the Fleet screen's mileage column is
                # not ten identical numbers.
                mileage=rng_mileage(make, model),
                seats=seats,
                transmission=transmission,
                fuel_type=fuel,
                body_style=body,
                vehicle_class=vehicle_class,
                engine_cc=engine_cc,
                status="available",
                created_at=datetime(2026, 1, 3, 9, 0),
            )
        )
    session.add_all(vehicles)
    session.flush()
    return vehicles


def rng_mileage(make: str, model: str) -> int:
    return 18_000 + (sum(ord(c) for c in make + model) % 47) * 1_000


#: Long-term and mid-length rental discounts, matching the domain classes.
DISCOUNT_7_PLUS = Decimal("0.90")
DISCOUNT_3_TO_6 = Decimal("0.95")


def rental_total(rate, days: int) -> Decimal:
    total = Decimal(rate) * days
    if days >= 7:
        total *= DISCOUNT_7_PLUS
    elif days >= 3:
        total *= DISCOUNT_3_TO_6
    return total.quantize(Decimal("0.01"))


def _channel(rng: random.Random, days_ago: int) -> str:
    online_share = _ONLINE_SHARE_NOW + _ONLINE_RAMP * (days_ago / 180)
    return "online" if rng.random() < online_share else "walk_in"


def _make_maintenance(
    session: Session,
    rng: random.Random,
    vehicles: list[Vehicle],
    in_workshop: list[Vehicle],
    today: date,
) -> list[Maintenance_Record]:
    for vehicle in vehicles:
        for _ in range(rng.randint(1, 2)):
            description, cost = rng.choice(MAINTENANCE_JOBS)
            start = today - timedelta(days=rng.randint(20, 170))
            session.add(
                Maintenance_Record(
                    vehicle=vehicle,
                    description=description,
                    cost=cost,
                    start_date=start,
                    end_date=start + timedelta(days=rng.randint(1, 4)),
                    status="completed",
                )
            )

    open_jobs = []
    for vehicle in in_workshop:
        description, cost = rng.choice(MAINTENANCE_JOBS)
        started = today - timedelta(days=rng.randint(0, 3))
        record = Maintenance_Record(
            vehicle=vehicle,
            description=description,
            cost=cost,
            start_date=started,
            # An end date in the future is the "expected back" figure, which
            # is what the Fleet screen's blocked-by column quotes.
            end_date=started + timedelta(days=rng.randint(2, 6)),
            status=(
                MAINTENANCE_OPEN_STATUS
                if started < today
                else MAINTENANCE_SCHEDULED_STATUS
            ),
        )
        session.add(record)
        open_jobs.append(record)

    session.flush()
    return open_jobs


def _make_bookings(
    session: Session,
    rng: random.Random,
    vehicles: list[Vehicle],
    customers: list[Users],
    staff: list[Users],
    *,
    days_back: int = 180,
    today: date,
) -> tuple[list[Booking], list[Booking], list[Booking]]:
    completed: list[Booking] = []
    ongoing: list[Booking] = []
    pending: list[Booking] = []

    # vehicle_id -> list of (start, end) already taken
    taken: dict[int, list[tuple[date, date]]] = {v.vehicle_id: [] for v in vehicles}
    free_after: dict[int, date] = {v.vehicle_id: today - timedelta(days=days_back) for v in vehicles}

    def place(vehicle_id: int, start: date, end: date) -> bool:
        if any(start < e and s < end for s, e in taken[vehicle_id]):
            return False
        if start < free_after[vehicle_id]:
            # The vehicle is still finishing an earlier rental. The random walk
            # below would otherwise "plan" a car into two places at once.
            return False
        taken[vehicle_id].append((start, end))
        free_after[vehicle_id] = end
        return True

    reserved = vehicles[-(WORKSHOP_CARS + len(TODAYS_PLANS)) :]
    idle = vehicles[-(WORKSHOP_CARS + len(TODAYS_PLANS) + UNBOOKED_CARS) : -(
        WORKSHOP_CARS + len(TODAYS_PLANS)
    )]
    walkable = [
        v for v in vehicles if v not in reserved and v not in idle
    ]

    for offset in range(days_back, -14, -1):
        start = today - timedelta(days=offset)
        # Weekends are busier, which is what makes the dashboard's booking
        # chart look like a real week rather than a flat line.
        busy = start.weekday() in (4, 5, 6)
        count = rng.randint(3, 6) if busy else rng.randint(1, 4)

        for _ in range(count):
            vehicle = rng.choice(walkable)
            span = rng.choice([1, 2, 2, 3, 3, 4, 5, 6, 7])
            end = start + timedelta(days=span)
            if not place(vehicle.vehicle_id, start, end):
                continue

            customer = rng.choice(customers)
            taker = rng.choice(staff)
            created = datetime.combine(
                start - timedelta(days=rng.randint(0, 6)), datetime.min.time()
            ) + timedelta(hours=rng.randint(8, 18))
            total = rental_total(vehicle.daily_rate, span)

            if end <= today:
                status = "completed"
            elif start <= today:
                status = "ongoing"
            else:
                status = rng.choice(["pending", "pending", "confirmed"])

            booking = Booking(
                user=customer,
                vehicle=vehicle,
                start_date=start,
                end_date=end,
                total_cost=total,
                status=status,
                created_at=created,
                created_by=taker.user_id,
                channel=_channel(rng, offset),
            )
            {"completed": completed, "ongoing": ongoing}.get(status, pending).append(
                booking
            )

    _force_todays_work(session, rng, vehicles, customers, staff, taken, free_after, today)
    session.add_all(completed + ongoing + pending)
    session.flush()
    return completed, ongoing, pending


def _force_todays_work(
    session,
    rng: random.Random,
    vehicles: list[Vehicle],
    customers: list[Users],
    staff: list[Users],
    taken: dict[int, list[tuple[date, date]]],
    free_after: dict[int, date],
    today: date,
) -> None:
    reserved = vehicles[-(WORKSHOP_CARS + len(TODAYS_PLANS)) :]
    for_rent = reserved[WORKSHOP_CARS:]

    for vehicle, (days_late, span) in zip(for_rent, TODAYS_PLANS):
        end = today - timedelta(days=days_late)
        start = end - timedelta(days=span)
        if start < free_after[vehicle.vehicle_id]:
            continue
        if any(start < e and s < end for s, e in taken[vehicle.vehicle_id]):
            continue
        taken[vehicle.vehicle_id].append((start, end))
        free_after[vehicle.vehicle_id] = end

        # `ongoing` with no `actual_return_date`. Where the end date is already
        # past, that is exactly the state `overdue_returns` is defined to find.
        session.add(
            Booking(
                user=rng.choice(customers),
                vehicle=vehicle,
                start_date=start,
                end_date=end,
                actual_return_date=None,
                total_cost=rental_total(vehicle.daily_rate, span),
                status="ongoing",
                created_at=datetime.combine(
                    start - timedelta(days=2), datetime.min.time()
                )
                + timedelta(hours=9),
                created_by=rng.choice(staff).user_id,
                # Overdue rentals are overwhelmingly walk-ins: nobody books
                # online for a car they are not going to collect.
                channel=_channel(rng, (today - start).days),
            )
        )


def _settle_completed(
    session: Session,
    rng: random.Random,
    completed: list[Booking],
    staff: list[Users],
    today: date,
) -> None:
    for booking in completed:
        taker = rng.choice(staff)
        balance_due = _owing(booking)

        roll = rng.random()
        if roll < 0.70:
            # Paid in full, sometimes in two parts.
            if rng.random() < 0.45:
                deposit = (balance_due * Decimal("0.4")).quantize(CENT)
                _pay(session, booking, deposit, taker, rng, days_late=rng.randint(0, 2), today=today)
                _pay(session, booking, balance_due - deposit, taker, rng, days_late=rng.randint(0, 1), today=today)
            else:
                _pay(session, booking, balance_due, taker, rng, days_late=rng.randint(0, 2), today=today)
        elif roll < 0.83:
            # Part-paid: leaves a balance, so Outstanding is not always zero.
            half = (balance_due * Decimal("0.5")).quantize(CENT)
            _pay(session, booking, half, taker, rng, days_late=rng.randint(0, 2), today=today)
        elif roll < 0.91:
            # Paid then refunded. `paid_at` stays set on purpose: the money did
            # arrive before it left again, and `refund_payment` only flips the
            # status, so a refund that blanked it would contradict the service.
            payment = _pay(session, booking, balance_due, taker, rng, days_late=rng.randint(1, 3), today=today)
            payment.status = "refunded"
            payment.note = "REFUNDED: rental cancelled by customer, deposit returned."
        elif roll < 0.96:
            # A GCash transfer that had not cleared yet. The balance therefore
            # still counts as owed, which is what `booking_balance` assumes.
            _pay(
                session,
                booking,
                (balance_due * Decimal("0.6")).quantize(CENT),
                taker,
                rng,
                days_late=0,
                today=today,
                status="pending",
            )
        elif roll < 0.99:
            # A declined card, then cash on arrival.
            _pay(
                session,
                booking,
                balance_due,
                taker,
                rng,
                days_late=0,
                today=today,
                status="failed",
                method="card",
                note="DECLINED: insufficient funds. Customer settled in cash.",
            )
        # else: never paid at all.

    session.flush()


def _owing(booking: Booking) -> Decimal:
    total = Decimal(booking.total_cost)
    for penalty in booking.penalties:
        total += Decimal(penalty.amount)
    return total.quantize(CENT)


def _settle_ongoing(
    session: Session,
    rng: random.Random,
    ongoing: list[Booking],
    staff: list[Users],
    today: date,
) -> None:
    for booking in ongoing:
        taker = rng.choice(staff)
        owed = _owing(booking)
        deposit = (owed * Decimal("0.4")).quantize(CENT)
        # Taken on the day the car was collected, or the day before when it was
        # booked online and collected in the morning.
        taken = booking.start_date - (
            timedelta(days=1) if rng.random() < 0.45 else timedelta()
        )
        if rng.random() < 0.86:
            _pay(session, booking, deposit, taker, rng, taken_on=taken, today=today)
        else:
            # In flight: a GCash transfer that has not cleared. The balance
            # still counts as owed, which is what `booking_balance` assumes.
            _pay(
                session,
                booking,
                deposit,
                taker,
                rng,
                taken_on=taken,
                today=today,
                status="pending",
                note="GCash transfer sent, awaiting confirmation.",
            )


def _pay(
    session,
    booking,
    amount,
    taker,
    rng,
    *,
    days_late: int = 0,
    taken_on: date | None = None,
    today: date | None = None,
    status: str = "paid",
    method: str | None = None,
    note: str | None = None,
):
    if taken_on is not None:
        received = datetime.combine(taken_on, datetime.min.time())
    elif days_late < 0:
        received = datetime.combine(
            booking.end_date + timedelta(days=-days_late), datetime.min.time()
        )
    else:
        received = datetime.combine(booking.end_date, datetime.min.time())

    if today is not None and received.date() > today:
        received = datetime.combine(today, datetime.max.time())

    received += timedelta(hours=rng.randint(8, 19))
    # The clamp can land on `today` exactly, and adding hours could push it over
    # midnight again. Cap the clock rather than trusting the sum.
    if today is not None:
        received = min(received, datetime.combine(today, datetime.max.time()))

    method = method or rng.choices(
        ["cash", "gcash", "card"], weights=[0.45, 0.4, 0.15]
    )[0]
    payment = Payment(
        booking=booking,
        amount=amount,
        method=method,
        status=status,
        paid_at=received if status == "paid" else None,
        recorded_by=taker.user_id,
        reference_no=(
            f"GCASH-{rng.randint(100000, 999999)}" if method == "gcash" else None
        ),
        created_at=received,
    )
    payment.note = note
    session.add(payment)
    return payment


def _add_penalties_and_inspections(
    session: Session,
    rng: random.Random,
    completed: list[Booking],
    ongoing: list[Booking],
    staff: list[Users],
) -> None:
    for booking in completed:
        inspector = rng.choice(staff)
        pre_mileage = rng.randint(12_000, 60_000)
        session.add(
            Inspection_Report(
                booking=booking,
                inspected_by=inspector.user_id,
                inspection_type="pre-rental",
                mileage_reading=pre_mileage,
                fuel_level="full",
                damage_notes=rng.choice(DAMAGE_NOTES),
                photo_url=None,
                inspected_at=datetime.combine(booking.start_date, datetime.min.time())
                + timedelta(hours=8),
            )
        )
        session.add(
            Inspection_Report(
                booking=booking,
                inspected_by=inspector.user_id,
                inspection_type="post-rental",
                mileage_reading=pre_mileage + rng.randint(20, 900),
                fuel_level=rng.choices(FUEL_LEVELS, weights=[1, 1, 2, 2, 4])[0],
                damage_notes=rng.choice(DAMAGE_NOTES),
                photo_url=None,
                inspected_at=datetime.combine(booking.actual_return_date, datetime.min.time())
                + timedelta(hours=rng.randint(14, 19)),
            )
        )

        # Late returns for a minority, damage for fewer still.
        if booking.actual_return_date and booking.actual_return_date > booking.end_date:
            days_late = (booking.actual_return_date - booking.end_date).days
            session.add(
                Penalty(
                    booking=booking,
                    penalty_type="late_return",
                    amount=CENT * 1000,
                    description=rng.choice(PENALTY_REASONS["late_return"]),
                    created_at=datetime.combine(booking.actual_return_date, datetime.min.time()),
                )
            )
        if rng.random() < 0.14:
            session.add(
                Penalty(
                    booking=booking,
                    penalty_type=rng.choice(["damage", "cleaning"]),
                    amount=CENT * (rng.randint(3, 45) * 100),
                    description=rng.choice(
                        PENALTY_REASONS["damage"] + PENALTY_REASONS["cleaning"]
                    ),
                    created_at=datetime.combine(booking.actual_return_date, datetime.min.time()),
                )
            )

    # Every ongoing rental has its pre-rental inspection and nothing else.
    for booking in ongoing:
        session.add(
            Inspection_Report(
                booking=booking,
                inspected_by=rng.choice(staff).user_id,
                inspection_type="pre-rental",
                mileage_reading=rng.randint(12_000, 60_000),
                fuel_level="full",
                damage_notes=None,
                photo_url=None,
                inspected_at=datetime.combine(booking.start_date, datetime.min.time())
                + timedelta(hours=8),
            )
        )
    session.flush()


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def seed(engine, *, days_back: int = 180, today: date | None = None) -> dict[str, int]:
    today = today or date.today()
    Base.metadata.create_all(engine)
    rng = random.Random(SEED)

    with Session(engine, future=True) as session:
        existing = session.execute(select(Users)).scalars().first()
        if existing is not None:
            raise UnsafeTarget(
                "This database already has users in it. Demo data is meant for an\n"
                "  empty database; seed a scratch file rather than mixing fictional\n"
                "  bookings into rows that are already there."
            )

        categories = {}
        for name, multiplier in (("Car", "1.00"), ("Motorcycle", "0.70")):
            category = Vehicle_Category(category_name=name, base_rate_multiplier=multiplier)
            session.add(category)
            categories[name] = category
        session.flush()

        # Offset into a range customers never reach: `USERS.email` and
        # `license_number` are both UNIQUE, and staff are generated from the
        # same name/email helper, so a shared index would collide immediately.
        staff = [
            _staff_member(rng, 900 + n, "staff") for n in (1, 2, 3)
        ]
        counter = Users(
            full_name="Demo Counter Staff",
            email=DEMO_STAFF_EMAIL,
            phone="09170000002",
            password_hash=hash_password("demo-password"),
            address="1 Demo Street, Quezon City",
            license_number="DL000001",
            license_expiry=date(2030, 12, 31),
            role="staff",
            created_at=datetime(2026, 1, 2, 8, 0),
        )
        admin = Users(
            full_name="Demo Branch Admin",
            email=DEMO_ADMIN_EMAIL,
            phone="09170000001",
            password_hash=hash_password("demo-password"),
            address="1 Demo Street, Quezon City",
            license_number="DL000000",
            license_expiry=date(2030, 12, 31),
            role="admin",
            created_at=datetime(2026, 1, 2, 8, 0),
        )
        session.add_all(staff + [counter, admin])
        session.flush()

        customers = _make_customers(session, rng, 24, today)
        vehicles = _make_vehicles(session, categories)
        workshop = vehicles[-(WORKSHOP_CARS + len(TODAYS_PLANS)) : -len(TODAYS_PLANS)]
        open_jobs = _make_maintenance(session, rng, vehicles, workshop, today)

        completed, ongoing, pending = _make_bookings(
            session, rng, vehicles, customers, staff, days_back=days_back, today=today
        )
        # `_force_todays_work` adds to the session directly rather than to
        # `ongoing`, so the ongoing list is rebuilt from the database to keep
        # the vehicle-status pass below and the reported count in agreement.
        ongoing = list(
            session.execute(
                select(Booking).where(Booking.status == "ongoing")
            ).scalars()
        )

        # Actual return dates and odometer movement for everything finished.
        for booking in completed:
            late = 0
            if rng.random() < 0.18:
                late = rng.randint(1, 4)
            booking.actual_return_date = booking.end_date + timedelta(days=late)
        session.flush()

        _add_penalties_and_inspections(session, rng, completed, ongoing, staff)
        _settle_completed(session, rng, completed, staff, today)
        _settle_ongoing(session, rng, ongoing, staff, today)

        # Vehicles out on rent right now should say so, or the Fleet tab will
        # show cars as available that are physically gone.
        in_workshop = {record.vehicle_id for record in open_jobs}
        for vehicle in vehicles:
            is_out = any(b.vehicle_id == vehicle.vehicle_id for b in ongoing)
            if vehicle.vehicle_id in in_workshop:
                vehicle.status = "maintenance"
            elif is_out:
                vehicle.status = "rented"
            else:
                vehicle.status = "available"
                vehicle.mileage += rng.randint(0, 40_000)
        session.flush()

        counts = {
            "staff": len(staff) + 2,  # generated, the demo counter, and the admin
            "customers": len(customers),
            "vehicles": len(vehicles),
            "bookings_completed": len(completed),
            "bookings_ongoing": len(ongoing),
            "bookings_pending": len(pending),
            "payments": len(session.execute(select(Payment)).scalars().all()),
            "penalties": len(session.execute(select(Penalty)).scalars().all()),
            "inspections": len(
                session.execute(select(Inspection_Report)).scalars().all()
            ),
        }
        session.commit()
        return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--database",
        default="demo.db",
        help="SQLite file to create. Defaults to demo.db in the project root.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=180,
        help="How many days of history to generate (default 180, about six months).",
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help=(
            "Anchor the six-month window to this date instead of today. "
            "Combined with the fixed seed this makes the output reproducible: "
            "the same --as-of always produces the same rows."
        ),
    )
    parser.add_argument(
        "--i-know-this-is-a-scratch-database",
        action="store_true",
        help="Acknowledge that the target is not a local SQLite file.",
    )
    parser.add_argument(
        "--yes-do-it",
        action="store_true",
        help="Second confirmation, required together with the flag above.",
    )
    args = parser.parse_args()

    url = args.database
    if not url.startswith("sqlite"):
        url = f"sqlite:///{url}"

    try:
        assert_safe_target(
            url,
            acknowledged=args.i_know_this_is_a_scratch_database,
            forced=args.yes_do_it,
        )
    except UnsafeTarget as exc:
        raise SystemExit(str(exc))

    anchor = args.as_of or date.today()
    print(
        f"Seeding {url} with {args.days} days of demo data "
        f"(seed={SEED}, as of {anchor.isoformat()})."
    )
    print("Every customer is fictional and uses an example.com address.\n")

    engine = create_engine(url, future=True)
    try:
        counts = seed(engine, days_back=args.days, today=anchor)
    except UnsafeTarget as exc:
        raise SystemExit(str(exc))
    finally:
        engine.dispose()

    width = max(len(k) for k in counts)
    for key, value in counts.items():
        print(f"  {key:<{width}}  {value:>6}")
    print("\nDone. Password for every demo login is 'demo-password'.")
    print(f"  admin  {DEMO_ADMIN_EMAIL}     full dashboard, admin actions")
    print(f"  staff  {DEMO_STAFF_EMAIL}     counter actions, same dashboard")


if __name__ == "__main__":
    main()
