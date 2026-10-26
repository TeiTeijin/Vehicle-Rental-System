"""The pictures on the customer screens.

Two widgets, both filled in the background so no click can freeze on a
network call:

* :class:`VehiclePhoto` -- one image view (front / left / right / thumbnail).
  It paints a shimmer block while the bytes are on their way and a quiet
  "no photo yet" line when the API has nothing for that vehicle.
* :class:`VehicleModelView` -- the 3D tab: the vehicle's .glb rendered by
  <model-viewer> inside a QWebEngineView, which is created only when the tab
  is first opened (a web engine per screen would otherwise cost ~100 MB).

Nothing here reads the database: the bytes come from
app/services/vehicle_photos.py, which talks to the CarImages API and keeps a
disk cache.
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QLabel,
    QSizePolicy,
    QStackedLayout,
    QWidget,
)

from app.services import vehicle_photos
from app.widgets.skeleton import Skeleton

#: Rounded corners on every photo, matching the cards around them.
PHOTO_RADIUS = 12

#: How long the fade from skeleton to photo takes, in ms.
FADE_MS = 220

#: The model-viewer runtime is bundled rather than loaded from Google's CDN:
#: a page opened from a local file cannot fetch a cross-origin ES module, so
#: the remote script would leave the 3D tab blank. Shipping the script keeps
#: the viewer working offline as well.
ASSETS_DIR = Path(__file__).with_name("assets")
MODEL_VIEWER_JS = ASSETS_DIR / "model-viewer.min.js"

_POOL: QThreadPool | None = None


def _file_url(path: Path) -> str:
    """A file:// URL Chromium can load from a setHtml() document."""
    return bytes(QUrl.fromLocalFile(str(path)).toEncoded()).decode("ascii")


def _pool() -> QThreadPool:
    """One shared pool: photos for the grid and the vehicle page line up in
    the same queue instead of spawning a thread per widget."""
    global _POOL
    if _POOL is None:
        pool = QThreadPool()
        pool.setMaxThreadCount(3)
        _POOL = pool
    return _POOL


class _Signals(QObject):
    ready = Signal(object, bytes)
    failed = Signal(object)


class _PhotoJob(QRunnable):
    """Fetch one view off the UI thread."""

    def __init__(self, key, vehicle, view: str, thumbnail: bool) -> None:
        super().__init__()
        self.key = key
        self.vehicle = vehicle
        self.view = view
        self.thumbnail = thumbnail
        self.signals = _Signals()

    @Slot()
    def run(self) -> None:
        try:
            data = (
                vehicle_photos.thumbnail_bytes(self.vehicle)
                if self.thumbnail
                else vehicle_photos.photo_bytes(self.vehicle, self.view)
            )
        except Exception:  # noqa: BLE001 - a photo never breaks a page
            data = None
        if data:
            self._emit(self.signals.ready, data)
        else:
            self._emit(self.signals.failed)

    def _emit(self, signal, *args) -> None:
        """Emit unless the page was torn down while this job was in flight.

        Navigating away deletes the label (and its signal object) while the
        pool still holds the job; Qt then raises instead of dropping the
        signal, which would surface as a stray traceback on stderr.
        """
        try:
            signal.emit(self.key, *args)
        except RuntimeError:
            pass


class _ModelJob(QRunnable):
    """Fetch the .glb off the UI thread."""

    def __init__(self, key, vehicle) -> None:
        super().__init__()
        self.key = key
        self.vehicle = vehicle
        self.signals = _Signals()

    @Slot()
    def run(self) -> None:
        try:
            path = vehicle_photos.model_glb_path(self.vehicle)
        except Exception:  # noqa: BLE001
            path = None
        if path is None:
            self._emit(self.signals.failed)
        else:
            self._emit(self.signals.ready, str(path).encode("utf-8"))

    def _emit(self, signal, *args) -> None:
        """See _PhotoJob._emit: a torn-down viewer must not raise here."""
        try:
            signal.emit(self.key, *args)
        except RuntimeError:
            pass


class _WarmJob(QRunnable):
    """Pull one thumbnail into the disk cache without showing it.

    The hero prefetches its neighbouring slides with this: by the time the
    customer clicks the arrow the bytes are usually already on disk.
    """

    def __init__(self, ref) -> None:
        super().__init__()
        self.ref = ref

    @Slot()
    def run(self) -> None:
        try:
            vehicle_photos.thumbnail_bytes(self.ref)
        except Exception:  # noqa: BLE001 - warming is best-effort
            pass


