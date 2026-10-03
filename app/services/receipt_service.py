"""PDF rental receipts.

This used to compute its own totals inline and read a single payment with
`.first()`, so a booking settled in two parts printed one payment and a total
that ignored the deposit. It now renders `payment_service.booking_balance`,
the same figure every other screen shows, so the receipt cannot disagree with
the screen it was printed from.

The content is built as a flat list of `(text, style)` rows by `receipt_rows`
and then drawn by `generate_receipt_pdf`. Splitting it this way means the
things worth asserting on -- the totals, the balance, the list of payments --
are ordinary Python values that tests can read, rather than strings buried
inside a compressed PDF stream. `generate_receipt_pdf` is left with nothing but
layout, which is the part that does not need testing.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

from app.models import Booking
from app.services import payment_service
from app.utils.money import pesos_ascii as pesos

RECEIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "receipts"

#: Right-hand column position, in points from the left edge.
RIGHT_MARGIN_X = letter[0] - inch


class Row(NamedTuple):
    """One line of the receipt.

    style is one of:
        blank     vertical space
        title     the business name, centred weight
        heading   a section heading
        line      body text
        amount    body text, right-aligned in the figures column
        total     right-aligned and bold, for totals
        note      small italic, for detail under a line
        verdict   large bold, the settled/outstanding line
    """

    text: str
    style: str = "line"


def receipt_rows(session, booking: Booking) -> list[Row]:
    """Build the receipt's content.

    Amounts are rendered through the shared money helpers so the receipt, the
    screen and the balance all print the same figure the same way.
    """
    user = booking.user
    vehicle = booking.vehicle
    balance = payment_service.booking_balance(session, booking)
    settled = [p for p in booking.payments if p.status in payment_service.SETTLED_STATUSES]

    rows: list[Row] = [
        Row("Vehicle Rental System", "title"),
        Row("Rental Receipt", "line"),
        Row("", "blank"),

        Row("Booking Details", "heading"),
        Row(f"Booking ID: {booking.booking_id}"),
        Row(f"Status: {booking.status.replace('_', ' ').title()}"),
    ]
    creator = getattr(booking, "creator", None)
    if creator is not None:
        rows.append(Row(f"Taken by: {creator.full_name}"))
    rows += [
        Row("", "blank"),

        Row("Customer", "heading"),
        Row(f"Name: {user.full_name}"),
        Row(f"Email: {user.email}"),
        Row(f"Phone: {user.phone}"),
        Row(f"Licence: {user.license_number}"),
        Row("", "blank"),

        Row("Vehicle", "heading"),
        Row(f"{vehicle.make} {vehicle.model} ({vehicle.year})"),
        Row(f"Plate: {vehicle.plate_number}"),
        Row("", "blank"),

        Row("Rental Period", "heading"),
        Row(f"Pick-up: {booking.start_date:%d %b %Y}"),
        Row(f"Return: {booking.end_date:%d %b %Y}"),
    ]
    if booking.actual_return_date:
        rows.append(Row(f"Actual return: {booking.actual_return_date:%d %b %Y}"))
    rows += [
        Row("", "blank"),

        Row("Charges", "heading"),
        Row("Base rental", "line"),
        Row(pesos(balance.rental_cost), "amount"),
    ]
    for penalty in booking.penalties:
        rows.append(Row(f"Penalty ({penalty.penalty_type.replace('_', ' ').title()})", "line"))
        rows.append(Row(pesos(penalty.amount), "amount"))
        if penalty.description:
            rows.append(Row(penalty.description, "note"))
    rows += [
        Row("Total Due", "total"),
        Row(pesos(balance.total_due), "total"),
        Row("", "blank"),

        Row("Payments", "heading"),
    ]

    if settled:
        for payment in settled:
            when = f"{payment.paid_at:%d %b %Y, %H:%M}" if payment.paid_at else ""
            rows.append(Row(f"{when} via {payment.method}".strip(), "line"))
            rows.append(Row(pesos(payment.amount), "amount"))
            if payment.recorder is not None:
                detail = f"Taken by {payment.recorder.full_name}"
                if payment.reference_no:
                    detail += f" (ref {payment.reference_no})"
                rows.append(Row(detail, "note"))
        rows.append(Row("Total Paid", "total"))
        rows.append(Row(pesos(balance.amount_paid), "total"))
    else:
        rows.append(Row("No payments recorded against this booking.", "note"))
    rows.append(Row("", "blank"))

    rows.append(
        Row(
            "PAID IN FULL - Thank you!"
            if balance.is_settled
            else f"OUTSTANDING: {pesos(balance.balance)}",
            "verdict",
        )
    )
    return rows


#: (font, size) per style.
_FONTS = {
    "title": ("Helvetica-Bold", 16),
    "heading": ("Helvetica-Bold", 12),
    "line": ("Helvetica", 10),
    "amount": ("Helvetica", 10),
    "total": ("Helvetica-Bold", 10),
    "note": ("Helvetica-Oblique", 8),
    "verdict": ("Helvetica-Bold", 13),
}

#: How far down the page each style moves the cursor.
_GAPS = {
    "title": 0.30,
    "heading": 0.24,
    "line": 0.16,
    "amount": 0.16,
    "total": 0.18,
    "note": 0.14,
    "verdict": 0.26,
    "blank": 0.18,
}


def generate_receipt_pdf(session, booking: Booking, dest_dir: Path | None = None) -> str:
    """Write a receipt for `booking` and return its path.

    Overwrites any previous receipt for the same booking, which is right: a
    reprinted receipt should reflect the current state of the account, not the
    state it had the first time it was printed.
    """
    target = Path(dest_dir) if dest_dir else RECEIPTS_DIR
    target.mkdir(parents=True, exist_ok=True)
    filepath = target / f"receipt_{booking.booking_id}.pdf"

    c = canvas.Canvas(str(filepath), pagesize=letter)
    y = letter[1] - inch

    for row in receipt_rows(session, booking):
        if not row.text:
            y -= _GAPS["blank"] * inch
            continue
        font, size = _FONTS[row.style]
        c.setFont(font, size)
        if row.style in ("amount", "total"):
            c.drawRightString(RIGHT_MARGIN_X, y, row.text)
        elif row.style == "note":
            c.drawString(inch + 0.2 * inch, y, row.text)
        else:
            c.drawString(inch, y, row.text)
        y -= _GAPS[row.style] * inch

    c.save()
    return str(filepath)
