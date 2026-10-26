"""Check-details view for the staff New Rental page.

The counter modal is one warm sheet: a header with the vehicle identity, a
photo/3D gallery on the left, specifications and availability on the right,
and a footer that keeps Close and Rent this vehicle in reach. The whole card
is fixed to the window height; only the right-hand column scrolls, exactly
like the reference.

The content lives in `VehicleDetailsPanel` so it can be shown two ways:

- `VehicleDetailsDialog`: thin modal-window wrapper (kept for compat).
- `VehicleDetailsOverlay`: the real presentation -- dim + card embedded
  inside the staff window itself. No new OS window is ever opened.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import (
    QDate,
    QEvent,
    QObject,
    QPointF,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QDateEdit,
    QDialog,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.models import Booking, Maintenance_Record, Vehicle
from app.services import booking_service, image_cache, vehicle_photos
from app.services.errors import ConflictError
from app.staff import icons, metrics, model_preview, theme
from app.staff.cards import Caption, Card, MoneyNumber, Pill, SectionLabel
from app.staff.rental_ui import (
    MODAL_MUTED,
    MODAL_RADIUS,
    SpecTile,
    StatusChip,
)
from app.staff.workers import emit_safely, media_pool
from app.widgets.spinner import Spinner

#: Hero height inside the details gallery.
PHOTO_H = 300

#: Placeholder text for columns the row leaves NULL.
DASH = "\u2014"

#: Booking states that keep a vehicle from being free.
ACTIVE_BOOKING_STATES = ("pending", "confirmed", "ongoing")

#: Overlay card width; the dim fills the whole window around it.
OVERLAY_CARD_W = metrics.DETAILS_CARD_W

#: Cache variants a missing `front` photo may borrow. Side views never fall
#: back to the three-quarter shot: showing the wrong side of a car is worse
#: than showing no photo at all.
FRONT_FALLBACK_VIEWS = (vehicle_photos.DEFAULT_VIEW, "default", "")

#: Frosted-glass backdrop: blur radius applied to the frozen page snapshot.
BACKDROP_BLUR_RADIUS = 12

#: Dim baked over the blurred snapshot (same ink as DimOverlay).
BACKDROP_DIM = 110

#: Snapshot downscale before blurring: faster and smoother (classic frosted
#: trick). The small image is blurred cheaply, then upscaled.
BACKDROP_DOWNSCALE = 4

#: Debounce for re-rendering the backdrop on window resizes while open.
BACKDROP_RERENDER_MS = 120

#: Left gallery width; the right column gets the rest of the 900px sheet.
GALLERY_WIDTH = 380

#: Gallery tabs, in reading order. "3d" launches the external preview.
GALLERY_VIEWS = vehicle_photos.VIEWS
GALLERY_LABELS = {
    "front": "Front",
    "left": "Left Side",
    "right": "Right Side",
    "3d": "3D",
}

#: Legacy three.js document kept for import compatibility. The staff gallery no
#: longer renders it: 3D opens in the separate model-viewer process.
MODAL_THREE_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html,body{margin:0;height:100%;background:#EFE6D5;overflow:hidden;font-family:Inter,sans-serif}
  #note{position:absolute;inset:auto 8px 8px 8px;font:11px sans-serif;color:#6B5E4F;text-align:center}
</style>
<script type="importmap">{"imports":{"three":"https://unpkg.com/three@0.160.0/build/three.module.js","three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}</script>
</head><body><div id="note">Loading 3D\u2026</div>
<script type="module">
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
const note = document.getElementById('note');
const scene = new THREE.Scene();
const cam = new THREE.PerspectiveCamera(42, innerWidth/innerHeight, 0.1, 100);
cam.position.set(3.2, 1.8, 4.2);
let renderer;
try {
  renderer = new THREE.WebGLRenderer({antialias:true, alpha:true});
} catch (err) {
  note.textContent = '3D preview is unavailable on this display.';
  throw err;
}
renderer.setClearColor(0xEFE6D5, 1);
renderer.setSize(innerWidth, innerHeight);
renderer.domElement.style.cursor = 'grab';
document.body.appendChild(renderer.domElement);
const ctl = new OrbitControls(cam, renderer.domElement);
ctl.enableDamping = true;
ctl.dampingFactor = 0.07;
ctl.autoRotate = true;
ctl.autoRotateSpeed = 1.25;
ctl.minDistance = 0.4;
window.addEventListener('error', () => {
  note.textContent = '3D preview is unavailable on this display.';
});
scene.add(new THREE.HemisphereLight(0xffffff, 0x8A8175, 1.8));
const sun = new THREE.DirectionalLight(0xffffff, 2.2);
sun.position.set(4, 6, 3); scene.add(sun);
new GLTFLoader().load("__URL__",
  g => {scene.add(g.scene);
    const b = new THREE.Box3().setFromObject(g.scene);
    const sphere = b.getBoundingSphere(new THREE.Sphere());
    ctl.target.copy(sphere.center);
    const dir = cam.position.clone().sub(sphere.center).normalize();
    cam.position.copy(sphere.center).addScaledVector(dir, sphere.radius * 2.35);
    ctl.update();
    ctl.saveState();
    note.textContent = '';},
  undefined,
  e => {note.textContent = '3D preview failed to load.';});
window.__setAutoRotate = on => { ctl.autoRotate = !!on; };
window.__resetView = () => { ctl.reset(); };
(function tick(){requestAnimationFrame(tick); ctl.update();
  renderer.render(scene, cam);})();
addEventListener('resize', () => {cam.aspect = innerWidth/innerHeight;
  cam.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight);});
</script></body></html>"""


def _decode(data: bytes) -> QImage | None:
    image = QImage()
    if image.loadFromData(data):
        return image
    return None


