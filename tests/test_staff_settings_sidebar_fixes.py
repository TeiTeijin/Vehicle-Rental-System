"""Regression tests for the settings, collapsed-sidebar, and filter fixes.

Three confirmed defects:

- The collapsed sidebar was only a narrower full sidebar: the wordmark and
  labels were clipped rather than replaced by a real icon rail.
- Several Settings controls wrote preferences that nothing in the app read,
  so they looked like they changed the product when they did not.
- A filter option greyed out by the other filters gave no reason; a clerk saw
  a dead box and no explanation.
"""

from __future__ import annotations

import os
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.database import Base  # noqa: E402
from app.staff import theme  # noqa: E402
from app.staff.metrics import NAV_ICON, SIDEBAR_W  # noqa: E402


class _MemorySettings:
    """A QSettings stand-in so tests never touch the developer's real prefs."""

    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, value) -> None:
        self.data[key] = value


@pytest.fixture
def pref_store(monkeypatch):
    import app.staff.prefs as prefs

    store = _MemorySettings()
    monkeypatch.setattr(prefs, "_store", lambda: store)
    return store


@pytest.fixture
def branch(tmp_path, qapp):
    from app.models import Users
    from app.staff.context import (
        DatabaseSelection,
        DatabaseTarget,
        StaffContext,
        StaffUser,
    )
    from app.utils.security import hash_password

    path = tmp_path / "settings_sidebar_branch.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    engine.dispose()

    context = StaffContext(
        DatabaseSelection(
            url=f"sqlite:///{path}", target=DatabaseTarget.LIVE, label="Test"
        )
    )
    with context.session() as session:
        admin = Users(
            full_name="Branch Admin",
            email="admin@example.com",
            password_hash=hash_password("x"),
            address="1 Test Street",
            license_number="LIC0000001",
            license_expiry=date(2030, 1, 1),
            role="admin",
            is_active=True,
            created_at=datetime(2026, 1, 1, 9, 0),
        )
        session.add(admin)
        session.flush()
        admin_id = admin.user_id
    context.sign_in(
        StaffUser(admin_id, "Branch Admin", "admin@example.com", "admin")
    )
    yield context
    context.dispose()


def _settings_page(context):
    from app.staff.pages.settings import SettingsPage
    from app.staff.shell import StaffShell

    window = StaffShell(context)
    # A real sidebar without booting the dashboard page: the settings page's
    # checkbox must drive the same object the shell builds after sign-in.
    window._sidebar = window._build_sidebar()
    window._body_layout.insertWidget(0, window._sidebar)
    page = SettingsPage(window)
    page.refresh()
    return window, page


class TestCollapsedSidebar:
    @staticmethod
    def _sidebar():
        from app.staff.sidebar import Sidebar

        bar = Sidebar()
        bar.build(
            [
                (
                    "General",
                    [
                        ("dashboard", "Dashboard", "dashboard", True),
                        ("fleet", "Fleet", "vehicle", True),
                    ],
                ),
                ("Tools", [("settings", "Settings", "settings", True)]),
            ]
        )
        bar.resize(SIDEBAR_W, 640)
        bar.show()
        return bar

    def test_collapsing_makes_a_64px_icon_rail(self, qapp):
        from app.staff.sidebar import COLLAPSED_W

        bar = self._sidebar()
        try:
            qapp.processEvents()
            assert bar.collapsed is False
            assert bar.width() == SIDEBAR_W

            bar.set_collapsed(True)
            qapp.processEvents()

            assert bar.collapsed is True
            assert bar.width() == COLLAPSED_W == 64
            assert bar.wordmark.isHidden() is True
            assert bar.sign_out.toolTip() == "Log out"
            for key in ("dashboard", "fleet", "settings"):
                item = bar.item(key)
                assert item is not None
                assert item.collapsed is True
                assert item.label_visible is False
                # The label is still the tooltip: collapsing hides the text,
                # never the way to find out what an icon means.
                assert item.toolTip() == item.label
            for header in bar.findChildren(
                type(bar.wordmark), theme.OBJ_SIDEBAR_GROUP
            ):
                assert header.isHidden() is True
        finally:
            bar.deleteLater()

    def test_the_collapsed_icons_are_centred(self, qapp):
        bar = self._sidebar()
        try:
            bar.set_collapsed(True)
            qapp.processEvents()
            for key in ("dashboard", "fleet", "settings"):
                item = bar.item(key)
                expected = (item.width() - NAV_ICON) // 2
                assert item.icon_x() == expected
        finally:
            bar.deleteLater()

    def test_expanding_restores_the_full_rail(self, qapp):
        bar = self._sidebar()
        try:
            bar.set_collapsed(True)
            qapp.processEvents()
            bar.set_collapsed(False)
            qapp.processEvents()

            assert bar.collapsed is False
            assert bar.width() == SIDEBAR_W
            assert bar.wordmark.isHidden() is False
            for key in ("dashboard", "fleet", "settings"):
                item = bar.item(key)
                assert item.collapsed is False
                assert item.label_visible is True
                assert item.icon_x() == 16
            for header in bar.findChildren(
                type(bar.wordmark), theme.OBJ_SIDEBAR_GROUP
            ):
                assert header.isHidden() is False
        finally:
            bar.deleteLater()

    def test_rebuilding_while_collapsed_keeps_the_rail_collapsed(self, qapp):
        bar = self._sidebar()
        try:
            bar.set_collapsed(True)
            bar.build([("General", [("dashboard", "Dashboard", "dashboard", True)])])
            qapp.processEvents()
            assert bar.collapsed is True
            assert bar.width() == 64
            assert bar.item("dashboard").label_visible is False
            assert bar.wordmark.isHidden() is True
        finally:
            bar.deleteLater()


