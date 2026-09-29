"""Today: what is happening at the counter right now.

This is the screen someone looks at all day, so it is built around the three
questions that come up constantly and should never need a second screen:

  * Who is due back today, and which of them are late?
  * Who is due to collect a car today, and is it ready?
  * Which cars are sitting in the workshop, and when are they free?

Overdue is separated from "due today" rather than sorted into it. A late
return is the one thing on this screen that needs chasing, and burying it in a
chronological list among on-time returns is how a car stays out for three
extra days.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.models import Booking, Maintenance_Record, Vehicle
from app.services import booking_service
from app.staff.pages.base import StaffPage
from app.staff.tables import ALIGN_LEFT, ALIGN_RIGHT, Column, LoadStateTable


def _due_back_today(context, today: date):
    with context.reading() as session:
        return [
            _booking_row(booking)
            for booking in session.execute(
                select(Booking)
                .where(Booking.status == "ongoing", Booking.end_date == today)
                .order_by(Booking.end_date)
            ).scalars().all()
        ]


def _overdue(context, today: date):
    with context.reading() as session:
        return [
            _booking_row(booking, today=today)
            for booking in booking_service.overdue_returns(session, today)
        ]


def _collections_today(context, today: date):
    with context.reading() as session:
        return [
            _booking_row(booking)
            for booking in booking_service.expected_vehicles_today(session, today)
        ]


def _booking_row(booking, today: date | None = None):
    vehicle = booking.vehicle
    customer = booking.user
    days_late = ""
    if today is not None and booking.end_date < today:
        days_late = (today - booking.end_date).days
    return (
        f"#{booking.booking_id}",
        customer.full_name,
        f"{vehicle.plate_number} {vehicle.make} {vehicle.model}",
        booking.start_date.strftime("%d %b"),
        booking.end_date.strftime("%d %b"),
        f"{days_late} day(s) late" if days_late else "-",
        booking.status,
    )


def _in_the_workshop(context, today: date):
    with context.reading() as session:
        records = session.execute(
            select(Maintenance_Record)
            .where(
                # The open states are the column's enum values. `in_progress`
                # is not one of them; using it raises a LookupError when the row
                # is read back, long after the query that selected it.
                Maintenance_Record.status.in_(("scheduled", "ongoing")),
                Maintenance_Record.start_date <= today + timedelta(days=14),
            )
            .order_by(Maintenance_Record.start_date)
        ).scalars().all()
        return [
            (
                f"{record.vehicle.plate_number} {record.vehicle.make} {record.vehicle.model}",
                record.description,
                record.status.replace("_", " ").title(),
                record.start_date.strftime("%d %b"),
                (record.end_date or record.start_date).strftime("%d %b"),
                f"{record.cost:,.2f}" if record.cost else "-",
            )
            for record in records
        ]


BOOKING_COLUMNS = [
    Column("Ref", ALIGN_RIGHT),
    Column("Customer", ALIGN_LEFT),
    Column("Vehicle", ALIGN_LEFT),
    Column("From", ALIGN_LEFT),
    Column("To", ALIGN_LEFT),
    Column("Late", ALIGN_LEFT),
    Column("Status"),
]


class TodayPage(StaffPage):
    def __init__(self, shell) -> None:
        super().__init__(shell, "Today", "What is due back, due out, and in the workshop.")

        self.overdue = LoadStateTable(
            BOOKING_COLUMNS,
            lambda: _overdue(self.context, date.today()),
            empty_message="Nothing is late.",
        )
        self.due_back = LoadStateTable(
            BOOKING_COLUMNS,
            lambda: _due_back_today(self.context, date.today()),
            empty_message="No cars due back today.",
        )
        self.collections = LoadStateTable(
            BOOKING_COLUMNS,
            lambda: _collections_today(self.context, date.today()),
            empty_message="No collections booked for today.",
        )
        self.workshop = LoadStateTable(
            [
                Column("Vehicle", ALIGN_LEFT),
                Column("Work", ALIGN_LEFT),
                Column("State"),
                Column("Started", ALIGN_LEFT),
                Column("Expected", ALIGN_LEFT),
                Column("Cost", ALIGN_RIGHT),
            ],
            lambda: _in_the_workshop(self.context, date.today()),
            empty_message="Nothing in the workshop.",
        )

        self.body.addWidget(self._heading("Overdue"))
        self.body.addWidget(self.overdue, 1)
        self.body.addWidget(self._heading("Due back today"))
        self.body.addWidget(self.due_back, 1)
        self.body.addWidget(self._heading("Collections today"))
        self.body.addWidget(self.collections, 1)
        self.body.addWidget(self._heading("In the workshop"))
        self.body.addWidget(self.workshop, 1)

    def _heading(self, text: str):
        from PySide6.QtWidgets import QLabel

        label = QLabel(text, self)
        label.setObjectName("pageSubtitle")
        return label

    def refresh(self) -> None:
        super().refresh()
        # "Today" is defined by the clock, so the dates are re-read on every
        # refresh. A window left open across midnight would otherwise keep
        # showing yesterday's collections.
        for table in (self.overdue, self.due_back, self.collections, self.workshop):
            table.load()

    @property
    def has_overdue(self) -> bool:
        return self.overdue.row_count() > 0


def build_today_page(shell) -> TodayPage:
    return TodayPage(shell)
