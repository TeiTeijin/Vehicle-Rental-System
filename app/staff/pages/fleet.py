from __future__ import annotations

from datetime import date, timedelta
from functools import partial

from sqlalchemy import select

from app.models import Vehicle
from app.services import booking_service, fleet_service
from app.staff.pages.base import StaffPage
from app.staff.tables import ALIGN_LEFT, ALIGN_RIGHT, Column, LoadStateTable


def _fleet_rows(context):
    today = date.today()
    with context.reading() as session:
        vehicles = session.execute(
            select(Vehicle).order_by(Vehicle.make, Vehicle.model)
        ).scalars().all()

        rows = []
        for vehicle in vehicles:
            open_bookings = booking_service.active_bookings(session, vehicle)
            due = min((b.end_date for b in open_bookings), default=None)

            blocked = fleet_service.maintenance_conflict(
                session, vehicle, today, today + timedelta(days=30)
            )
            issues = []
            if blocked:
                issues.append(f"Maintenance until {blocked.end_date:%d %b}")
            if not fleet_service.is_rentable(vehicle):
                issues.append("Not rentable")

            rows.append(
                (
                    vehicle.plate_number,
                    f"{vehicle.make} {vehicle.model}",
                    str(vehicle.year),
                    f"{vehicle.daily_rate:,.2f}",
                    f"{vehicle.mileage:,} km" if vehicle.mileage else "-",
                    vehicle.status.replace("_", " ").title(),
                    due.strftime("%d %b") if due else "-",
                    "Yes" if due and due < today else "-",
                    "; ".join(issues) or "-",
                )
            )
        return rows


class FleetPage(StaffPage):
    def __init__(self, shell) -> None:
        super().__init__(
            shell,
            "Fleet",
            "Every vehicle, whether it is out, and what is stopping it.",
        )
        self.table = LoadStateTable(
            [
                Column("Plate", ALIGN_LEFT),
                Column("Vehicle", ALIGN_LEFT),
                Column("Year", ALIGN_RIGHT),
                Column("Rate / day", ALIGN_RIGHT),
                Column("Odometer", ALIGN_RIGHT),
                Column("Status"),
                Column("Due back", ALIGN_RIGHT),
                Column("Overdue"),
                Column("Blocked by", ALIGN_LEFT),
            ],
            partial(_fleet_rows, self.context),
            empty_message="No vehicles yet. Add one from the admin tools.",
        )
        self.body.addWidget(self.table, 1)

    def refresh(self) -> None:
        super().refresh()
        self.table.load()


def build_fleet_page(shell) -> FleetPage:
    return FleetPage(shell)
