from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.staff import theme
from app.staff.context import DatabaseSelection, DatabaseTarget, StaffContext
from app.staff.metrics import REFERENCE_H, REFERENCE_W
from app.utils.fonts import load_fonts

DEFAULT_OUT = Path(r"C:\Users\Lily Ann\Desktop")
DEMO = "sqlite:///demo.db"
EMAIL = {
    "admin": "admin@rentwheels.local",
    "staff": "counter@rentwheels.local",
}

#: (tag, width, height)
SHAPES = (
    ("wide", REFERENCE_W, REFERENCE_H),
    ("medium", 1000, REFERENCE_H),
    ("narrow", 800, REFERENCE_H),
)


def build(role: str, out_dir: Path, tag: str) -> None:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    load_fonts()
    app.setStyleSheet(theme.load_stylesheet())

    context = StaffContext(
        DatabaseSelection(url=DEMO, target=DatabaseTarget.DEMO, label="demo")
    )

    from app.services.auth_service import sign_in
    from app.staff.context import StaffUser

    # Snapshot inside the session: reading() rolls back and expires the row.
    with context.reading() as session:
        row = sign_in(session, EMAIL[role], "demo-password")
        identity = StaffUser.from_row(row)

    context.sign_in(identity)

    from app.staff.pages.dashboard import DashboardPage

    class _Shell:
        def __init__(self, ctx: StaffContext) -> None:
            self.context = ctx

    page = DashboardPage(_Shell(context), animate=False)
    page.resize(REFERENCE_W, REFERENCE_H)
    page.show()
    # The shell owns the refresh, so ask explicitly before grabbing.
    page.refresh()

    out_dir.mkdir(parents=True, exist_ok=True)

    for shape, width, height in SHAPES:
        page.resize(width, height)
        # Two pumps: the grid reflows from its own resizeEvent.
        app.processEvents()
        app.processEvents()
        page.grab().save(str(out_dir / f"dash_{tag}_{shape}.png"))
        print(f"{tag}/{shape}: grid shape={page.grid.shape}, width={page.grid.width()}")

    # Per-card shots are taken at the reference width.
    page.resize(REFERENCE_W, REFERENCE_H)
    app.processEvents()
    app.processEvents()
    for name, card in (
        ("revenue", page.revenue_card),
        ("channels", page.channels_card),
        ("activity", page.activity_card),
        ("transactions", page.transactions_card),
        ("target", page.target_card),
    ):
        if card.isVisible():
            card.grab().save(str(out_dir / f"dash_{tag}_{name}.png"))

    print(f"wrote dash_{tag}_*.png to {out_dir}")


def main(argv: list[str]) -> int:
    role = argv[1] if len(argv) > 1 else "admin"
    out = Path(argv[2]) if len(argv) > 2 else DEFAULT_OUT
    build(role, out, role)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
