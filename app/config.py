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

    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), ""))


DATABASE_URL = _build_database_url(os.getenv("DATABASE_URL"))
CI_API_KEY = os.getenv("CI_API_KEY", "")
CI_API_SECRET = os.getenv("CI_API_SECRET", "")