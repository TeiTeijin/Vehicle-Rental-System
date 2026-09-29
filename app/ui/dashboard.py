from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFontDatabase, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

# VS Code's "Run Python File" button (and `python app/ui/dashboard.py`) runs
# this file *by path*, which puts `app/ui` on sys.path instead of the project
# root, and every `app.*` import below then fails with
# `ModuleNotFoundError: No module named 'app'`. Running it as a module
# (`python -m app.ui.dashboard`, which is what a launch.json with
# "module": "app.ui.dashboard" does) has the root already in place. This puts
# the root back for the by-path case only, so both ways of launching work.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.ui.hero import HeroCarousel

QSS_PATH = Path(__file__).with_name("styles.qss")
FONTS_DIR = Path(__file__).with_name("fonts")
ICONS_DIR = Path(__file__).with_name("icons")

NAV_HEIGHT = 72
NAV_ITEMS = ("Vehicles", "Bookings", "Payments", "Notification")
NAV_SPACING = 40
ACTION_SPACING = 24
AUTH_LABEL = "Sign In/Sign Up"
AUTH_HEIGHT = 40
AUTH_ICON_SIZE = 34
AUTH_TOGGLE_KEY = "Ctrl+L"


def load_fonts() -> None:
    for font_file in FONTS_DIR.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(font_file))


def load_styles() -> str:
    return QSS_PATH.read_text(encoding="utf-8")


def _section() -> QWidget:
    holder = QWidget()
    holder.setObjectName("navSection")
    return holder


class AuthButton(QPushButton):
    def __init__(self, authenticated: bool = False) -> None:
        super().__init__()
        self.setObjectName("authButton")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(AUTH_HEIGHT)
        self.clicked.connect(self._on_clicked)
        self.set_authenticated(authenticated)

    def sizeHint(self) -> QSize:
        if self.property("authenticated"):
            return QSize(AUTH_HEIGHT, AUTH_HEIGHT)
        return super().sizeHint()

    def set_authenticated(self, authenticated: bool) -> None:
        if authenticated:
            self.setText("")
            self.setIcon(QIcon(QPixmap(str(ICONS_DIR / "account.png"))))
            self.setIconSize(QSize(AUTH_ICON_SIZE, AUTH_ICON_SIZE))
        else:
            self.setText(AUTH_LABEL)
            self.setIcon(QIcon())
            self.setIconSize(QSize(0, 0))

        self.setProperty("authenticated", authenticated)
        self.style().unpolish(self)
        self.style().polish(self)
        self.updateGeometry()

    @staticmethod
    def _on_clicked() -> None:
        print("auth -> sign in / sign up")


class NavBar(QFrame):
    def __init__(self, authenticated: bool = False) -> None:
        super().__init__()
        self.setObjectName("navbar")
        self.setFixedHeight(NAV_HEIGHT)

        self.auth_button = AuthButton(authenticated)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(28, 0, 28, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_logo(), 1)
        layout.addWidget(self._build_nav_group(), 1)
        layout.addWidget(self._build_actions(), 1)

    def set_authenticated(self, authenticated: bool) -> None:
        self.auth_button.set_authenticated(authenticated)

    def _build_logo(self) -> QWidget:
        holder = _section()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)

        logo = QLabel("Logo")
        logo.setObjectName("logo")
        row.addWidget(logo, 0, Qt.AlignLeft | Qt.AlignVCenter)
        row.addStretch(1)
        return holder

    def _build_nav_group(self) -> QWidget:
        holder = _section()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(NAV_SPACING)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for index, label in enumerate(NAV_ITEMS):
            button = QPushButton(label)
            button.setObjectName("navItem")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.toggled.connect(
                lambda checked, name=label: self._on_nav_toggled(name, checked)
            )
            self._group.addButton(button, index)
            row.addWidget(button)

        return holder

    def _build_actions(self) -> QWidget:
        holder = _section()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(ACTION_SPACING)

        row.addStretch(1)
        row.addWidget(self._build_icon_button("search.png", 18))
        row.addWidget(self.auth_button)
        return holder

    @staticmethod
    def _build_icon_button(filename: str, size: int) -> QToolButton:
        button = QToolButton()
        button.setObjectName("navIcon")
        button.setFixedSize(QSize(size, size))
        button.setIconSize(QSize(size, size))
        button.setIcon(QIcon(QPixmap(str(ICONS_DIR / filename))))
        button.setStyleSheet("QToolButton { background: transparent; border: none; }")
        button.setCursor(Qt.PointingHandCursor)
        return button

    @staticmethod
    def _on_nav_toggled(name: str, checked: bool) -> None:
        if checked:
            print(f"nav -> {name}")


class DashboardWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Vehicle Rental System")
        self.resize(1280, 800)
        self.setStyleSheet(load_styles())

        self.navbar = NavBar()
        self.hero = HeroCarousel()
        self.content = self._build_content(self.hero)
        self._authenticated = False

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.navbar)
        root.addWidget(self.content, 1)

        self.setCentralWidget(central)

        self._auth_toggle = QShortcut(QKeySequence(AUTH_TOGGLE_KEY), self)
        self._auth_toggle.activated.connect(self.toggle_authenticated_debug)

    @staticmethod
    def _build_content(hero: QWidget) -> QScrollArea:
        """The hero is taller than the viewport, so it scrolls vertically."""
        area = QScrollArea()
        area.setObjectName("heroScroll")
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(hero)
        return area

    def set_authenticated(self, authenticated: bool) -> None:
        self._authenticated = authenticated
        self.navbar.set_authenticated(authenticated)

    def toggle_authenticated_debug(self) -> None:
        self.set_authenticated(not self._authenticated)
        print(f"debug: authenticated={self._authenticated} ({AUTH_TOGGLE_KEY})")


def main() -> None:
    app = QApplication(sys.argv)
    load_fonts()
    window = DashboardWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