class TestSettingsHonesty:
    def test_settings_checkbox_collapses_the_real_shell_sidebar(
        self, branch, pref_store
    ):
        window, page = _settings_page(branch)
        try:
            assert page.sidebar_check.toolTip()
            page.sidebar_check.setChecked(True)
            assert window._sidebar.collapsed is True
            assert window._sidebar.width() == 64
            assert window._sidebar.item("settings").label_visible is False
            assert pref_store.data["appearance/sidebar_collapsed"] is True

            page.sidebar_check.setChecked(False)
            assert window._sidebar.collapsed is False
            assert window._sidebar.width() == SIDEBAR_W
            assert window._sidebar.item("settings").label_visible is True
        finally:
            window.deleteLater()

    def test_theme_is_disabled_because_no_theme_consumer_exists(
        self, branch, pref_store
    ):
        window, page = _settings_page(branch)
        try:
            assert page.theme_combo.isEnabled() is False
            tip = page.theme_combo.toolTip().lower()
            assert "dark" in tip
            assert "later" in tip or "not available" in tip
            assert page.theme_combo.currentText() == "light"
        finally:
            window.deleteLater()

    def test_preview_only_controls_say_so_and_do_not_persist(
        self, branch, pref_store, monkeypatch
    ):
        import app.staff.pages.settings as settings_mod

        window, page = _settings_page(branch)
        written: list[tuple[str, object]] = []
        monkeypatch.setattr(
            settings_mod.prefs, "set", lambda key, value: written.append((key, value))
        )
        monkeypatch.setattr(settings_mod, "toast", lambda *args, **kwargs: None)
        try:
            for control in (page.date_combo, page.currency_combo):
                tip = control.toolTip().lower()
                assert "preview only" in tip
                assert "unchanged" in tip or "not applied" in tip or "not saved" in tip

            accent_tip = page.accent_btn.toolTip().lower()
            assert "preview" in page.accent_btn.text().lower()
            assert "preview" in accent_tip
            assert "not" in accent_tip

            page.date_combo.setCurrentText("yyyy-MM-dd")
            page.currency_combo.setCurrentText("$")
            page._on_accent_picked({"accent": "#112233"})

            changed = {key for key, _value in written}
            assert "behavior/date_format" not in changed
            assert "behavior/currency" not in changed
            assert "appearance/accent" not in changed
            assert "#112233" in page.accent_preview.styleSheet()
        finally:
            window.deleteLater()

    def test_dead_pref_keys_are_gone(self):
        import app.staff.prefs as prefs

        for key in (
            "appearance/theme",
            "appearance/accent",
            "behavior/date_format",
            "behavior/currency",
        ):
            assert key not in prefs.DEFAULTS


class TestDisabledFilterExplanations:
    def test_a_muted_filter_group_explains_why_it_is_dead(self, qapp):
        from app.staff.filter_popover import FilterPopover

        pop = FilterPopover()
        try:
            pop.set_availability(
                {
                    "makes": {"Toyota"},
                    "vehicle_classes": {"suv"},
                    "cc_buckets": set(),
                    "transmissions": {"Automatic"},
                    "fuel_types": {"Diesel"},
                    "seats": {7},
                },
                exempt="vehicle_classes",
            )
            assert pop.cc_group.isEnabled() is False
            for tip in (
                pop.cc_group.toolTip(),
                pop.cc_group.heading.toolTip(),
                pop.cc_group.boxes["Over 400cc"].toolTip(),
                pop.cc_group.boxes["Over 400cc"].name.toolTip(),
            ):
                lowered = tip.lower()
                assert "no matches" in lowered
                assert "filter" in lowered
        finally:
            pop.deleteLater()

    def test_an_unavailable_option_explains_why_it_is_dead(self, qapp):
        from app.staff.filter_popover import FilterPopover

        pop = FilterPopover()
        try:
            pop.set_brands(["Toyota", "Honda"])
            pop.set_availability({"makes": {"Toyota"}})

            honda = pop.brand_row("Honda")
            assert honda.isEnabled() is True  # the row stays clickable to untick
            assert pop.brand_pane.boxes["Honda"].isEnabled() is False
            assert "no matches" in honda.toolTip().lower()
            assert "no matches" in honda.name.toolTip().lower()

            toyota = pop.brand_row("Toyota")
            assert toyota.toolTip() == "Toggle Toyota"
            assert toyota.name.toolTip() == "Toggle Toyota"
        finally:
            pop.deleteLater()

    def test_clearing_the_other_filters_restores_normal_tooltips(self, qapp):
        from app.staff.filter_popover import FilterPopover

        pop = FilterPopover()
        try:
            pop.set_brands(["Toyota", "Honda"])
            pop.set_availability(
                {
                    "makes": {"Toyota"},
                    "vehicle_classes": {"suv"},
                    "cc_buckets": set(),
                    "transmissions": {"Automatic"},
                    "fuel_types": {"Diesel"},
                    "seats": {7},
                },
                exempt="vehicle_classes",
            )
            pop.clear_availability()

            assert pop.cc_group.isEnabled() is True
            assert pop.cc_group.toolTip() == ""
            assert pop.cc_group.heading.toolTip() == ""
            card = pop.cc_group.boxes["Over 400cc"]
            assert card.isEnabled() is True
            assert card.toolTip() == "Toggle Over 400cc"
            assert pop.brand_row("Toyota").toolTip() == "Toggle Toyota"
        finally:
            pop.deleteLater()
