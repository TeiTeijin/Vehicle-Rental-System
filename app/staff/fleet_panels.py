"""Fleet action panels: frosted-overlay forms, never OS windows.

Same wizard-step recipe as the bookings panels (transparent host + white 8px
card, inline validation, page toasts results):

- VehicleFormPanel: add / edit a vehicle (plate, brand, model, year, color,
  category, transmission, fuel, seats, class, engine CC, rate, odometer,
  registration + insurance expiry).
- BulkPanel: rate or status across a class/category set, with a live
  preview line and an exception guard for per-vehicle rates.
- FleetDetailsPanel: gallery + external 3D preview + refresh, booking
  history with current/next pin, inspections, maintenance + cost, and
  stats with a 30/90/365 utilization switcher.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QTimer, Signal, QObject, QRunnable
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.models import Booking, Vehicle
from app.services import fleet_service, image_cache, media_service, vehicle_photos
from app.services.vehicle_service import TRANSMISSIONS, FUEL_TYPES, VEHICLE_CLASSES, VEHICLE_CLASS_LABELS
from app.staff import model_preview, theme
from app.staff.cards import Caption, Card, MoneyNumber
from app.staff.feedback import describe
from app.staff.rental_ui import SpecTile, StatusChip
from app.staff.rental_wizard import _StepBase, make_choice_button
from app.staff.ui_kit import (
    ChipGroup,
    FormField,
    MetricTile,
    NoticeBox,
    SectionTitle,
)
from app.staff.workers import emit_safely, media_pool
from app.utils.money import ZERO, money, pesos

try:
    import requests
except ImportError:  # noqa: BLE001 - gallery degrades to captions
    requests = None

DASH = "—"

#: Utilization windows offered in the stats switcher. 30d default: recent
#: enough to act on; lifetime would flatter old cars and hide new ones.
STAT_WINDOWS = (30, 90, 365)

#: Viewer height: matches the details hero photo rhythm.
VIEWER_H = 300

_THREE_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<style>html,body{margin:0;height:100%;background:transparent;overflow:hidden}
#note{position:absolute;inset:auto 8px 8px 8px;font:12px sans-serif;color:#8A8175}</style>
<script type="importmap">{"imports":{"three":"https://unpkg.com/three@0.160.0/build/three.module.js","three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}</script>
</head><body><div id="note">Loading 3D…</div>
<script type="module">
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
const note = document.getElementById('note');
const scene = new THREE.Scene();
const cam = new THREE.PerspectiveCamera(45, innerWidth/innerHeight, 0.1, 100);
cam.position.set(3.2, 1.8, 4.2);
const renderer = new THREE.WebGLRenderer({antialias:true, alpha:true});
renderer.setClearColor(0x000000, 0);
renderer.setSize(innerWidth, innerHeight);
document.body.appendChild(renderer.domElement);
const ctl = new OrbitControls(cam, renderer.domElement);
ctl.autoRotate = true; ctl.autoRotateSpeed = 1.2;
scene.add(new THREE.HemisphereLight(0xffffff, 0x8A8175, 1.8));
const sun = new THREE.DirectionalLight(0xffffff, 2.2);
sun.position.set(4, 6, 3); scene.add(sun);
new GLTFLoader().load("__URL__",
  g => {scene.add(g.scene);
    const b = new THREE.Box3().setFromObject(g.scene);
    const sphere = b.getBoundingSphere(new THREE.Sphere());
    ctl.target.copy(sphere.center);
    const dir = cam.position.clone().sub(sphere.center).normalize();
    cam.position.copy(sphere.center).addScaledVector(dir, sphere.radius * 2.4);
    note.textContent = '';},
  undefined,
  e => {note.textContent = '3D preview failed to load.';});
(function tick(){requestAnimationFrame(tick); ctl.update();
  renderer.render(scene, cam);})();
addEventListener('resize', () => {cam.aspect = innerWidth/innerHeight;
  cam.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight);});
</script></body></html>"""


def _field_label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("fieldLabel")
    return label


def _friendly(error: BaseException) -> str:
    message, _field, _kind = describe(error)
    return message


# -- fleet modal shell ------------------------------------------------------

#: The fleet references are one 900px white sheet, unlike the 540px booking
#: confirmations. The overlay is told this width per panel, so the bulk sheet
#: can keep the compact 640px column.
FLEET_MODAL_WIDTH = 900

FLEET_INK = "#171717"
FLEET_MUTED = "#6B5E4F"

_STATUS_TONES = {
    "available": "ok",
    "reserved": "warn",
    "rented": "warn",
    "maintenance": "danger",
    "retired": "neutral",
    "pending": "warn",
    "confirmed": "ok",
    "ongoing": "warn",
    "completed": "ok",
    "cancelled": "danger",
}


def _fleet_text(
    text: str,
    *,
    parent: QWidget | None = None,
    size: int = 13,
    weight: QFont.Weight = QFont.Weight.Medium,
    colour: str = FLEET_INK,
) -> QLabel:
    label = QLabel(text, parent)
    font = QFont("Inter")
    font.setPixelSize(size)
    font.setWeight(weight)
    label.setFont(font)
    label.setStyleSheet(f"color: {colour}; background: transparent;")
    return label


def _chip_selector(
    combo: QComboBox, parent: QWidget, *, expanding: bool = True
) -> ChipGroup:
    """Visible chips driving one of the form's hidden combos.

    The combo stays the single source of truth, so ``currentText()``,
    ``setCurrentText()``, prefill, and validation keep working unchanged.
    """
    options = [
        (combo.itemText(index), combo.itemText(index))
        for index in range(combo.count())
    ]
    chips = ChipGroup(options, expanding=expanding, parent=parent)
    chips.select(combo.currentText())
    chips.changed.connect(combo.setCurrentText)
    combo.currentTextChanged.connect(chips.select)
    combo.hide()
    return chips


