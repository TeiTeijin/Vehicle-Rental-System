from __future__ import annotations

import argparse
import sys

from app.staff.context import StaffContext, select_database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.staff", description=__doc__
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Open the local demo database instead of the live one.",
    )
    parser.add_argument(
        "--demo-file",
        metavar="PATH",
        help="Which demo database to open. Implies --demo.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        selection = select_database(
            demo=args.demo or bool(args.demo_file),
            demo_file=args.demo_file,
        )
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2

    from PySide6.QtWidgets import QApplication

    from app.staff.shell import StaffShell
    from app.utils.fonts import load_fonts

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("RentWheels Staff")

    # Needs the QApplication above to exist.
    load_fonts()

    context = StaffContext(selection)
    try:
        shell = StaffShell(context)
    except Exception as exc:
        # Fails before any window exists: report rather than show a broken shell.
        print(f"Could not start RentWheels Staff: {exc}", file=sys.stderr)
        if not selection.is_demo:
            print(
                "\nIf this is the staff schema migration:\n"
                "    python -m scripts.migrate_staff_schema --dry-run",
                file=sys.stderr,
            )
        context.dispose()
        return 2

    shell.show()
    try:
        return app.exec()
    finally:
        context.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
