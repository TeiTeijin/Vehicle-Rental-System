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

    with context.reading() as session:
        row = sign_in(session, EMAIL[role], "demo-password")
        identity = StaffUser.from_row(row)

    context.sign_in(identity)

    from app.staff.pages.new_rental import NewRentalPage

    class _Shell:
        def __init__(self, ctx: StaffContext) -> None:
            self.context = ctx

    page = NewRentalPage(_Shell(context))
    page.resize(REFERENCE_W, REFERENCE_H)
    page.show()
    page.refresh()

    while not page._exhausted:
        page._load_page()

    _drain(page, app)

    out_dir.mkdir(parents=True, exist_ok=True)

    for shape, width, height in SHAPES:
        page.resize(width, height)
        app.processEvents()
        app.processEvents()
        page.grab().save(str(out_dir / f"rental_{tag}_{shape}.png"))
        print(
            f"{tag}/{shape}: brands={len(page.brands_shown)} "
            f"slots={len(page.slots)} loaded={_loaded(page)} "
            f"width={page.width()}"
        )

    page.resize(REFERENCE_W, REFERENCE_H)
    _drain(page, app)
    for slot in page.slots:
        if slot.built and slot.card is not None:
            slot.card.grab().save(str(out_dir / f"rental_{tag}_card.png"))
            break

    print(f"wrote rental_{tag}_*.png to {out_dir}")


def _loaded(page) -> int:
    return sum(
        1
        for slot in page.slots
        if slot.built and slot.card is not None and slot.card.photo._image is not None
    )


def _drain(page, app, timeout: float = 90.0) -> None:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        page._pool.waitForDone(250)
        app.processEvents()
        built = len(page.slots)
        if built and _loaded(page) >= built:
            return


def main(argv: list[str]) -> int:
    role = argv[1] if len(argv) > 1 else "admin"
    out = Path(argv[2]) if len(argv) > 2 else DEFAULT_OUT
    build(role, out, role)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
