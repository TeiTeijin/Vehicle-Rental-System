"""Inspections: the handover record for every rental.

A pre-rental and a post-rental report are what turn a disputed damage claim
into a settled one, so this screen exists to make gaps in the record obvious.
A rental with only a pre-rental inspection means the car has not come back yet
or has come back uninspected, and the "Missing" column is how that gets noticed
without reading every row.

Photos are optional. `photo_url` is nullable and most inspections have none, so
this shows a plain indicator rather than a broken image placeholder.
"""

from __future__ import annotations

from functools import partial

from sqlalchemy import select

from app.models import Booking, Inspection_Report, Users
from app.staff.pages.base import StaffPage
from app.staff.tables import ALIGN_LEFT, ALIGN_RIGHT, Column, LoadStateTable


def _inspection_rows(context):
    with context.reading() as session:
        reports = session.execute(
            select(Inspection_Report).order_by(Inspection_Report.inspected_at.desc()).limit(500)
        ).scalars().all()

        rows = []
        for report in reports:
            booking = report.booking
            vehicle = booking.vehicle if booking is not None else None
            inspector = session.get(Users, report.inspected_by) if report.inspected_by else None
            rows.append(
                (
                    f"#{booking.booking_id}" if booking is not None else "-",
                    f"{vehicle.plate_number} {vehicle.make} {vehicle.model}" if vehicle else "-",
                    booking.user.full_name if booking is not None else "-",
                    report.inspection_type.replace("-", " ").title(),
                    report.inspected_at.strftime("%d %b %Y %H:%M") if report.inspected_at else "-",
                    f"{report.mileage_reading:,} km" if report.mileage_reading is not None else "-",
                    (report.fuel_level or "-").replace("_", " ").title(),
                    report.damage_notes or "None recorded",
                    "Yes" if report.photo_url else "No",
                    inspector.full_name if inspector is not None else "-",
                )
            )
        return rows


class InspectionsPage(StaffPage):
    def __init__(self, shell) -> None:
        super().__init__(
            shell,
            "Inspections",
            "Every handover check, and anything found at the time.",
        )
        self.table = LoadStateTable(
            [
                Column("Booking", ALIGN_RIGHT),
                Column("Vehicle", ALIGN_LEFT),
                Column("Customer", ALIGN_LEFT),
                Column("Type", ALIGN_LEFT),
                Column("When", ALIGN_LEFT),
                Column("Odometer", ALIGN_RIGHT),
                Column("Fuel", ALIGN_LEFT),
                Column("Damage", ALIGN_LEFT),
                Column("Photo"),
                Column("Inspector", ALIGN_LEFT),
            ],
            partial(_inspection_rows, self.context),
            empty_message="No inspections recorded yet.",
        )
        self.body.addWidget(self.table, 1)

    def refresh(self) -> None:
        super().refresh()
        self.table.load()


def build_inspections_page(shell) -> InspectionsPage:
    return InspectionsPage(shell)
