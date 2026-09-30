"""Render the dashboard to PNGs for review. Not part of the app.

    python -m app.staff.dashboard_shots

Writes one full-window shot and one per card at the 1440x900 reference, so the
grid and each card can be looked at separately. Signs in against `demo.db` as
the admin by default; pass `staff` for the counter view, which is the one that
proves the role gate hides the cards.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.staff import theme
from app.staff.context import DatabaseSelection, DatabaseTarget, StaffContext
from app.staff.metrics import REFERENCE_H, REFERENCE_W
from app.utils.fonts import load_fonts

DEFAULT_OUT = Path(r"C:\Users\Lily Ann\Desktop")
DEMO = "sqlite:///demo.db"
EMAIL = {
    "admin": "admin@rentdesk.local",
    "staff": "counter@rentdesk.local",
}


def build(role: str, out_dir: Path, tag: str) -> None:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    load_fonts()
    app.setStyleSheet(theme.load_stylesheet())

    context = StaffContext(
        DatabaseSelection(url=DEMO, target=DatabaseTarget.DEMO, label="demo")
    )

    from app.services.auth_service import sign_in
    from app.staff.context import StaffUser

    # Snapshot inside the session, exactly as `LoginView.attempt_sign_in` does.
    # Reading the row after the read session closes raises DetachedInstanceError
    # because `reading()` rolls back, and rollback expires every attribute.
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
    # The shell owns the refresh, and this script stands in for the shell rather
    # than subclassing it -- so the shot has to ask for the data explicitly. A
    # page that has only been `show()`n renders its empty widgets, which is how
    # an admin and a staff shot of the same build can come out byte-identical.
    page.refresh()
    QTest.qWait(400)
    app.processEvents()

    out_dir.mkdir(parents=True, exist_ok=True)
    page.grab().save(str(out_dir / f"dash_{tag}_full.png"))

    print(
        f"{tag}: {identity.role}, cards visible={page.grid.isVisible()}, "
        f"strip visible={page.strip.isVisible()}"
    )

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
