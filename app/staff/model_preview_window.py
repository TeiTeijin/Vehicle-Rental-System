"""Standalone 3D model preview window.

This module is the only place the staff app imports QtWebEngine. It is launched
as `python -m app.staff.model_preview_window <glb> <label>` by
`app.staff.model_preview.launch_model_preview`, so a WebEngine crash can never
take down the main staff process.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow

from app.ui import vehicle_photo

#: Canvas colour behind the model, matching the details gallery.
BACKGROUND = "#EFE6D5"


def create_window(glb_path: Path, label: str) -> QMainWindow:
    """Build the preview window for one local GLB.

    The window is deliberately left hidden here; :func:`load_document` shows
    it before handing the document to WebEngine. Creating the page while the
    view is hidden is what could leave the external preview blank.
    """
    window = QMainWindow()
    window.setWindowTitle(f"3D preview - {label}")
    window.resize(960, 640)

    view = QWebEngineView(window)
    view.setMinimumSize(520, 380)
    settings = view.settings()
    settings.setAttribute(
        QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
    )
    settings.setAttribute(
        QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
    )
    view.page().setBackgroundColor(QColor(BACKGROUND))

    window.setCentralWidget(view)
    return window


def load_document(window: QMainWindow, glb_path: Path, label: str) -> None:
    """Show *window*, then load the model-viewer document into its view."""
    path = Path(glb_path)
    view = window.centralWidget()
    window.show()

    # `model_viewer_document` names the vehicle in the model-viewer alt text.
    vehicle = SimpleNamespace(make=label, model="")
    document = vehicle_photo.model_viewer_document(path, vehicle)
    view.setHtml(document, QUrl.fromLocalFile(str(path.parent) + "/"))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 3:
        print(
            "usage: python -m app.staff.model_preview_window <glb> <label>",
            file=sys.stderr,
        )
        return 2
    glb = Path(argv[1])
    label = argv[2]
    if not glb.is_file():
        print(f"3D model not found: {glb}", file=sys.stderr)
        return 3

    app = QApplication.instance() or QApplication(argv[:1])
    window = create_window(glb, label)
    load_document(window, glb, label)
    return app.exec()


if __name__ == "__main__":  # pragma: no cover - exercised by the real app
    raise SystemExit(main())
