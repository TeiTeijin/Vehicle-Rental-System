"""Six months of realistic demo data, for screenshots and for trying the app.

This is the one script in the project that must be impossible to point at real
data by accident. Seeding six months of bookings into the Aiven instance would
make the branch's live history a mixture of invention, which is not something a
mistake should be able to do. So rather than trusting a flag, the guard is
structural:

  * It refuses to run unless the target database is SQLite *or* is explicitly
    opted in with --i-know-this-is-a-scratch-database, which prints what it is
    about to do and requires a second flag to actually proceed.
  * Aiven hostnames are rejected outright, before any connection is opened.
  * There is no code path that writes to a MySQL database without both flags.

Everything is deterministic. A fixed seed drives the randomness, so the same
commit always produces the same dashboard -- a demo that reshuffles on every
run cannot be used to compare screenshots, and "it looked different this time"
is not a bug report anyone can act on. The six-month window is anchored to
today by default, so the demo always looks current; pass ``--as-of`` to pin
that anchor and get byte-identical rows on any day.

    python -m scripts.seed_demo_data --help
    python -m scripts.seed_demo_data --database demo.db
    python -m scripts.seed_demo_data --database demo.db --as-of 2026-09-29
"""

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

DEMO_ADMIN_EMAIL = "admin@rentdesk.local"

#: A known counter-staff login. The role split is a headline feature -- a staff
#: member may check a car out but not cancel a confirmed booking -- and it
#: cannot be demonstrated without a known staff address. The other staff are
#: generated, so their emails change with the seed.
DEMO_STAFF_EMAIL = "counter@rentdesk.local"

#: How many cars sit in the workshop in the demo. They are held out of the
#: random booking walk, so a car is never both in the workshop and out on rent.
WORKSHOP_CARS = 2

#: The online share of new bookings today, and how far it has risen over the
#: 180-day window. `days_ago / 180` runs 1 -> 0 as history approaches the
#: present, so the ramp climbs towards the present. See `_channel`.
_ONLINE_SHARE_NOW = 0.45
_ONLINE_RAMP = 0.18

#: The rentals forced onto the Today screen, as
#: (days since it was due back, length in days). A day count of 0 means due
#: back today; a positive one means it should have come back already, which is
#: what puts a row in the Overdue table; a negative one means still running.
#:
#: These are the cases a random walk essentially never produces on its own --
#: they are all one- or two-day windows -- and without them the most useful
#: tables on the Today screen render empty, which looks identical to a broken
#: query.
TODAYS_PLANS = [
    (2, 4),    # overdue by two days
    (5, 3),    # overdue by five days
    (0, 5),    # due back today
    (0, 2),    # due back today, short rental
    (-9, 12),  # still out, long rental
]

#: How many cars are left with no booking at all, so the branch is not running
#: at 100% and every "free to rent" style figure is non-zero. A demo where the
#: whole fleet is out cannot show what the Fleet screen looks like when someone
#: is actually looking for a car.
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

VEHICLE_SPECS = [
    # (make, model, year, plate, rate, category, seats, transmission, fuel, body)
    ("Toyota", "Vios", 2022, "DEM-101", "2500.00", "Car", 5, "Automatic", "Petrol", "Sedan"),
    ("Honda", "Civic", 2021, "DEM-102", "3000.00", "Car", 5, "Manual", "Petrol", "Sedan"),
    ("Toyota", "Camry", 2020, "DEM-103", "3500.00", "Car", 5, "Automatic", "Hybrid", "Sedan"),
    ("Toyota", "Fortuner", 2023, "DEM-104", "5500.00", "Car", 7, "Automatic", "Diesel", "SUV"),
    ("Mitsubishi", "Xpander", 2022, "DEM-105", "3800.00", "Car", 7, "Automatic", "Petrol", "MPV"),
    ("Mazda", "CX-5", 2023, "DEM-106", "5200.00", "Car", 5, "Automatic", "Petrol", "SUV"),
    ("Nissan", "Altima", 2021, "DEM-107", "3300.00", "Car", 5, "Automatic", "Petrol", "Sedan"),
    ("Ford", "Explorer", 2022, "DEM-108", "5800.00", "Car", 7, "Automatic", "Petrol", "SUV"),
    ("Toyota", "Innova", 2023, "DEM-109", "4000.00", "Car", 7, "Automatic", "Diesel", "MPV"),
    ("Honda", "City", 2022, "DEM-110", "2700.00", "Car", 5, "Automatic", "Petrol", "Sedan"),
    ("Isuzu", "D-Max", 2022, "DEM-111", "4800.00", "Car", 5, "Manual", "Diesel", "Pickup"),
    ("Mitsubishi", "Mirage", 2021, "DEM-112", "2300.00", "Car", 4, "Automatic", "Petrol", "Hatchback"),
    ("Honda", "Click 125i", 2023, "DEM-201", "800.00", "Motorcycle", 2, "Automatic", "Petrol", "Underbone"),
    ("Yamaha", "NMAX 155", 2022, "DEM-202", "1000.00", "Motorcycle", 2, "Automatic", "Petrol", "Scooter"),
    ("Kawasaki", "Ninja 400", 2023, "DEM-203", "1400.00", "Motorcycle", 2, "Manual", "Petrol", "Underbone"),
    ("Honda", "PCX 150", 2023, "DEM-204", "1200.00", "Motorcycle", 2, "Automatic", "Petrol", "Scooter"),
    ("Yamaha", "FZ-S", 2022, "DEM-205", "1100.00", "Motorcycle", 2, "Manual", "Petrol", "Underbone"),
    ("Kymco", "P200", 2023, "DEM-206", "950.00", "Motorcycle", 2, "Automatic", "Petrol", "Scooter"),
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
    """Refuse to seed anything that might be real.

    Called before any connection is opened, so a refusal cannot have already
    written a row.
    """
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
    """A stable (name, email, phone, address) for a given index."""
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
    """Customers with licence expiry spread over the next three years.

    A couple are deliberately close to expiry, and one has already lapsed, so
    the licence checks in `booking_service` have something to complain about
    when the demo is used to exercise those paths.
    """
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
    for make, model, year, plate, rate, category, seats, transmission, fuel, body in VEHICLE_SPECS:
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
                status="available",
                created_at=datetime(2026, 1, 3, 9, 0),
            )
        )
    session.add_all(vehicles)
    session.flush()
    return vehicles