def warm_thumbnail(ref) -> None:
    """Fetch one thumbnail in the background (see `_WarmJob`)."""
    if ref is not None:
        _pool().start(_WarmJob(ref))


class VehiclePhoto(QLabel):
    """One vehicle image, loaded in the background and letter-boxed.

    `thumbnail` uses the shared three-quarter shot (the same bytes the browse
    grid uses), so a list of vehicles does not pay for five views each.

    `cutout` is for the showroom hero: CarImages serves the cars as WebP
    cutouts (alpha corners), so the label lets the page show through instead
    of painting the usual white plate. The huge name behind the car then
    reads through everywhere except the bodywork itself.

    `skeleton` can be switched off where a grey block would be louder than
    a short blank: the hero fades the car straight in instead.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        thumbnail: bool = False,
        box: QSize | None = None,
        cutout: bool = False,
        skeleton: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("photoCutout" if cutout else "photoCanvas")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        if box is not None:
            self.setFixedSize(box)

        self._thumbnail = thumbnail
        self._cutout = cutout
        self._vehicle = None
        self._view = vehicle_photos.DEFAULT_VIEW
        self._photo_key: tuple | None = None
        self._image: QImage | None = None
        self._placeholder = "No photo available for this vehicle"
        self._skeleton_enabled = skeleton
        self._fade: QPropertyAnimation | None = None

        self._skeleton = Skeleton(self, radius=PHOTO_RADIUS)
        self._skeleton.hide()

    # ----- api -----
    def set_vehicle(self, vehicle, view: str | None = None) -> None:
        if view is not None:
            self._view = view
        self._vehicle = vehicle
        self._image = None
        self._reload()

    def set_view(self, view: str) -> None:
        if view == self._view:
            return
        self._view = view
        self._image = None
        self._reload()

    def set_waiting(self) -> None:
        """The busy block, without asking for bytes.

        Shelf cards defer their fetch until they scroll into view; the page
        calls `set_vehicle` then. Until that happens this shows the shimmer
        instead of the "no photo" line, which would be a lie.
        """
        self._vehicle = None
        self._photo_key = None
        self._image = None
        self._show_text("")
        if self._skeleton_enabled:
            self._skeleton.set_busy(True)

    @property
    def vehicle(self):
        return self._vehicle

    # ----- loading -----
    def _view_name(self) -> str:
        return vehicle_photos.DEFAULT_VIEW if self._thumbnail else self._view

    def _reload(self) -> None:
        if self._vehicle is None:
            self._show_text(self._placeholder)
            return

        self._photo_key = (
            self._vehicle.make,
            self._vehicle.model,
            self._vehicle.year,
            self._view_name(),
        )
        self._show_text("")
        if self._skeleton_enabled:
            self._skeleton.set_busy(True)
        job = _PhotoJob(self._photo_key, self._vehicle, self._view, self._thumbnail)
        job.signals.ready.connect(self._on_ready)
        job.signals.failed.connect(self._on_failed)
        _pool().start(job)

    def _show_text(self, text: str) -> None:
        self.setPixmap(QPixmap())
        self.setText(text)
        self.setProperty("empty", bool(text))
        self.style().unpolish(self)
        self.style().polish(self)

    @Slot(object, bytes)
    def _on_ready(self, key, data: bytes) -> None:
        if key != self._photo_key:
            return  # an older view answered after the customer moved on
        image = QImage()
        if not image.loadFromData(data):
            self._on_failed(key)
            return
        self._image = image
        self._skeleton.set_busy(False)
        self._paint()
        self._fade_in()

    @Slot(object)
    def _on_failed(self, key) -> None:
        if key != self._photo_key:
            return
        self._skeleton.set_busy(False)
        self._show_text(self._placeholder)

    def _fade_in(self) -> None:
        """Ease the bytes in so a freshly loaded car never pops on screen."""
        if not self.isVisible() or self.width() <= 0 or self.height() <= 0:
            return
        if self._fade is not None:
            self._fade.stop()
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(0.0)
        self.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(FADE_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)

        def clear(e=effect) -> None:
            # A newer photo may already own the label (fast view switching).
            if self.graphicsEffect() == e:
                self.setGraphicsEffect(None)

        fade.finished.connect(clear)
        self._fade = fade
        fade.start()

    def _paint(self) -> None:
        if self._image is None:
            return
        scaled = self._image.scaled(
            max(1, self.width() - 2),
            max(1, self.height() - 2),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        if self._cutout:
            # The hero's WebP already has transparent bodywork corners; a
            # rounded plate would cut into the car instead of the card.
            self.setPixmap(QPixmap.fromImage(scaled))
            return

        # QLabel paints a pixmap square over its stylesheet background, so the
        # card's rounded corner would otherwise be covered by the photo's own
        # square corner. Clip the bytes to the same bubble radius.
        canvas = QPixmap(scaled.size())
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = min(PHOTO_RADIUS, scaled.width() / 2, scaled.height() / 2)
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(0, 0, scaled.width(), scaled.height()), radius, radius
        )
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, QPixmap.fromImage(scaled))
        painter.end()
        self.setPixmap(canvas)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._skeleton.setGeometry(self.rect())
        self._paint()


MODEL_VIEWER_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script type="module" src="{viewer_js}"></script>
<style>
  html, body {{ margin: 0; height: 100%; background: #ffffff; }}
  model-viewer {{ width: 100%; height: 100%; --poster-color: #ffffff; }}
</style>
</head>
<body>
<model-viewer src="{src}" alt="{alt}" camera-controls touch-action="pan-y"
  auto-rotate rotation-per-second="18deg" shadow-intensity="0.9"
  exposure="1" interaction-prompt="none"></model-viewer>
</body>
</html>"""


