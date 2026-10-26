from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import Booking
from app.services import payment_service
from app.services.booking_service import SCHEDULING_FEE
from app.utils.money import pesos_ascii as pesos

RECEIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "receipts"


class Row(NamedTuple):
    text: str
    style: str = "line"


def receipt_rows(session, booking: Booking) -> list[Row]:
    user = booking.user
    vehicle = booking.vehicle
    balance = payment_service.booking_balance(session, booking)
    settled = [
        p
        for p in booking.payments
        if p.status in payment_service.SETTLED_STATUSES
        and p.kind != "deposit_applied"
    ]
    try:
        from app.services import settings_service as _settings
        branch = _settings.get_raw(session, "receipt_branch_name") or "Vehicle Rental System"
        addr = _settings.get_raw(session, "receipt_branch_address")
        phone = _settings.get_raw(session, "receipt_branch_phone")
        show_fee = _settings.get_bool(session, "receipt_fee_note")
        fee = _settings.scheduling_fee(session)
    except Exception:
        branch, addr, phone, show_fee, fee = (
            "Vehicle Rental System", "", "", True, SCHEDULING_FEE)

    rows: list[Row] = [
        Row(branch or "Vehicle Rental System", "title"),
        Row("Rental Receipt", "line"),
        Row("", "blank"),
    ]
    if addr:
        rows.append(Row(addr, "note"))
    if phone:
        rows.append(Row(phone, "note"))
    if addr or phone:
        rows.append(Row("", "blank"))
    rows += [
        Row("Booking Details", "heading"),
        Row(f"Booking ID: {booking.booking_id}"),
    ]
    if booking.reference_code:
        rows.append(Row(f"Reference: {booking.reference_code}"))
    rows.append(Row(f"Status: {booking.status.replace('_', ' ').title()}"))
    creator = getattr(booking, "creator", None)
    if creator is not None:
        rows.append(Row(f"Taken by: {creator.full_name}"))
    rows += [
        Row("", "blank"),

        Row("Customer", "heading"),
        Row(f"Name: {user.full_name}"),
        Row(f"Email: {user.email}"),
        Row(f"Phone: {user.phone}"),
        Row(f"Licence: {user.license_number or 'Not on file (verified at pickup)'}"),
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
    ]
    breakdown = (
        booking.subtotal,
        booking.insurance_fee,
        booking.service_fee,
        booking.vat,
    )
    if all(value is not None for value in breakdown):
        # Online bookings store the same lines the checkout showed. Print
        # them separately so "Rental" is the subtotal, not the grand total.
        rows += [
            Row("Rental", "line"),
            Row(pesos(booking.subtotal), "amount"),
            Row("Insurance", "line"),
            Row(pesos(booking.insurance_fee), "amount"),
            Row("Service fee", "line"),
            Row(pesos(booking.service_fee), "amount"),
            Row("VAT (12%)", "line"),
            Row(pesos(booking.vat), "amount"),
        ]
    else:
        # Counter bookings only have the grand total; label it honestly.
        rows += [
            Row("Rental total", "line"),
            Row(pesos(balance.rental_cost), "amount"),
        ]
    for penalty in booking.penalties:
        rows.append(Row(f"Penalty ({penalty.penalty_type.replace('_', ' ').title()})", "line"))
        rows.append(Row(pesos(penalty.amount), "amount"))
        if penalty.description:
            rows.append(Row(penalty.description, "note"))
    if (
        balance.deposit_held > 0
        or balance.deposit_returned > 0
        or balance.deposit_applied > 0
    ):
        rows.append(Row("", "blank"))
        rows.append(Row("Security deposit (refundable hold)", "heading"))
        if balance.deposit_held > 0:
            rows.append(Row("Deposit held", "line"))
            rows.append(Row(pesos(balance.deposit_held), "amount"))
        if balance.deposit_applied > 0:
            # A ledger adjustment against penalties, never a cash tender.
            rows.append(Row("Deposit applied", "line"))
            rows.append(Row(pesos(balance.deposit_applied), "amount"))
        if balance.deposit_returned > 0:
            rows.append(Row("Deposit returned", "line"))
            rows.append(Row(pesos(balance.deposit_returned), "amount"))
    rows += [
        Row("Total Due", "total"),
        Row(pesos(balance.total_due), "total"),
        Row("", "blank"),

        Row("Payments", "heading"),
    ]

    if settled:
        for payment in settled:
            when = f"{payment.paid_at:%d %b %Y, %H:%M}" if payment.paid_at else ""
            kind = " (deposit)" if payment.kind == "deposit" else ""
            rows.append(Row(f"{when} via {payment.method}{kind}".strip(), "line"))
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
    if show_fee:
        rows.append(Row(f"Includes {pesos(fee)} non-refundable scheduling fee.", "note"))
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

#: How far down the page each style moves the cursor (kept: tests import it).
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

