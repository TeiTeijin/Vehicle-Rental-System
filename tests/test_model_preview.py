"""The 3D preview lives in its own process so WebEngine cannot kill staff."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QWidget


class _FakeSettings:
    def __init__(self) -> None:
        self.attributes = []

    def setAttribute(self, attribute, value) -> None:
        self.attributes.append((attribute, value))


class _FakeView(QWidget):
    made = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._settings = _FakeSettings()
        self.document = ""
        self.base_url = None
        self.background = None
        _FakeView.made.append(self)

    def settings(self):
        return self._settings

    def setHtml(self, document, base_url) -> None:
        self.document = document
        self.base_url = base_url

    def page(self):
        return SimpleNamespace(
            setBackgroundColor=lambda colour: setattr(
                self, "background", colour
            )
        )


def test_create_window_loads_the_bundled_viewer_and_the_glb(
    qapp, monkeypatch, tmp_path
):
    from app.staff import model_preview_window as preview
    from app.ui import vehicle_photo

    glb = tmp_path / "car model.glb"
    glb.write_bytes(b"glTF")
    monkeypatch.setattr(preview, "QWebEngineView", _FakeView)
    _FakeView.made.clear()

    window = preview.create_window(glb, "Kia Soluto")
    try:
        view = _FakeView.made[-1]
        preview.load_document(window, glb, "Kia Soluto")
        assert window.windowTitle() == "3D preview - Kia Soluto"
        assert window.centralWidget() is view
        assert vehicle_photo._file_url(glb) in view.document
        assert vehicle_photo._file_url(vehicle_photo.MODEL_VIEWER_JS) in view.document
        assert "model-viewer" in view.document
        assert "Kia Soluto" in view.document
        assert view.base_url.toLocalFile() == str(glb.parent) + "/"
        assert window.isVisible() is True
    finally:
        window.close()
        window.deleteLater()


def test_load_document_shows_the_window_before_handing_it_the_page(
    qapp, monkeypatch, tmp_path
):
    """A hidden QWebEngineView can render its document blank and never recover."""
    from app.staff import model_preview_window as preview

    glb = tmp_path / "car.glb"
    glb.write_bytes(b"glTF")
    seen = []

    class _RecordingView(_FakeView):
        def setHtml(self, document, base_url) -> None:
            seen.append(self.window().isVisible())
            super().setHtml(document, base_url)

    monkeypatch.setattr(preview, "QWebEngineView", _RecordingView)
    window = preview.create_window(glb, "Kia Soluto")
    try:
        assert window.isVisible() is False
        preview.load_document(window, glb, "Kia Soluto")
        assert seen == [True], "the document was loaded into a hidden window"
    finally:
        window.close()
        window.deleteLater()


def test_main_rejects_an_incomplete_command_line(qapp, capsys):
    from app.staff import model_preview_window as preview

    assert preview.main(["model_preview_window"]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_main_rejects_a_missing_model(qapp, tmp_path, capsys):
    from app.staff import model_preview_window as preview

    code = preview.main(["model_preview_window", str(tmp_path / "nope.glb"), "X"])
    assert code == 3
    assert "not found" in capsys.readouterr().err.lower()


def test_main_creates_then_loads_the_window(qapp, monkeypatch, tmp_path):
    from app.staff import model_preview_window as preview

    glb = tmp_path / "car.glb"
    glb.write_bytes(b"glTF")
    events = []

    class _Window:
        pass

    class _App:
        def exec(self):
            return 7

    monkeypatch.setattr(
        preview,
        "create_window",
        lambda path, label: events.append("create") or _Window(),
    )
    monkeypatch.setattr(
        preview,
        "load_document",
        lambda window, path, label: events.append("load"),
    )
    monkeypatch.setattr(preview, "QApplication", SimpleNamespace(
        instance=lambda: _App(),
        __call__=lambda *a, **k: _App(),
    ))
    assert preview.main(["model_preview_window", str(glb), "Kia Soluto"]) == 7
    assert events == ["create", "load"]
