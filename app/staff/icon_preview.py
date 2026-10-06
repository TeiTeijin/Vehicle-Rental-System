from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from app.staff.icons import PATHS, pixmap
from app.staff.theme import BG, INK, MUTED, TEXT

COLUMNS = 7
CELL_W, CELL_H = 130, 96
SIZE = 40


def build(path: Path) -> None:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    names = sorted(PATHS)
    rows = (len(names) + COLUMNS - 1) // COLUMNS

    pm = QPixmap(COLUMNS * CELL_W, rows * CELL_H)
    pm.fill(QColor(BG))

    painter = QPainter(pm)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        label_font = QFont("Inter", 10)
        for index, name in enumerate(names):
            col, row = index % COLUMNS, index // COLUMNS
            x = col * CELL_W + CELL_W // 2
            y = row * CELL_H

            painter.drawPixmap(
                x - SIZE // 2, y + 14, pixmap(name, INK, SIZE)
            )
            painter.setFont(label_font)
            painter.setPen(QColor(TEXT))
            painter.drawText(
                x - CELL_W // 2, y + CELL_H - 26, CELL_W, 20,
                Qt.AlignmentFlag.AlignCenter, name,
            )
            painter.setPen(QColor(MUTED))
            painter.drawText(
                x - CELL_W // 2, y + CELL_H - 12, CELL_W, 16,
                Qt.AlignmentFlag.AlignCenter, "24 grid / 1.6 stroke",
            )
    finally:
        painter.end()

    pm.save(str(path))
    print(f"wrote {path} ({len(names)} icons, {rows}x{COLUMNS})")
    del app


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("icon_preview.png")
    build(target)
