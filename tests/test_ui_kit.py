"""Shared warm UI kit: chips, selection bar, tiles, warm tables, modals."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QTimer, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QPushButton,
    QWidget,
)

from app.staff import theme  # noqa: E402
from app.staff.overlay import (  # noqa: E402
    OVERLAY_CARD_W,
    FrostedOverlay,
)
from app.staff.tables import (  # noqa: E402
    ALIGN_LEFT,
    Column,
    LoadStateTable,
    TableChip,
)
from app.staff.ui_kit import (  # noqa: E402
    BookingModal,
    ChipGroup,
    ConfirmPanel,
    MetricTile,
    SelectionBar,
    StatusChipRow,
    WarmPanel,
)
from app.staff.widgets import confirm_modal  # noqa: E402


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


# -- warm confirmation modal -------------------------------------------------


def test_confirm_panel_title_detail_and_destructive_button(qapp):
    panel = ConfirmPanel(
        "Discard this rental?",
        "The customer, dates, and payment choice will be lost.",
        destructive=True,
        confirm_text="Discard rental",
    )

    assert panel.eyebrow_label.text() == "PLEASE CONFIRM"
    assert panel.title_label.text() == "Discard this rental?"
    assert panel.subtitle_label.text() == (
        "The customer, dates, and payment choice will be lost."
    )
    assert panel.keep_button.text() == "Keep editing"
    assert panel.confirm_button.text() == "Discard rental"
    assert panel.keep_button.objectName() == "modalSecondary"
    assert panel.confirm_button.objectName() == "modalDanger"
    assert panel.card.width() == 540
    assert panel.objectName() == "confirmPanel"
    # The renamed host needs its own transparent rule; otherwise the blanket
    # QWidget cream paints a 540px column behind the sheet.
    assert "QWidget#confirmPanel" in theme.load_stylesheet()


def test_confirm_panel_default_button_is_not_destructive(qapp):
    panel = ConfirmPanel("Remove this vehicle?")

    assert panel.confirm_button.text() == "Yes"
    assert panel.confirm_button.objectName() == "modalPrimary"


def test_confirm_modal_honours_the_settings_toggle(qapp, monkeypatch):
    from app.staff import prefs

    monkeypatch.setattr(prefs, "get", lambda _key: False)
    parent = QWidget()
    try:
        assert (
            confirm_modal(
                parent,
                "Retire DEM-108?",
                pref="behavior/confirm_retire",
            )
            is True
        )
        assert parent.findChildren(FrostedOverlay) == []
    finally:
        parent.deleteLater()


def test_confirm_panel_buttons_emit_confirmed_and_cancelled(qapp):
    panel = ConfirmPanel("Discard this rental?")
    seen: list[str] = []
    panel.confirmed.connect(lambda: seen.append("confirmed"))
    panel.cancelled.connect(lambda: seen.append("cancelled"))

    panel.confirm_button.click()
    panel.keep_button.click()

    assert seen == ["confirmed", "cancelled"]


def _open_window(qapp: QApplication) -> QWidget:
    window = QWidget()
    window.resize(900, 700)
    window.show()
    qapp.processEvents()
    return window


def _close_window(qapp: QApplication, window: QWidget) -> None:
    window.close()
    window.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _drive_confirm_modal(
    qapp: QApplication, window: QWidget, action: str, **kwargs
) -> tuple[bool, dict]:
    """Run confirm_modal and act on it from inside its nested event loop.

    The action fires on a zero-delay timer, so the overlay and panel are
    already on screen. A watchdog closes any overlay left behind if the
    action never lands, turning a wiring regression into a failure instead
    of a hung test run.
    """
    seen: dict = {}
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.setInterval(3000)

    def abort() -> None:
        seen["timed_out"] = True
        for overlay in window.findChildren(FrostedOverlay):
            overlay.hide_overlay()

    watchdog.timeout.connect(abort)
    watchdog.start()

    def act() -> None:
        overlay = window.findChild(FrostedOverlay)
        seen["overlay"] = overlay
        if overlay is None:
            return
        panel = overlay.findChild(ConfirmPanel)
        seen["shown"] = overlay.isVisible()
        seen["tint"] = overlay.styleSheet()
        seen["card_width"] = panel.card.width()
        seen["title"] = panel.title_label.text()
        seen["detail"] = panel.subtitle_label.text()
        seen["confirm_text"] = panel.confirm_button.text()
        seen["confirm_object"] = panel.confirm_button.objectName()
        seen["keep_text"] = panel.keep_button.text()
        seen["keep_focus"] = qapp.focusWidget() is panel.keep_button
        if action == "confirm":
            panel.confirm_button.click()
        elif action == "keep":
            panel.keep_button.click()
        elif action == "escape":
            QTest.keyClick(panel.keep_button, Qt.Key.Key_Escape)
        elif action == "outside":
            QTest.mouseClick(
                overlay, Qt.MouseButton.LeftButton, pos=QPoint(4, 4)
            )
        else:  # pragma: no cover - guards against a typo in a test
            raise AssertionError(action)

    QTimer.singleShot(0, act)
    try:
        result = confirm_modal(window, **kwargs)
    finally:
        watchdog.stop()
    return result, seen


def _assert_confirm_modal_cleaned_up(
    qapp: QApplication, window: QWidget, seen: dict
) -> None:
    assert seen.get("timed_out") is not True
    assert seen["shown"] is True
    assert seen["keep_focus"] is True
    assert "rgba(23, 23, 23, 110)" in seen["tint"]
    assert seen["card_width"] == 540
    overlay = seen["overlay"]
    assert overlay.isVisible() is False
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert window.findChildren(FrostedOverlay) == []


def test_confirm_modal_confirm_returns_true_and_cleans_up(qapp):
    window = _open_window(qapp)
    try:
        result, seen = _drive_confirm_modal(
            qapp,
            window,
            "confirm",
            question="Discard this rental?",
            detail="The customer, dates, and payment choice will be lost.",
            destructive=True,
            confirm_text="Discard rental",
        )

        assert result is True
        assert seen["title"] == "Discard this rental?"
        assert seen["detail"] == (
            "The customer, dates, and payment choice will be lost."
        )
        assert seen["confirm_text"] == "Discard rental"
        assert seen["confirm_object"] == "modalDanger"
        assert seen["keep_text"] == "Keep editing"
        _assert_confirm_modal_cleaned_up(qapp, window, seen)
    finally:
        _close_window(qapp, window)


def test_confirm_modal_keep_returns_false_and_cleans_up(qapp):
    window = _open_window(qapp)
    try:
        result, seen = _drive_confirm_modal(
            qapp,
            window,
            "keep",
            question="Discard this rental?",
            destructive=True,
            confirm_text="Discard rental",
        )

        assert result is False
        _assert_confirm_modal_cleaned_up(qapp, window, seen)
    finally:
        _close_window(qapp, window)


def test_confirm_modal_escape_returns_false_and_cleans_up(qapp):
    window = _open_window(qapp)
    try:
        result, seen = _drive_confirm_modal(
            qapp, window, "escape", question="Discard this rental?"
        )

        assert result is False
        _assert_confirm_modal_cleaned_up(qapp, window, seen)
    finally:
        _close_window(qapp, window)


def test_confirm_modal_outside_click_returns_false_and_cleans_up(qapp):
    window = _open_window(qapp)
    try:
        result, seen = _drive_confirm_modal(
            qapp, window, "outside", question="Discard this rental?"
        )

        assert result is False
        _assert_confirm_modal_cleaned_up(qapp, window, seen)
    finally:
        _close_window(qapp, window)