#: Section grid colours. Sand heading, hairline rules, ink text -- the same
#: family as the staff app, so the paper reads as one product with it.
_GRID = colors.HexColor(0xD8CCBB)
_SHADE = colors.HexColor(0xEEEAE1)
_INK = colors.HexColor(0x1A1A1A)
_MUTED = colors.HexColor(0x8A8175)


def _para(text: str, style: str, *, align: str = "LEFT") -> Paragraph:
    font, size = _FONTS[style]
    return Paragraph(
        text.replace("&", "&amp;"),
        ParagraphStyle(
            f"receipt_{style}_{align}",
            fontName=font,
            fontSize=size,
            leading=size * 1.35,
            textColor=_INK,
            alignment={"LEFT": 0, "RIGHT": 2}[align],
        ),
    )


def _note_para(text: str) -> Paragraph:
    font, size = _FONTS["note"]
    return Paragraph(
        text.replace("&", "&amp;"),
        ParagraphStyle(
            "receipt_note",
            fontName=font,
            fontSize=size,
            leading=size * 1.35,
            textColor=_MUTED,
            leftIndent=12,
        ),
    )


def _section_table(
    title: str, body: list[list], spans: list[int], totals: list[int]
) -> Table:
    """One boxed money grid: shaded title row, hairline rules, bold totals.

    Only money sections get a grid. `spans` holds the 0-based body-row
    indexes stretching across both columns (note rows); `totals` holds the
    indexes getting a rule above (Total Due / Total Paid). The title row
    always spans.
    """
    data = [[_para(title, "heading"), ""]] + body
    table = Table(data, colWidths=[5.0 * inch, 2.0 * inch])
    style = [
        ("BOX", (0, 0), (-1, -1), 1, _GRID),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, _GRID),
        ("BACKGROUND", (0, 0), (-1, 0), _SHADE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("SPAN", (0, 0), (1, 0)),
    ]
    for index in spans:
        style.append(("SPAN", (0, index + 1), (1, index + 1)))
    for index in totals:
        style.append(("LINEABOVE", (0, index + 1), (-1, index + 1), 1, _INK))
    table.setStyle(TableStyle(style))
    return table


#: Sections carrying money get a grid; everything else prints as plain lines.
MONEY_SECTIONS = ("Charges", "Security deposit (refundable hold)", "Payments")


def generate_receipt_pdf(session, booking: Booking, dest_dir: Path | None = None) -> str:
    if dest_dir is None:
        try:
            from app.staff import prefs as _prefs
            target = _prefs.export_folder(RECEIPTS_DIR)
        except Exception:
            target = RECEIPTS_DIR
    else:
        target = Path(dest_dir)
    target.mkdir(parents=True, exist_ok=True)
    filepath = target / f"receipt_{booking.booking_id}.pdf"

    # The content model stays the contract (tests assert on receipt_rows);
    # only the drawing changes: money sections fold into boxed grids,
    # everything else prints as plain lines on one compact page.
    title_rows: list = []
    sections: list[tuple[str, list[list], list[int], list[int]]] = []
    current_title: str | None = None
    current_body: list[list] = []
    current_spans: list[int] = []
    current_totals: list[int] = []
    pending_left: str | None = None
    verdict = ""

    def flush() -> None:
        nonlocal current_title, current_body, current_spans, current_totals
        nonlocal pending_left
        if pending_left is not None:
            current_body.append([_para(pending_left, "line"), ""])
            pending_left = None
        if current_title is not None:
            sections.append(
                (current_title, current_body, current_spans, current_totals)
            )
            current_title, current_body = None, []
            current_spans, current_totals = [], []

    for row in receipt_rows(session, booking):
        if not row.text:
            flush()
            continue
        if row.style == "title":
            title_rows.append(row.text)
            continue
        if row.style == "heading":
            flush()
            current_title = row.text
            continue
        if row.style == "verdict":
            flush()
            verdict = row.text
            continue
        if row.style == "note":
            if pending_left is not None:
                current_body.append([_para(pending_left, "line"), ""])
                pending_left = None
            current_spans.append(len(current_body))
            current_body.append([_note_para(row.text), ""])
            continue
        if row.style in ("amount", "total"):
            left = pending_left if pending_left is not None else ""
            pending_left = None
            if row.style == "total":
                current_totals.append(len(current_body))
            left_para = _para(left, "total" if row.style == "total" else "line")
            current_body.append([left_para, _para(row.text, row.style, align="RIGHT")])
            continue
        # Plain line: may pair with a following amount row.
        if pending_left is not None:
            current_body.append([_para(pending_left, "line"), ""])
        pending_left = row.text
    flush()

    story: list = []
    for text in title_rows:
        story.append(_para(text, "title"))
    if title_rows:
        story.append(Spacer(1, 0.08 * inch))
    for section_title, body, spans, totals in sections:
        if section_title in MONEY_SECTIONS:
            story.append(_section_table(section_title, body, spans, totals))
        else:
            # Plain info section: heading + lines, no grid.
            story.append(_para(section_title, "heading"))
            for left, right in body:
                if isinstance(right, Paragraph) and right.text.strip():
                    story.append(
                        _para(f"{left.text}  {right.text}", "line")
                    )
                else:
                    story.append(left)
        story.append(Spacer(1, 0.08 * inch))
    if verdict:
        story.append(_para(verdict, "verdict"))
        story.append(Spacer(1, 0.06 * inch))

    doc = SimpleDocTemplate(
        str(filepath),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=f"Rental Receipt #{booking.booking_id}",
    )
    doc.build(story)
    return str(filepath)


