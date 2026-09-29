"""Customers: who rents from the branch, and whether they can.

The licence columns are the reason this screen exists. A customer whose
licence has expired cannot be handed a car, and finding that out at the counter
is a confrontation. Showing expiry and a "blocked" flag up front turns it into
something noticed at booking time instead.

The expiry test deliberately ignores the exact date: a licence is usable on
the day before it expires and not on the day after, so anything within the next
30 days is flagged as expiring rather than treated as valid or invalid. Same
reasoning as the booking window, and for the same reason.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select

from app.models import Booking, Users
from app.services.booking_service import check_licence
from app.staff.pages.base import StaffPage
from app.staff.tables import ALIGN_LEFT, ALIGN_RIGHT, Column, LoadStateTable

#: A licence inside this window is called out, but not treated as blocking.
#: The customer can still rent today; they just need renewing this week.
EXPIRING_SOON_DAYS = 30


def _customer_rows(context):
    today = date.today()
    soon = today + timedelta(days=EXPIRING_SOON_DAYS)

    with context.reading() as session:
        # One grouped count instead of a query per customer. With a few hundred
        # customers the per-row version is measurably slow, and the screen is
        # on the 30-second refresh path.
        rental_counts = dict(
            session.execute(
                select(Booking.user_id, func.count(Booking.booking_id)).group_by(
                    Booking.user_id
                )
            ).all()
        )

        customers = session.execute(
            select(Users)
            .where(Users.role == "customer")
            .order_by(Users.full_name)
        ).scalars().all()

        rows = []
        for customer in customers:
            expiry = customer.license_expiry
            if expiry is None:
                licence = "Not on file"
                blocked = "Yes"
            elif expiry < today:
                licence = f"Expired {expiry:%d %b %Y}"
                blocked = "Yes"
            elif expiry <= soon:
                licence = f"Expires {expiry:%d %b %Y}"
                blocked = "Soon"
            else:
                licence = f"Valid to {expiry:%d %b %Y}"
                blocked = "-"

            rows.append(
                (
                    customer.full_name,
                    customer.email,
                    customer.phone or "-",
                    customer.license_number or "-",
                    licence,
                    blocked,
                    rental_counts.get(customer.user_id, 0),
                    customer.created_at.strftime("%d %b %Y") if customer.created_at else "-",
                )
            )
        return rows


class CustomersPage(StaffPage):
    def __init__(self, shell) -> None:
        super().__init__(
            shell,
            "Customers",
            "Everyone who rents, and whether their licence is still good.",
        )
        self.table = LoadStateTable(
            [
                Column("Name", ALIGN_LEFT),
                Column("Email", ALIGN_LEFT),
                Column("Phone", ALIGN_LEFT),
                Column("Licence no.", ALIGN_LEFT),
                Column("Licence", ALIGN_LEFT),
                Column("Blocked"),
                Column("Rentals", ALIGN_RIGHT),
                Column("Since", ALIGN_LEFT),
            ],
            lambda: _customer_rows(self.context),
            empty_message="No customers yet.",
        )
        self.body.addWidget(self.table, 1)

    def refresh(self) -> None:
        super().refresh()
        self.table.load()

    def blocked_customers(self) -> list[str]:
        """Names of customers who cannot rent today.

        Used by the dashboard to explain why a booking was refused, so the
        answer is computed once from the service rather than guessed at in two
        places.
        """
        today = date.today()
        blocked = []
        with self.context.reading() as session:
            for customer in session.execute(
                select(Users).where(Users.role == "customer")
            ).scalars():
                try:
                    check_licence(session, customer, today)
                except Exception:  # noqa: BLE001 - any refusal means blocked
                    blocked.append(customer.full_name)
        return blocked


def build_customers_page(shell) -> CustomersPage:
    return CustomersPage(shell)