def rng_mileage(make: str, model: str) -> int:
    """A stable odometer for a vehicle, derived from its name."""
    return 18_000 + (sum(ord(c) for c in make + model) % 47) * 1_000


#: Long-term and mid-length rental discounts, matching the domain classes.
DISCOUNT_7_PLUS = Decimal("0.90")
DISCOUNT_3_TO_6 = Decimal("0.95")


def rental_total(rate, days: int) -> Decimal:
    """What a rental of `days` costs, before penalties.

    The discounts mirror `app.domain.vehicle_types`, so demo bookings price the
    same way real ones do. A float anywhere in here would reintroduce exactly
    the rounding error `app.utils.money` exists to prevent.
    """
    total = Decimal(rate) * days
    if days >= 7:
        total *= DISCOUNT_7_PLUS
    elif days >= 3:
        total *= DISCOUNT_3_TO_6
    return total.quantize(Decimal("0.01"))


def _channel(rng: random.Random, days_ago: int) -> str:
    """Whether a booking was walked in or taken online.

    Not a coin flip. The counter still does most of the volume -- walk-ins are
    a rental with no planning involved, and a car is usually needed the same
    day -- but the online share climbs over the eighteen months, because that
    is the direction a real branch moves in once it has a booking page.

    The trend is a linear ramp across the window rather than a curve, so it
    stays smooth and obvious on the dashboard's channel chart without making
    any claim about a real market that this fixture cannot support.
    """
    online_share = _ONLINE_SHARE_NOW + _ONLINE_RAMP * (days_ago / 180)
    return "online" if rng.random() < online_share else "walk_in"


