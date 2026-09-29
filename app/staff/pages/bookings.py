"""Bookings: the full list, and the actions that change a rental's state.

This page owns the write paths -- confirm, cancel, check in, check out, record
a payment -- so the rules about who may do what belong here rather than being
sprinkled through the widgets.

The important one is the role split. A `staff` member may do the counter work:
check a car out, take payment, record damage. A `staff` member may **not**
cancel a confirmed booking or reverse a payment; those are the actions that
change what the branch owes someone, and they are admin-only. Hiding the
button is not the enforcement -- every action calls the service, and the
service's own checks are the enforcement. The button is hidden so the screen
does not offer something that will be refused.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import Booking
from app.services import booking_service, payment_service
from app.services.errors import ServiceError
from app.staff.pages.base import StaffPage
from app.staff.tables import ALIGN_LEFT, ALIGN_RIGHT, Column, LoadStateTable
from app.staff.widgets import blocking_error, confirm, toast

#: Actions a plain staff member may perform. Everything not in here is admin.
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
            lambda: _booking_rows(self.context),
            empty_message="No bookings yet.",
        )
        self.body.addWidget(self.table, 1)
        self._selected: Booking | None = None

    def refresh(self) -> None:
        super().refresh()
        self.table.load()

    def selected_booking_id(self) -> int | None:
        """The id under the selection, or None.

        Only the id comes back, never the row. `context.reading()` rolls back
        and closes on the way out, which expires every attribute on anything
        still attached -- so an `Booking` handed back from it raises
        `DetachedInstanceError` the moment the caller reads `booking_id`. The
        write path below opens its own session anyway, so it wants the id, not
        the row.
        """
        row = self.table.table.currentRow()
        if row < 0:
            return None
        text = self.table.cell_text(row, 0)
        if not text.startswith("#"):
            return None
        return int(text[1:])

    def can(self, action: str) -> bool:
        """Whether this user may perform `action`."""
        if self.context.is_admin:
            return True
        return action in COUNTER_ACTIONS

    def run(self, action: str, **kwargs) -> bool:
        """Perform a state change, and report the outcome.

        Returns True on success. On refusal it shows the service's own message
        and returns False, because a silent no-op on a booking screen looks
        identical to a click that did not land.
        """
        booking_id = self.selected_booking_id()
        if booking_id is None:
            toast(self, "Select a booking first.", "warn")
            return False
        if not self.can(action):
            message = "That action is for administrators."
            toast(self, message, "error")
            return False

        try:
            # Re-read inside the write session rather than trusting the list
            # view: a booking settled by a colleague a moment ago must not be
            # the one that gets cancelled.
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
# Each takes a session and a *fresh* booking, and is called inside
# `StaffContext.session()` so the commit and rollback are handled in one place.


def _inspector_row(session, context):
    """The signed-in staff member as a `Users` row in *this* session.

    `check_in`/`check_out` take an inspector and assign it to
    `INSPECTION_REPORT.inspected_by`. `StaffUser` is a detached value with no
    `user_id`-backed relationship, so it cannot be assigned -- the row has to
    be fetched in the same session that will write the report.
    """
    from app.models import Users

    return session.get(Users, context.user.user_id)


def _do_confirm(session, booking, context, **_) -> None:
    # `created_by` is the audit trail and the service does not take a separate
    # "who confirmed this", so the confirmation actor is already recorded.
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