def statement_rows(session, customer) -> list[Row]:
    """Customer history for disputes: every booking with money + payments."""
    from app.services import customer_service

    rows: list[Row] = [
        Row("Vehicle Rental System", "title"),
        Row("Customer Statement", "line"),
        Row("", "blank"),

        Row("Customer", "heading"),
        Row(f"Name: {customer.full_name}"),
        Row(f"Email: {customer.email}"),
        Row(f"Phone: {customer.phone or '-'}"),
        Row(f"Licence: {customer.license_number or 'Not on file (verified at pickup)'}"),
        Row("", "blank"),
    ]
    bookings = (
        session.query(Booking)
        .filter(Booking.user_id == customer.user_id)
        .order_by(Booking.start_date.desc())
        .all()
    )
    if not bookings:
        rows.append(Row("No rentals on record.", "note"))
        return rows
    for booking in bookings:
        vehicle = booking.vehicle
        balance = payment_service.booking_balance(session, booking)
        rows += [
            Row(
                f"Booking #{booking.booking_id} · "
                f"{booking.status.replace('_', ' ').title()}",
                "heading",
            ),
            Row(
                f"{vehicle.make} {vehicle.model} ({vehicle.year}) · "
                f"{booking.start_date:%d %b %Y} → {booking.end_date:%d %b %Y}"
            ),
            Row("Total", "line"),
            Row(pesos(balance.total_due), "amount"),
            Row("Paid", "line"),
            Row(pesos(balance.amount_paid), "amount"),
            Row("Owed", "line"),
            Row(pesos(balance.balance), "amount"),
        ]
        for payment in booking.payments:
            when = payment.paid_at.strftime("%d %b %Y") if payment.paid_at else "pending"
            rows.append(
                Row(f"{when} · {payment.method} · {payment.status} · {payment.kind}", "note")
            )
            rows.append(Row(pesos(payment.amount), "amount"))
        rows.append(Row("", "blank"))
    rows.append(Row("Total outstanding", "total"))
    rows.append(
        Row(pesos(customer_service.outstanding_for(session, customer)), "total")
    )
    return rows


def generate_statement_pdf(session, customer, dest_dir: Path | None = None) -> str:
    """Same grid recipe as receipts (styles frozen); new content function."""
    if dest_dir is None:
        try:
            from app.staff import prefs as _prefs
            target = _prefs.export_folder(RECEIPTS_DIR)
        except Exception:
            target = RECEIPTS_DIR
    else:
        target = Path(dest_dir)
    target.mkdir(parents=True, exist_ok=True)
    filepath = target / f"statement_{customer.user_id}.pdf"
    story: list = [_para("Vehicle Rental System", "title")]
    body: list[list] = []
    spans: list[int] = []
    totals: list[int] = []
    pending_left: str | None = None

    def flush_section(title: str | None) -> None:
        nonlocal body, spans, totals, pending_left
        if pending_left is not None:
            body.append([_para(pending_left, "line"), ""])
            pending_left = None
        if title is not None:
            story.append(_section_table(title, body, spans, totals))
            story.append(Spacer(1, 0.08 * inch))
            body, spans, totals = [], [], []

    current_title: str | None = None
    for row in statement_rows(session, customer):
        if not row.text:
            if current_title is not None:
                flush_section(current_title)
                current_title = None
            continue
        if row.style == "title":
            story.append(_para(row.text, "title"))
            continue
        if row.style == "heading":
            if current_title is not None:
                flush_section(current_title)
            current_title = row.text
            continue
        if row.style == "note":
            if pending_left is not None:
                body.append([_para(pending_left, "line"), ""])
                pending_left = None
            spans.append(len(body))
            body.append([_note_para(row.text), ""])
            continue
        if row.style in ("amount", "total"):
            left = pending_left if pending_left is not None else ""
            pending_left = None
            if row.style == "total":
                totals.append(len(body))
            body.append(
                [
                    _para(left, "total" if row.style == "total" else "line"),
                    _para(row.text, row.style, align="RIGHT"),
                ]
            )
            continue
        if pending_left is not None:
            body.append([_para(pending_left, "line"), ""])
        pending_left = row.text
    if current_title is not None:
        flush_section(current_title)

    doc = SimpleDocTemplate(
        str(filepath),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=f"Customer Statement #{customer.user_id}",
    )
    doc.build(story)
    return str(filepath)