def _make_maintenance(
    session: Session,
    rng: random.Random,
    vehicles: list[Vehicle],
    in_workshop: list[Vehicle],
    today: date,
) -> list[Maintenance_Record]:
    """Historical service per vehicle, plus open jobs on the workshop cars.

    All the historical work is `completed`; the open jobs belong to
    `in_workshop` only. Without open jobs the Today page's workshop table is
    always empty, and an empty workshop in a demo is indistinguishable from a
    broken query -- the exact confusion the rest of this seeding avoids.

    `in_workshop` is passed in rather than sliced off the end of `vehicles`
    here, because the same vehicles are reserved for today's rentals. Picking
    them in two places independently is how a car ends up marked both
    `maintenance` and `rented`.
    """
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
    """Six months of rentals.

    Returns (completed, ongoing, pending) so the caller can report on each.
    A vehicle is never double-booked, and no rental starts in the past, because
    the app's own availability check would refuse them -- the demo data has to
    be data the application considers valid.
    """
    completed: list[Booking] = []
    ongoing: list[Booking] = []
    pending: list[Booking] = []

    # vehicle_id -> list of (start, end) already taken
    taken: dict[int, list[tuple[date, date]]] = {v.vehicle_id: [] for v in vehicles}
    free_after: dict[int, date] = {v.vehicle_id: today - timedelta(days=days_back) for v in vehicles}

    def place(vehicle_id: int, start: date, end: date) -> bool:
        """Claim [start, end) for a vehicle, or report it is already taken."""
        if any(start < e and s < end for s, e in taken[vehicle_id]):
            return False
        if start < free_after[vehicle_id]:
            # The vehicle is still finishing an earlier rental. The random walk
            # below would otherwise "plan" a car into two places at once.
            return False
        taken[vehicle_id].append((start, end))
        free_after[vehicle_id] = end
        return True

    # Vehicles held back from the walk. The walk fills every vehicle's calendar
    # out to today+14, so a car needed for a rental that is due back now has to
    # be kept out of the walk entirely -- there is no gap in a fully booked
    # calendar to put one in.
    #
    # The split must line up with `_force_todays_work` and `seed()`: the first
    # `WORKSHOP_CARS` go to the workshop, the rest carry today's rentals. Slicing
    # these independently in two places is how a car ends up both in the
    # workshop and out on rent, so the split is stated once, here.
    # Two groups are held out of the walk:
    #
    #   * the tail, which `seed()` and `_force_todays_work` split between the
    #     workshop and today's rentals -- the walk fills every calendar out to
    #     today+14, so there is no gap left to place a car that is due back now;
    #   * `UNBOOKED_CARS` before it, which get no bookings whatsoever, so the
    #     branch is not running at 100% and "free to rent" is never zero.
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
    """Guarantee the cases the Today screen exists to show.

    The random walk above reliably produces rentals that are out now and
    collections coming up, but it almost never produces a car that is *due back
    today* and essentially never one that is *overdue* -- both are narrow
    windows, and a day is a short time to be in one. So the demo would show an
    empty "Overdue" table and an empty "Due back today" table, and the most
    useful part of that screen would look broken.

    Two overdue, two due back today, and one rental that started a while ago
    and is still legitimately running. Each is placed on a vehicle reserved
    from the random walk, through the same `taken`/`free_after` bookkeeping, so
    nothing collides and the app still considers the data valid.

    The first `WORKSHOP_CARS` of the reserved slice are skipped: they are in the
    workshop, and a car cannot be in the workshop and out on rent.
    """
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
    """Give each completed rental a payment history.

    Aiming for a spread the Payments tab can actually be tested against, not a
    uniformly green one:

      * paid in full, sometimes as a deposit plus a balance;
      * part-paid, which leaves the booking on the Outstanding tile;
      * never paid, because customers walk;
      * paid then refunded, so the refund path has history to reconcile;
      * a pending GCash transfer or failed card attempt, which is the only
        thing that exercises a NULL `paid_at`.

    The last two matter more than they look: without a payment that never
    cleared, nothing in the demo can show what a missing `paid_at` means.
    """
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
    """Take a deposit on each rental that is currently out.

    `_settle_completed` only looks at finished rentals, which left the demo with
    no money taken on the current day at all -- a rental collected from the shelf
    this morning is paid for this morning, and without that the dashboard's
    revenue card read zero on the one day anyone looks at it.

    It also left every live rental at zero on the Outstanding tile, which is not
    how a branch works: a car is handed over against a deposit and the balance is
    settled on return.

    The deposit is a fraction of what is owed, never all of it, so the balance
    genuinely remains. One in eight is still in flight, which is the case that
    gives `paid_at IS NULL` something to be about.
    """
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
    """Write one payment row against `booking`.

    Two ways to say when the money changed hands:

    ``days_late``
        Offset from the return date. Negative is *early* -- the deposit taken
        when the car was collected. So `days_late=-2` lands two days before the
        booking ends, and the seeder's "paid late" cases use a small positive
        value, which lands after it.

    ``taken_on``
        An exact date, for when the answer is "the day it started" rather than
        anything relative to the return.

    Only a `paid` row gets a `paid_at`: that NULL is what distinguishes an
    in-flight transfer from money in the drawer, so it is set from the status
    rather than from the clock.

    ``today`` clamps the result. Without it the fixture invents payments dated
    in the future -- a rental due back today and paid three days late is
    stamped three days ahead -- and a dashboard then reports takings that have
    not happened yet and lists tomorrow's receipts as the most recent ones.
    """
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
    """The noise that makes the data look used."""
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
    """Populate an empty database. Returns counts for the summary.

    `today` anchors the six-month window. It defaults to the real current date
    so the demo always looks current, but every generated timestamp derives
    from it, which is what makes the output reproducible: pin it and you get
    byte-identical rows on any day, forever. That is what the tests do, and
    what you want when regenerating reference screenshots.
    """
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
        # The tail of the vehicle list is reserved from the random walk: the
        # first two sit in the workshop, the rest carry today's rentals. Both
        # groups have to be excluded from the walk, and choosing them once here
        # is what stops a car being marked `maintenance` and `rented` at once.
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
        #
        # A car in the workshop is not out, even if a stray booking says
        # otherwise -- and the two open maintenance jobs are on cars that are
        # deliberately not rented, so `maintenance` wins cleanly rather than
        # needing a tiebreak.
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
    print(f"  admin  {DEMO_ADMIN_EMAIL}     sees the dashboard charts")
    print(f"  staff  {DEMO_STAFF_EMAIL}     counter actions only, no charts")


if __name__ == "__main__":
    main()
