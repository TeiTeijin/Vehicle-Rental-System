from __future__ import annotations

from datetime import date
from decimal import Decimal
from functools import partial

from sqlalchemy import select

from app.models import Booking
from app.services import booking_service, payment_service
from app.services.errors import ServiceError
from app.staff.pages.base import StaffPage
from app.staff.tables import ALIGN_LEFT, ALIGN_RIGHT, Column, LoadStateTable
from app.staff.widgets import blocking_error, confirm, toast

#: Actions a plain staff member may perform. Anything else is admin.
COUNTER_ACTIONS = ("check_in", "check_out", "record_payment")


def _booking_rows(context):
    with context.reading() as session:
        bookings = session.execute(
            select(Booking).order_by(Booking.start_date.desc()).limit(500)
        ).scalars().all()
        rows = []
        for booking in bookings:
            balance = payment_service.booking_balance(session, booking)
            vehicle = booking.vehicle
            rows.append(
                (
                    f"#{booking.booking_id}",
                    booking.user.full_name,
                    f"{vehicle.plate_number} {vehicle.make} {vehicle.model}",
                    booking.start_date.strftime("%d %b %Y"),
                    booking.end_date.strftime("%d %b %Y"),
                    booking.status,
                    f"{balance.total_due:,.2f}",
                    f"{balance.amount_paid:,.2f}",
                    f"{balance.balance:,.2f}",
                    booking.created_at.strftime("%d %b"),
                )
            )
        return rows


class BookingsPage(StaffPage):
    def __init__(self, shell) -> None:
        super().__init__(
            shell,
            "Bookings",
            "Every rental, its state, and what is still owed on it.",
        )
        self.table = LoadStateTable(
            [
                Column("Ref", ALIGN_RIGHT),
                Column("Customer", ALIGN_LEFT),
                Column("Vehicle", ALIGN_LEFT),
                Column("From", ALIGN_LEFT),
                Column("To", ALIGN_LEFT),
                Column("Status"),
                Column("Total due", ALIGN_RIGHT),
                Column("Paid", ALIGN_RIGHT),
                Column("Owed", ALIGN_RIGHT),
                Column("Booked", ALIGN_LEFT),
            ],
            partial(_booking_rows, self.context),
            empty_message="No bookings yet.",
        )
        self.body.addWidget(self.table, 1)
        self._selected: Booking | None = None

    def refresh(self) -> None:
        super().refresh()
        self.table.load()

    def selected_booking_id(self) -> int | None:
        row = self.table.table.currentRow()
        if row < 0:
            return None
        text = self.table.cell_text(row, 0)
        if not text.startswith("#"):
            return None
        return int(text[1:])

    def can(self, action: str) -> bool:
        if self.context.is_admin:
            return True
        return action in COUNTER_ACTIONS

    def run(self, action: str, **kwargs) -> bool:
        booking_id = self.selected_booking_id()
        if booking_id is None:
            toast(self, "Select a booking first.", "warn")
            return False
        if not self.can(action):
            message = "That action is for administrators."
            toast(self, message, "error")
            return False

        try:
            # Re-read here, not from the list view: a booking settled by a
            # colleague a moment ago must not be the one cancelled.
            with self.context.session() as session:
                fresh = session.get(Booking, booking_id)
                if fresh is None:
                    raise ServiceError("That booking no longer exists.")
                _ACTIONS[action](session, fresh, self.context, **kwargs)
        except Exception as exc:  # noqa: BLE001
            blocking_error(self, exc, title="That did not go through")
            return False

        self.table.load()
        toast(self, f"#{booking_id} updated.")
        return True

    def confirm_booking(self) -> bool:
        return self.run("confirm")

    def cancel_booking(self, reason: str) -> bool:
        if not confirm(
            self,
            "Cancel this booking?",
            detail="The customer will be told to collect their deposit back.",
            destructive=True,
        ):
            return False
        return self.run("cancel", reason=reason)


# -- the actions ----------------------------------------------------------
#
# Each takes a session and a *fresh* booking, called inside
# `StaffContext.session()`.


def _inspector_row(session, context):
    from app.models import Users

    return session.get(Users, context.user.user_id)


def _do_confirm(session, booking, context, **_) -> None:
    booking_service.confirm_booking(session, booking)


def _do_cancel(session, booking, context, *, reason: str, **_) -> None:
    booking_service.cancel_booking(session, booking, reason=reason)


def _do_check_in(session, booking, context, *, mileage: int, fuel_level: str, damage_notes: str, **_) -> None:
    booking_service.check_in(
        session,
        booking,
        inspector=_inspector_row(session, context),
        mileage_reading=mileage,
        fuel_level=fuel_level,
        damage_notes=damage_notes or None,
    )


def _do_check_out(
    session,
    booking,
    context,
    *,
    mileage: int,
    fuel_level: str,
    damage_notes: str,
    damage_charge: Decimal | None,
    **_,
) -> None:
    booking_service.check_out(
        session,
        booking,
        inspector=_inspector_row(session, context),
        actual_return_date=date.today(),
        mileage_reading=mileage,
        fuel_level=fuel_level,
        damage_notes=damage_notes or None,
        damage_charge=damage_charge,
    )


def _do_record_payment(
    session,
    booking,
    context,
    *,
    amount: Decimal,
    method: str,
    reference_no: str,
    note: str,
    **_,
) -> None:
    payment_service.record_payment(
        session,
        booking,
        amount,
        method,
        recorded_by=context.user.user_id,
        reference_no=reference_no or None,
        note=note or None,
    )


_ACTIONS = {
    "confirm": _do_confirm,
    "cancel": _do_cancel,
    "check_in": _do_check_in,
    "check_out": _do_check_out,
    "record_payment": _do_record_payment,
}


def build_bookings_page(shell) -> BookingsPage:
    return BookingsPage(shell)
