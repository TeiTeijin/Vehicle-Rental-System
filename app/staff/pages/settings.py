"""Settings: per-PC prefs (QSettings) + shared branch policy (APP_SETTINGS).

Local (this PC only): Appearance, Behavior, Files, session.
Shared (all counters, admin only): Pricing & policy, Operations defaults,
receipt header, sales target. Services read shared values with fallback
to the code constants, so an unmigrated database behaves as before.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services import settings_service, staff_service
from app.services.errors import ServiceError
from app.staff import prefs, theme
from app.staff.feedback import describe, show_field_error
from app.staff.metrics import CARD_GUTTER, PAGE_PANEL_PAD, SIDEBAR_GAP, TOOL_ROW_H
from app.staff.pages.base import StaffPage
from app.staff.tables import ALIGN_LEFT, Column, LoadStateTable
from app.staff.widgets import toast
from app.utils.money import pesos

VERSION = "1.0.0-settings.1"


def _panel() -> tuple[QFrame, QVBoxLayout]:
    box = QFrame()
    box.setObjectName(theme.OBJ_PAGE_PANEL)
    box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD, PAGE_PANEL_PAD)
    layout.setSpacing(8)
    return box, layout


def _heading(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("pageSubtitle")
    font = QFont("Inter")
    font.setPixelSize(15)
    font.setWeight(QFont.Weight.DemiBold)
    label.setFont(font)
    label.setStyleSheet(f"color: {theme.TEXT}; background: transparent;")
    return label


def _caption(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("pageSubtitle")
    label.setWordWrap(True)
    return label


def _combo(items: list[str], parent: QWidget) -> QComboBox:
    combo = QComboBox(parent)
    combo.addItems(items)
    combo.setFixedHeight(TOOL_ROW_H)
    return combo


def _error(parent: QWidget) -> QLabel:
    label = QLabel("", parent)
    label.setObjectName("fieldError")
    label.setWordWrap(True)
    label.setVisible(False)
    return label


class SettingsPage(StaffPage):
    HEADER = False

    def __init__(self, shell) -> None:
        super().__init__(shell, "Settings", "This PC and the shared branch policy.")
        self.layout().setContentsMargins(SIDEBAR_GAP, 0, SIDEBAR_GAP, 0)
        self.body.setSpacing(CARD_GUTTER)
        self._overlay = None
        self._shared: dict[str, str] = dict(settings_service.DEFAULTS)

        tools, tools_layout = _panel()
        tools_layout.addLayout(self._tools_row())
        self.body.addWidget(tools)
        self._tools_panel = tools

        self.body.addWidget(self._build_appearance())
        self.body.addWidget(self._build_behavior())
        self.body.addWidget(self._build_files())
        self.body.addWidget(self._build_account())
        self._users_panel = self._build_users()
        self.body.addWidget(self._users_panel)
        self._pricing_panel = self._build_pricing()
        self.body.addWidget(self._pricing_panel)
        self._ops_panel = self._build_operations()
        self.body.addWidget(self._ops_panel)
        self._receipt_panel = self._build_receipts()
        self.body.addWidget(self._receipt_panel)
        self.body.addWidget(self._build_system())

    # -- tools --------------------------------------------------------------

    def _tools_row(self):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.save_shared_btn = QPushButton("Save shared policy", self)
        self.save_shared_btn.setObjectName("primaryButton")
        self.save_shared_btn.setFixedHeight(TOOL_ROW_H)
        self.save_shared_btn.clicked.connect(self._on_save_shared)
        row.addWidget(self.save_shared_btn)
        self.reload_btn = QPushButton("Reload", self)
        self.reload_btn.setObjectName("ghostButton")
        self.reload_btn.setFixedHeight(TOOL_ROW_H)
        self.reload_btn.clicked.connect(lambda: self.refresh())
        row.addWidget(self.reload_btn)
        row.addStretch(1)
        self.admin_note = QLabel("", self)
        self.admin_note.setObjectName("pageSubtitle")
        row.addWidget(self.admin_note)
        return row

    # -- appearance ---------------------------------------------------------

    def _build_appearance(self):
        box, layout = _panel()
        layout.addWidget(_heading("Appearance", self))
        layout.addWidget(_caption("This PC only. Theme dark is stored now; "
                                  "the full dark palette ships later.", self))
        form = QFormLayout()
        form.setSpacing(8)
        self.theme_combo = _combo(["light", "dark", "system"], self)
        self.theme_combo.currentTextChanged.connect(self._on_theme)
        form.addRow("Theme", self.theme_combo)
        accent_row = QHBoxLayout()
        self.accent_preview = QLabel("    ", self)
        self.accent_preview.setFixedSize(44, 24)
        self.accent_btn = QPushButton("Pick accent", self)
        self.accent_btn.setObjectName("ghostButton")
        self.accent_btn.clicked.connect(self._on_accent)
        accent_row.addWidget(self.accent_preview)
        accent_row.addWidget(self.accent_btn)
        accent_row.addStretch(1)
        accent_host = QWidget(self)
        accent_host.setLayout(accent_row)
        accent_host.setStyleSheet("background: transparent;")
        form.addRow("Accent", accent_host)
        self.font_spin = QDoubleSpinBox(self)
        self.font_spin.setRange(0.9, 1.25)
        self.font_spin.setSingleStep(0.05)
        self.font_spin.valueChanged.connect(self._on_font_scale)
        form.addRow("UI scale", self.font_spin)
        self.sidebar_check = QCheckBox("Collapse sidebar by default", self)
        self.sidebar_check.toggled.connect(self._on_sidebar)
        form.addRow("", self.sidebar_check)
        self.motion_check = QCheckBox("Reduce motion (no shimmer / chart animation)", self)
        self.motion_check.toggled.connect(
            lambda on: prefs.set("appearance/reduce_motion", bool(on)))
        form.addRow("", self.motion_check)
        layout.addLayout(form)
        return box

    def _on_theme(self, value: str) -> None:
        if value in prefs.THEMES:
            prefs.set("appearance/theme", value)
            toast(self, f"Theme: {value} (applies on next open).")

    def _on_accent(self) -> None:
        current = QColor(str(prefs.get("appearance/accent")))
        picked = QColorDialog.getColor(current, self, "Accent color")
        if picked.isValid():
            prefs.set("appearance/accent", picked.name())
            self._paint_accent()
            toast(self, f"Accent: {picked.name()}.")

    def _paint_accent(self) -> None:
        colour = str(prefs.get("appearance/accent"))
        self.accent_preview.setStyleSheet(
            f"background: {colour}; border: 1px solid {theme.BORDER}; border-radius: 6px;")

    def _on_font_scale(self, value: float) -> None:
        prefs.set("appearance/font_scale", float(value))
        self._apply_font_scale()

    def _apply_font_scale(self) -> None:
        from PySide6.QtWidgets import QApplication
        try:
            scale = float(prefs.get("appearance/font_scale"))
        except (TypeError, ValueError):
            scale = 1.0
        app = QApplication.instance()
        if app is not None:
            font = app.font()
            base = 10
            font.setPointSizeF(max(8.0, min(14.0, base * scale)))
            app.setFont(font)

    def _on_sidebar(self, on: bool) -> None:
        prefs.set("appearance/sidebar_collapsed", bool(on))
        self._apply_sidebar()

    def _apply_sidebar(self) -> None:
        bar = getattr(self.shell, "_sidebar", None)
        if bar is None:
            return
        collapsed = bool(prefs.get("appearance/sidebar_collapsed"))
        try:
            from app.staff.metrics import SIDEBAR_W
            bar.setFixedWidth(64 if collapsed else SIDEBAR_W)
        except Exception:
            pass

    # -- behavior -----------------------------------------------------------

    def _build_behavior(self):
        box, layout = _panel()
        layout.addWidget(_heading("Behavior", self))
        layout.addWidget(_caption("This PC only. Refresh Off stops the "
                                  "dashboard timer.", self))
        form = QFormLayout()
        form.setSpacing(8)
        self.refresh_combo = _combo([label for label, _ms in prefs.REFRESH_CHOICES], self)
        self.refresh_combo.currentIndexChanged.connect(self._on_refresh)
        form.addRow("Dashboard refresh", self.refresh_combo)
        self.default_page_combo = _combo(["dashboard", "new_rental"], self)
        self.default_page_combo.currentTextChanged.connect(
            lambda v: prefs.set("behavior/default_page", v)
            if v in prefs.DEFAULT_PAGES else None)
        form.addRow("Page after login", self.default_page_combo)
        self.rows_combo = _combo([str(n) for n in prefs.ROWS_CHOICES], self)
        self.rows_combo.currentTextChanged.connect(
            lambda v: prefs.set("behavior/rows_per_page", int(v))
            if v.isdigit() else None)
        form.addRow("Table rows", self.rows_combo)
        self.date_combo = _combo(list(prefs.DATE_FORMATS), self)
        self.date_combo.currentTextChanged.connect(
            lambda v: (prefs.set("behavior/date_format", v), self._preview_formats()))
        form.addRow("Date format", self.date_combo)
        self.currency_combo = _combo(list(prefs.CURRENCIES), self)
        self.currency_combo.currentTextChanged.connect(
            lambda v: (prefs.set("behavior/currency", v), self._preview_formats()))
        form.addRow("Currency", self.currency_combo)
        self.format_preview = QLabel("", self)
        self.format_preview.setObjectName("pageSubtitle")
        form.addRow("Preview", self.format_preview)
        self.confirm_cancel = QCheckBox("Confirm before cancel", self)
        self.confirm_retire = QCheckBox("Confirm before retire", self)
        self.confirm_deactivate = QCheckBox("Confirm before deactivate", self)
        self.confirm_cancel.toggled.connect(
            lambda on: prefs.set("behavior/confirm_cancel", bool(on)))
        self.confirm_retire.toggled.connect(
            lambda on: prefs.set("behavior/confirm_retire", bool(on)))
        self.confirm_deactivate.toggled.connect(
            lambda on: prefs.set("behavior/confirm_deactivate", bool(on)))
        form.addRow("", self.confirm_cancel)
        form.addRow("", self.confirm_retire)
        form.addRow("", self.confirm_deactivate)
        self.toast_spin = QSpinBox(self)
        self.toast_spin.setRange(1000, 10000)
        self.toast_spin.setSingleStep(200)
        self.toast_spin.valueChanged.connect(
            lambda v: prefs.set("behavior/toast_ms", int(v)))
        form.addRow("Toast (ms)", self.toast_spin)
        self.logout_combo = _combo([label for label, _m in prefs.LOGOUT_CHOICES], self)
        self.logout_combo.currentIndexChanged.connect(self._on_logout)
        form.addRow("Auto-logout", self.logout_combo)
        layout.addLayout(form)
        return box

    def _on_refresh(self, index: int) -> None:
        _label, ms = prefs.REFRESH_CHOICES[index]
        prefs.set("behavior/dashboard_refresh_ms", int(ms))
        try:
            self.shell.apply_prefs()
        except Exception:
            pass
        toast(self, "Refresh updated.")

    def _on_logout(self, index: int) -> None:
        _label, mins = prefs.LOGOUT_CHOICES[index]
        prefs.set("behavior/auto_logout_mins", int(mins))
        try:
            self.shell.apply_prefs()
        except Exception:
            pass

    def _preview_formats(self) -> None:
        from datetime import date
        symbol = str(prefs.get("behavior/currency"))
        fmt = str(prefs.get("behavior/date_format"))
        mapping = {"dd MMM yyyy": "%d %b %Y", "MM/dd/yyyy": "%m/%d/%Y",
                   "yyyy-MM-dd": "%Y-%m-%d", "dd-MM-yyyy": "%d-%m-%Y"}
        self.format_preview.setText(
            f"{date.today().strftime(mapping.get(fmt, '%d %b %Y'))} · {symbol}2,500.00")

    # -- files --------------------------------------------------------------

    def _build_files(self):
        box, layout = _panel()
        layout.addWidget(_heading("Files", self))
        layout.addWidget(_caption("Where PDFs and CSVs land on this PC. "
                                  "Empty = the receipts folder.", self))
        row = QHBoxLayout()
        self.export_edit = QLineEdit(self)
        self.export_edit.setPlaceholderText("Default: receipts folder")
        self.export_edit.setFixedHeight(TOOL_ROW_H)
        row.addWidget(self.export_edit, 1)
        browse = QPushButton("Browse", self)
        browse.setObjectName("ghostButton")
        browse.setFixedHeight(TOOL_ROW_H)
        browse.clicked.connect(self._on_browse_export)
        row.addWidget(browse)
        save = QPushButton("Save", self)
        save.setObjectName("primaryButton")
        save.setFixedHeight(TOOL_ROW_H)
        save.clicked.connect(self._on_save_export)
        row.addWidget(save)
        layout.addLayout(row)
        self.export_error = _error(self)
        layout.addWidget(self.export_error)
        return box

    def _on_browse_export(self) -> None:
        picked = QFileDialog.getExistingDirectory(self, "Export folder")
        if picked:
            self.export_edit.setText(picked)

    def _on_save_export(self) -> None:
        raw = self.export_edit.text().strip()
        if raw:
            path = Path(raw)
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                show_field_error(self.export_error, f"Cannot use that folder: {exc}")
                return
        show_field_error(self.export_error, None)
        prefs.set("files/export_folder", raw)
        toast(self, "Export folder saved.")

    # -- account ------------------------------------------------------------

    def _build_account(self):
        box, layout = _panel()
        layout.addWidget(_heading("Account", self))
        self.must_change_banner = QLabel("", self)
        self.must_change_banner.setObjectName("fieldError")
        self.must_change_banner.setWordWrap(True)
        self.must_change_banner.setVisible(False)
        layout.addWidget(self.must_change_banner)
        form = QFormLayout()
        form.setSpacing(8)
        self.cur_pw = QLineEdit(self)
        self.cur_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_pw = QLineEdit(self)
        self.new_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_pw = QLineEdit(self)
        self.confirm_pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Current password", self.cur_pw)
        form.addRow("New password", self.new_pw)
        form.addRow("Confirm new", self.confirm_pw)
        layout.addLayout(form)
        self.pw_error = _error(self)
        layout.addWidget(self.pw_error)
        btn = QPushButton("Change password", self)
        btn.setObjectName("primaryButton")
        btn.setFixedHeight(TOOL_ROW_H)
        btn.clicked.connect(self._on_change_pw)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignLeft)
        return box

    def _on_change_pw(self) -> None:
        if self.new_pw.text() != self.confirm_pw.text():
            show_field_error(self.pw_error, "New passwords do not match.")
            return
        try:
            with self.context.session() as session:
                from app.models import Users
                row = session.get(Users, self.context.user.user_id)
                if row is None:
                    raise ServiceError("Account no longer exists.")
                staff_service.change_password(
                    session, row,
                    current_password=self.cur_pw.text(),
                    new_password=self.new_pw.text())
        except Exception as exc:
            message, _field, _kind = describe(exc)
            show_field_error(self.pw_error, message)
            toast(self, message, "error")
            return
        show_field_error(self.pw_error, None)
        self.cur_pw.clear()
        self.new_pw.clear()
        self.confirm_pw.clear()
        self.must_change_banner.setVisible(False)
        toast(self, "Password changed.")

    # -- users (admin) ------------------------------------------------------

    def _build_users(self):
        box, layout = _panel()
        layout.addWidget(_heading("Staff accounts (admin)", self))
        layout.addWidget(_caption("Temp passwords must be changed on first login. "
                                  "The last active admin cannot be deactivated.", self))
        self.users_table = LoadStateTable(
            [Column("Name", ALIGN_LEFT), Column("Email", ALIGN_LEFT),
             Column("Role", ALIGN_LEFT), Column("Active", ALIGN_LEFT),
             Column("Must change", ALIGN_LEFT), Column("Since", ALIGN_LEFT)],
            lambda: [],
            empty_message="No staff accounts yet.",
        )
        layout.addWidget(self.users_table)
        row = QHBoxLayout()
        for text, slot in (("Add", self.open_add_staff),
                           ("Reset password", self.open_reset_pw),
                           ("Deactivate / Reactivate", self._on_toggle_active)):
            btn = QPushButton(text, self)
            btn.setObjectName("ghostButton")
            btn.setFixedHeight(TOOL_ROW_H)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch(1)
        layout.addLayout(row)
        self.users_error = _error(self)
        layout.addWidget(self.users_error)
        return box

    def _staff_rows(self):
        with self.context.reading() as session:
            rows = []
            for user in staff_service.list_staff(session):
                rows.append((
                    user.full_name, user.email, user.role,
                    "Yes" if user.is_active else "No",
                    "Yes" if getattr(user, "must_change_password", False) else "No",
                    user.created_at.strftime("%d %b %Y") if user.created_at else "-",
                ))
            return rows

    def _selected_staff_email(self) -> str | None:
        row = self.users_table.table.currentRow()
        if row < 0:
            return None
        return self.users_table.cell_text(row, 1) or None

    def _overlay_panel(self, panel: QWidget):
        from app.staff.overlay import FrostedOverlay
        if self._overlay is not None:
            self._overlay.hide_overlay()
            self._overlay.deleteLater()
            self._overlay = None
        window = self.window()
        overlay = FrostedOverlay(window if window is not None else self)
        overlay.set_content(panel)
        overlay.show_overlay()
        self._overlay = overlay
        return overlay

    def _hide_overlay(self) -> None:
        if self._overlay is not None:
            self._overlay.hide_overlay()

    def open_add_staff(self) -> bool:
        if not self.context.is_admin:
            toast(self, "That action is for administrators.", "error")
            return False
        from app.staff.settings_panels import StaffFormPanel
        panel = StaffFormPanel(self)
        panel.submitted.connect(self._on_add_staff)
        panel.cancelled.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def _on_add_staff(self, values: dict) -> None:
        try:
            with self.context.session() as session:
                staff_service.create_staff(
                    session, full_name=values["full_name"], email=values["email"],
                    role=values["role"], temp_password=values["temp_password"],
                    created_by=self.context.user.user_id)
        except Exception as exc:
            message, _f, _k = describe(exc)
            toast(self, message, "error")
            return
        self._hide_overlay()
        self._reload_users()
        toast(self, f"{values['email']} added (temp password set).")

    def open_reset_pw(self) -> bool:
        email = self._selected_staff_email()
        if email is None:
            toast(self, "Select a staff account first.", "warn")
            return False
        if not self.context.is_admin:
            toast(self, "That action is for administrators.", "error")
            return False
        from app.staff.settings_panels import ResetPwPanel
        panel = ResetPwPanel(email, self)
        panel.submitted.connect(lambda v: self._on_reset_pw(email, v))
        panel.cancelled.connect(self._hide_overlay)
        self._overlay_panel(panel)
        return True

    def _on_reset_pw(self, email: str, values: dict) -> None:
        try:
            with self.context.session() as session:
                from app.models import Users
                from sqlalchemy import select
                row = session.execute(
                    select(Users).where(Users.email == email)).scalar_one_or_none()
                if row is None:
                    raise ServiceError("That account no longer exists.")
                staff_service.reset_password(
                    session, row, temp_password=values["temp_password"])
        except Exception as exc:
            toast(self, describe(exc)[0], "error")
            return
        self._hide_overlay()
        self._reload_users()
        toast(self, f"{email} reset (must change on next login).")

    def _on_toggle_active(self) -> None:
        email = self._selected_staff_email()
        if email is None:
            toast(self, "Select a staff account first.", "warn")
            return
        if not self.context.is_admin:
            toast(self, "That action is for administrators.", "error")
            return
        if not self._confirm("confirm_deactivate", f"Change active state for {email}?",
                             destructive=True):
            return
        try:
            with self.context.session() as session:
                from app.models import Users
                from sqlalchemy import select
                row = session.execute(
                    select(Users).where(Users.email == email)).scalar_one_or_none()
                if row is None:
                    raise ServiceError("That account no longer exists.")
                staff_service.set_active(session, row, not row.is_active)
                state = "reactivated" if row.is_active else "deactivated"
        except Exception as exc:
            toast(self, describe(exc)[0], "error")
            return
        self._reload_users()
        toast(self, f"{email} {state}.")

    def _reload_users(self) -> None:
        try:
            self.users_table.set_rows(self._staff_rows())
        except Exception as exc:
            self.users_table.set_failed(exc)

    # -- pricing (admin, shared) --------------------------------------------

    def _money(self, key: str, label: str, form: QFormLayout) -> QLineEdit:
        edit = QLineEdit(self)
        edit.setObjectName(f"setting_{key}")
        edit.setFixedHeight(TOOL_ROW_H)
        edit.setPlaceholderText(label)
        form.addRow(label, edit)
        setattr(self, f"edit_{key}", edit)
        return edit

    def _build_pricing(self):
        box, layout = _panel()
        layout.addWidget(_heading("Pricing & policy (admin, shared)", self))
        layout.addWidget(_caption("Applies to every counter. Rhodium source: "
                                  "booking_service constants are the fallback.", self))
        form = QFormLayout()
        form.setSpacing(8)
        self._money("scheduling_fee", "Scheduling fee", form)
        for key, label in (
                ("deposit_motorcycle", "Deposit · motorcycle"),
                ("deposit_small", "Deposit · small"),
                ("deposit_medium", "Deposit · medium"),
                ("deposit_suv", "Deposit · suv"),
                ("deposit_van", "Deposit · van"),
                ("deposit_pickup", "Deposit · pickup"),
                ("deposit_truck", "Deposit · truck"),
                ("deposit_default", "Deposit · default"),
                ("late_fee_per_day", "Late fee / day"),
                ("sales_target", "Annual sales target")):
            self._money(key, label, form)
        self.long_days = QSpinBox(self)
        self.long_days.setRange(2, 60)
        form.addRow("Long-term from (days)", self.long_days)
        self.long_disc = QLineEdit(self)
        self.long_disc.setFixedHeight(TOOL_ROW_H)
        form.addRow("Long-term multiplier (0.10–1.00)", self.long_disc)
        self.moto_mult = QLineEdit(self)
        self.moto_mult.setFixedHeight(TOOL_ROW_H)
        form.addRow("Motorcycle multiplier", self.moto_mult)
        layout.addLayout(form)
        self.pricing_error = _error(self)
        layout.addWidget(self.pricing_error)
        return box

    # -- operations (admin, shared) -----------------------------------------

    def _build_operations(self):
        box, layout = _panel()
        layout.addWidget(_heading("Operations (admin, shared)", self))
        form = QFormLayout()
        form.setSpacing(8)
        self.method_combo = _combo(["cash", "card", "gcash"], self)
        form.addRow("Default payment method", self.method_combo)
        self.channel_combo = _combo(["walk_in", "online"], self)
        form.addRow("Default channel", self.channel_combo)
        self.gcash_check = QCheckBox("Require GCash reference", self)
        form.addRow("", self.gcash_check)
        self.sameday_check = QCheckBox("Allow same-day as 1-day rental", self)
        form.addRow("", self.sameday_check)
        self.licence_spin = QSpinBox(self)
        self.licence_spin.setRange(0, 365)
        form.addRow("Licence expiring window (days)", self.licence_spin)
        self.maint_days = QSpinBox(self)
        self.maint_days.setRange(0, 365)
        form.addRow("Maintenance due (days)", self.maint_days)
        self.maint_km = QSpinBox(self)
        self.maint_km.setRange(0, 100000)
        form.addRow("Maintenance due (km)", self.maint_km)
        layout.addLayout(form)
        self.ops_error = _error(self)
        layout.addWidget(self.ops_error)
        return box

    # -- receipts (admin, shared) -------------------------------------------

    def _build_receipts(self):
        box, layout = _panel()
        layout.addWidget(_heading("Receipts (admin, shared)", self))
        form = QFormLayout()
        form.setSpacing(8)
        self.branch_name = QLineEdit(self)
        self.branch_addr = QLineEdit(self)
        self.branch_phone = QLineEdit(self)
        for widget, label in ((self.branch_name, "Branch name"),
                              (self.branch_addr, "Branch address"),
                              (self.branch_phone, "Branch phone")):
            widget.setFixedHeight(TOOL_ROW_H)
            form.addRow(label, widget)
        self.fee_note_check = QCheckBox("Show scheduling-fee note on receipts", self)
        self.auto_open_check = QCheckBox("Open PDF after export", self)
        form.addRow("", self.fee_note_check)
        form.addRow("", self.auto_open_check)
        layout.addLayout(form)
        self.receipt_error = _error(self)
        layout.addWidget(self.receipt_error)
        return box

    # -- system -------------------------------------------------------------

    def _build_system(self):
        box, layout = _panel()
        layout.addWidget(_heading("System", self))
        self.db_label = QLabel("", self)
        self.db_label.setObjectName("pageSubtitle")
        self.db_label.setWordWrap(True)
        layout.addWidget(self.db_label)
        row = QHBoxLayout()
        test_db = QPushButton("Test connection", self)
        test_db.setObjectName("ghostButton")
        test_db.setFixedHeight(TOOL_ROW_H)
        test_db.clicked.connect(self._on_test_db)
        row.addWidget(test_db)
        backup = QPushButton("Backup database", self)
        backup.setObjectName("ghostButton")
        backup.setFixedHeight(TOOL_ROW_H)
        backup.clicked.connect(self._on_backup)
        row.addWidget(backup)
        row.addStretch(1)
        layout.addLayout(row)
        self.cache_label = QLabel("", self)
        self.cache_label.setObjectName("pageSubtitle")
        layout.addWidget(self.cache_label)
        row2 = QHBoxLayout()
        clear = QPushButton("Clear image cache", self)
        clear.setObjectName("ghostButton")
        clear.setFixedHeight(TOOL_ROW_H)
        clear.clicked.connect(self._on_clear_cache)
        row2.addWidget(clear)
        test_api = QPushButton("Test CarImages API", self)
        test_api.setObjectName("ghostButton")
        test_api.setFixedHeight(TOOL_ROW_H)
        test_api.clicked.connect(self._on_test_api)
        row2.addWidget(test_api)
        row2.addStretch(1)
        layout.addLayout(row2)
        self.api_label = QLabel("", self)
        self.api_label.setObjectName("pageSubtitle")
        self.api_label.setWordWrap(True)
        layout.addWidget(self.api_label)
        about = QLabel(f"RentWheels Staff {VERSION} · "
                       "PySide6 · SQLAlchemy · SQLite/MySQL", self)
        about.setObjectName("pageSubtitle")
        layout.addWidget(about)
        self.system_error = _error(self)
        layout.addWidget(self.system_error)
        return box

    def _on_test_db(self) -> None:
        import time
        try:
            start = time.monotonic()
            with self.context.reading() as session:
                from sqlalchemy import text
                session.execute(text("SELECT 1"))
            ms = (time.monotonic() - start) * 1000
            self.db_label.setText(
                f"{self.context.selection.label} · OK ({ms:.0f} ms).")
            toast(self, "Database connection OK.")
        except Exception as exc:
            message, _f, _k = describe(exc)
            self.db_label.setText(f"{self.context.selection.label} · FAILED: {message}")
            toast(self, message, "error")

    def _on_backup(self) -> None:
        from sqlalchemy.engine import make_url
        try:
            url = make_url(self.context.selection.url)
        except Exception:
            toast(self, "Cannot parse database URL.", "error")
            return
        if url.drivername.startswith("sqlite"):
            import shutil
            from datetime import datetime
            src = Path(url.database) if url.database else None
            if src is None or not src.exists():
                toast(self, "SQLite file not found.", "error")
                return
            dest_dir = prefs.export_folder(Path("receipts"))
            dest = dest_dir / f"backup_{src.stem}_{datetime.now():%Y%m%d_%H%M%S}{src.suffix}"
            try:
                shutil.copyfile(src, dest)
            except OSError as exc:
                toast(self, f"Backup failed: {exc}", "error")
                return
            toast(self, f"Backup saved: {dest.name}.")
        else:
            toast(
                self,
                "Live database: run `python -m scripts.backup_live_db` "
                "for a local copy.",
                "warn",
            )

    def _refresh_cache_label(self) -> None:
        try:
            from app.services import api_cache, image_cache
            count, size = image_cache.stats()
            api_count, api_size = api_cache.stats()
            self.cache_label.setText(
                f"Image cache: {count} file(s), {size / 1024:.1f} KB. "
                f"API lookups: {api_count} record(s), {api_size / 1024:.1f} KB.")
        except Exception:
            self.cache_label.setText("Image cache: unavailable.")

    def _on_clear_cache(self) -> None:
        if not self._confirm("confirm_retire", "Clear cached vehicle images?",
                             detail="They re-download on demand."):
            return
        try:
            from app.services import api_cache, image_cache
            removed = image_cache.clear() + api_cache.clear()
        except Exception as exc:
            toast(self, describe(exc)[0], "error")
            return
        self._refresh_cache_label()
        toast(self, f"Cache cleared ({removed} file(s)).")

    def _on_test_api(self) -> None:
        from app.config import CI_API_KEY, CI_API_SECRET
        if not CI_API_KEY or not CI_API_SECRET:
            self.api_label.setText("CarImages: no key/secret in .env.")
            toast(self, "No API key configured.", "warn")
            return
        try:
            from app.services import carimages_client
            ok = carimages_client.ping()
        except AttributeError:
            # No ping helper: report config presence only (no network call).
            self.api_label.setText("CarImages: key configured (no ping helper).")
            return
        except Exception as exc:
            self.api_label.setText(f"CarImages: FAILED ({describe(exc)[0]})")
            toast(self, describe(exc)[0], "error")
            return
        self.api_label.setText("CarImages: OK." if ok else "CarImages: check failed.")
        toast(self, "CarImages OK." if ok else "CarImages check failed.", "ok" if ok else "warn")

    # -- shared save --------------------------------------------------------

    def _collect_shared(self) -> dict[str, object]:
        values: dict[str, object] = {}
        for key in ("scheduling_fee", "deposit_motorcycle", "deposit_small",
                    "deposit_medium", "deposit_suv", "deposit_van",
                    "deposit_pickup", "deposit_truck", "deposit_default",
                    "late_fee_per_day", "sales_target"):
            values[key] = getattr(self, f"edit_{key}").text()
        values["long_term_days"] = self.long_days.value()
        values["long_term_discount"] = self.long_disc.text()
        values["moto_multiplier"] = self.moto_mult.text()
        values["default_payment_method"] = self.method_combo.currentText()
        values["default_channel"] = self.channel_combo.currentText()
        values["gcash_ref_required"] = "1" if self.gcash_check.isChecked() else "0"
        values["allow_same_day"] = "1" if self.sameday_check.isChecked() else "0"
        values["licence_expiring_days"] = self.licence_spin.value()
        values["maint_due_days"] = self.maint_days.value()
        values["maint_due_km"] = self.maint_km.value()
        values["receipt_branch_name"] = self.branch_name.text()
        values["receipt_branch_address"] = self.branch_addr.text()
        values["receipt_branch_phone"] = self.branch_phone.text()
        values["receipt_fee_note"] = "1" if self.fee_note_check.isChecked() else "0"
        values["receipt_auto_open"] = "1" if self.auto_open_check.isChecked() else "0"
        return values

    def _fill_shared(self, values: dict[str, str]) -> None:
        for key in ("scheduling_fee", "deposit_motorcycle", "deposit_small",
                    "deposit_medium", "deposit_suv", "deposit_van",
                    "deposit_pickup", "deposit_truck", "deposit_default",
                    "late_fee_per_day", "sales_target"):
            getattr(self, f"edit_{key}").setText(values.get(key, ""))
        try:
            self.long_days.setValue(int(values.get("long_term_days", "7")))
        except ValueError:
            self.long_days.setValue(7)
        self.long_disc.setText(values.get("long_term_discount", "0.90"))
        self.moto_mult.setText(values.get("moto_multiplier", "0.70"))
        self._set_combo(self.method_combo, values.get("default_payment_method", "cash"))
        self._set_combo(self.channel_combo, values.get("default_channel", "walk_in"))
        self.gcash_check.setChecked(values.get("gcash_ref_required", "1") == "1")
        self.sameday_check.setChecked(values.get("allow_same_day", "1") == "1")
        try:
            self.licence_spin.setValue(int(values.get("licence_expiring_days", "30")))
            self.maint_days.setValue(int(values.get("maint_due_days", "14")))
            self.maint_km.setValue(int(values.get("maint_due_km", "500")))
        except ValueError:
            pass
        self.branch_name.setText(values.get("receipt_branch_name", "RentWheels"))
        self.branch_addr.setText(values.get("receipt_branch_address", ""))
        self.branch_phone.setText(values.get("receipt_branch_phone", ""))
        self.fee_note_check.setChecked(values.get("receipt_fee_note", "1") == "1")
        self.auto_open_check.setChecked(values.get("receipt_auto_open", "1") == "1")

    @staticmethod
    def _set_combo(combo: QComboBox, value: str) -> None:
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _on_save_shared(self) -> None:
        if not self.context.is_admin:
            toast(self, "Shared policy is for administrators.", "error")
            return
        values = self._collect_shared()
        try:
            with self.context.session() as session:
                settings_service.set_many(
                    session, values, updated_by=self.context.user.user_id)
                self._shared = settings_service.get_all(session)
        except Exception as exc:
            message, field, _kind = describe(exc)
            if field in (settings_service.MONEY_KEYS
                         | {"long_term_days", "long_term_discount",
                            "moto_multiplier", "sales_target"}):
                show_field_error(self.pricing_error, message)
            elif field in (settings_service.BOOL_KEYS
                           | set(settings_service.VALID_CHOICES)
                           | settings_service.INT_KEYS):
                show_field_error(self.ops_error, message)
            else:
                show_field_error(self.receipt_error, message)
            toast(self, message, "error")
            return
        show_field_error(self.pricing_error, None)
        show_field_error(self.ops_error, None)
        show_field_error(self.receipt_error, None)
        self._fill_shared(self._shared)
        toast(self, "Shared policy saved.")

    # -- helpers ------------------------------------------------------------

    def _confirm(self, pref_key: str, question: str, *,
                 detail: str = "", destructive: bool = False) -> bool:
        from app.staff.widgets import confirm
        pref = pref_key if "/" in pref_key else f"behavior/{pref_key}"
        return confirm(self, question, detail=detail,
                       destructive=destructive, pref=pref)

    def _fill_local(self) -> None:
        self._set_combo_text(self.theme_combo, str(prefs.get("appearance/theme")))
        self._paint_accent()
        try:
            self.font_spin.setValue(float(prefs.get("appearance/font_scale")))
        except (TypeError, ValueError):
            self.font_spin.setValue(1.0)
        self.sidebar_check.setChecked(bool(prefs.get("appearance/sidebar_collapsed")))
        self.motion_check.setChecked(bool(prefs.get("appearance/reduce_motion")))
        ms = int(prefs.get("behavior/dashboard_refresh_ms"))
        for i, (_label, value) in enumerate(prefs.REFRESH_CHOICES):
            if value == ms:
                self.refresh_combo.setCurrentIndex(i)
                break
        self._set_combo_text(self.default_page_combo, str(prefs.get("behavior/default_page")))
        self._set_combo_text(self.rows_combo, str(prefs.get("behavior/rows_per_page")))
        self._set_combo_text(self.date_combo, str(prefs.get("behavior/date_format")))
        self._set_combo_text(self.currency_combo, str(prefs.get("behavior/currency")))
        self._preview_formats()
        self.confirm_cancel.setChecked(bool(prefs.get("behavior/confirm_cancel")))
        self.confirm_retire.setChecked(bool(prefs.get("behavior/confirm_retire")))
        self.confirm_deactivate.setChecked(bool(prefs.get("behavior/confirm_deactivate")))
        try:
            self.toast_spin.setValue(int(prefs.get("behavior/toast_ms")))
        except (TypeError, ValueError):
            self.toast_spin.setValue(3200)
        mins = int(prefs.get("behavior/auto_logout_mins"))
        for i, (_label, value) in enumerate(prefs.LOGOUT_CHOICES):
            if value == mins:
                self.logout_combo.setCurrentIndex(i)
                break
        self.export_edit.setText(str(prefs.get("files/export_folder")))

    @staticmethod
    def _set_combo_text(combo: QComboBox, value: str) -> None:
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    # -- refresh ------------------------------------------------------------

    def refresh(self) -> None:
        super().refresh()
        self._fill_local()
        self._apply_font_scale()
        try:
            with self.context.reading() as session:
                self._shared = settings_service.get_all(session)
        except Exception:
            self._shared = dict(settings_service.DEFAULTS)
        self._fill_shared(self._shared)
        self._reload_users()
        self._refresh_cache_label()
        self.db_label.setText(f"{self.context.selection.label} · not tested yet.")
        admin = self.context.is_admin
        self.admin_note.setText(
            "Admin: shared policy editable." if admin else "Staff: shared policy read-only.")
        for widget in (self.save_shared_btn,):
            widget.setEnabled(admin)
        for panel in (self._users_panel, self._pricing_panel,
                      self._ops_panel, self._receipt_panel):
            panel.setEnabled(True)
        # Must-change banner for the signed-in account.
        try:
            with self.context.reading() as session:
                from app.models import Users
                row = session.get(Users, self.context.user.user_id)
                must = bool(getattr(row, "must_change_password", False))
        except Exception:
            must = False
        self.must_change_banner.setVisible(must)
        if must:
            self.must_change_banner.setText(
                "Temporary password: change it below before continuing.")

    def can(self, action: str) -> bool:
        if self.context.is_admin:
            return True
        return action in ("change_password",)


def build_settings_page(shell) -> SettingsPage:
    return SettingsPage(shell)
