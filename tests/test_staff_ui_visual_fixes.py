"""Regression tests for the staff sidebar and dashboard-chart clipping.

Two confirmed visual defects:

* At the 900x640 window minimum the sidebar forced its nav scrollbar off, so
  Maintenance and Settings sat below the viewport with no way to reach them.
* Painted dashboard labels clipped their text: the heatmap's 22px weekday
  rect rendered "Mon" as "von", and the sales-axis formatter dropped the M
  from "₱1M"/"₱2M".
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402

from app.staff.dashboard_charts import (  # noqa: E402
    ActivityHeatmap,
    _compact,
    _font,
)
from app.staff.metrics import (  # noqa: E402
    AXIS_SIZE,
    AXIS_SIZE_LARGE,
    CELL_MIN,
    HEATMAP_LABEL_GUTTER,
    HEATMAP_TOP,
    SIDEBAR_W,
    WINDOW_MIN_H,
)
from app.staff.sidebar import COLLAPSED_W, Sidebar  # noqa: E402

#: The full staff nav: enough rows to overflow the rail at the 640px minimum.
SECTIONS = [
    (
        "General",
        [
            ("dashboard", "Dashboard", "dashboard", True),
            ("new_rental", "New Rental", "pos", True),
            ("bookings", "Bookings", "bookings", True),
            ("fleet", "Fleet", "vehicle", True),
            ("customers", "Customers", "customers", True),
            ("payments", "Payments", "card", True),
            ("inspections", "Inspections", "check", True),
            ("reports", "Reports", "reports", True),
        ],
    ),
    (
        "Tools",
        [
            ("maintenance", "Maintenance", "maintenance", True),
            ("settings", "Settings", "settings", True),
        ],
    ),
]


class TestSidebarScrollsAtTheMinimumHeight:
    def test_the_last_nav_row_is_reachable_in_a_short_sidebar(self, qapp):
        bar = Sidebar()
        bar.build(SECTIONS)
        bar.resize(SIDEBAR_W, WINDOW_MIN_H - 32)  # the 640px window's rail
        bar.show()
        try:
            qapp.processEvents()

            scrollbar = bar._scroll.verticalScrollBar()
            assert (
                bar._scroll.verticalScrollBarPolicy()
                == Qt.ScrollBarPolicy.ScrollBarAsNeeded
            )
            assert scrollbar.maximum() > 0, "the nav list did not overflow"

            last = bar.item("settings")
            assert last is not None
            scrollbar.setValue(scrollbar.maximum())
            qapp.processEvents()

            top = last.mapTo(bar._scroll.viewport(), QPoint(0, 0)).y()
            viewport_h = bar._scroll.viewport().height()
            assert top >= 0
            assert top + last.height() <= viewport_h + 1, (
                "the last nav row is still below the viewport after scrolling"
            )
        finally:
            bar.deleteLater()

    def test_the_collapsed_rail_keeps_its_64px_width_and_scrolls(self, qapp):
        bar = Sidebar()
        bar.build(SECTIONS)
        bar.resize(SIDEBAR_W, WINDOW_MIN_H - 32)
        bar.show()
        try:
            bar.set_collapsed(True)
            qapp.processEvents()

            assert bar.width() == COLLAPSED_W == 64
            assert (
                bar._scroll.verticalScrollBarPolicy()
                == Qt.ScrollBarPolicy.ScrollBarAsNeeded
            )
            assert bar._scroll.verticalScrollBar().maximum() > 0
        finally:
            bar.deleteLater()


class TestPaintedLabelsAreNotClipped:
    def test_the_heatmap_weekday_rect_fits_the_widest_day(self, qapp):
        metrics = QFontMetrics(_font(AXIS_SIZE_LARGE))
        widest = max(
            metrics.horizontalAdvance(day) for day in ("Mon", "Wed", "Fri")
        )
        rect = ActivityHeatmap._weekday_rect(HEATMAP_TOP, 2, float(CELL_MIN))

        assert rect.width() >= widest, (
            f"weekday rect {rect.width()}px is narrower than "
            f"the {widest}px 'Wed'"
        )
        assert rect.right() <= HEATMAP_LABEL_GUTTER

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (1_000_000, "₱1M"),
            (2_000_000, "₱2M"),
            (1_500_000, "₱1.5M"),
            (500_000, "₱500k"),
            (0, "₱0"),
        ],
    )
    def test_compact_money_keeps_the_million_suffix(self, value, expected):
        assert _compact(value) == expected

    def test_sales_axis_ticks_fit_their_label_column(self, qapp):
        metrics = QFontMetrics(_font(AXIS_SIZE, tabular=True))
        for value in (500_000, 1_000_000, 1_500_000, 2_000_000):
            assert metrics.horizontalAdvance(_compact(value)) <= 40
