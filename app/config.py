import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _build_database_url(raw: str | None) -> str:
    if not raw:
        return "sqlite:///rental.db"

    parts = urlsplit(raw)
    scheme = "mysql+pymysql" if parts.scheme.startswith("mysql") else parts.scheme

    query = []
    for key, value in parse_qsl(parts.query):
        if key == "ssl":
            key = "ssl_ca"
            value = str(BASE_DIR / value)
        query.append((key, value))

    search = urlencode(query)

    if parts.netloc:
        return urlunsplit((scheme, parts.netloc, parts.path, search, ""))

    # urlunsplit() drops the "//" authority marker whenever netloc is empty, so
    # rebuilding a SQLite URL through it turns "sqlite:///rental.db" into
    # "sqlite:/rental.db" -- a string SQLAlchemy refuses to parse. SQLite URLs
    # carry everything in the path with no authority, so emit them by hand.
    return f"{scheme}://{parts.path}{'?' + search if search else ''}"


DATABASE_URL = _build_database_url(os.getenv("DATABASE_URL"))
CI_API_KEY = os.getenv("CI_API_KEY", "")
CI_API_SECRET = os.getenv("CI_API_SECRET", "")