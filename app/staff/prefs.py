"""Per-PC preferences via QSettings (never the shared database).

Appearance / Behavior / Files / session prefs live here so each counter
PC keeps its own theme, scale, and export folder. Shared branch policy
(prices, deposits, defaults) lives in `settings_service` instead.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

ORG = "RentWheels"
APP = "Staff"

DEFAULTS: dict[str, object] = {
    # -- appearance --
    "appearance/theme": "light",          # light | dark | system
    "appearance/accent": "#1F4E5F",       # petrol default
    "appearance/font_scale": 1.0,
    "appearance/sidebar_collapsed": False,
    "appearance/reduce_motion": False,
    # -- behavior --
    "behavior/dashboard_refresh_ms": 30000,  # 0 = off
    "behavior/default_page": "dashboard",    # dashboard | new_rental
    "behavior/rows_per_page": 100,
    "behavior/date_format": "dd MMM yyyy",
    "behavior/currency": "₱",
    "behavior/confirm_cancel": True,
    "behavior/confirm_retire": True,
    "behavior/confirm_deactivate": True,
    "behavior/toast_ms": 3200,
    "behavior/auto_logout_mins": 0,       # 0 = off
    # -- files --
    "files/export_folder": "",
}

THEMES = ("light", "dark", "system")
DEFAULT_PAGES = ("dashboard", "new_rental")
DATE_FORMATS = ("dd MMM yyyy", "MM/dd/yyyy", "yyyy-MM-dd", "dd-MM-yyyy")
CURRENCIES = ("₱", "PHP ", "$")
REFRESH_CHOICES = (("Off", 0), ("15s", 15000), ("30s", 30000), ("60s", 60000))
ROWS_CHOICES = (25, 50, 100, 200)
LOGOUT_CHOICES = (("Off", 0), ("5 min", 5), ("15 min", 15), ("30 min", 30))


def _store() -> QSettings:
    return QSettings(ORG, APP)


def get(key: str):
    store = _store()
    default = DEFAULTS.get(key)
    value = store.value(key, default)
    # QSettings returns strings for everything when an .ini backend is
    # used; coerce back to the default's type.
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return value if value is not None else default


def set(key: str, value: object) -> None:
    _store().setValue(key, value)


def all_prefs() -> dict[str, object]:
    return {key: get(key) for key in DEFAULTS}


def dashboard_refresh_ms() -> int:
    try:
        return int(get("behavior/dashboard_refresh_ms"))
    except (TypeError, ValueError):
        return 30000


def rows_limit(default: int = 500) -> int:
    """Row cap for ledger queries: pref value, clamped sane."""
    try:
        value = int(get("behavior/rows_per_page"))
    except (TypeError, ValueError):
        return default
    return max(25, min(value, 500))


def export_folder(fallback: Path) -> Path:
    raw = str(get("files/export_folder") or "").strip()
    if raw:
        path = Path(raw)
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            return fallback
    return fallback


def currency_symbol() -> str:
    raw = str(get("behavior/currency") or "₱")
    return raw if raw in CURRENCIES else "₱"


def date_format() -> str:
    raw = str(get("behavior/date_format") or "dd MMM yyyy")
    return raw if raw in DATE_FORMATS else "dd MMM yyyy"