def model_viewer_document(glb_path: Path, vehicle) -> str:
    """The HTML for one vehicle's .glb.

    `src` is substituted without its own quotes: the surrounding attribute
    quotes in MODEL_VIEWER_HTML are the only pair. Double-quoting the URL
    (the old json.dumps call) made Chromium parse the path as extra empty
    attributes, so the tab showed a blank white page.
    """
    return MODEL_VIEWER_HTML.format(
        viewer_js=_file_url(MODEL_VIEWER_JS),
        src=_file_url(glb_path),
        alt=html.escape(f"{vehicle.make} {vehicle.model}"),
    )


class VehicleModelView(QWidget):
    """The 3D tab: <model-viewer> over the vehicle's downloaded .glb."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modelCanvas")
        self.setMinimumHeight(320)

        self._vehicle = None
        self._key: tuple | None = None
        self._view = None

        self._message = QLabel("Loading the 3D model...")
        self._message.setObjectName("photoEmpty")
        self._message.setAlignment(Qt.AlignCenter)
        self._message.setWordWrap(True)

        self._skeleton = Skeleton(self)

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self._message)

        self._signal_host = _Signals()
        self._signal_host.ready.connect(self._on_ready)
        self._signal_host.failed.connect(self._on_failed)

    def set_vehicle(self, vehicle) -> None:
        self._vehicle = vehicle
        self._key = (vehicle.make, vehicle.model, vehicle.year)
        self._message.setText("Loading the 3D model...")
        self._message.show()
        self._skeleton.set_busy(True)
        job = _ModelJob(self._key, vehicle)
        job.signals.ready.connect(self._on_ready)
        job.signals.failed.connect(self._on_failed)
        _pool().start(job)
        self._stack.setCurrentWidget(self._message)

    @property
    def vehicle(self):
        return self._vehicle

    def _viewer(self):
        """The QWebEngineView, created on first use (it is heavy)."""
        if self._view is not None:
            return self._view
        try:
            from PySide6.QtWebEngineCore import QWebEngineSettings
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except ImportError:  # pragma: no cover - Qt built without WebEngine
            return None

        view = QWebEngineView(self)
        settings = view.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        self._stack.addWidget(view)
        self._view = view
        return view

    @Slot(object, bytes)
    def _on_ready(self, key, payload: bytes) -> None:
        if key != self._key:
            return
        if not MODEL_VIEWER_JS.is_file():
            self._message.setText(
                "The 3D viewer is missing from this installation.\n"
                "Use the side views to inspect the body."
            )
            self._skeleton.set_busy(False)
            self._stack.setCurrentWidget(self._message)
            return
        view = self._viewer()
        if view is None:
            self._on_failed(key)
            return
        path = Path(payload.decode("utf-8"))
        document = model_viewer_document(path, self._vehicle)
        # Show the engine before handing it the document: a QWebEngineView
        # that is still hidden when setHtml() is called never starts its
        # renderer, and the tab stays white even though the model exists.
        self._stack.setCurrentWidget(view)
        view.setHtml(document, QUrl.fromLocalFile(str(path.parent) + "/"))
        self._skeleton.set_busy(False)

    @Slot(object)
    def _on_failed(self, key) -> None:
        if key != self._key:
            return
        self._skeleton.set_busy(False)
        self._message.setText(
            "No 3D model for this vehicle yet.\n"
            "Use the side views to inspect the body."
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._skeleton.setGeometry(self.rect())
