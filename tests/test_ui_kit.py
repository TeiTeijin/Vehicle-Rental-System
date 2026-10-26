"""Shared warm UI kit: chips, selection bar, tiles, warm tables, modals."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QPushButton,
    QWidget,
)

from app.staff import theme  # noqa: E402
from app.staff.overlay import OVERLAY_CARD_W, FrostedOverlay  # noqa: E402
from app.staff.tables import (  # noqa: E402
    ALIGN_LEFT,
    Column,
    LoadStateTable,
    TableChip,
)
from app.staff.ui_kit import (  # noqa: E402
    BookingModal,
    ChipGroup,
    MetricTile,
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


def test_table_chip_is_a_true_pill(qapp):
    chip = TableChip("Active", tone="ok")
    assert chip.height() == TableChip.HEIGHT == 22
    block = theme.load_stylesheet().split("QLabel#tableChip {", 1)[1].split(
        "}", 1
    )[0]
    # Qt drops a border radius larger than half the label height, which is
    # what left the old 19px chip square. Tie the QSS value to the height.
    assert f"border-radius: {TableChip.HEIGHT // 2}px" in block


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


def test_overlay_wheel_scrolls_from_the_backdrop(qapp):
    window = QWidget()
    window.resize(700, 600)
    overlay = FrostedOverlay(window, card_width=540)
    content = QWidget()
    content.setMinimumHeight(1200)
    overlay.set_content(content)
    overlay.show_overlay()
    qapp.processEvents()

    bar = overlay.scroll.verticalScrollBar()
    assert bar.maximum() > 0
    # x=10 is on the frosted glass, left of the 540px column.
    event = QWheelEvent(
        QPointF(10, 300),
        QPointF(10, 300),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(overlay, event)

    assert event.isAccepted() is True
    assert bar.value() > 0
    overlay.hide_overlay()
    window.deleteLater()


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