def _fit_image(image: QImage, width: int, height: int) -> QPixmap:
    scaled = image.scaled(
        max(1, width),
        max(1, height),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return QPixmap.fromImage(scaled)


class _ScaledImage(QLabel):
    """Photo label that keeps the bytes and rescales on every resize."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_image(self, image: QImage | None) -> None:
        self._image = image
        self._paint()

    def clear_image(self) -> None:
        self._image = None
        self.setPixmap(QPixmap())
        self.setText("")

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._paint()

    def _paint(self) -> None:
        if self._image is None:
            return
        width = max(1, self.width() - 8)
        height = max(1, self.height() - 8)
        scaled = self._image.scaled(
            width,
            height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(QPixmap.fromImage(scaled))


def _cube_pixmap(size: int = 26) -> QPixmap:
    """A small wireframe cube for the 3D tab, drawn in ink."""
    scale = 2
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = painter.pen()
        pen.setColor(QColor("#171717"))
        pen.setWidthF(1.5 * scale)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        s = float(size * scale)
        top = [
            QPointF(s * 0.50, s * 0.14),
            QPointF(s * 0.82, s * 0.34),
            QPointF(s * 0.50, s * 0.54),
            QPointF(s * 0.18, s * 0.34),
        ]
        painter.drawPolygon(top)
        painter.drawLine(top[3], QPointF(s * 0.18, s * 0.72))
        painter.drawLine(top[1], QPointF(s * 0.82, s * 0.72))
        painter.drawLine(top[2], QPointF(s * 0.50, s * 0.90))
        painter.drawLine(QPointF(s * 0.18, s * 0.72), QPointF(s * 0.50, s * 0.90))
        painter.drawLine(QPointF(s * 0.82, s * 0.72), QPointF(s * 0.50, s * 0.90))
    finally:
        painter.end()
    return pm.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _status_box(status: str, parent: QWidget) -> Pill:
    """Status badge as a box (radius 8), never a stadium."""
    return Pill(
        theme.status_label(status),
        parent=parent,
        colour=theme.TEXT,
        background=theme.SURFACE,
        dot_colour=theme.STATUS_COLOURS.get(status, theme.MUTED),
        radius=8,
    )


# -- media workers ------------------------------------------------------------


class _MediaSignals(QObject):
    photo_ready = Signal(str, bytes)
    photo_failed = Signal(str)
    model_ready = Signal(str)
    model_failed = Signal()


class _PhotoJob(QRunnable):
    def __init__(self, ref, view: str, signals: _MediaSignals) -> None:
        super().__init__()
        self._ref = ref
        self._view = view
        self._signals = signals

    @Slot()
    def run(self) -> None:
        try:
            data = vehicle_photos.photo_bytes(self._ref, self._view)
        except Exception:  # noqa: BLE001 - a photo never breaks the modal
            data = None
        if data:
            emit_safely(self._signals.photo_ready, self._view, data)
        else:
            emit_safely(self._signals.photo_failed, self._view)


class _ModelJob(QRunnable):
    def __init__(self, ref, signals: _MediaSignals) -> None:
        super().__init__()
        self._ref = ref
        self._signals = signals

    @Slot()
    def run(self) -> None:
        try:
            path = vehicle_photos.model_glb_path(self._ref)
        except Exception:  # noqa: BLE001
            path = None
        if path is None:
            emit_safely(self._signals.model_failed)
        else:
            emit_safely(self._signals.model_ready, str(path))


class _AvailabilitySignals(QObject):
    finished = Signal(int, object)


class _AvailabilityJob(QRunnable):
    """The booking conflict query, never on the GUI thread."""

    def __init__(
        self,
        context,
        vehicle_id: int,
        start,
        end,
        generation: int,
        signals: _AvailabilitySignals,
    ) -> None:
        super().__init__()
        self._context = context
        self._vehicle_id = vehicle_id
        self._start = start
        self._end = end
        self._generation = generation
        self._signals = signals

    @Slot()
    def run(self) -> None:
        try:
            with self._context.reading() as session:
                vehicle = session.get(Vehicle, self._vehicle_id)
                if vehicle is None:
                    raise ConflictError("That vehicle no longer exists.")
                booking_service.assert_available(
                    session, vehicle, self._start, self._end
                )
                rate = vehicle.daily_rate or 0
        except ConflictError as exc:
            emit_safely(
                self._signals.finished,
                self._generation,
                {"ok": False, "message": str(exc)},
            )
        except Exception as exc:  # noqa: BLE001 - surfaced in the GUI
            emit_safely(
                self._signals.finished,
                self._generation,
                {"ok": None, "error": exc},
            )
        else:
            emit_safely(
                self._signals.finished,
                self._generation,
                {"ok": True, "rate": rate},
            )


# -- gallery ------------------------------------------------------------------


class Gallery(QWidget):
    """Photo views plus the ready-card hand-off to the external 3D preview.

    Only the active photo is fetched over the network; thumbnails use the
    shared on-disk cache. Nothing here creates a QWebEngineView: the 3D tab
    shows a ready card and launches the separate preview process.
    """

    def __init__(
        self,
        ref,
        make: str,
        model: str,
        year: int,
        *,
        views: tuple[str, ...] | None = None,
        autoload: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("modalGallery")
        self.views = (
            tuple(views)
            if views is not None
            else vehicle_photos.views_for(ref)
        )
        self.view_buttons: dict[str, QToolButton] = {}
        self._ref = ref
        self._make = make
        self._model = model
        self._year = year
        self._current = "front"
        self._photos: dict[str, QImage] = {}
        self._model_path: str | None = None
        self._model_requested = False
        self._model_loading = False
        self._loading_views: set[str] = set()
        self._autoload = autoload
        self._signals = _MediaSignals(self)
        self._signals.photo_ready.connect(self._on_photo_ready)
        self._signals.photo_failed.connect(self._on_photo_failed)
        self._signals.model_ready.connect(self._on_model_ready)
        self._signals.model_failed.connect(self._on_model_failed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        self.hero = QFrame(self)
        self.hero.setObjectName("galleryHero")
        self.hero.setMinimumHeight(PHOTO_H)
        self.hero.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        hero_layout = QVBoxLayout(self.hero)
        hero_layout.setContentsMargins(10, 10, 10, 8)
        hero_layout.setSpacing(6)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        self.badge = QLabel("Photo", self.hero)
        self.badge.setObjectName("galleryBadge")
        controls.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        controls.addStretch(1)
        # Compatibility attributes only: the external preview owns its own
        # controls, so these must never be shown in the staff gallery.
        self.auto_button = QPushButton("Auto-rotate", self.hero)
        self.auto_button.setObjectName("viewerControl")
        self.auto_button.hide()
        self.reset_button = QPushButton("Reset", self.hero)
        self.reset_button.setObjectName("viewerControlLight")
        self.reset_button.hide()
        controls.addWidget(self.auto_button, 0, Qt.AlignmentFlag.AlignTop)
        controls.addWidget(self.reset_button, 0, Qt.AlignmentFlag.AlignTop)
        self.badge.hide()
        hero_layout.addLayout(controls)

        self._stage = QWidget(self.hero)
        self._stage.setObjectName("galleryStage")
        self._stack = QStackedLayout(self._stage)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self.image_label = _ScaledImage(self._stage)
        self.image_label.setObjectName("galleryImage")
        self.image_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        self._stack.addWidget(self.image_label)

        self.model_card = QFrame(self._stage)
        self.model_card.setObjectName("gallery3DCard")
        self.model_card.setStyleSheet(
            "QFrame#gallery3DCard { background: #F7F1E6;"
            " border: 1px solid #E6DECD; border-radius: 12px; }"
            " QFrame#gallery3DCard QLabel { background: transparent; }"
        )
        card_layout = QVBoxLayout(self.model_card)
        card_layout.setContentsMargins(24, 22, 24, 22)
        card_layout.setSpacing(8)
        card_layout.addStretch(1)
        cube = QLabel(self.model_card)
        cube.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cube.setPixmap(_cube_pixmap(48))
        card_layout.addWidget(cube)
        ready = QLabel("3D model ready", self.model_card)
        ready.setObjectName("gallery3DTitle")
        ready.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = ready.font()
        font.setPixelSize(16)
        font.setWeight(QFont.Weight.DemiBold)
        ready.setFont(font)
        card_layout.addWidget(ready)
        description = Caption(
            "Opens in a separate preview window, so a display or 3D engine "
            "problem can never close the rental desk.",
            parent=self.model_card,
            colour=MODAL_MUTED,
            size=11,
        )
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(description)
        self.model_note = QLabel("", self.model_card)
        self.model_note.setObjectName("galleryHint")
        self.model_note.setWordWrap(True)
        self.model_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.model_note)
        self.open_3d_button = QPushButton("Open 3D preview", self.model_card)
        self.open_3d_button.setObjectName("secondaryButton")
        self.open_3d_button.setMinimumHeight(36)
        self.open_3d_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_3d_button.clicked.connect(self._open_preview)
        card_layout.addWidget(self.open_3d_button)
        card_layout.addStretch(1)
        self._stack.addWidget(self.model_card)
        self._stack.setCurrentWidget(self.image_label)

        self.spinner = Spinner(self._stage, size=26)
        self.spinner.raise_()

        hero_layout.addWidget(self._stage, 1)

        self.hint = QLabel("Drag to rotate \u00b7 scroll to zoom", self.hero)
        self.hint.setObjectName("galleryHint")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.hide()
        hero_layout.addWidget(self.hint, 0)
        outer.addWidget(self.hero)

        tabs = QHBoxLayout()
        tabs.setContentsMargins(0, 0, 0, 0)
        tabs.setSpacing(6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for view in self.views:
            button = QToolButton(self)
            button.setObjectName("galleryTab")
            button.setText(
                GALLERY_LABELS.get(
                    view, vehicle_photos.VIEW_LABELS.get(view, view)
                )
            )
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(46, 26))
            button.setFixedHeight(66)
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if view == "3d":
                button.setIcon(_icon_from_pixmap(_cube_pixmap(26)))
                button.setIconSize(QSize(26, 26))
            else:
                thumb = self._thumb_pixmap(view)
                if thumb is not None:
                    button.setIcon(_icon_from_pixmap(thumb, 46, 26))
                else:
                    button.setIcon(icons.icon("vehicle", MODAL_MUTED, 22))
            button.clicked.connect(lambda _checked=False, v=view: self.set_view(v))
            self._group.addButton(button)
            self.view_buttons[view] = button
            tabs.addWidget(button, 1)
        outer.addLayout(tabs)

        self.view_buttons["front"].setChecked(True)
        # The list card has usually already cached the three-quarter shot.
        # Otherwise fetch it in the background once, unless a test run has no
        # business reaching the network.
        if not self._load_cached("front"):
            if self._autoload and "pytest" not in sys.modules:
                self.image_label.setText("Loading photo\u2026")
                self._start_photo("front")
            else:
                self.image_label.setText("Select a view to load its photo")

    # -- tabs ----------------------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._place_spinner()

    def _place_spinner(self) -> None:
        spinner = getattr(self, "spinner", None)
        if spinner is None:
            return
        spinner.move(
            max(0, (self._stage.width() - spinner.width()) // 2),
            max(0, (self._stage.height() - spinner.height()) // 2),
        )
        spinner.raise_()

    def _thumb_pixmap(self, view: str) -> QPixmap | None:
        data = self._cached_bytes(view)
        if data is None:
            return None
        image = _decode(data)
        if image is None:
            return None
        return _fit_image(image, 46, 26)

    def _cached_bytes(self, view: str) -> bytes | None:
        """Exact view first; only `front` may borrow the three-quarter shot."""
        try:
            cached = image_cache.load(self._make, self._model, self._year, view)
            if cached is not None and getattr(cached, "data", None):
                return cached.data
            if view != "front":
                return None
            for variant in FRONT_FALLBACK_VIEWS:
                cached = image_cache.load(
                    self._make, self._model, self._year, variant
                )
                if cached is not None and getattr(cached, "data", None):
                    return cached.data
            return None
        except Exception:  # noqa: BLE001
            return None

    def _load_cached(self, view: str) -> bool:
        data = self._cached_bytes(view)
        if data is None:
            return False
        image = _decode(data)
        if image is None:
            return False
        self._photos[view] = image
        if view == self._current and view != "3d":
            self.image_label.set_image(image)
        return True

    def _is_loading(self) -> bool:
        if self._current == "3d":
            return self._model_loading
        return self._current in self._loading_views

    def _sync_spinner(self) -> None:
        self.spinner.set_running(self._is_loading())
        if self.spinner.is_running():
            self._place_spinner()

    def _start_photo(self, view: str) -> None:
        self._loading_views.add(view)
        self._sync_spinner()
        job = _PhotoJob(self._ref, view, self._signals)
        media_pool().start(job)

    def set_view(self, view: str) -> None:
        if view not in self.view_buttons:
            return
        self._current = view
        self.view_buttons[view].setChecked(True)
        if view == "3d":
            self._show_3d()
            return
        self.badge.hide()
        self.auto_button.hide()
        self.reset_button.hide()
        self.hint.hide()
        self._stack.setCurrentWidget(self.image_label)
        self._show_photo(view)
        self._sync_spinner()

    def _show_photo(self, view: str) -> None:
        image = self._photos.get(view)
        if image is not None:
            self.image_label.set_image(image)
            self.image_label.setText("")
            self._sync_spinner()
            return
        if self._load_cached(view):
            self.image_label.setText("")
            self._sync_spinner()
            return
        if self._current == view:
            self.image_label.clear_image()
            self.image_label.setText("Loading photo\u2026")
        self._start_photo(view)

    @Slot(str, bytes)
    def _on_photo_ready(self, view: str, data: bytes) -> None:
        self._loading_views.discard(view)
        image = _decode(data)
        if image is None:
            if self._current == view:
                self.image_label.clear_image()
                self.image_label.setText("No photo available for this vehicle")
            self._sync_spinner()
            return
        self._photos[view] = image
        if view != "3d":
            button = self.view_buttons.get(view)
            if button is not None:
                thumb = _fit_image(image, 46, 26)
                button.setIcon(_icon_from_pixmap(thumb, 46, 26))
        if self._current == view:
            self.image_label.set_image(image)
            self.image_label.setText("")
        self._sync_spinner()

    @Slot(str)
    def _on_photo_failed(self, view: str) -> None:
        self._loading_views.discard(view)
        if self._current == view:
            self.image_label.clear_image()
            self.image_label.setText("No photo available for this vehicle")
        self._sync_spinner()

    # -- 3D ------------------------------------------------------------

    def _preview_label(self) -> str:
        parts = (self._make, self._model, str(self._year or ""))
        return " ".join(part for part in parts if part).strip()

    def _show_3d(self) -> None:
        self.badge.setText("3D preview")
        self.badge.show()
        self.auto_button.hide()
        self.reset_button.hide()
        self.hint.hide()
        if self._model_path:
            self.model_note.setText("")
            self.open_3d_button.setEnabled(True)
            self._stack.setCurrentWidget(self.model_card)
            self._sync_spinner()
            return
        self._stack.setCurrentWidget(self.image_label)
        self.image_label.clear_image()
        self.image_label.setText("Loading the 3D model\u2026")
        if not self._model_requested:
            self._model_requested = True
            self._model_loading = True
            media_pool().start(_ModelJob(self._ref, self._signals))
        self._sync_spinner()

    @Slot(str)
    def _on_model_ready(self, path: str) -> None:
        self._model_path = path
        self._model_loading = False
        if self._current != "3d":
            # The user moved on while the download ran: never steal their tab.
            self._sync_spinner()
            return
        self.model_note.setText("")
        self.open_3d_button.setEnabled(True)
        self._stack.setCurrentWidget(self.model_card)
        self._sync_spinner()

    @Slot()
    def _on_model_failed(self) -> None:
        self._model_requested = False
        self._model_loading = False
        if self._current != "3d":
            self._sync_spinner()
            return
        self.image_label.clear_image()
        self.image_label.setText(
            "No 3D model for this vehicle yet.\n"
            "Use the photo views to inspect the body."
        )
        self._sync_spinner()

    def _open_preview(self) -> None:
        if not self._model_path:
            self.model_note.setText("No 3D model is available for this vehicle.")
            return
        if model_preview.launch_model_preview(
            Path(self._model_path), self._preview_label()
        ):
            self.model_note.setText("The 3D preview opened in its own window.")
        else:
            self.model_note.setText(
                "Could not open the 3D preview. Try again in a moment."
            )


def _icon_from_pixmap(
    pixmap: QPixmap, width: int | None = None, height: int | None = None
) -> QIcon:
    if width is not None and height is not None:
        pixmap = pixmap.scaled(
            width,
            height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return QIcon(pixmap)


class VehicleDetailsPanel(QWidget):
    """The check-details content: no window of its own.

    Emits `rent_requested` with the vehicle id when the clerk picks Rent this
    vehicle, and `close_requested` on Close. Hosts (dialog or overlay) decide
    what those mean.
    """

    rent_requested = Signal(int)
    close_requested = Signal()

    def __init__(
        self,
        context,
        vehicle_id: int,
        parent: QWidget | None = None,
        *,
        autoload: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("vehicleDetailsPanel")
        self._context = context
        self._vehicle_id = vehicle_id
        self._autoload = autoload
        #: Last clean availability check, as (start, end) dates; None until checked.
        self.checked_window: tuple[QDate, QDate] | None = None
        #: Runtime checker state: a generation token drops stale worker landings.
        self._check_generation = 0
        self._checking = False
        self._check_failed = False
        self._pending_window: tuple[QDate, QDate] | None = None
        self._check_signals = _AvailabilitySignals(self)
        self._check_signals.finished.connect(self._on_check_finished)

        self.spec_values: dict[str, str] = {}
        self.spec_tiles: dict[str, SpecTile] = {}
        self.missing_label = QLabel("", self)
        self.missing_label.setObjectName(theme.OBJ_ERROR)
        self.missing_label.setWordWrap(True)
        self.missing_label.setVisible(False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.card = Card(self, radius=MODAL_RADIUS)
        self.card.setObjectName("vehicleDetailsCard")
        self.card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.card.setMinimumHeight(560)
        outer.addWidget(self.card, 1)

        self._build()

    # -- data ---------------------------------------------------------------

    def _read_vehicle(self) -> dict | None:
        with self._context.reading() as session:
            vehicle = session.get(Vehicle, self._vehicle_id)
            if vehicle is None:
                return None
            category = vehicle.category.category_name if vehicle.category else DASH
            bookings = (
                session.query(Booking)
                .filter(
                    Booking.vehicle_id == vehicle.vehicle_id,
                    Booking.status.in_(ACTIVE_BOOKING_STATES),
                )
                .order_by(Booking.start_date)
                .limit(4)
                .all()
            )
            booking_rows = [
                {
                    "ref": row.booking_id,
                    "customer": row.user.full_name if row.user else DASH,
                    "start": row.start_date,
                    "end": row.end_date,
                    "status": row.status,
                }
                for row in bookings
            ]
            maintenance = (
                session.query(Maintenance_Record)
                .filter(Maintenance_Record.vehicle_id == vehicle.vehicle_id)
                .order_by(Maintenance_Record.start_date.desc())
                .limit(3)
                .all()
            )
            maintenance_rows = [
                {
                    "start": row.start_date,
                    "end": row.end_date,
                    "description": row.description or DASH,
                    "status": row.status,
                }
                for row in maintenance
            ]
            return {
                "name": f"{vehicle.make} {vehicle.model}".strip(),
                "subtitle": (
                    f"{vehicle.year} \u00b7 {vehicle.plate_number} \u00b7 {category}"
                ),
                "rate": vehicle.daily_rate,
                "status": vehicle.status,
                "make": vehicle.make or "",
                "model": vehicle.model or "",
                "year": int(vehicle.year or 0),
                "plate": vehicle.plate_number or DASH,
                "ref": vehicle_photos.ref_for(vehicle),
                "specs": [
                    ("Plate", vehicle.plate_number or DASH),
                    ("Seats", str(vehicle.seats) if vehicle.seats is not None else DASH),
                    ("Transmission", vehicle.transmission or DASH),
                    ("Fuel", vehicle.fuel_type or DASH),
                    ("Body style", vehicle.body_style or DASH),
                    ("Class", vehicle.vehicle_class or DASH),
                    (
                        "Engine",
                        f"{vehicle.engine_cc} cc"
                        if vehicle.engine_cc is not None
                        else DASH,
                    ),
                    (
                        "Mileage",
                        f"{vehicle.mileage:,} km" if vehicle.mileage is not None else DASH,
                    ),
                    ("Category", category),
                ],
                "bookings": booking_rows,
                "maintenance": maintenance_rows,
            }

    # -- layout ---------------------------------------------------------------

    def _build(self) -> None:
        data = self._read_vehicle()
        if data is None:
            self._build_missing()
            return

        self._header(data)
        self._body(data)
        self._footer(data)

    def _build_missing(self) -> None:
        header = QWidget(self.card)
        lay = QVBoxLayout(header)
        lay.setContentsMargins(24, 22, 24, 16)
        lay.setSpacing(8)
        title = SectionLabel("Vehicle details", parent=header)
        title.setFontSize(22, QFont.Weight.Bold)
        lay.addWidget(title)
        lay.addWidget(self.missing_label)
        self.card.layout().addWidget(header)

        body = QWidget(self.card)
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(24, 0, 24, 24)
        self.missing_label.setText("That vehicle no longer exists.")
        self.missing_label.setVisible(True)
        body_lay.addWidget(self.missing_label)
        body_lay.addStretch(1)
        self.card.layout().addWidget(body, 1)

        self.close_button = QPushButton("Close", self.card)
        self.close_button.setObjectName("secondaryButton")
        self.close_button.clicked.connect(self.close_requested.emit)
        self.rent_button = QPushButton("Rent this vehicle", self.card)
        self.rent_button.setObjectName("primaryButton")
        self.rent_button.setEnabled(False)
        footer = self._footer_host()
        footer.layout().addStretch(1)
        footer.layout().addWidget(self.close_button)
        footer.layout().addWidget(self.rent_button)

    def _header(self, data: dict) -> None:
        header = QWidget(self.card)
        header.setObjectName("rentalModalBody")
        lay = QVBoxLayout(header)
        lay.setContentsMargins(24, 20, 24, 0)
        lay.setSpacing(4)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.name_label = SectionLabel(data["name"], parent=header)
        self.name_label.setFontSize(22, QFont.Weight.Bold)
        row.addWidget(self.name_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.plate_chip = QLabel(data["plate"], header)
        self.plate_chip.setObjectName("plateChip")
        self.plate_chip.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        row.addWidget(self.plate_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        row.addWidget(_status_box(data["status"], header), 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(row)
        self.subtitle_label = Caption(
            data["subtitle"], parent=header, colour=MODAL_MUTED, size=12
        )
        lay.addWidget(self.subtitle_label)
        lay.addSpacing(10)
        rule = QFrame(header)
        rule.setObjectName("modalRule")
        rule.setFixedHeight(1)
        lay.addWidget(rule)
        self.card.layout().addWidget(header)

    def _body(self, data: dict) -> None:
        body = QWidget(self.card)
        body.setObjectName("rentalModalBody")
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(24, 18, 24, 16)
        body_lay.setSpacing(20)

        self.gallery = Gallery(
            data["ref"],
            data["make"],
            data["model"],
            data["year"],
            autoload=self._autoload,
            parent=body,
        )
        self.gallery.setFixedWidth(GALLERY_WIDTH)
        self.gallery.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding
        )
        # Back-compat: older tests (and the outside-click regression) treat
        # the hero as the photo surface.
        self.photo = self.gallery.image_label
        body_lay.addWidget(self.gallery, 1)

        scroll = QScrollArea(body)
        scroll.setObjectName("modalColumnScroll")
        scroll.viewport().setObjectName("modalColumnViewport")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            "QScrollArea#modalColumnScroll { background: transparent; border: none; }"
            " QScrollArea#modalColumnScroll QWidget#modalColumnViewport {"
            " background: transparent; border: none; }"
        )
        self.column = QWidget(scroll)
        self.column.setObjectName("modalColumnViewport")
        column = QVBoxLayout(self.column)
        column.setContentsMargins(0, 0, 10, 10)
        column.setSpacing(14)

        price_row = QHBoxLayout()
        price_row.setContentsMargins(0, 0, 0, 0)
        price_row.setSpacing(5)
        self.price = MoneyNumber(
            parent=self.column,
            size=34,
            dim=MODAL_MUTED,
            bright="#171717",
            weight=QFont.Weight.DemiBold,
        )
        self.price.set_amount(data["rate"] or 0)
        price_row.addWidget(self.price)
        suffix = Caption(
            "/ day", parent=self.column, colour=MODAL_MUTED, size=13
        )
        suffix.setObjectName("detailPriceSuffix")
        price_row.addWidget(suffix, 0, Qt.AlignmentFlag.AlignBottom)
        price_row.addStretch(1)
        column.addLayout(price_row)

        column.addWidget(SectionLabel("Specifications", parent=self.column, size=13))
        specs = QGridLayout()
        specs.setContentsMargins(0, 0, 0, 0)
        specs.setHorizontalSpacing(7)
        specs.setVerticalSpacing(7)
        spec_map = dict(data["specs"])
        tile_keys = (
            "Seats",
            "Transmission",
            "Fuel",
            "Body style",
            "Mileage",
            "Engine",
        )
        for index, key in enumerate(tile_keys):
            value = spec_map.get(key, DASH)
            tile = SpecTile(key, value, self.column)
            self.spec_tiles[key] = tile
            specs.addWidget(tile, index // 3, index % 3)
        for col in range(3):
            specs.setColumnStretch(col, 1)
        column.addLayout(specs)

        column.addWidget(
            SectionLabel("Bookings and maintenance", parent=self.column, size=13)
        )
        rows = QWidget(self.column)
        rows.setObjectName("rentalModalBody")
        rows_lay = QVBoxLayout(rows)
        rows_lay.setContentsMargins(0, 0, 0, 0)
        rows_lay.setSpacing(0)
        self._availability_rows(rows_lay, data)
        column.addWidget(rows)

        self._build_checker(column)
        column.addStretch(1)
        scroll.setWidget(self.column)
        body_lay.addWidget(scroll, 1)
        self.card.layout().addWidget(body, 1)

        self.spec_values = {key: value for key, value in data["specs"]}

    def _availability_rows(self, layout: QVBoxLayout, data: dict) -> None:
        entries = []
        for row in data["bookings"]:
            entries.append(
                (
                    f"{row['start']:%d %b} \u2192 {row['end']:%d %b}",
                    f"{row['customer']} \u00b7 #{row['ref']}",
                    theme.status_label(row["status"]),
                    "ok" if row["status"] in ("confirmed", "ongoing") else "warn",
                )
            )
        for row in data["maintenance"]:
            end = f" \u2192 {row['end']:%d %b %Y}" if row["end"] else ""
            status = (
                "Maintenance done"
                if (row["status"] or "").lower() in ("completed", "done")
                else theme.status_label(row["status"])
            )
            entries.append(
                (
                    f"{row['start']:%d %b %Y}{end}",
                    row["description"],
                    status,
                    "neutral",
                )
            )
        if not entries:
            layout.addWidget(
                Caption("No bookings or maintenance on record.", parent=self.column)
            )
            return
        for index, (dates, meta, status, tone) in enumerate(entries):
            frame = QFrame(self.column)
            frame.setObjectName("availabilityRow")
            frame.setProperty("divider", index > 0)
            row = QHBoxLayout(frame)
            row.setContentsMargins(0, 7 if index else 0, 0, 7)
            row.setSpacing(10)
            text = QVBoxLayout()
            text.setContentsMargins(0, 4, 0, 4)
            text.setSpacing(1)
            date_label = Caption(
                dates, parent=frame, colour="#171717", size=12,
                weight=QFont.Weight.DemiBold,
            )
            date_label.setObjectName("availabilityDate")
            meta_label = Caption(meta, parent=frame, colour=MODAL_MUTED, size=11)
            meta_label.setObjectName("availabilityMeta")
            text.addWidget(date_label)
            text.addWidget(meta_label)
            row.addLayout(text, 1)
            row.addWidget(StatusChip(status, tone=tone, parent=frame), 0, Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(frame)

    def _build_checker(self, column: QVBoxLayout) -> None:
        self.check_heading = SectionLabel(
            "Check a period", parent=self.column, size=13
        )
        column.addWidget(self.check_heading)

        dates = QHBoxLayout()
        dates.setContentsMargins(0, 0, 0, 0)
        dates.setSpacing(8)
        today = QDate.currentDate()
        for label, initial, minimum, attr in (
            ("Start", today, today, "start_edit"),
            ("End", today.addDays(1), today, "end_edit"),
        ):
            cell = QVBoxLayout()
            cell.setContentsMargins(0, 0, 0, 0)
            cell.setSpacing(3)
            caption = Caption(label, parent=self.column, colour=MODAL_MUTED, size=10)
            cell.addWidget(caption)
            edit = QDateEdit(self.column)
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("dd MMM yyyy")
            edit.setDate(initial)
            edit.setMinimumDate(minimum)
            cell.addWidget(edit)
            dates.addLayout(cell, 1)
            setattr(self, attr, edit)
        column.addLayout(dates)

        self.start_edit.dateChanged.connect(self._on_start_changed)
        self.end_edit.dateChanged.connect(self._reset_check)
        self.check_button = QPushButton("Check availability", self.column)
        self.check_button.setObjectName("secondaryButton")
        self.check_button.setMinimumHeight(36)
        self.check_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.check_button.clicked.connect(self._on_check)
        check_row = QHBoxLayout()
        check_row.setContentsMargins(0, 0, 0, 0)
        check_row.setSpacing(8)
        check_row.addWidget(self.check_button, 1)
        self.check_spinner = Spinner(self.column, size=18)
        check_row.addWidget(
            self.check_spinner, 0, Qt.AlignmentFlag.AlignVCenter
        )
        column.addLayout(check_row)

        self.check_error = QLabel("", self.column)
        self.check_error.setObjectName(theme.OBJ_ERROR)
        self.check_error.setWordWrap(True)
        self.check_error.setVisible(False)
        column.addWidget(self.check_error)

        self.check_bar = QFrame(self.column)
        self.check_bar.setObjectName("resultBar")
        bar = QHBoxLayout(self.check_bar)
        bar.setContentsMargins(11, 9, 11, 9)
        bar.setSpacing(8)
        glyph = QLabel(self.check_bar)
        glyph.setObjectName("modalNoticeIcon")
        glyph.setPixmap(icons.pixmap("check", "#3F6D4E", 15))
        bar.addWidget(glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        self.check_result = QLabel("", self.check_bar)
        self.check_result.setObjectName("resultText")
        self.check_result.setWordWrap(True)
        bar.addWidget(self.check_result, 1)
        self.check_amount = MoneyNumber(
            parent=self.check_bar,
            size=13,
            dim=MODAL_MUTED,
            bright="#171717",
            weight=QFont.Weight.DemiBold,
        )
        self.check_amount.setVisible(False)
        bar.addWidget(self.check_amount, 0, Qt.AlignmentFlag.AlignVCenter)
        self.check_bar.setVisible(False)
        self.check_error.setVisible(False)
        column.addWidget(self.check_bar)

    def _footer_host(self) -> QFrame:
        footer = QFrame(self.card)
        footer.setObjectName("modalFooter")
        lay = QHBoxLayout(footer)
        lay.setContentsMargins(24, 12, 24, 14)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.card.layout().addWidget(footer)
        return footer

    def _footer(self, data: dict) -> None:
        footer = self._footer_host()
        lay = footer.layout()
        rate_label = Caption(
            "Daily rate", parent=footer, colour=MODAL_MUTED, size=11
        )
        lay.addWidget(rate_label)
        self.footer_rate = MoneyNumber(
            parent=footer,
            size=14,
            dim=MODAL_MUTED,
            bright="#171717",
            weight=QFont.Weight.DemiBold,
        )
        self.footer_rate.set_amount(data["rate"] or 0)
        lay.addWidget(self.footer_rate)
        lay.addStretch(1)

        self.close_button = QPushButton("Close", footer)
        self.close_button.setObjectName("secondaryButton")
        self.close_button.setMinimumHeight(38)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self.close_requested.emit)
        self.rent_button = QPushButton("Rent this vehicle", footer)
        self.rent_button.setObjectName("primaryButton")
        self.rent_button.setMinimumHeight(38)
        self.rent_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rent_button.clicked.connect(self._on_rent)
        lay.addWidget(self.close_button)
        lay.addWidget(self.rent_button)

    def _on_start_changed(self, day: QDate) -> None:
        self.end_edit.setMinimumDate(day)
        if self.end_edit.date() < day:
            self.end_edit.setDate(day)
        self._reset_check()

    def _reset_check(self) -> None:
        self.checked_window = None
        self._check_generation += 1
        self._checking = False
        self._pending_window = None
        spinner = getattr(self, "check_spinner", None)
        if spinner is not None:
            spinner.stop()
        button = getattr(self, "check_button", None)
        if button is not None:
            button.setEnabled(True)
            button.setText("Check availability")
        rent = getattr(self, "rent_button", None)
        if rent is not None:
            # A previous failure keeps Rent gated until a check succeeds.
            rent.setEnabled(not self._check_failed)
        self.check_bar.setVisible(False)
        self.check_result.setVisible(False)
        self.check_amount.setVisible(False)
        self.check_error.setVisible(False)

    def _fail_check(self, message: str, *, conflict: bool = False) -> None:
        """A check that did not produce a clean window gates Rent."""
        self.checked_window = None
        self._check_failed = True
        self.check_error.setText("" if conflict else message)
        self.check_error.setVisible(not conflict)
        self.check_bar.setVisible(conflict)
        if conflict:
            self.check_result.setText(message)
            self.check_result.setVisible(True)
            self.check_bar.setProperty("tone", "danger")
            self._polish(self.check_bar)
            self.check_amount.setVisible(False)
        else:
            self.check_result.setVisible(False)
            self.check_amount.setVisible(False)
        self.rent_button.setEnabled(False)

    def _on_check(self) -> None:
        start = self.start_edit.date()
        end = self.end_edit.date()
        today = QDate.currentDate()
        error = ""
        if end < start:
            error = "End cannot be before start."
        elif start < today:
            error = "Start is in the past."
        if error:
            self._fail_check(error)
            return
        self.check_error.setVisible(False)

        self.checked_window = None
        self._check_generation += 1
        generation = self._check_generation
        self._checking = True
        self._pending_window = (start, end)
        self.check_bar.setVisible(False)
        self.check_result.setVisible(False)
        self.check_amount.setVisible(False)
        self.check_button.setEnabled(False)
        self.check_button.setText("Checking availability\u2026")
        self.check_spinner.start()
        # Rent cannot produce a wizard while the window is unverified.
        self.rent_button.setEnabled(False)

        job = _AvailabilityJob(
            self._context,
            self._vehicle_id,
            start.toPython(),
            end.toPython(),
            generation,
            self._check_signals,
        )
        media_pool().start(job)

    @Slot(int, object)
    def _on_check_finished(self, generation: int, result: dict) -> None:
        if generation != self._check_generation:
            return
        self._checking = False
        self.check_spinner.stop()
        self.check_button.setEnabled(True)
        self.check_button.setText("Check availability")

        if result.get("ok") is True:
            from app.domain.rental_calculator import RentalCalculator

            window = self._pending_window
            if window is None:
                self._fail_check("The checked dates were lost. Check again.")
                return
            start, end = window
            days = RentalCalculator.rental_days(
                start.toPython(), end.toPython()
            )
            self.checked_window = window
            self._check_failed = False
            self.check_result.setText(
                f"Available for these dates \u00b7 "
                f"{days} day{'s' if days != 1 else ''}"
            )
            self.check_result.setVisible(True)
            self.check_bar.setProperty("tone", "ok")
            self._polish(self.check_bar)
            self.check_amount.set_amount((result.get("rate") or 0) * days)
            self.check_amount.setVisible(True)
            self.check_bar.setVisible(True)
            self.check_error.setVisible(False)
            self.rent_button.setEnabled(True)
            return

        if result.get("ok") is False:
            self._fail_check(
                f"Not available \u2014 {result.get('message') or 'booked'}",
                conflict=True,
            )
            return

        error = result.get("error")
        detail = f": {error}" if error else "."
        self._fail_check(f"Couldn't check availability{detail}")

    @staticmethod
    def _polish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _on_rent(self) -> None:
        # A failed or still-running check must never open the wizard with a
        # fallback window that differs from what the clerk asked for.
        if self._checking or self._check_failed:
            return
        self.rent_requested.emit(self._vehicle_id)


class VehicleDetailsDialog(QDialog):
    """Thin modal-window wrapper around the panel (kept for compat)."""

    rent_requested = Signal(int)

    def __init__(self, context, vehicle_id: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Vehicle details")
        self.setModal(True)
        self.setMinimumSize(920, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.panel = VehicleDetailsPanel(context, vehicle_id, self)
        layout.addWidget(self.panel)

        # Alias the panel's pieces so existing callers keep working. The
        # vehicle bit is only present when the row still exists.
        panel = self.panel
        self.spec_values = panel.spec_values
        self.spec_tiles = getattr(panel, "spec_tiles", {})
        self.missing_label = panel.missing_label
        self.photo = getattr(panel, "photo", None)
        self.gallery = getattr(panel, "gallery", None)
        self.name_label = getattr(panel, "name_label", None)
        self.subtitle_label = getattr(panel, "subtitle_label", None)
        self.price = getattr(panel, "price", None)
        self.start_edit = getattr(panel, "start_edit", None)
        self.end_edit = getattr(panel, "end_edit", None)
        self.check_button = getattr(panel, "check_button", None)
        self.check_spinner = getattr(panel, "check_spinner", None)
        self.check_error = getattr(panel, "check_error", None)
        self.check_result = getattr(panel, "check_result", None)
        self.close_button = panel.close_button
        self.rent_button = panel.rent_button
        self.panel.close_requested.connect(self.reject)
        self.panel.rent_requested.connect(self._on_panel_rent)

    @property
    def checked_window(self):
        return self.panel.checked_window

    def _on_panel_rent(self, vehicle_id: int) -> None:
        self.rent_requested.emit(vehicle_id)
        self.accept()


class VehicleDetailsOverlay(QWidget):
    """Check details embedded in the staff window: frosted glass holding a
    centered 900px card.

    Not a window at all (`isWindow()` is False), so nothing new ever opens on
    the taskbar. Covers `window` edge to edge, blocks mouse input behind it,
    and closes on Esc or a click outside the card.
    """

    def __init__(
        self,
        context,
        vehicle_id: int,
        window: QWidget,
        *,
        autoload: bool = True,
    ) -> None:
        super().__init__(window)
        self._window = window
        #: False while the rental wizard owns this overlay: Escape and
        #: outside clicks must not discard a half-filled rental.
        self._dismissible = True
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "VehicleDetailsOverlay { background: rgba(23, 23, 23, 110);"
            " border: none; }"
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.backdrop = QLabel(self)
        self.backdrop.setObjectName("vehicleDetailsBackdrop")
        self.backdrop.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._blur_timer = QTimer(self)
        self._blur_timer.setSingleShot(True)
        self._blur_timer.setInterval(BACKDROP_RERENDER_MS)
        self._blur_timer.timeout.connect(self._on_blur_timeout)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(0)

        middle = QHBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(0)
        middle.addStretch(1)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("vehicleDetailsScroll")
        self.scroll.viewport().setObjectName("vehicleDetailsViewport")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setStyleSheet(
            "QScrollArea#vehicleDetailsScroll { background: transparent;"
            " border: none; }"
            " QScrollArea#vehicleDetailsScroll QWidget#vehicleDetailsViewport {"
            " background: transparent; border: none; }"
        )
        self.scroll.setFixedWidth(OVERLAY_CARD_W)
        middle.addWidget(self.scroll)
        middle.addStretch(1)
        outer.addLayout(middle, 1)

        self.panel = VehicleDetailsPanel(
            context, vehicle_id, self.scroll, autoload=autoload
        )
        self.scroll.setWidget(self.panel)
        self.backdrop.lower()
        self.hide()

    def set_dismissible(self, flag: bool) -> None:
        """Allow or refuse Escape/outside-click closing."""
        self._dismissible = bool(flag)

    def _render_backdrop(self) -> bool:
        """Freeze, blur and dim the live page into the backdrop label."""
        try:
            window = self._window
            if window is None or window.size().isEmpty():
                return False
            src = window.grab()
            if src.isNull():
                return False
            dpr = src.devicePixelRatio()
            small = src.scaled(
                max(1, src.width() // BACKDROP_DOWNSCALE),
                max(1, src.height() // BACKDROP_DOWNSCALE),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            effect = QGraphicsBlurEffect()
            effect.setBlurRadius(BACKDROP_BLUR_RADIUS)
            scene = QGraphicsScene()
            item = QGraphicsPixmapItem(small)
            item.setGraphicsEffect(effect)
            scene.addItem(item)
            scene.setSceneRect(QRectF(small.rect()))
            blurred = QPixmap(small.size())
            blurred.setDevicePixelRatio(small.devicePixelRatio())
            blurred.fill(Qt.GlobalColor.transparent)
            painter = QPainter(blurred)
            try:
                scene.render(
                    painter, QRectF(blurred.rect()), scene.sceneRect()
                )
            finally:
                painter.end()
            target = self.size()
            if target.isEmpty():
                target = window.size()
            full = blurred.scaled(
                max(1, int(target.width() * dpr)),
                max(1, int(target.height() * dpr)),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            full.setDevicePixelRatio(dpr)
            painter = QPainter(full)
            try:
                painter.fillRect(
                    full.rect(), QColor(23, 23, 23, BACKDROP_DIM)
                )
            finally:
                painter.end()
            self.backdrop.setPixmap(full)
            return True
        except Exception:  # noqa: BLE001 - backdrop must never break modal
            return False

    def _render_soon(self) -> None:
        if not self._blur_timer.isActive():
            self._blur_timer.start()
        else:
            self._blur_timer.stop()
            self._blur_timer.start()

    def _on_blur_timeout(self) -> None:
        if self.isVisible():
            self._render_backdrop()

    def show_overlay(self) -> None:
        self._fit()
        self._render_backdrop()
        self._window.installEventFilter(self)
        self.raise_()
        self.backdrop.lower()
        self.show()
        self.setFocus()

    def hide_overlay(self) -> None:
        self._window.removeEventFilter(self)
        self._blur_timer.stop()
        self.backdrop.clear()
        self.hide()

    def _details_width(self) -> int:
        """Card width capped by the window, with a gutter on both sides.

        The real window never gets narrower than 900px; the fallback to the
        design width below that keeps standalone/test hosts sane.
        """
        window_width = self._window.width()
        if window_width < metrics.WINDOW_MIN_W:
            return OVERLAY_CARD_W
        available = window_width - 2 * metrics.DETAILS_OVERLAY_MARGIN
        return max(
            metrics.DETAILS_CARD_MIN_W,
            min(OVERLAY_CARD_W, available),
        )

    def _fit(self) -> None:
        self.setGeometry(self._window.rect())
        self.backdrop.setGeometry(self.rect())
        self.scroll.setFixedWidth(self._details_width())
        self.scroll.setMaximumHeight(max(320, self._window.height() - 96))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        try:
            is_window_resize = (
                watched is self._window and event.type() == QEvent.Type.Resize
            )
        except (AttributeError, RuntimeError):
            return False
        if is_window_resize:
            try:
                self._fit()
                if self.isVisible():
                    self._render_soon()
            except (AttributeError, RuntimeError):
                pass
        try:
            return super().eventFilter(watched, event)
        except RuntimeError:
            return False

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() == Qt.Key.Key_Escape and self._dismissible:
            self.hide_overlay()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # Outside-click-to-close only: presses inside the card column do
        # nothing at all. The scroll is the stable frame: wizard steps swap
        # panels, never the scroll.
        if (
            self._dismissible
            and event.button() == Qt.MouseButton.LeftButton
            and not self.scroll.geometry().contains(event.position().toPoint())
        ):
            self.hide_overlay()
        super().mousePressEvent(event)