class _FleetPreview(QFrame):
    """Cream live preview beside the add/edit form."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("fleetPreview")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(8)

        eyebrow = _fleet_text(
            "PREVIEW",
            parent=self,
            size=10,
            weight=QFont.Weight.DemiBold,
            colour=FLEET_MUTED,
        )
        layout.addWidget(eyebrow)
        self.title_label = _fleet_text(
            "New vehicle", parent=self, size=20, weight=QFont.Weight.Bold
        )
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        layout.addSpacing(4)

        self.rows: dict[str, QLabel] = {}
        for key in ("Plate", "Year", "Seats", "Fuel", "Rate"):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            name = _fleet_text(
                key, parent=self, size=12, colour=FLEET_MUTED
            )
            value = _fleet_text(
                "",
                parent=self,
                size=12,
                weight=QFont.Weight.DemiBold,
            )
            value.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            value.setWordWrap(True)
            row.addWidget(name, 1)
            row.addWidget(value)
            layout.addLayout(row)
            self.rows[key] = value

    def update_preview(
        self,
        *,
        make: str,
        model: str,
        plate: str,
        year: int,
        seats: int,
        fuel: str,
        rate_text: str,
        editing: bool,
    ) -> None:
        name = f"{make} {model}".strip()
        self.title_label.setText(
            name or ("Vehicle preview" if editing else "New vehicle")
        )
        self.rows["Plate"].setText(plate or "ABC-123")
        self.rows["Year"].setText(str(year))
        self.rows["Seats"].setText(str(seats))
        self.rows["Fuel"].setText(fuel)
        self.rows["Rate"].setText(f"{rate_text} / day")


class _FleetSheet(QWidget):
    """Transparent host around a 900px white modal sheet.

    Header (eyebrow / title / subtitle), scrollable two-column body, and a
    sticky footer. Mirrors the modal language in rental_ui.ModalStep without
    inheriting its step rail or its 540px confirmation geometry.
    """

    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # The root host must stay transparent: the blanket QWidget cream
        # would otherwise paint a square column behind the rounded sheet.
        self.setObjectName("fleetModal")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addStretch(1)

        self.card = Card(self, radius=22)
        self.card.setObjectName("fleetModalCard")
        self.card.setMaximumWidth(FLEET_MODAL_WIDTH)
        self.card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        outer.addWidget(self.card)
        outer.addStretch(1)

        card_layout = self.card.layout()

        self.header_host = QWidget(self.card)
        self.header_host.setObjectName("fleetModalHeader")
        self.header_layout = QVBoxLayout(self.header_host)
        self.header_layout.setContentsMargins(28, 22, 28, 0)
        self.header_layout.setSpacing(8)
        card_layout.addWidget(self.header_host)

        eyebrow_row = QHBoxLayout()
        eyebrow_row.setContentsMargins(0, 0, 0, 0)
        eyebrow_row.setSpacing(10)
        self.eyebrow_label = QLabel("", self.header_host)
        self.eyebrow_label.setObjectName("fleetEyebrow")
        self.eyebrow_label.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self.eyebrow_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        eyebrow_row.addWidget(self.eyebrow_label, 0, Qt.AlignmentFlag.AlignLeft)
        eyebrow_row.addStretch(1)
        self.status_host = QHBoxLayout()
        self.status_host.setContentsMargins(0, 0, 0, 0)
        self.status_host.setSpacing(8)
        eyebrow_row.addLayout(self.status_host)
        self.header_layout.addLayout(eyebrow_row)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(16)
        self.title_label = _fleet_text(
            "",
            parent=self.header_host,
            size=30,
            weight=QFont.Weight.Bold,
        )
        self.title_label.setObjectName("fleetModalTitle")
        self.title_label.setWordWrap(True)
        title_row.addWidget(self.title_label, 1)
        self.title_right = QHBoxLayout()
        self.title_right.setContentsMargins(0, 0, 0, 0)
        self.title_right.setSpacing(6)
        title_row.addLayout(self.title_right)
        self.header_layout.addLayout(title_row)

        self.subtitle_label = _fleet_text(
            "",
            parent=self.header_host,
            size=13,
            colour=FLEET_MUTED,
        )
        self.subtitle_label.setObjectName("fleetModalSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.header_layout.addWidget(self.subtitle_label)

        rule = QFrame(self.header_host)
        rule.setObjectName("fleetModalRule")
        rule.setFixedHeight(1)
        self.header_layout.addWidget(rule)

        self.content_host = QWidget(self.card)
        self.content_host.setObjectName("fleetModalBody")
        self._content_outer = QVBoxLayout(self.content_host)
        self._content_outer.setContentsMargins(28, 22, 28, 18)
        self._content_outer.setSpacing(14)
        card_layout.addWidget(self.content_host, 1)

        self._columns: QHBoxLayout | None = None
        self._left: QVBoxLayout | None = None
        self._body: QVBoxLayout | None = None
        self._error: QLabel | None = None

        self.footer = QFrame(self.card)
        self.footer.setObjectName("fleetModalFooter")
        self.footer_layout = QHBoxLayout(self.footer)
        self.footer_layout.setContentsMargins(28, 14, 28, 16)
        self.footer_layout.setSpacing(10)
        self.footer.setVisible(False)
        card_layout.addWidget(self.footer)

    def _head(self, eyebrow: str, title: str, subtitle: str) -> None:
        self.eyebrow_label.setText(eyebrow.upper())
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)
        self.subtitle_label.setVisible(bool(subtitle))

    def _set_header(self, title: str, subtitle: str) -> None:
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)
        self.subtitle_label.setVisible(bool(subtitle))

    def _start_columns(
        self, *, left_stretch: int = 5, right_stretch: int = 4
    ) -> tuple[QVBoxLayout, QVBoxLayout]:
        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(24)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(16)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(10)
        columns.addLayout(left, left_stretch)
        columns.addLayout(right, right_stretch)
        self._content_outer.addLayout(columns)
        self._columns = columns
        self._left = left
        self._body = right
        return left, right

    def _error_label(self) -> QLabel:
        label = QLabel("", self.content_host)
        label.setObjectName("fieldError")
        label.setWordWrap(True)
        label.setVisible(False)
        self._content_outer.addWidget(label)
        self._error = label
        return label

    def _buttons(
        self, primary_text: str, *, back_text: str = "Back"
    ) -> tuple[QPushButton, QPushButton]:
        while self.footer_layout.count():
            item = self.footer_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.footer_layout.addStretch(1)
        back = QPushButton(back_text, self.footer)
        back.setObjectName("secondaryButton")
        back.setMinimumHeight(38)
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        primary = QPushButton(primary_text, self.footer)
        primary.setObjectName("primaryButton")
        primary.setMinimumHeight(38)
        primary.setCursor(Qt.CursorShape.PointingHandCursor)
        self.footer_layout.addWidget(back)
        self.footer_layout.addWidget(primary)
        self.footer.setVisible(True)
        return back, primary


def _media_failure_message(exc: object) -> str:
    """Failed refresh, phrased as cause + next step instead of raw API text."""
    from app.services import carimages_client as ci

    if isinstance(exc, BaseException):
        if type(exc) is ci.CarImagesError or "404" in str(exc):
            return (
                "No photos found for this make and model — check the spelling "
                "in Edit, then Refresh photos."
            )
        return (
            f"{_friendly(exc)} Check the connection, then Refresh photos to retry."
        )
    return "Media refresh failed. Refresh photos to retry."


#: Keystroke quiet period before the spelling probe runs (one API call, not one per letter).
PROBE_DEBOUNCE_MS = 500


class _ProbeSignals(QObject):
    done = Signal(int, str, str, str)


class _MediaProbe(QRunnable):
    """Spelling probe off the GUI thread; the generation drops stale landings."""

    def __init__(
        self,
        make: str,
        model: str,
        year: int,
        category: str,
        gen: int,
        signals: _ProbeSignals,
    ) -> None:
        super().__init__()
        self._make = make
        self._model = model
        self._year = year
        self._category = category
        self._gen = gen
        self._done = signals.done  # bound while the form panel is alive

    def run(self) -> None:
        try:
            status = media_service.probe_media(
                self._make, self._model, self._year, self._category
            )
        except Exception:  # noqa: BLE001 - doubt means retry later, never a crash
            status = "unreachable"
        emit_safely(self._done, self._gen, status, self._make, self._model)


def _hero_bytes(make: str, model: str, year: int) -> bytes | None:
    try:
        cached = image_cache.load(make, model, year)
    except Exception:  # noqa: BLE001
        return None
    return getattr(cached, "data", None)


def _pixmap(data: bytes | None, width: int, height: int) -> QPixmap | None:
    if not data:
        return None
    image = QImage()
    if not image.loadFromData(data):
        return None
    scaled = image.scaled(
        width, height,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return QPixmap.fromImage(scaled)


def _download(url: str, timeout: int = 10) -> bytes | None:
    if not requests or not url:
        return None
    try:
        resp = requests.get(url, timeout=timeout)
    except Exception:  # noqa: BLE001 - offline / slow: gallery degrades
        return None
    if resp.status_code != 200 or not resp.content:
        return None
    return resp.content


def _store_model_glb(vehicle, data: bytes | None) -> str:
    """Write downloaded GLB bytes where the preview process can open them."""
    if not data:
        return ""
    try:
        key = image_cache._identity(
            vehicle.make, vehicle.model, vehicle.year, "3d"
        )
        path = image_cache.CACHE_DIR / f"{key}.glb"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError:
        return ""
    return str(path)


# -- add / edit form --------------------------------------------------------


class VehicleFormPanel(_FleetSheet):
    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(self, context, vehicle_id: int | None = None, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        self._vehicle_id = vehicle_id
        editing = vehicle_id is not None
        self._head(
            "Edit vehicle" if editing else "Add vehicle",
            "Update plate, specs, rate and paperwork"
            if editing
            else "Plate, specs, rate and paperwork",
            "Changes apply to future bookings only."
            if editing
            else "Required: plate, brand, model and daily rate.",
        )
        left, right = self._start_columns(left_stretch=5, right_stretch=4)
        self._error = self._error_label()

        def field(label: str, widget: QWidget, *, optional: bool = False) -> FormField:
            return FormField(
                label, widget, optional=optional, parent=self.card
            )

        # -- identity ---------------------------------------------------
        left.addWidget(SectionTitle("Identity", self.card))
        identity = QGridLayout()
        identity.setContentsMargins(0, 0, 0, 0)
        identity.setHorizontalSpacing(20)
        identity.setVerticalSpacing(14)
        identity.setColumnStretch(0, 1)
        identity.setColumnStretch(1, 1)

        self.plate_edit = QLineEdit(self.card)
        self.plate_edit.setObjectName("warmInput")
        self.plate_edit.setPlaceholderText("ABC-123 (max 7 chars)")
        identity.addWidget(field("Plate", self.plate_edit), 0, 0)

        self.color_edit = QLineEdit(self.card)
        self.color_edit.setObjectName("warmInput")
        self.color_edit.setPlaceholderText("Optional")
        identity.addWidget(field("Color", self.color_edit, optional=True), 0, 1)

        self.make_edit = QLineEdit(self.card)
        self.make_edit.setObjectName("warmInput")
        identity.addWidget(field("Brand", self.make_edit), 1, 0)
        self.model_edit = QLineEdit(self.card)
        self.model_edit.setObjectName("warmInput")
        identity.addWidget(field("Model", self.model_edit), 1, 1)

        self.year_edit = QSpinBox(self.card)
        self.year_edit.setObjectName("warmInput")
        self.year_edit.setRange(1900, date.today().year + 2)
        self.year_edit.setValue(date.today().year)
        identity.addWidget(field("Year", self.year_edit), 2, 0)

        self.seats_edit = QSpinBox(self.card)
        self.seats_edit.setObjectName("warmInput")
        self.seats_edit.setRange(0, 60)
        self.seats_edit.setValue(5)
        identity.addWidget(field("Seats (0 = unset)", self.seats_edit), 2, 1)
        left.addLayout(identity)
        left.addSpacing(2)

        # -- specs ------------------------------------------------------
        left.addWidget(SectionTitle("Specs", self.card))
        specs = QGridLayout()
        specs.setContentsMargins(0, 0, 0, 0)
        specs.setHorizontalSpacing(20)
        specs.setVerticalSpacing(14)
        specs.setColumnStretch(0, 1)
        specs.setColumnStretch(1, 1)

        self.category_combo = QComboBox(self.card)
        self.category_combo.addItems(["Car", "Motorcycle"])
        self.category_chips = _chip_selector(self.category_combo, self.card)
        specs.addWidget(field("Category", self.category_chips), 0, 0, 1, 2)

        self.trans_combo = QComboBox(self.card)
        self.trans_combo.addItems(list(TRANSMISSIONS))
        self.transmission_chips = _chip_selector(self.trans_combo, self.card)
        specs.addWidget(field("Transmission", self.transmission_chips), 1, 0)

        self.class_combo = QComboBox(self.card)
        self.class_combo.setObjectName("warmInput")
        self.class_combo.addItem("Unset", None)
        for cls in VEHICLE_CLASSES:
            self.class_combo.addItem(VEHICLE_CLASS_LABELS[cls], cls)
        specs.addWidget(field("Size class", self.class_combo), 1, 1)

        self.fuel_combo = QComboBox(self.card)
        self.fuel_combo.addItems(list(FUEL_TYPES))
        self.fuel_chips = _chip_selector(self.fuel_combo, self.card)
        specs.addWidget(field("Fuel type", self.fuel_chips), 2, 0, 1, 2)

        self.cc_edit = QSpinBox(self.card)
        self.cc_edit.setObjectName("warmInput")
        self.cc_edit.setRange(0, 2000)
        specs.addWidget(field("Engine size (cc)", self.cc_edit), 3, 0, 1, 2)
        left.addLayout(specs)
        left.addSpacing(2)

        # -- rate and odometer -----------------------------------------
        left.addWidget(SectionTitle("Rate and odometer", self.card))
        rate_grid = QGridLayout()
        rate_grid.setContentsMargins(0, 0, 0, 0)
        rate_grid.setHorizontalSpacing(20)
        rate_grid.setVerticalSpacing(14)
        rate_grid.setColumnStretch(0, 1)
        rate_grid.setColumnStretch(1, 1)

        self.rate_edit = QLineEdit(self.card)
        self.rate_edit.setObjectName("warmInput")
        self.rate_edit.setPlaceholderText("0.00")
        rate_grid.addWidget(field("Daily rate (₱)", self.rate_edit), 0, 0)

        self.mileage_edit = QSpinBox(self.card)
        self.mileage_edit.setObjectName("warmInput")
        self.mileage_edit.setRange(0, 9_999_999)
        rate_grid.addWidget(field("Odometer (km)", self.mileage_edit), 0, 1)
        left.addLayout(rate_grid)
        left.addSpacing(2)

        # -- paperwork --------------------------------------------------
        left.addWidget(SectionTitle("Paperwork dates", self.card))
        dates = QGridLayout()
        dates.setContentsMargins(0, 0, 0, 0)
        dates.setHorizontalSpacing(20)
        dates.setVerticalSpacing(14)
        dates.setColumnStretch(0, 1)
        dates.setColumnStretch(1, 1)

        self.reg_edit = QDateEdit(self.card)
        self.reg_edit.setObjectName("warmInput")
        self.reg_edit.setCalendarPopup(True)
        self.reg_edit.setDisplayFormat("dd MMM yyyy")
        self.reg_edit.setDate(QDate.currentDate().addYears(1))
        self.reg_set = QCheckBox("Track this expiry", self.card)
        self.reg_set.setObjectName("fleetCheck")
        reg_host = QWidget(self.card)
        reg_host.setObjectName("fleetFieldHost")
        reg_lay = QVBoxLayout(reg_host)
        reg_lay.setContentsMargins(0, 0, 0, 0)
        reg_lay.setSpacing(8)
        reg_lay.addWidget(self.reg_edit)
        reg_lay.addWidget(self.reg_set)
        dates.addWidget(field("Registration expiry", reg_host), 0, 0)

        self.ins_edit = QDateEdit(self.card)
        self.ins_edit.setObjectName("warmInput")
        self.ins_edit.setCalendarPopup(True)
        self.ins_edit.setDisplayFormat("dd MMM yyyy")
        self.ins_edit.setDate(QDate.currentDate().addYears(1))
        self.ins_set = QCheckBox("Track this expiry", self.card)
        self.ins_set.setObjectName("fleetCheck")
        ins_host = QWidget(self.card)
        ins_host.setObjectName("fleetFieldHost")
        ins_lay = QVBoxLayout(ins_host)
        ins_lay.setContentsMargins(0, 0, 0, 0)
        ins_lay.setSpacing(8)
        ins_lay.addWidget(self.ins_edit)
        ins_lay.addWidget(self.ins_set)
        dates.addWidget(field("Insurance expiry", ins_host), 0, 1)
        left.addLayout(dates)
        left.addStretch(1)

        # -- live preview ----------------------------------------------
        self._preview = _FleetPreview(self.card)
        right.addWidget(self._preview)
        right.addWidget(
            NoticeBox(
                "Expiry reminders only run for dates you tick.",
                tone="amber",
                parent=self.card,
            )
        )
        self._media_hint = Caption("", parent=self.card, colour=theme.WARN)
        self._media_hint.setWordWrap(True)
        right.addWidget(self._media_hint)
        right.addStretch(1)

        back, go = self._buttons("Save vehicle")
        back.clicked.connect(self.cancelled.emit)
        go.clicked.connect(self._on_go)
        self._go = go

        # Spelling probe: debounced, off-thread, advisory only -- a miss
        # never blocks the save (the API may simply be down).
        self._probe_pool = media_pool()
        self._probe_signals = _ProbeSignals(self)
        self._probe_signals.done.connect(self._on_probe_done)
        self._probe_gen = 0
        self._probe_timer = QTimer(self)
        self._probe_timer.setSingleShot(True)
        self._probe_timer.setInterval(PROBE_DEBOUNCE_MS)
        self._probe_timer.timeout.connect(self._run_probe)
        self.make_edit.textChanged.connect(lambda _t: self._schedule_probe())
        self.model_edit.textChanged.connect(lambda _t: self._schedule_probe())
        self.year_edit.valueChanged.connect(lambda _v: self._schedule_probe())
        self.category_combo.currentIndexChanged.connect(lambda _i: self._schedule_probe())

        for edit in (self.plate_edit, self.make_edit, self.model_edit, self.rate_edit):
            edit.textChanged.connect(lambda _t: self._refresh_preview())
        for spin in (self.year_edit, self.seats_edit):
            spin.valueChanged.connect(lambda _v: self._refresh_preview())
        self.fuel_combo.currentTextChanged.connect(
            lambda _t: self._refresh_preview()
        )

        if vehicle_id is not None:
            self._prefill()
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        try:
            rate = money(self.rate_edit.text(), field="daily_rate")
        except Exception:  # noqa: BLE001 - preview is advisory, never blocks
            rate = ZERO
        self._preview.update_preview(
            make=self.make_edit.text().strip(),
            model=self.model_edit.text().strip(),
            plate=self.plate_edit.text().strip().upper(),
            year=self.year_edit.value(),
            seats=self.seats_edit.value(),
            fuel=self.fuel_combo.currentText(),
            rate_text=pesos(rate),
            editing=self._vehicle_id is not None,
        )

    def _prefill(self) -> None:
        with self._context.reading() as session:
            vehicle = session.get(Vehicle, self._vehicle_id)
            if vehicle is None:
                return
            category = vehicle.category.category_name if vehicle.category else "Car"
            self._snapshot = {
                "plate_number": vehicle.plate_number,
                "make": vehicle.make,
                "model": vehicle.model,
                "year": vehicle.year,
                "color": vehicle.color,
                "category": category,
                "transmission": vehicle.transmission,
                "fuel_type": vehicle.fuel_type,
                "seats": vehicle.seats,
                "vehicle_class": vehicle.vehicle_class,
                "engine_cc": vehicle.engine_cc,
                "daily_rate": vehicle.daily_rate,
                "mileage": vehicle.mileage,
                "registration_expiry": vehicle.registration_expiry,
                "insurance_expiry": vehicle.insurance_expiry,
            }
        snap = self._snapshot
        self.plate_edit.setText(snap["plate_number"] or "")
        self.make_edit.setText(snap["make"] or "")
        self.model_edit.setText(snap["model"] or "")
        self.year_edit.setValue(int(snap["year"] or date.today().year))
        self.color_edit.setText(snap["color"] or "")
        self.category_combo.setCurrentText(snap["category"] or "Car")
        if snap["transmission"]:
            self.trans_combo.setCurrentText(snap["transmission"])
        if snap["fuel_type"]:
            self.fuel_combo.setCurrentText(snap["fuel_type"])
        self.seats_edit.setValue(int(snap["seats"] or 0))
        if snap["vehicle_class"]:
            self.class_combo.setCurrentText(VEHICLE_CLASS_LABELS.get(snap["vehicle_class"], "Unset"))
        self.cc_edit.setValue(int(snap["engine_cc"] or 0))
        self.rate_edit.setText(f"{snap['daily_rate']:,.2f}" if snap["daily_rate"] is not None else "")
        self.mileage_edit.setValue(int(snap["mileage"] or 0))
        if snap["registration_expiry"] is not None:
            self.reg_edit.setDate(QDate(snap["registration_expiry"].year, snap["registration_expiry"].month, snap["registration_expiry"].day))
            self.reg_set.setChecked(True)
        if snap["insurance_expiry"] is not None:
            self.ins_edit.setDate(QDate(snap["insurance_expiry"].year, snap["insurance_expiry"].month, snap["insurance_expiry"].day))
            self.ins_set.setChecked(True)

    def _fail(self, message: str) -> None:
        self._error.setText(message)
        self._error.setVisible(True)

    def _schedule_probe(self) -> None:
        self._probe_timer.start()

    def _run_probe(self) -> None:
        make = self.make_edit.text().strip()
        model = self.model_edit.text().strip()
        if not make or not model:
            self._media_hint.setText("")
            return
        self._probe_gen += 1
        self._probe_pool.start(
            _MediaProbe(
                make,
                model,
                self.year_edit.value(),
                self.category_combo.currentText(),
                self._probe_gen,
                self._probe_signals,
            )
        )

    def _on_probe_done(self, gen: int, status: str, make: str, model: str) -> None:
        if gen != self._probe_gen:
            return
        if status == "not_found":
            self._media_hint.setText(
                f"No photos found for '{make} {model}' — check spelling? "
                "You can still save."
            )
        elif status == "unreachable":
            self._media_hint.setText(
                "Couldn't reach CarImages — you can still save; "
                "photos + 3D will fetch automatically."
            )
        else:
            self._media_hint.setText("")

    def _on_go(self) -> None:
        # A late probe result must not land after the save: the panel may be
        # gone by then, and the hint is advisory anyway.
        self._probe_timer.stop()
        self._probe_gen += 1
        try:
            rate = money(self.rate_edit.text(), field="daily_rate")
        except Exception as exc:  # noqa: BLE001
            self._fail(f"Daily rate must be a number: {exc}")
            return
        if rate <= ZERO:
            self._fail("Daily rate must be greater than zero.")
            return
        year = self.year_edit.value()
        plate = self.plate_edit.text().strip().upper()
        if not plate:
            self._fail("Plate number is required.")
            return
        if len(plate) > 7:
            self._fail("Plate number must be 7 characters or fewer.")
            return
        if not self.make_edit.text().strip() or not self.model_edit.text().strip():
            self._fail("Brand and model are required.")
            return
        if year < 1900 or year > date.today().year + 2:
            self._fail(f"Year must be between 1900 and {date.today().year + 2}.")
            return
        if getattr(self, "_vehicle_id", None) is not None:
            snap_mileage = int((getattr(self, "_snapshot", {}) or {}).get("mileage") or 0)
            if self.mileage_edit.value() < snap_mileage:
                self._fail(
                    f"Mileage cannot go backwards: it is currently {snap_mileage:,} km."
                )
                return
        self._error.setVisible(False)
        self.submitted.emit(
            {
                "plate_number": self.plate_edit.text().strip().upper(),
                "make": self.make_edit.text().strip(),
                "model": self.model_edit.text().strip(),
                "year": year,
                "color": self.color_edit.text().strip() or None,
                "category_name": self.category_combo.currentText(),
                "transmission": self.trans_combo.currentText(),
                "fuel_type": self.fuel_combo.currentText(),
                "seats": self.seats_edit.value() or None,
                "vehicle_class": self.class_combo.currentData(),
                "engine_cc": self.cc_edit.value() or None,
                "daily_rate": rate,
                "mileage": self.mileage_edit.value(),
                "registration_expiry": self.reg_edit.date().toPython() if self.reg_set.isChecked() else None,
                "insurance_expiry": self.ins_edit.date().toPython() if self.ins_set.isChecked() else None,
            }
        )


# -- bulk update ------------------------------------------------------------


class BulkPanel(_StepBase):
    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(self, context, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        self._head("Bulk update", "One change across many vehicles.")
        self._error = self._error_label()

        self.mode_group = QButtonGroup(self)
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(8)
        self.rate_button = make_choice_button("Rate", self.card)
        self.status_button = make_choice_button("Status", self.card)
        self.mode_group.addButton(self.rate_button)
        self.mode_group.addButton(self.status_button)
        self.rate_button.setChecked(True)
        self.rate_button.clicked.connect(self._on_mode)
        self.status_button.clicked.connect(self._on_mode)
        mode_row.addWidget(self.rate_button, 1)
        mode_row.addWidget(self.status_button, 1)
        self._body.addLayout(mode_row)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        self.class_combo = QComboBox(self.card)
        self.class_combo.addItem("All classes", None)
        for cls in VEHICLE_CLASSES:
            self.class_combo.addItem(VEHICLE_CLASS_LABELS[cls], cls)
        form.addRow(_field_label("Size class", self.card), self.class_combo)
        self.category_combo = QComboBox(self.card)
        self.category_combo.addItems(["All categories", "Car", "Motorcycle"])
        form.addRow(_field_label("Category", self.card), self.category_combo)
        self._body.addLayout(form)

        # -- rate controls --
        self._rate_host = QWidget(self.card)
        self._rate_host.setObjectName("wizardStep")
        rate_lay = QFormLayout(self._rate_host)
        rate_lay.setContentsMargins(0, 0, 0, 0)
        rate_lay.setSpacing(6)
        self.new_rate_edit = QLineEdit(self._rate_host)
        self.new_rate_edit.setPlaceholderText("0.00")
        rate_lay.addRow(_field_label("New daily rate (PHP)", self._rate_host), self.new_rate_edit)
        guard_row = QHBoxLayout()
        guard_row.setContentsMargins(0, 0, 0, 0)
        guard_row.setSpacing(8)
        self.guard_check = QCheckBox("Only update vehicles currently at", self._rate_host)
        self.old_rate_edit = QLineEdit(self._rate_host)
        self.old_rate_edit.setPlaceholderText("0.00")
        self.old_rate_edit.setEnabled(False)
        self.guard_check.toggled.connect(self.old_rate_edit.setEnabled)
        guard_row.addWidget(self.guard_check)
        guard_row.addWidget(self.old_rate_edit, 1)
        rate_lay.addRow(_field_label("Protect exceptions", self._rate_host), guard_row)
        self._body.addWidget(self._rate_host)

        # -- status controls --
        self._status_host = QWidget(self.card)
        self._status_host.setObjectName("wizardStep")
        status_lay = QFormLayout(self._status_host)
        status_lay.setContentsMargins(0, 0, 0, 0)
        status_lay.setSpacing(6)
        self.status_combo = QComboBox(self._status_host)
        self.status_combo.addItems(["available", "maintenance", "retired"])
        status_lay.addRow(_field_label("New status", self._status_host), self.status_combo)
        self._body.addWidget(self._status_host)
        self._body.addWidget(
            Caption(
                "Retiring is refused per vehicle while it holds an active booking.",
                parent=self.card,
            )
        )

        self.preview = Caption("", parent=self.card)
        self._body.addWidget(self.preview)

        for widget in (
            self.class_combo, self.category_combo, self.new_rate_edit,
            self.old_rate_edit, self.status_combo,
        ):
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(lambda _t: self._refresh_preview())
            else:
                widget.currentIndexChanged.connect(lambda _i: self._refresh_preview())
        self.guard_check.toggled.connect(lambda _on: self._refresh_preview())

        back, go = self._buttons("Apply to set")
        back.clicked.connect(self.cancelled.emit)
        go.clicked.connect(self._on_go)
        self._on_mode()
        self._refresh_preview()

    def _mode(self) -> str:
        return "status" if self.status_button.isChecked() else "rate"

    def _on_mode(self) -> None:
        rate = self._mode() == "rate"
        self._rate_host.setVisible(rate)
        self._status_host.setVisible(not rate)
        self._refresh_preview()

    def _scope_ids(self) -> list[int]:
        vehicle_class = self.class_combo.currentData()
        category = self.category_combo.currentText()
        if category == "All categories":
            category = None
        with self._context.reading() as session:
            query = session.query(Vehicle.vehicle_id)
            if vehicle_class:
                query = query.filter(Vehicle.vehicle_class == vehicle_class)
            if category:
                from app.models import Vehicle_Category

                query = query.join(
                    Vehicle_Category, Vehicle.category_id == Vehicle_Category.category_id
                ).filter(Vehicle_Category.category_name == category)
            return [vid for (vid,) in query.all()]

    def _refresh_preview(self) -> None:
        try:
            ids = self._scope_ids()
        except Exception:  # noqa: BLE001 - advisory preview only
            self.preview.setText("")
            return
        if self._mode() == "status":
            target = self.status_combo.currentText()
            self.preview.setText(f"{len(ids)} vehicle(s) → {target}.")
            return
        try:
            new_rate = money(self.new_rate_edit.text(), field="new_rate") if self.new_rate_edit.text().strip() else None
        except Exception:  # noqa: BLE001
            self.preview.setText("New rate must be a number.")
            return
        if new_rate is None:
            self.preview.setText(f"{len(ids)} vehicle(s) in scope.")
            return
        guard = None
        if self.guard_check.isChecked():
            try:
                guard = money(self.old_rate_edit.text(), field="old_rate") if self.old_rate_edit.text().strip() else None
            except Exception:  # noqa: BLE001
                self.preview.setText("Guard rate must be a number.")
                return
            if guard is None:
                self.preview.setText("Type the rate to protect, or untick the guard.")
                return
        try:
            with self._context.reading() as session:
                preview = fleet_service.preview_bulk_rate(session, ids, only_at_rate=guard)
        except Exception:  # noqa: BLE001
            self.preview.setText("")
            return
        if guard is not None:
            self.preview.setText(f"{preview['count']} vehicle(s): ₱{guard:,.2f} → ₱{new_rate:,.2f}.")
        elif preview["mode"] is not None and len(preview["spread"]) == 1:
            self.preview.setText(f"{preview['count']} vehicle(s): ₱{preview['mode']} → ₱{new_rate:,.2f}.")
        else:
            self.preview.setText(f"{preview['count']} vehicle(s) (mixed rates) → ₱{new_rate:,.2f}.")

    def _on_go(self) -> None:
        ids = self._scope_ids()
        if not ids:
            self._error.setText("Nothing in scope: loosen the class/category filters.")
            self._error.setVisible(True)
            return
        if self._mode() == "status":
            self._error.setVisible(False)
            self.submitted.emit(
                {"mode": "status", "ids": ids, "new_status": self.status_combo.currentText()}
            )
            return
        try:
            new_rate = money(self.new_rate_edit.text(), field="new_rate")
        except Exception as exc:  # noqa: BLE001
            self._error.setText(f"New rate must be a number: {exc}")
            self._error.setVisible(True)
            return
        if new_rate <= ZERO:
            self._error.setText("New rate must be greater than zero.")
            self._error.setVisible(True)
            return
        guard = None
        if self.guard_check.isChecked():
            try:
                guard = money(self.old_rate_edit.text(), field="old_rate")
            except Exception as exc:  # noqa: BLE001
                self._error.setText(f"Guard rate must be a number: {exc}")
                self._error.setVisible(True)
                return
        self._error.setVisible(False)
        self.submitted.emit(
            {"mode": "rate", "ids": ids, "new_rate": new_rate, "only_at_rate": guard}
        )


# -- fleet details ----------------------------------------------------------


class _MediaSignals(QObject):
    done = Signal(list)
    failed = Signal(object)


class _MediaRefresh(QRunnable):
    """Fetch CarImages rows (and their bytes) off the GUI thread.

    Uses a write session so freshly fetched rows persist. `force` clears
    existing rows first, making Refresh a true re-fetch instead of a
    freshness no-op. Image bytes ride along in the snapshot so the GUI
    never blocks on the network.
    """

    def __init__(self, context, vehicle_id: int, signals: _MediaSignals, *, force: bool = False) -> None:
        super().__init__()
        self._context = context
        self._vehicle_id = vehicle_id
        self._done = signals.done  # bound while the panel is alive
        self._failed = signals.failed
        self._force = force

    def run(self) -> None:
        try:
            with self._context.session() as session:
                vehicle = session.get(Vehicle, self._vehicle_id)
                if vehicle is None:
                    emit_safely(self._done, [])
                    return
                rows = media_service.get_or_fetch_media(
                    session,
                    vehicle,
                    include_3d=True,
                    force=self._force,
                )
                snapshot = []
                model_glb = ""
                for r in rows:
                    model_url = media_service.playable_model_url(session, r)
                    if model_url and not model_glb:
                        model_glb = _store_model_glb(
                            vehicle, _download(model_url)
                        )
                    snapshot.append(
                        {
                            "view_angle": r.view_angle,
                            "image_url": r.image_url,
                            "model_3d_url": model_url,
                            "model_glb": model_glb,
                            "is_watermarked": r.is_watermarked,
                            "image_data": _download(r.image_url),
                        }
                    )
        except Exception as exc:  # noqa: BLE001 - forwarded to the GUI slot
            emit_safely(self._failed, exc)
        else:
            emit_safely(self._done, snapshot)


class FleetDetailsPanel(_FleetSheet):
    closed = Signal()
    edit_requested = Signal(int)
    refreshed = Signal(str)

    def __init__(self, context, vehicle_id: int, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        self._vehicle_id = vehicle_id
        self._window_days = 30
        self._media_rows: list[dict] = []
        self._bytes_by_url: dict[str, bytes] = {}
        #: False until the first fetch worker lands; drives the hero's
        #: loading vs empty text.
        self._media_loaded = False
        #: Manual Refresh toasts; the silent open-fetch stays quiet.
        self._quiet_refresh = False
        self._pool = media_pool()
        self._media_signals = _MediaSignals(self)
        self._media_signals.done.connect(self._on_media_done)
        self._media_signals.failed.connect(self._on_media_failed)
        self._head(f"Vehicle #{vehicle_id}", "Vehicle", "")
        self.edit_button, self.close_button = self._buttons(
            "Close", back_text="Edit"
        )
        self.edit_button.clicked.connect(
            lambda: self.edit_requested.emit(self._vehicle_id)
        )
        self.close_button.clicked.connect(self.closed.emit)
        self._load()

    # -- data -----------------------------------------------------------

    def _read(self) -> dict | None:
        with self._context.reading() as session:
            vehicle = session.get(Vehicle, self._vehicle_id)
            if vehicle is None:
                return None
            from app.models import Inspection_Report, Maintenance_Record

            actives = (
                session.query(Booking)
                .filter(
                    Booking.vehicle_id == vehicle.vehicle_id,
                    Booking.status.in_(("pending", "confirmed", "ongoing")),
                )
                .order_by(Booking.start_date.desc())
                .limit(3)
                .all()
            )
            past = (
                session.query(Booking)
                .filter(
                    Booking.vehicle_id == vehicle.vehicle_id,
                    Booking.status.in_(("completed", "cancelled")),
                )
                .order_by(Booking.start_date.desc())
                .limit(5)
                .all()
            )

            def _booking_row(b) -> dict:
                return {
                    "ref": b.booking_id,
                    "customer": b.user.full_name if b.user else DASH,
                    "start": b.start_date,
                    "end": b.end_date,
                    "status": b.status,
                    "total": b.total_cost,
                }

            inspections = (
                session.query(Inspection_Report)
                .join(Booking, Inspection_Report.booking_id == Booking.booking_id)
                .filter(Booking.vehicle_id == vehicle.vehicle_id)
                .order_by(Inspection_Report.inspected_at.desc())
                .limit(5)
                .all()
            )
            inspection_rows = [
                {
                    "ref": r.booking.booking_id if r.booking else DASH,
                    "type": (r.inspection_type or "").replace("-", " ").title(),
                    "mileage": r.mileage_reading,
                    "fuel": (r.fuel_level or "-").replace("_", " ").title(),
                    "damage": r.damage_notes or "No damage",
                }
                for r in inspections
            ]
            maintenance = (
                session.query(Maintenance_Record)
                .filter(Maintenance_Record.vehicle_id == vehicle.vehicle_id)
                .order_by(Maintenance_Record.start_date.desc())
                .limit(5)
                .all()
            )
            maintenance_rows = [
                {
                    "start": r.start_date,
                    "end": r.end_date,
                    "description": r.description or DASH,
                    "status": r.status,
                    "cost": r.cost,
                }
                for r in maintenance
            ]
            media = [
                {
                    "view_angle": r.view_angle,
                    "image_url": r.image_url,
                    "model_3d_url": r.model_3d_url,
                    "is_watermarked": r.is_watermarked,
                }
                for r in vehicle.media
            ]
            stats = fleet_service.vehicle_stats(session, vehicle, self._window_days)
            return {
                "name": f"{vehicle.make} {vehicle.model}".strip(),
                "subtitle": f"{vehicle.year} · {vehicle.plate_number}",
                "rate": vehicle.daily_rate,
                "status": vehicle.status,
                "make": vehicle.make or "",
                "model": vehicle.model or "",
                "year": int(vehicle.year or 0),
                "specs": [
                    ("Plate", vehicle.plate_number or DASH),
                    ("Color", vehicle.color or DASH),
                    ("Seats", str(vehicle.seats) if vehicle.seats is not None else DASH),
                    ("Transmission", vehicle.transmission or DASH),
                    ("Fuel", vehicle.fuel_type or DASH),
                    (
                        "Class",
                        VEHICLE_CLASS_LABELS.get(
                            vehicle.vehicle_class or "", vehicle.vehicle_class or DASH
                        ),
                    ),
                    ("Mileage", f"{vehicle.mileage:,} km" if vehicle.mileage is not None else DASH),
                    (
                        "Registration",
                        vehicle.registration_expiry.strftime("%d %b %Y") if vehicle.registration_expiry else DASH,
                    ),
                    (
                        "Insurance",
                        vehicle.insurance_expiry.strftime("%d %b %Y") if vehicle.insurance_expiry else DASH,
                    ),
                ],
                "bookings": [_booking_row(b) for b in actives],
                "history": [_booking_row(b) for b in past],
                "inspections": inspection_rows,
                "maintenance": maintenance_rows,
                "media": media,
                "stats": stats,
            }

    def _section(self, title: str, lines: list[str]) -> None:
        self._body.addWidget(SectionTitle(title, self.card))
        for line in lines:
            self._body.addWidget(Caption(line, parent=self.card))

    # -- layout ---------------------------------------------------------

    def _load(self) -> None:
        data = self._read()
        left, right = self._start_columns(left_stretch=4, right_stretch=6)
        self._error = self._error_label()
        if data is None:
            self._set_header("Vehicle", f"#{self._vehicle_id}")
            self.edit_button.setEnabled(False)
            self._section("Missing", ["That vehicle no longer exists."])
            return
        self._data = data
        self._media_rows = list(data["media"])

        self._set_header(data["name"], data["subtitle"])
        self.status_host.addWidget(
            StatusChip(
                data["status"].title(),
                tone=_STATUS_TONES.get(data["status"], "neutral"),
                parent=self.card,
            ),
            0,
            Qt.AlignmentFlag.AlignRight,
        )
        self.price = MoneyNumber(
            parent=self.card,
            size=30,
            dim=FLEET_MUTED,
            bright=FLEET_INK,
            weight=QFont.Weight.Bold,
        )
        self.price.set_amount(data["rate"] or 0)
        self.title_right.addWidget(self.price)
        self.title_right.addWidget(
            _fleet_text("/ day", parent=self.card, size=13, colour=FLEET_MUTED)
        )

        # -- left column: gallery + 3D ---------------------------------
        left.addWidget(SectionTitle("Gallery", self.card))
        self._hero = QLabel(self.card)
        self._hero.setObjectName("fleetHero")
        self._hero.setFixedHeight(300)
        self._hero.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self._hero)
        self._strip = QHBoxLayout()
        self._strip.setContentsMargins(0, 0, 0, 0)
        self._strip.setSpacing(8)
        strip_host = QWidget(self.card)
        strip_host.setObjectName("fleetGalleryStrip")
        strip_host.setLayout(self._strip)
        left.addWidget(strip_host)
        self._fill_gallery()

        left.addWidget(SectionTitle("3D preview", self.card))
        self._viewer_host = QVBoxLayout()
        self._viewer_host.setContentsMargins(0, 0, 0, 0)
        self._viewer_host.setSpacing(8)
        viewer_box = QWidget(self.card)
        viewer_box.setObjectName("fleetViewerHost")
        viewer_box.setLayout(self._viewer_host)
        left.addWidget(viewer_box)
        self._fill_viewer()

        self.refresh_button = QPushButton("Refresh photos + 3D", self.card)
        self.refresh_button.setObjectName("secondaryButton")
        self.refresh_button.setMinimumHeight(38)
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_button.clicked.connect(self._on_refresh_media)
        left.addWidget(self.refresh_button)
        left.addStretch(1)

        # -- right column: metrics, specs, history ---------------------
        stats = data["stats"]
        metrics = QHBoxLayout()
        metrics.setContentsMargins(0, 0, 0, 0)
        metrics.setSpacing(8)
        for label, value in (
            ("Times rented", f"{stats['times_rented']}×"),
            ("All-time revenue", pesos(stats["total_revenue"])),
            ("Maintenance spend", pesos(stats["maintenance_cost"])),
        ):
            metrics.addWidget(MetricTile(label, value, parent=self.card), 1)
        right.addLayout(metrics)

        actives = [
            b
            for b in data["bookings"]
            if b["status"] in ("pending", "confirmed", "ongoing")
        ]
        current = actives[0] if actives else None
        if current:
            right.addWidget(
                NoticeBox(
                    f"Out now: #{current['ref']} · {current['customer']} · "
                    f"{current['start']:%d %b} → {current['end']:%d %b}",
                    tone="amber",
                    parent=self.card,
                )
            )
        for b in actives[1:2]:
            right.addWidget(
                NoticeBox(
                    f"Next: #{b['ref']} · {b['customer']} · "
                    f"{b['start']:%d %b} → {b['end']:%d %b}",
                    tone="cream",
                    parent=self.card,
                )
            )

        right.addWidget(SectionTitle("Specifications", self.card))
        spec_grid = QGridLayout()
        spec_grid.setContentsMargins(0, 0, 0, 0)
        spec_grid.setHorizontalSpacing(8)
        spec_grid.setVerticalSpacing(8)
        for index, (key, value) in enumerate(data["specs"]):
            spec_grid.addWidget(
                SpecTile(key, value, self.card), index // 3, index % 3
            )
        right.addLayout(spec_grid)

        right.addWidget(SectionTitle("Booking history", self.card))
        history = data["history"]
        if history:
            for index, booking in enumerate(history):
                right.addWidget(self._history_row(booking))
                if index < len(history) - 1:
                    right.addWidget(self._row_rule())
        else:
            right.addWidget(Caption("No past rentals.", parent=self.card))

        right.addWidget(SectionTitle("Inspections", self.card))
        if data["inspections"]:
            for index, inspection in enumerate(data["inspections"]):
                right.addWidget(self._inspection_row(inspection))
                if index < len(data["inspections"]) - 1:
                    right.addWidget(self._row_rule())
        else:
            right.addWidget(Caption("No inspection records.", parent=self.card))

        right.addWidget(SectionTitle("Maintenance", self.card))
        if data["maintenance"]:
            for index, record in enumerate(data["maintenance"]):
                right.addWidget(self._maintenance_row(record))
                if index < len(data["maintenance"]) - 1:
                    right.addWidget(self._row_rule())
        else:
            right.addWidget(Caption("No maintenance records.", parent=self.card))

        right.addWidget(SectionTitle("Utilization", self.card))
        switch = QHBoxLayout()
        switch.setContentsMargins(0, 0, 0, 0)
        switch.setSpacing(8)
        self._window_group = QButtonGroup(self)
        self._window_buttons: dict[int, QPushButton] = {}
        for days in STAT_WINDOWS:
            button = QPushButton(f"{days}d", self.card)
            button.setObjectName("choiceChip")
            button.setProperty("variant", "choice")
            button.setCheckable(True)
            button.setChecked(days == self._window_days)
            button.setFixedHeight(34)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _c=False, _d=days: self._on_window(_d)
            )
            self._window_group.addButton(button)
            switch.addWidget(button)
            self._window_buttons[days] = button
        switch.addStretch(1)
        right.addLayout(switch)

        self._util_bar = QProgressBar(self.card)
        self._util_bar.setObjectName("fleetUtilBar")
        self._util_bar.setRange(0, 100)
        self._util_bar.setTextVisible(False)
        self._util_bar.setFixedHeight(8)
        right.addWidget(self._util_bar)

        self._stats_caption = Caption("", parent=self.card)
        self._stats_caption.setVisible(False)
        right.addWidget(self._stats_caption)
        self._util_caption = Caption("", parent=self.card)
        right.addWidget(self._util_caption)
        right.addStretch(1)
        self._refresh_stats_text()
        # Fetch on open: the panel calls the CarImages API itself instead
        # of waiting for Refresh. Freshness-aware, usually a cache hit.
        self._start_silent_refresh()

    def _row_rule(self) -> QFrame:
        rule = QFrame(self.card)
        rule.setObjectName("fleetRowRule")
        rule.setFixedHeight(1)
        return rule

    def _history_row(self, booking: dict) -> QFrame:
        row = QFrame(self.card)
        row.setObjectName("fleetHistoryRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(10)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        text.addWidget(
            _fleet_text(
                f"#{booking['ref']} {booking['customer']}",
                parent=row,
                size=13,
                weight=QFont.Weight.DemiBold,
            )
        )
        text.addWidget(
            Caption(
                f"{booking['start']:%d %b %Y} → {booking['end']:%d %b %Y}",
                parent=row,
                colour=FLEET_MUTED,
                size=12,
            )
        )
        layout.addLayout(text, 1)
        layout.addWidget(
            StatusChip(
                booking["status"].title(),
                tone=_STATUS_TONES.get(booking["status"], "neutral"),
                parent=row,
            ),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        return row

    def _inspection_row(self, inspection: dict) -> QFrame:
        row = QFrame(self.card)
        row.setObjectName("fleetHistoryRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(10)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        text.addWidget(
            _fleet_text(
                f"#{inspection['ref']} {inspection['type']}",
                parent=row,
                size=13,
                weight=QFont.Weight.DemiBold,
            )
        )
        text.addWidget(
            Caption(
                inspection["damage"],
                parent=row,
                colour=FLEET_MUTED,
                size=12,
            )
        )
        layout.addLayout(text, 1)
        chip_text = (
            f"{inspection['mileage']:,} km · fuel {inspection['fuel']}"
            if inspection["mileage"] is not None
            else f"fuel {inspection['fuel']}"
        )
        chip = QLabel(chip_text, row)
        chip.setObjectName("fleetMetaChip")
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chip.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        layout.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _maintenance_row(self, record: dict) -> QFrame:
        row = QFrame(self.card)
        row.setObjectName("fleetHistoryRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(10)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        text.addWidget(
            _fleet_text(
                record["description"],
                parent=row,
                size=13,
                weight=QFont.Weight.DemiBold,
            )
        )
        text.addWidget(
            Caption(
                f"{record['start']:%d %b %Y} → {record['end']:%d %b %Y}",
                parent=row,
                colour=FLEET_MUTED,
                size=12,
            )
        )
        layout.addLayout(text, 1)
        layout.addWidget(
            StatusChip(
                record["status"].title(),
                tone=_STATUS_TONES.get(record["status"], "neutral"),
                parent=row,
            ),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        return row

    def _refresh_stats_text(self) -> None:
        stats = self._data["stats"]
        self._stats_caption.setText(
            f"All-time: Rented {stats['times_rented']}× · revenue {pesos(stats['total_revenue'])} · "
            f"maintenance {pesos(stats['maintenance_cost'])}."
        )
        if stats["utilization"] is None:
            self._util_bar.setValue(0)
            self._util_caption.setText(
                f"Last {stats['window_days']}d: unavailable all window (shop/retired)."
            )
        else:
            self._util_bar.setValue(round(stats["utilization"] * 100))
            self._util_caption.setText(
                f"Last {stats['window_days']}d: {stats['rented_days']} rented / "
                f"{stats['available_days']} available = {stats['utilization']:.0%}."
            )

    def _on_window(self, days: int) -> None:
        self._window_days = days
        with self._context.reading() as session:
            vehicle = session.get(Vehicle, self._vehicle_id)
            self._data["stats"] = fleet_service.vehicle_stats(session, vehicle, days)
        self._refresh_stats_text()

    # -- gallery + viewer -----------------------------------------------

    def _fill_gallery(self) -> None:
        while self._strip.count():
            item = self._strip.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        cache_bytes = _hero_bytes(
            self._data["make"], self._data["model"], self._data["year"]
        )
        self._bytes_by_url = {}
        hero_shown = False
        for index, row in enumerate(self._media_rows):
            url = row.get("image_url") or ""
            data = row.get("image_data") or cache_bytes
            if url and data:
                self._bytes_by_url[url] = data
            if not hero_shown and data:
                hero = _pixmap(data, 620, 300)
                if hero is not None:
                    self._hero.setPixmap(hero)
                    hero_shown = True
            thumb = _pixmap(data, 96, 72)
            label = QLabel(self.card)
            label.setObjectName("fleetThumb")
            label.setProperty("selected", "true" if index == 0 else "false")
            label.setFixedSize(96, 72)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if thumb is not None:
                label.setPixmap(thumb)
            else:
                label.setText(row.get("view_angle") or "photo")
            label.setCursor(Qt.CursorShape.PointingHandCursor)
            label.mousePressEvent = lambda _e, _u=url: self._show_url(_u)
            self._strip.addWidget(label)
        self._strip.addStretch(1)
        if not hero_shown:
            if cache_bytes:
                hero = _pixmap(cache_bytes, 620, 300)
                if hero is not None:
                    self._hero.setPixmap(hero)
                    return
            self._hero.setText(
                "Loading photos…" if not self._media_loaded else "No photos cached yet."
            )

    def _show_url(self, url: str) -> None:
        data = self._bytes_by_url.get(url)
        if data is None:
            data = _hero_bytes(
                self._data["make"], self._data["model"], self._data["year"]
            )
        pix = _pixmap(data, 620, 300)
        if pix is not None:
            self._hero.setPixmap(pix)
        else:
            self._hero.setText("Photo failed to load.")

    def _model_url(self) -> str:
        for row in self._media_rows:
            if row.get("model_3d_url"):
                return row["model_3d_url"]
        return ""

    def _model_glb_path(self) -> str:
        for row in self._media_rows:
            path = row.get("model_glb")
            if path and Path(path).is_file():
                return path
        return ""

    def _fill_viewer(self) -> None:
        while self._viewer_host.count():
            item = self._viewer_host.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        path = self._model_glb_path()
        url = self._model_url()
        if path:
            ready = QFrame(self.card)
            ready.setObjectName("fleetViewerReady")
            ready_lay = QVBoxLayout(ready)
            ready_lay.setContentsMargins(16, 14, 16, 14)
            ready_lay.setSpacing(6)
            title = _fleet_text(
                "3D model ready",
                parent=ready,
                size=14,
                weight=QFont.Weight.DemiBold,
            )
            ready_lay.addWidget(title)
            caption = Caption(
                "Opens in a separate preview window, so a 3D display problem "
                "cannot close the desk.",
                parent=ready,
                colour=FLEET_MUTED,
                size=12,
            )
            caption.setWordWrap(True)
            ready_lay.addWidget(caption)
            self._viewer_note = QLabel("", ready)
            self._viewer_note.setObjectName("fleetViewerPlaceholder")
            self._viewer_note.setWordWrap(True)
            ready_lay.addWidget(self._viewer_note)
            self.open_3d_button = QPushButton("Open 3D preview", ready)
            self.open_3d_button.setObjectName("secondaryButton")
            self.open_3d_button.setMinimumHeight(38)
            self.open_3d_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.open_3d_button.clicked.connect(self._open_3d_preview)
            ready_lay.addWidget(self.open_3d_button)
            self._viewer_host.addWidget(ready)
            return

        reason = (
            "No 3D model cached for this vehicle yet."
            if not url
            else "The 3D model could not be downloaded. Refresh photos + 3D "
            "to retry."
        )
        placeholder = QLabel(reason, self.card)
        placeholder.setObjectName("fleetViewerPlaceholder")
        placeholder.setMinimumHeight(72)
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setWordWrap(True)
        self._viewer_host.addWidget(placeholder)
        self.open_3d_button = None

    def _open_3d_preview(self) -> None:
        path = self._model_glb_path()
        if not path:
            if getattr(self, "_viewer_note", None) is not None:
                self._viewer_note.setText(
                    "No 3D model is available. Refresh photos + 3D to retry."
                )
            return
        label = self._data.get("name") or f"Vehicle #{self._vehicle_id}"
        if model_preview.launch_model_preview(Path(path), label):
            self._viewer_note.setText(
                "The 3D preview opened in its own window."
            )
        else:
            self._viewer_note.setText(
                "Could not open the 3D preview. Try again in a moment."
            )

    def _on_refresh_media(self) -> None:
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("Refreshing…")
        self._quiet_refresh = False
        self._pool.start(
            _MediaRefresh(self._context, self._vehicle_id, self._media_signals, force=True)
        )

    def _start_silent_refresh(self) -> None:
        """Fetch on open (freshness-aware, usually a no-op): the panel must
        call the CarImages API itself instead of waiting for Refresh."""
        self._quiet_refresh = True
        self._pool.start(
            _MediaRefresh(self._context, self._vehicle_id, self._media_signals, force=False)
        )

    def _on_media_done(self, rows: list) -> None:
        self._media_loaded = True
        self._media_rows = list(rows)
        self._fill_gallery()
        self._fill_viewer()
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("Refresh photos + 3D")
        if not self._quiet_refresh:
            self.refreshed.emit(f"Media refreshed ({len(rows)} row(s)).")
        self._quiet_refresh = False

    def _on_media_failed(self, exc: object) -> None:
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("Refresh photos + 3D")
        self._error.setText(_media_failure_message(exc))
        self._error.setVisible(True)
