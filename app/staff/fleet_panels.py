"""Fleet action panels: frosted-overlay forms, never OS windows.

Same wizard-step recipe as the bookings panels (transparent host + white 8px
card, inline validation, page toasts results):

- VehicleFormPanel: add / edit a vehicle (plate, brand, model, year, color,
  category, transmission, fuel, seats, class, engine CC, rate, odometer,
  registration + insurance expiry).
- BulkPanel: rate or status across a class/category set, with a live
  preview line and an exception guard for per-vehicle rates.
- FleetDetailsPanel: gallery + embedded 3D viewer + refresh, booking
  history with current/next pin, inspections, maintenance + cost, and
  stats with a 30/90/365 utilization switcher.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from PySide6.QtCore import QDate, Qt, QTimer, QUrl, Signal, QObject, QRunnable, QThreadPool
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

try:  # Optional: viewer degrades to a link when WebEngine is absent.
    from PySide6.QtWebEngineWidgets import QWebEngineView
except ImportError:  # noqa: BLE001
    QWebEngineView = None

from app.models import Booking, Vehicle
from app.services import fleet_service, image_cache, media_service
from app.services.vehicle_service import TRANSMISSIONS, FUEL_TYPES, VEHICLE_CLASSES, VEHICLE_CLASS_LABELS
from app.staff import theme
from app.staff.cards import Caption, SectionLabel
from app.staff.feedback import describe
from app.staff.rental_wizard import _StepBase, make_choice_button, make_status_box
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
        self._signals = signals

    def run(self) -> None:
        try:
            status = media_service.probe_media(
                self._make, self._model, self._year, self._category
            )
        except Exception:  # noqa: BLE001 - doubt means retry later, never a crash
            status = "unreachable"
        self._signals.done.emit(self._gen, status, self._make, self._model)


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


# -- add / edit form --------------------------------------------------------


class VehicleFormPanel(_StepBase):
    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(self, context, vehicle_id: int | None = None, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        self._vehicle_id = vehicle_id
        self._head(
            "Edit vehicle" if vehicle_id else "Add vehicle",
            "Plate, specs, rate, and paperwork dates.",
        )
        self._error = self._error_label()
        grid = QFormLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)

        self.plate_edit = QLineEdit(self.card)
        self.plate_edit.setPlaceholderText("ABC-123 (max 7 chars)")
        grid.addRow(_field_label("Plate", self.card), self.plate_edit)

        self.make_edit = QLineEdit(self.card)
        grid.addRow(_field_label("Brand", self.card), self.make_edit)
        self.model_edit = QLineEdit(self.card)
        grid.addRow(_field_label("Model", self.card), self.model_edit)

        self.year_edit = QSpinBox(self.card)
        self.year_edit.setRange(1900, date.today().year + 2)
        self.year_edit.setValue(date.today().year)
        grid.addRow(_field_label("Year", self.card), self.year_edit)

        self.color_edit = QLineEdit(self.card)
        self.color_edit.setPlaceholderText("Optional")
        grid.addRow(_field_label("Color", self.card), self.color_edit)

        self.category_combo = QComboBox(self.card)
        self.category_combo.addItems(["Car", "Motorcycle"])
        grid.addRow(_field_label("Category", self.card), self.category_combo)

        self.trans_combo = QComboBox(self.card)
        self.trans_combo.addItems(list(TRANSMISSIONS))
        grid.addRow(_field_label("Transmission", self.card), self.trans_combo)

        self.fuel_combo = QComboBox(self.card)
        self.fuel_combo.addItems(list(FUEL_TYPES))
        grid.addRow(_field_label("Fuel type", self.card), self.fuel_combo)

        self.seats_edit = QSpinBox(self.card)
        self.seats_edit.setRange(0, 60)
        self.seats_edit.setValue(5)
        grid.addRow(_field_label("Seats (0 = unset)", self.card), self.seats_edit)

        self.class_combo = QComboBox(self.card)
        self.class_combo.addItem("Unset", None)
        for cls in VEHICLE_CLASSES:
            self.class_combo.addItem(VEHICLE_CLASS_LABELS[cls], cls)
        grid.addRow(_field_label("Size class", self.card), self.class_combo)

        self.cc_edit = QSpinBox(self.card)
        self.cc_edit.setRange(0, 2000)
        grid.addRow(_field_label("Engine CC (0 = unset)", self.card), self.cc_edit)

        self.rate_edit = QLineEdit(self.card)
        self.rate_edit.setPlaceholderText("0.00")
        grid.addRow(_field_label("Daily rate (PHP)", self.card), self.rate_edit)

        self.mileage_edit = QSpinBox(self.card)
        self.mileage_edit.setRange(0, 9_999_999)
        grid.addRow(_field_label("Odometer (km)", self.card), self.mileage_edit)

        self.reg_edit = QDateEdit(self.card)
        self.reg_edit.setCalendarPopup(True)
        self.reg_edit.setDisplayFormat("dd MMM yyyy")
        self.reg_edit.setDate(QDate.currentDate().addYears(1))
        self.reg_set = QCheckBox("Set", self.card)
        reg_row = QHBoxLayout()
        reg_row.setContentsMargins(0, 0, 0, 0)
        reg_row.addWidget(self.reg_edit, 1)
        reg_row.addWidget(self.reg_set)
        grid.addRow(_field_label("Registration expiry", self.card), reg_row)

        self.ins_edit = QDateEdit(self.card)
        self.ins_edit.setCalendarPopup(True)
        self.ins_edit.setDisplayFormat("dd MMM yyyy")
        self.ins_edit.setDate(QDate.currentDate().addYears(1))
        self.ins_set = QCheckBox("Set", self.card)
        ins_row = QHBoxLayout()
        ins_row.setContentsMargins(0, 0, 0, 0)
        ins_row.addWidget(self.ins_edit, 1)
        ins_row.addWidget(self.ins_set)
        grid.addRow(_field_label("Insurance expiry", self.card), ins_row)

        self._body.addLayout(grid)
        self._media_hint = Caption("", parent=self.card, colour=theme.WARN)
        self._media_hint.setWordWrap(True)
        self._body.addWidget(self._media_hint)
        back, go = self._buttons("Save vehicle")
        back.clicked.connect(self.cancelled.emit)
        go.clicked.connect(self._on_go)
        self._go = go

        # Spelling probe: debounced, off-thread, advisory only -- a miss
        # never blocks the save (the API may simply be down).
        self._probe_pool = QThreadPool(self)
        self._probe_pool.setMaxThreadCount(1)
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

        if vehicle_id is not None:
            self._prefill()

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
        self._signals = signals
        self._force = force

    def run(self) -> None:
        try:
            from app.models import Vehicle_Media

            with self._context.session() as session:
                vehicle = session.get(Vehicle, self._vehicle_id)
                if vehicle is None:
                    self._signals.done.emit([])
                    return
                if self._force:
                    session.query(Vehicle_Media).filter_by(
                        vehicle_id=self._vehicle_id
                    ).delete()
                rows = media_service.get_or_fetch_media(session, vehicle, include_3d=True)
                snapshot = [
                    {
                        "view_angle": r.view_angle,
                        "image_url": r.image_url,
                        "model_3d_url": media_service.playable_model_url(session, r),
                        "is_watermarked": r.is_watermarked,
                        "image_data": _download(r.image_url),
                    }
                    for r in rows
                ]
        except Exception as exc:  # noqa: BLE001 - forwarded to the GUI slot
            self._signals.failed.emit(exc)
        else:
            self._signals.done.emit(snapshot)


class FleetDetailsPanel(_StepBase):
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
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._media_signals = _MediaSignals(self)
        self._media_signals.done.connect(self._on_media_done)
        self._media_signals.failed.connect(self._on_media_failed)
        self._head("Vehicle", f"Vehicle #{vehicle_id}")
        self._error = self._error_label()
        self._load()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addStretch(1)
        self.edit_button = QPushButton("Edit", self.card)
        self.edit_button.setObjectName("secondaryButton")
        self.edit_button.clicked.connect(lambda: self.edit_requested.emit(self._vehicle_id))
        self.close_button = QPushButton("Close", self.card)
        self.close_button.setObjectName("primaryButton")
        self.close_button.clicked.connect(self.closed.emit)
        row.addWidget(self.edit_button)
        row.addWidget(self.close_button)
        self._body.addLayout(row)

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
                    ("Class", vehicle.vehicle_class or DASH),
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
        from app.staff.cards import SectionLabel

        self._body.addWidget(SectionLabel(title, parent=self.card))
        for line in lines:
            self._body.addWidget(Caption(line, parent=self.card))

    # -- layout ---------------------------------------------------------

    def _load(self) -> None:
        from app.staff.cards import MoneyNumber, SectionLabel

        data = self._read()
        if data is None:
            self._section("Missing", ["That vehicle no longer exists."])
            self.edit_button.setEnabled(False)
            return
        self._data = data
        self._media_rows = list(data["media"])

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        head.addWidget(SectionLabel(data["name"], parent=self.card), 1)
        head.addWidget(
            make_status_box(
                data["status"].title(),
                theme.STATUS_COLOURS.get(data["status"], theme.MUTED),
                self.card,
            )
        )
        self._body.addLayout(head)
        self._body.addWidget(Caption(data["subtitle"], parent=self.card))
        price = MoneyNumber(parent=self.card)
        price.set_colours(dim=theme.MUTED, bright=theme.TEXT)
        price.set_amount(data["rate"] or 0)
        price_row = QHBoxLayout()
        price_row.setContentsMargins(0, 0, 0, 0)
        price_row.addWidget(price)
        price_row.addWidget(Caption("/ day", parent=self.card))
        price_row.addStretch(1)
        self._body.addLayout(price_row)

        self._body.addWidget(SectionLabel("Gallery", parent=self.card))
        self._hero = QLabel(self.card)
        self._hero.setFixedHeight(220)
        self._hero.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hero.setStyleSheet("QLabel { background: #FFFFFF; }")
        self._body.addWidget(self._hero)
        self._strip = QHBoxLayout()
        self._strip.setContentsMargins(0, 0, 0, 0)
        self._strip.setSpacing(8)
        strip_host = QWidget(self.card)
        strip_host.setObjectName("fleetGalleryStrip")
        strip_host.setLayout(self._strip)
        self._body.addWidget(strip_host)
        self._fill_gallery()

        self._body.addWidget(SectionLabel("3D preview", parent=self.card))
        self._viewer_host = QVBoxLayout()
        self._viewer_host.setContentsMargins(0, 0, 0, 0)
        self._viewer_host.setSpacing(8)
        viewer_box = QWidget(self.card)
        viewer_box.setObjectName("fleetViewerHost")
        viewer_box.setLayout(self._viewer_host)
        self._body.addWidget(viewer_box)
        self._fill_viewer()

        refresh_row = QHBoxLayout()
        refresh_row.setContentsMargins(0, 0, 0, 0)
        refresh_row.addStretch(1)
        self.refresh_button = QPushButton("Refresh photos + 3D", self.card)
        self.refresh_button.setObjectName("secondaryButton")
        self.refresh_button.clicked.connect(self._on_refresh_media)
        refresh_row.addWidget(self.refresh_button)
        self._body.addLayout(refresh_row)

        self._body.addWidget(SectionLabel("Specifications", parent=self.card))
        for key, value in data["specs"]:
            self._body.addWidget(Caption(f"{key}: {value}", parent=self.card))

        actives = [b for b in data["bookings"] if b["status"] in ("pending", "confirmed", "ongoing")]
        current = actives[0] if actives else None
        if current:
            self._body.addWidget(
                make_status_box(
                    f"Out now: #{current['ref']} · {current['customer']} · "
                    f"{current['start']:%d %b} → {current['end']:%d %b}",
                    theme.WARN,
                    self.card,
                )
            )
        upcoming = [b for b in actives[1:2]]
        for b in upcoming:
            self._body.addWidget(
                Caption(
                    f"Next: #{b['ref']} · {b['customer']} · {b['start']:%d %b} → {b['end']:%d %b}",
                    parent=self.card,
                )
            )
        history = data["history"]
        self._section(
            "Booking history",
            [
                f"#{b['ref']} · {b['customer']} · {b['start']:%d %b %Y} → {b['end']:%d %b %Y} · {b['status']}"
                for b in history
            ]
            or ["No past rentals."],
        )

        self._section(
            "Inspections",
            [
                f"#{r['ref']} {r['type']} · {r['mileage']:,} km · fuel {r['fuel']} · {r['damage']}"
                if r["mileage"] is not None
                else f"#{r['ref']} {r['type']} · fuel {r['fuel']} · {r['damage']}"
                for r in data["inspections"]
            ]
            or ["No inspection records."],
        )

        cost_lines = [
            f"{r['start']:%d %b %Y} → {r['end']:%d %b %Y} · {r['description']} · {r['status']}"
            for r in data["maintenance"]
        ] or ["No maintenance records."]
        self._section("Maintenance", cost_lines)
        self._stats_caption = Caption("", parent=self.card)
        self._body.addWidget(self._stats_caption)

        self._body.addWidget(SectionLabel("Utilization", parent=self.card))
        switch = QHBoxLayout()
        switch.setContentsMargins(0, 0, 0, 0)
        switch.setSpacing(8)
        self._window_group = QButtonGroup(self)
        self._window_buttons: dict[int, QPushButton] = {}
        for days in STAT_WINDOWS:
            button = make_choice_button(f"{days}d", self.card)
            button.setCheckable(True)
            button.setChecked(days == self._window_days)
            button.clicked.connect(lambda _c=False, _d=days: self._on_window(_d))
            self._window_group.addButton(button)
            switch.addWidget(button, 1)
            self._window_buttons[days] = button
        switch.addStretch(1)
        self._body.addLayout(switch)
        self._util_caption = Caption("", parent=self.card)
        self._body.addWidget(self._util_caption)
        self._refresh_stats_text()
        # Fetch on open: the panel calls the CarImages API itself instead
        # of waiting for Refresh. Freshness-aware, usually a cache hit.
        self._start_silent_refresh()

    def _refresh_stats_text(self) -> None:
        stats = self._data["stats"]
        self._stats_caption.setText(
            f"Rented {stats['times_rented']}× · revenue {pesos(stats['total_revenue'])} · "
            f"maintenance {pesos(stats['maintenance_cost'])}."
        )
        if stats["utilization"] is None:
            self._util_caption.setText(
                f"Last {stats['window_days']}d: unavailable all window (shop/retired)."
            )
        else:
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
        for row in self._media_rows:
            url = row.get("image_url") or ""
            data = row.get("image_data") or cache_bytes
            if url and data:
                self._bytes_by_url[url] = data
            if not hero_shown and data:
                hero = _pixmap(data, 600, 220)
                if hero is not None:
                    self._hero.setPixmap(hero)
                    hero_shown = True
            thumb = _pixmap(data, 96, 72)
            label = QLabel(self.card)
            if thumb is not None:
                label.setPixmap(thumb)
            else:
                label.setText(row.get("view_angle") or "photo")
            label.setStyleSheet("QLabel { background: #FFFFFF; border: 1px solid #D8CCBB; }")
            label.setCursor(Qt.CursorShape.PointingHandCursor)
            label.mousePressEvent = lambda _e, _u=url: self._show_url(_u)
            self._strip.addWidget(label)
        self._strip.addStretch(1)
        if not hero_shown:
            if cache_bytes:
                hero = _pixmap(cache_bytes, 600, 220)
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
        pix = _pixmap(data, 600, 220)
        if pix is not None:
            self._hero.setPixmap(pix)
        else:
            self._hero.setText("Photo failed to load.")

    def _model_url(self) -> str:
        for row in self._media_rows:
            if row.get("model_3d_url"):
                return row["model_3d_url"]
        return ""

    def _fill_viewer(self) -> None:
        while self._viewer_host.count():
            item = self._viewer_host.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        url = self._model_url()
        if url and QWebEngineView is not None:
            view = QWebEngineView(self.card)
            view.setFixedHeight(VIEWER_H)
            safe = url.replace("\\", "\\\\").replace('"', "%22")
            view.setHtml(_THREE_HTML.replace("__URL__", safe), QUrl("https://localhost/"))
            self._viewer_host.addWidget(view)
            link_row = QHBoxLayout()
            link_row.setContentsMargins(0, 0, 0, 0)
            link_row.addStretch(1)
            open_button = QPushButton("Open 3D in browser", self.card)
            open_button.setObjectName("secondaryButton")
            open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
            link_row.addWidget(open_button)
            self._viewer_host.addLayout(link_row)
        else:
            reason = (
                "No 3D model cached for this vehicle yet."
                if not url
                else "3D viewer unavailable in this build."
            )
            self._viewer_host.addWidget(Caption(reason, parent=self.card))
            if url:
                open_button = QPushButton("Open 3D in browser", self.card)
                open_button.setObjectName("secondaryButton")
                open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
                wrap = QHBoxLayout()
                wrap.setContentsMargins(0, 0, 0, 0)
                wrap.addStretch(1)
                wrap.addWidget(open_button)
                self._viewer_host.addLayout(wrap)

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
