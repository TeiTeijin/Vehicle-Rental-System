"""Visual contract for the "When is it out?" schedule step.

The step is a white sheet inside the frosted rental modal, but the blanket
``QWidget { background: #F1E8D8 }`` rule in staff.qss paints cream behind
every plain host. The date cells and legend items are plain ``QWidget``
hosts, so they need the same transparent ``wizardStep`` recipe as the step
root; otherwise each label sits in its own cream rectangle on the sheet.

These tests pin the transparent hosts and the painted surfaces that must
survive the cleanup: the legend colour swatches, the duration chip, the day
tiles, and the status chips.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate  # noqa: E402
from PySide6.QtWidgets import QLabel, QWidget  # noqa: E402

from app.staff import theme  # noqa: E402
from app.staff.rental_wizard import _date_cell, _legend_item  # noqa: E402


def _qss_block(selector: str) -> str:
    """Return the declarations inside the first ``selector { ... }`` block."""
    css = theme.load_stylesheet()
    marker = f"{selector} {{"
    assert marker in css, f"staff.qss has no rule for {selector}"
    return css.split(marker, 1)[1].split("}", 1)[0]


def test_date_cell_hosts_stay_transparent(qapp):
    parent = QWidget()
    try:
        start_host, start_edit = _date_cell(
            parent, "Pick-up", QDate.currentDate(), QDate.currentDate()
        )
        end_host, end_edit = _date_cell(
            parent, "Return", QDate.currentDate(), QDate.currentDate()
        )

        assert start_host.objectName() == "wizardStep"
        assert end_host.objectName() == "wizardStep"
        assert start_edit.parent() is start_host
        assert end_edit.parent() is end_host

        rule = _qss_block("QWidget#wizardStep")
        assert "background: transparent" in rule
        assert "border: none" in rule
    finally:
        parent.deleteLater()


def test_legend_item_hosts_stay_transparent_and_keep_their_swatches(qapp):
    parent = QWidget()
    try:
        legend = [
            ("Your dates", "#171717"),
            ("Confirmed booking", "#9CC5A7"),
            ("Pending booking", "#EACF9F"),
            ("Free", "#EEEAE1"),
        ]
        hosts = [
            _legend_item(text, colour, parent) for text, colour in legend
        ]

        for host, (text, colour) in zip(hosts, legend):
            assert host.objectName() == "wizardStep"
            chips = [
                label
                for label in host.findChildren(QLabel)
                if label.objectName() == "legendChip"
            ]
            assert len(chips) == 1
            assert f"background: {colour}" in chips[0].styleSheet()
            assert "border-radius: 2px" in chips[0].styleSheet()
            assert any(
                label.text() == text for label in host.findChildren(QLabel)
            )

        assert "border-radius: 2px" in _qss_block("QLabel#legendChip")
    finally:
        parent.deleteLater()


def test_legend_frame_has_no_cream_fill_but_stays_rounded(qapp):
    rule = _qss_block("QFrame#wizardLegend")

    assert "background: transparent" in rule
    assert "border-radius: 8px" in rule
    assert "#F1E8D8" not in rule


def test_schedule_painted_surfaces_keep_their_backgrounds(qapp):
    day_tile = _qss_block("QFrame#dayTile")
    assert "background: #EEEAE1" in day_tile

    assert "background: #171717" in _qss_block('QFrame#dayTile[state="selected"]')
    assert "background: #D7E9DA" in _qss_block(
        'QFrame#dayTile[state="confirmed"]'
    )
    assert "background: #F6E5C6" in _qss_block(
        'QFrame#dayTile[state="pending"]'
    )
    assert "background: #EEEAE1" in _qss_block('QFrame#dayTile[state="free"]')

    assert "background: #EEEAE1" in _qss_block("QLabel#plateChip")

    assert "background: #EEEAE1" in _qss_block("QLabel#statusChip")
    assert "background: #DCEEDC" in _qss_block('QLabel#statusChip[tone="ok"]')
    assert "background: #F6E6C8" in _qss_block(
        'QLabel#statusChip[tone="warn"]'
    )
    assert "background: #F6E0DB" in _qss_block(
        'QLabel#statusChip[tone="danger"]'
    )
    assert "background: #EEEAE1" in _qss_block(
        'QLabel#statusChip[tone="neutral"]'
    )
