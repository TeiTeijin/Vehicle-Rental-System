"""Checkout payment cards, logos and the GCash reference rule."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.services import card_validation
from app.services.errors import ValidationError
from app.ui import booking_checkout, theme


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous_style = app.styleSheet()
    theme.apply_theme(app)
    yield app
    app.processEvents()
    app.setStyleSheet(previous_style)
    app.processEvents()


def test_payment_assets_are_installed():
    icons = Path(theme.ICONS_DIR)
    for _key, _title, _note, logo in booking_checkout.PAYMENT_METHODS:
        assert (icons / logo).is_file(), logo


def test_gcash_reference_accepts_one_to_fifteen_digits():
    assert card_validation.GCASH_REFERENCE_MAX_DIGITS == 15
    for value in (
        "123456789",
        "1234567890123",
        "12345678901234",
        "123456789012345",
    ):
        assert card_validation.validate_gcash_reference(value) == value
    assert card_validation.validate_gcash_reference("1234 5678 90123") == (
        "1234567890123"
    )

    for bad in ("", "1234567890123456", "ABCDEFGHIJKLM"):
        with pytest.raises(ValidationError) as excinfo:
            card_validation.validate_gcash_reference(bad)
        assert excinfo.value.field == "gcash_reference"


def test_gcash_charge_keeps_the_customer_reference():
    result = card_validation.collect(
        "gcash",
        Decimal("1500.00"),
        reference="1234567890123",
    )
    assert result.ok is True
    assert result.reference == "1234567890123"
    assert "1234567890123" in result.message


def test_method_card_uses_the_supplied_logo_and_darkens_when_selected(qapp):
    key, title, note, logo = booking_checkout.PAYMENT_METHODS[0]
    card = booking_checkout.MethodCard(key, title, note, logo)
    try:
        assert card.logo.pixmap().isNull() is False
        card.set_selected(True)
        assert card.property("selected") is True
        assert card.title_label.property("selected") is True
        assert card.note_label.property("selected") is True
        assert card.check.isHidden() is False
    finally:
        card.deleteLater()


def test_gcash_qr_label_click_opens_the_preview(qapp):
    label = booking_checkout.ClickableQrLabel()
    seen: list[bool] = []
    label.clicked.connect(lambda: seen.append(True))
    try:
        label.resize(100, 100)
        label.show()
        qapp.processEvents()
        QTest.mouseClick(label, Qt.MouseButton.LeftButton)
        assert seen == [True]
    finally:
        label.close()


def test_gcash_qr_preview_is_fixed_and_keeps_the_source_sharp(qapp):
    source = QPixmap(str(Path(theme.ICONS_DIR) / "Gcash_QR.jpg"))
    preview = booking_checkout.qr_preview_pixmap(source)
    assert preview.width() < source.width()
    assert 0.8 <= preview.width() / preview.height() <= 1.2

    dialog = booking_checkout.QrPreviewDialog(preview)
    try:
        assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert dialog.minimumSize() == dialog.maximumSize()
        initial = dialog.image.pixmap().size()
        dialog.resize(1000, 800)
        qapp.processEvents()
        assert dialog.image.pixmap().size() == initial
        assert initial.isNull() is False
    finally:
        dialog.close()
