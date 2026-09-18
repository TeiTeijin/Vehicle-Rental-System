import html
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QToolBar, QVBoxLayout, QWidget

from app.database import SessionLocal
from app.models import Vehicle, Vehicle_Media
from app.services import carimages_client as ci
from app.services import media_service

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSET_DIR = PROJECT_ROOT / "media_assets"

IMAGE_BLOCK = '<img src="{url}" alt="{alt}" loading="lazy">'

MODEL_BLOCK = """<model-viewer src="{url}" alt="{alt}" camera-controls auto-rotate
  shadow-intensity="1" style="width:100%;height:320px;background:#111"></model-viewer>"""


def _slugify(value: str) -> str:
    keep = [ch if ch.isalnum() else "-" for ch in value.strip().lower()]
    return "".join(keep).replace("--", "-").strip("-")


def ensure_glb(vehicle, rows) -> str | None:
    row = next((r for r in rows if r.view_angle == "model_3d" and r.model_3d_url), None)
    if row is None:
        return None
    ASSET_DIR.mkdir(exist_ok=True)
    filename = f"{_slugify(f'{vehicle.make} {vehicle.model}')}.glb"
    path = ASSET_DIR / filename
    if not path.exists():
        print(f"Downloading GLB for {vehicle.make} {vehicle.model}...")
        path.write_bytes(ci.download_model_glb(row.model_3d_url))
    return f"media_assets/{filename}"


def build_html(entries, glb_files) -> str:
    cards = []
    seen = 0
    for vehicle, rows in entries:
        media = []
        safe_make = html.escape(vehicle.make)
        safe_model = html.escape(vehicle.model)
        safe_year = html.escape(str(vehicle.year))
        for row in rows:
            alt = f"{vehicle.make} {vehicle.model}"
            if row.view_angle == "model_3d" and glb_files.get(vehicle.vehicle_id):
                media.append(MODEL_BLOCK.format(url=glb_files[vehicle.vehicle_id], alt=html.escape(alt)))
                seen += 1
            elif row.image_url:
                media.append(IMAGE_BLOCK.format(url=html.escape(row.image_url, quote=True), alt=html.escape(alt)))
                seen += 1
        is_moto = any("type=moto" in (r.image_url or "") for r in rows)
        tag = "MOTORCYCLE" if is_moto else "CAR"
        cards.append(
            f"""<div class="card">
<h3>{safe_make} {safe_model} {safe_year} <span class="tag">{tag}</span></h3>
<div class="media">
  {"  ".join(media)}
</div>
</div>"""
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Vehicle Gallery</title>
<script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/4.0.0/model-viewer.min.js"></script>
<style>
  body {{ font-family: system-ui, sans-serif; background: #0f1117; color: #eaeaea; margin: 24px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(400px, 1fr)); gap: 20px; }}
  .card {{ background: #1a1d26; border-radius: 12px; padding: 16px; }}
  h3 {{ margin: 0 0 12px; font-size: 16px; }}
  .tag {{ font-size: 11px; color: #9ab; background: #262b38; padding: 2px 8px; border-radius: 6px; }}
  .media img {{ width: 100%; border-radius: 8px; display: block; margin-bottom: 8px; }}
  model-viewer {{ border-radius: 8px; }}
</style>
</head>
<body>
<h2>Vehicle Gallery ({seen} media assets)</h2>
<div class="grid">
{chr(10).join(cards)}
</div>
</body>
</html>"""


def collect_entries(session, refresh: bool) -> list:
    vehicles = session.query(Vehicle).all()
    for vehicle in vehicles:
        if refresh:
            media_service.get_or_fetch_media(session, vehicle)
    session.commit()

    entries = []
    for vehicle in vehicles:
        rows = (
            session.query(Vehicle_Media)
            .filter_by(vehicle_id=vehicle.vehicle_id)
            .order_by(Vehicle_Media.media_id)
            .all()
        )
        if rows:
            entries.append((vehicle, rows))
    return entries


class GalleryWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Vehicle Rentals - Media Gallery (PySide6 + WebEngine)")
        self.resize(1280, 900)

        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.status = QLabel(" loading gallery...")
        self.status.setMargin(6)
        toolbar.addWidget(self.status)

        refresh = toolbar.addAction("Refresh from API (cache-aware)")
        refresh.triggered.connect(self.rebuild_media)

        self.view = QWebEngineView()
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.setCentralWidget(central)

        self.rebuild_media()

    def rebuild_media(self) -> None:
        session = SessionLocal()
        try:
            entries = collect_entries(session, refresh=True)
        except Exception as exc:
            self.status.setText(f" error: {exc}")
            return
        finally:
            session.close()

        glb_files = {}
        for vehicle, rows in entries:
            local = ensure_glb(vehicle, rows)
            if local:
                glb_files[vehicle.vehicle_id] = local

        base = QUrl.fromLocalFile(str(PROJECT_ROOT / "gallery.html"))
        self.view.setHtml(build_html(entries, glb_files), base)
        self.status.setText(f" loaded {len(entries)} vehicles from DB/API")
        self.view.loadFinished.connect(self._report_3d_status, Qt.ConnectionType.UniqueConnection)

    def _report_3d_status(self, ok: bool) -> None:
        script = """
        setTimeout(() => {
          const items = Array.from(document.querySelectorAll('model-viewer'));
          const models = items.map(mv => mv.getAttribute('src') + ' -> loaded=' + mv.loaded + ' rendering=' + mv.modelIsVisible);
          const imgs = Array.from(document.querySelectorAll('.media img'));
          const images = imgs.map(img => (img.currentSrc || img.src).split('?')[0] + ' -> ' + (img.naturalWidth > 0 ? 'OK' : 'BROKEN'));
          window.ci3d = models;
          window.ciimg = images;
        }, 6000);
        """
        self.view.page().runJavaScript(script)
        from PySide6.QtCore import QTimer

        def poll():
            self.view.page().runJavaScript(
                "window.ciimg ? JSON.stringify({m: window.ci3d, i: window.ciimg}) : 'pending'",
                self._print_3d_status,
            )

        QTimer.singleShot(7000, poll)

    def _print_3d_status(self, result) -> None:
        print("MEDIA DIAG:", result)


def main() -> None:
    import os

    from PySide6.QtCore import QTimer

    app = QApplication(sys.argv)
    window = GalleryWindow()
    window.show()
    if os.environ.get("GALLERY_SELFTEST") == "1":
        QTimer.singleShot(16000, lambda: (print("SELFTEST COMPLETE"), sys.exit(0)))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()