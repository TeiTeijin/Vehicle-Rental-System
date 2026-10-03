"""Tests for the receipt.

The receipt is the artefact the customer takes away, so the property that
matters most is that it agrees with every other screen. These tests assert
against `receipt_rows` -- the content model the PDF is drawn from -- because
the figures that must be right are ordinary Python values there, rather than
strings buried in a compressed PDF stream.
"""

from datetime import date

import pytest

from app.services import booking_service, payment_service
from app.services.receipt_service import generate_receipt_pdf, receipt_rows

D1 = date(2026, 9, 1)
D6 = date(2026, 9, 6)


@pytest.fixture(autouse=True)
def receipts_dir(tmp_path, monkeypatch):
    """Keep generated PDFs out of the real receipts/ folder."""
    import app.services.receipt_service as receipt_service

    monkeypatch.setattr(receipt_service, "RECEIPTS_DIR", tmp_path / "receipts")
    return tmp_path / "receipts"


def lines(session, booking) -> str:
    """The receipt's text, newline-separated, as a customer would read it."""
    return "\n".join(row.text for row in receipt_rows(session, booking))


def amounts(session, booking) -> list[str]:
    """Just the figures, in the order they appear in the amounts column."""
    return [row.text for row in receipt_rows(session, booking) if row.style == "amount"]


class TestReceiptContent:
    def test_shows_the_rental_total(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        assert "PHP 12,500.00" in lines(session, booking)

    def test_reports_an_unpaid_booking_as_outstanding(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        text = lines(session, booking)
        assert "OUTSTANDING" in text
        assert "PAID IN FULL" not in text

    def test_reports_a_settled_booking_as_paid(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        payment_service.record_payment(session, booking, booking.total_cost, "cash")
        text = lines(session, booking)
        assert "PAID IN FULL" in text
        assert "OUTSTANDING" not in text

    def test_total_due_matches_the_service_balance(self, session, customer, vehicle, staff):
        """The property that matters: one number, computed once."""
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.apply_penalty(session, booking, "cleaning", 750, "Interior")
        payment_service.record_payment(session, booking, 5000, "gcash")
        balance = payment_service.booking_balance(session, booking)

        assert balance.total_due == 13250
        assert balance.balance == 8250
        text = lines(session, booking)
        assert "PHP 13,250.00" in text
        assert "PHP 5,000.00" in text
        assert "PHP 8,250.00" in text

    def test_lists_every_payment_not_just_the_first(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        payment_service.record_payment(session, booking, 5000, "gcash")
        payment_service.record_payment(session, booking, 7500, "cash")
        assert amounts(session, booking) == [
            "PHP 12,500.00",
            "PHP 5,000.00",
            "PHP 7,500.00",
        ]
        assert "PAID IN FULL" in lines(session, booking)

    def test_omits_a_refunded_payment_from_the_total(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        payment = payment_service.record_payment(session, booking, 12500, "card")
        payment_service.refund_payment(session, payment, reason="Cancelled")
        text = lines(session, booking)
        assert "OUTSTANDING" in text
        assert "PAID IN FULL" not in text

    def test_omits_an_unpaid_attempt_from_the_total(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        payment_service.record_payment(session, booking, 12500, "card", status="failed")
        assert "OUTSTANDING" in lines(session, booking)

    def test_says_so_when_nothing_was_paid(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        assert "No payments recorded" in lines(session, booking)

    def test_shows_who_took_the_booking(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(
            session, customer, vehicle, D1, D6, created_by=staff.user_id
        )
        assert "Taken by: Staff User" in lines(session, booking)

    def test_shows_who_took_each_payment(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        payment_service.record_payment(
            session, booking, 12500, "gcash", recorded_by=staff.user_id, reference_no="GC-9"
        )
        text = lines(session, booking)
        assert "Taken by Staff User (ref GC-9)" in text

    def test_shows_the_licence(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        assert customer.license_number in lines(session, booking)

    def test_shows_the_actual_return_when_there_is_one(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        payment_service.record_payment(session, booking, booking.total_cost, "cash")
        booking_service.check_in(session, booking, staff, 42000, "full")
        booking_service.check_out(session, booking, staff, D6, 42100, "full")
        assert "Actual return" in lines(session, booking)

    def test_omits_the_return_line_for_an_open_rental(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        assert "Actual return" not in lines(session, booking)

    def test_shows_each_penalty_and_its_reason(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.apply_penalty(session, booking, "damage", 2500, "Rear bumper")
        text = lines(session, booking)
        assert "Penalty (Damage)" in text
        assert "Rear bumper" in text
        assert "PHP 2,500.00" in text


class TestReceiptFile:
    def test_writes_a_pdf(self, session, customer, vehicle, staff, receipts_dir):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        path = generate_receipt_pdf(session, booking)
        assert path.endswith(f"receipt_{booking.booking_id}.pdf")

    def test_creates_the_directory(self, session, customer, vehicle, staff, tmp_path):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        dest = tmp_path / "deeply" / "nested"
        generate_receipt_pdf(session, booking, dest_dir=dest)
        assert dest.is_dir()

    def test_the_file_is_a_real_pdf(self, session, customer, vehicle, staff):
        from pathlib import Path

        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        path = Path(generate_receipt_pdf(session, booking))
        assert path.stat().st_size > 0
        assert path.read_bytes().startswith(b"%PDF")

    def test_reprinting_replaces_the_file_and_updates_the_content(
        self, session, customer, vehicle, staff
    ):
        from pathlib import Path

        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        first = Path(generate_receipt_pdf(session, booking))
        assert "OUTSTANDING" in lines(session, booking)

        payment_service.record_payment(session, booking, booking.total_cost, "cash")
        second = Path(generate_receipt_pdf(session, booking))
        assert first == second
        assert "PAID IN FULL" in lines(session, booking)

    def test_a_long_history_does_not_overflow_into_a_crash(self, session, customer, vehicle, staff):
        from pathlib import Path

        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        for i in range(6):
            payment_service.record_payment(session, booking, 1000, "cash")
        path = Path(generate_receipt_pdf(session, booking))
        assert path.read_bytes().startswith(b"%PDF")


class TestReceiptCurrencyIsRenderable:
    """The peso glyph does not exist in the fonts reportlab ships with.

    Drawing "₱1,250.00" with the built-in Helvetica silently produces a blank
    box, so the receipt spells the currency out instead. This class is here to
    stop someone "tidying" `pesos_ascii` back to `pesos` and shipping a receipt
    whose every amount is unreadable.
    """

    def test_the_receipt_never_contains_the_peso_glyph(self, session, customer, vehicle, staff):
        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        text = lines(session, booking)
        assert "₱" not in text
        assert "PHP" in text

    def test_every_receipt_row_is_drawable_in_helvetica(self, session, customer, vehicle, staff):
        from reportlab.pdfbase.pdfmetrics import stringWidth

        from app.services.receipt_service import _FONTS

        booking = booking_service.create_booking(session, customer, vehicle, D1, D6)
        booking_service.apply_penalty(session, booking, "damage", 2500, "Rear bumper")
        payment_service.record_payment(
            session, booking, 12500, "gcash", recorded_by=staff.user_id
        )
        for row in receipt_rows(session, booking):
            if not row.text:
                continue
            font, size = _FONTS[row.style]
            assert stringWidth(row.text, font, size) > 0, row.text

    def test_the_ascii_formatter_agrees_with_the_unicode_one(self):
        from app.utils.money import pesos, pesos_ascii

        assert pesos_ascii(12500) == "PHP " + pesos(12500).lstrip("₱")
