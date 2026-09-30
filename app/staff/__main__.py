"""Entry point for the staff application.

    python -m app.staff                 # live database (DATABASE_URL)
    python -m app.staff --demo          # demo.db, with a banner saying so
    python -m app.staff --demo-file x.db

The default is the live database. That is a deliberate asymmetry with the
demo seeder, which defaults to a scratch file: the staff app is a tool the
branch uses, and the realistic accident is someone reaching for `--demo`
during a demo and then reconciling against it, not someone opening the real
app and being unable to. The banner and the window title both say DEMO when
the flag is used.
"""

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

    # `--demo-file path` is the same intent as `--demo`, spelled out. Accepting
    # both means nobody has to remember which one carries the path.
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
    app.setApplicationName("RentDesk Staff")

    # The stylesheet names the bundled family; without this the widgets render
    # in whatever Qt picks instead. Needs the QApplication above to exist.
    load_fonts()

    context = StaffContext(selection)
    try:
        shell = StaffShell(context)
    except Exception as exc:
        # A bad DATABASE_URL, or a schema that has not been migrated, fails
        # here -- before any window exists. Say so plainly rather than
        # showing a window that errors on every page.
        print(f"Could not start RentDesk Staff: {exc}", file=sys.stderr)
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
