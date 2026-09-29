"""Tests for the DATABASE_URL normalisation in app.config.

_build_database_url() rewrites a user-supplied URL before SQLAlchemy sees it:
it forces the pymysql driver and turns Aiven's `?ssl=ca.pem` into an absolute
`?ssl_ca=<path>`. It also has to survive SQLite URLs, which carry no authority
component -- the case urllib's urlunsplit() silently mangles.
"""

import pytest
from sqlalchemy.engine import make_url
from urllib.parse import urlencode

from app.config import BASE_DIR, _build_database_url


@pytest.mark.parametrize(
    "raw",
    [
        "sqlite://",
        "sqlite:///:memory:",
        "sqlite:///rental.db",
        "sqlite:///demo.db",
        "mysql://user:pw@host:24893/defaultdb",
        "mysql+pymysql://user:pw@host:24893/defaultdb?ssl=ca.pem",
    ],
)
def test_result_is_always_parseable(raw):
    """Regression: urlunsplit() drops the '//' when netloc is empty, which
    turned 'sqlite:///rental.db' into 'sqlite:/rental.db' -- unparseable."""
    make_url(_build_database_url(raw))


def test_missing_url_falls_back_to_local_sqlite():
    assert _build_database_url(None) == "sqlite:///rental.db"
    assert _build_database_url("") == "sqlite:///rental.db"


def test_sqlite_path_survives_intact():
    assert _build_database_url("sqlite:///demo.db") == "sqlite:///demo.db"


def test_mysql_scheme_is_forced_to_pymysql():
    built = _build_database_url("mysql://u:p@host:3306/db")
    assert built.startswith("mysql+pymysql://")


def test_explicit_pymysql_scheme_is_left_alone():
    built = _build_database_url("mysql+pymysql://u:p@host:3306/db")
    assert built.startswith("mysql+pymysql://")


def test_non_mysql_scheme_is_preserved():
    built = _build_database_url("postgresql://u:p@host:5432/db")
    assert built.startswith("postgresql://")


def test_aiven_ssl_param_becomes_an_absolute_ssl_ca_path():
    built = _build_database_url("mysql://u:p@host:24893/db?ssl=ca.pem")
    assert "ssl=" not in built
    expected = urlencode({"ssl_ca": str(BASE_DIR / "ca.pem")})
    assert built.endswith("?" + expected)


def test_other_query_params_are_preserved():
    built = _build_database_url("mysql://u:p@host:3306/db?charset=utf8mb4")
    assert "charset=utf8mb4" in built
