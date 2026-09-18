from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

from app.models import Booking, Penalty, Payment, Users, Vehicle

RECEIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "receipts"


def generate_receipt_pdf(session, booking: Booking) -> str:
    RECEIPTS_DIR.mkdir(exist_ok=True)
    filepath = RECEIPTS_DIR / f"receipt_{booking.booking_id}.pdf"

    user = session.query(Users).filter_by(user_id=booking.user_id).one()
    vehicle = session.query(Vehicle).filter_by(vehicle_id=booking.vehicle_id).one()
    payment = session.query(Payment).filter_by(booking_id=booking.booking_id).first()
    penalties = session.query(Penalty).filter_by(booking_id=booking.booking_id).all()

    c = canvas.Canvas(str(filepath), pagesize=letter)
    width, height = letter
    y = height - inch

    def _heading(text):
        nonlocal y
        c.setFont("Helvetica-Bold", 12)
        c.drawString(inch, y, text)
        y -= 0.2 * inch

    def _line(text, bold=False):
        nonlocal y
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
        c.drawString(inch, y, text)
        y -= 0.15 * inch

    def _gap(size=0.15):
        nonlocal y
        y -= size * inch

    _heading("Vehicle Rental System")
    c.setFont("Helvetica", 10)
    c.drawString(inch, y, "Rental Receipt")
    _gap(0.35)

    _heading("Booking Details")
    _line(f"Booking ID: {booking.booking_id}")
    _line(f"Status: {booking.status}")
    _gap()

    _heading("Customer")
    _line(f"Name: {user.full_name}")
    _line(f"Email: {user.email}")
    _line(f"Phone: {user.phone}")
    _gap()

    _heading("Vehicle")
    _line(f"{vehicle.make} {vehicle.model} ({vehicle.year})")
    _line(f"Plate: {vehicle.plate_number}")
    _gap()

    _heading("Rental Period")
    _line(f"Start: {booking.start_date}")
    _line(f"End: {booking.end_date}")
    if booking.actual_return_date:
        _line(f"Actual Return: {booking.actual_return_date}")
    _gap()

    _heading("Charges")
    _line(f"Base Cost: Php {booking.total_cost}")
    total_penalty = 0.0
    for p in penalties:
        _line(f"Penalty ({p.penalty_type}): Php {p.amount}")
        total_penalty += float(p.amount)
    if total_penalty > 0:
        _line(f"Total Penalties: Php {total_penalty:.2f}", bold=True)
    total = float(booking.total_cost) + total_penalty
    _line(f"Total: Php {total:.2f}", bold=True)
    _gap()

    if payment:
        _heading("Payment")
        _line(f"Method: {payment.method}")
        _line(f"Status: {payment.status}")
        if payment.paid_at:
            _line(f"Paid At: {payment.paid_at}")
        _gap()

    c.save()
    return str(filepath)