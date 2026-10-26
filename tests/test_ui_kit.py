"""Shared warm UI kit: chips, selection bar, tiles, warm tables, modals."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QLabel, QPushButton, QWidget  # noqa: E402

from app.staff.overlay import OVERLAY_CARD_W, FrostedOverlay  # noqa: E402
from app.staff.tables import ALIGN_LEFT, Column, LoadStateTable  # noqa: E402
from app.staff.ui_kit import (  # noqa: E402
    BookingModal,
    ChipGroup,
    MetricTile,
    PageHeading,
    SelectionBar,
    StatusChipRow,
    WarmPanel,
)


def test_chip_row_selects_and_emits_once(qapp):
    row = StatusChipRow(
        [("all", "All"), ("pending", "Pending"), ("confirmed", "Confirmed")]
    )
    seen: list[str] = []
    row.changed.connect(seen.append)

    row.buttons["pending"].click()

    assert row.selected == "pending"
    assert seen == ["pending"]
    assert row.buttons["pending"].isChecked()
    assert not row.buttons["all"].isChecked()


def test_selection_bar_inactive_disables_and_prompts(qapp):
    bar = SelectionBar()
    action = QPushButton("Details")
    primary = QPushButton("Record payment")
    bar.add_action(action)
    bar.add_action(primary, kind="primary")

    bar.set_active(False)

    assert not action.isEnabled()
    assert not primary.isEnabled()
    assert "Select a row" in bar.summary_label.text()


def test_selection_bar_summary_accepts_rich_parts(qapp):
    bar = SelectionBar()
    bar.set_parts("Selected ", "BK-1042", "Toyota Vios")
    text = bar.summary_label.text()
    assert "BK-1042" in text
    assert "Toyota Vios" in text


def test_metric_tile_updates(qapp):
    tile = MetricTile("Total due", "₱ 0.00")
    tile.set_value("₱ 16,625.00")
    assert tile.value_label.text() == "₱ 16,625.00"
    assert tile.label.text() == "Total due"


def test_warm_panel_marks_variant(qapp):
    panel = WarmPanel()
    assert panel.objectName() == "pagePanel"
    assert panel.property("variant") == "warm"
    assert panel.body.contentsMargins().left() == 16


def test_page_heading_has_inline_subtitle(qapp):
    heading = PageHeading("Bookings", "Latest 100")
    assert heading.title_label.text() == "Bookings"
    assert heading.subtitle_label.text() == "Latest 100"
    heading.set_subtitle("1-10 of latest 10")
    assert heading.subtitle_label.text() == "1-10 of latest 10"


def test_warm_table_sets_variant_height_and_chip(qapp):
    table = LoadStateTable(
        [
            Column("Ref", ALIGN_LEFT),
            Column("Status", ALIGN_LEFT, kind="status"),
            Column("Customer", ALIGN_LEFT, kind="strong"),
        ],
        lambda: [("BK-1", "confirmed", "Ana Cruz")],
        variant="warm",
    )

    assert table._table.property("variant") == "warm"
    assert table._table.verticalHeader().defaultSectionSize() == 56
    assert table._table.alternatingRowColors() is False
    assert table._table.wordWrap() is True
    assert table.state.name == "LOADED"
    assert table.cell_text(0, 1) == "Confirmed"
    assert table.cell_text(0, 2) == "Ana Cruz"


def test_default_table_stays_unchanged(qapp):
    table = LoadStateTable(
        [Column("Ref", ALIGN_LEFT)], lambda: [("BK-1",)]
    )
    assert table._table.property("variant") is None
    assert table._table.alternatingRowColors() is True
    assert table._table.wordWrap() is False


def test_overlay_card_width_override(qapp):
    window = QWidget()
    overlay = FrostedOverlay(window, card_width=540)
    assert overlay.scroll.width() == 540
    overlay.set_card_width(OVERLAY_CARD_W)
    assert overlay.scroll.width() == OVERLAY_CARD_W


def test_booking_modal_is_540_and_exposes_parts(qapp):
    modal = BookingModal("Booking · BK-1", "Booking details", "Toyota Vios")
    assert modal.card.width() == 540
    assert modal.eyebrow_label.text() == "BOOKING · BK-1"
    assert modal.title_label.text() == "Booking details"
    assert modal.subtitle_label.text() == "Toyota Vios"
    assert modal.body is modal._body
    assert isinstance(modal.error_label, QLabel)
    primary = modal.add_primary("Close")
    assert isinstance(primary, QPushButton)
    assert primary.objectName() == "modalPrimary"


def test_chip_group_variants_and_selection(qapp):
    group = ChipGroup(
        [("fuel", "Full"), ("half", "Half")], variant="fuel", expanding=True
    )
    group.select("half")
    assert group.selected == "half"
    assert group.buttons["half"].property("variant") == "fuel"
    assert group.buttons["half"].isChecked()
