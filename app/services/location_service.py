"""Philippine provinces and cities/municipalities, straight from PSGC.

The live database already carries the full PSGC reference set
(`PSGC_PROVINCES`, `PSGC_CITIES_MUNICIPALITIES`). The sign-up form searches
those rows; the same module checks what the form submits, so a typed guess
can never become a stored address.

Two PSGC quirks the form has to smooth over:

* Metro Manila has no province row. Its 17 cities hang directly off the
  National Capital Region, so they are grouped under one "Metro Manila"
  choice (`NCR_KEY`).
* Isabela City and Cotabato City are independent cities: their rows have no
  province either. Each becomes its own single-city choice so nobody is
  unreachable from the dropdown.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import PsgcCityMunicipality, PsgcProvince
from app.services.errors import ValidationError

#: The key of the synthetic Metro Manila choice in the province dropdown.
NCR_KEY = "NCR"
NCR_REGION_CODE = "130000000"
NCR_NAME = "Metro Manila"

#: Prefix for an independent city's synthetic province choice.
INDEPENDENT_PREFIX = "CITY:"

#: Longest street line we accept; leaves room for city + province inside
#: USERS.address (VARCHAR(255)).
MAX_STREET_LENGTH = 180


@dataclass(frozen=True)
class ProvinceOption:
    """One row in the sign-up province dropdown."""

    key: str
    name: str
    region_code: str
    province_code: str | None


@dataclass(frozen=True)
class CityOption:
    """One row in the municipality/city dropdown."""

    code: str
    name: str
    kind: str
    region_code: str


@dataclass(frozen=True)
class Location:
    """A validated province + city pair, ready to store on `Users`."""

    region_code: str
    province_code: str | None
    province_name: str
    city_code: str
    city_name: str
    city_kind: str

    def address_line(self, street: str) -> str:
        """The stored `USERS.address`, e.g. '123 Mabini St., Batac, Ilocos Norte'."""
        parts = [" ".join((street or "").split()), self.city_name]
        if self.province_name:
            parts.append(self.province_name)
        return ", ".join(part for part in parts if part)


def province_options(session: Session) -> list[ProvinceOption]:
    """Every province, plus Metro Manila and the independent cities."""
    rows = session.query(PsgcProvince).order_by(PsgcProvince.name).all()
    options = [
        ProvinceOption(
            key=row.code,
            name=row.name,
            region_code=row.region_code,
            province_code=row.code,
        )
        for row in rows
    ]

    orphans = (
        session.query(PsgcCityMunicipality)
        .filter(PsgcCityMunicipality.province_code.is_(None))
        .order_by(PsgcCityMunicipality.name)
        .all()
    )
    if any(city.region_code == NCR_REGION_CODE for city in orphans):
        options.append(
            ProvinceOption(
                key=NCR_KEY,
                name=NCR_NAME,
                region_code=NCR_REGION_CODE,
                province_code=None,
            )
        )
    for city in orphans:
        if city.region_code == NCR_REGION_CODE:
            continue  # already covered by the Metro Manila group
        options.append(
            ProvinceOption(
                key=f"{INDEPENDENT_PREFIX}{city.code}",
                name=f"{city.name} (independent city)",
                region_code=city.region_code,
                province_code=None,
            )
        )

    options.sort(key=lambda option: option.name.lower())
    return options


def municipalities(session: Session, province_key: str) -> list[CityOption]:
    """Cities/municipalities belonging to one dropdown choice."""
    query = session.query(PsgcCityMunicipality)
    if province_key == NCR_KEY:
        query = query.filter(
            PsgcCityMunicipality.region_code == NCR_REGION_CODE,
            PsgcCityMunicipality.province_code.is_(None),
        )
    elif province_key.startswith(INDEPENDENT_PREFIX):
        city_code = province_key[len(INDEPENDENT_PREFIX):]
        query = query.filter(PsgcCityMunicipality.code == city_code)
    else:
        query = query.filter(PsgcCityMunicipality.province_code == province_key)

    return [
        CityOption(
            code=row.code,
            name=row.name,
            kind=row.kind,
            region_code=row.region_code,
        )
        for row in query.order_by(PsgcCityMunicipality.name).all()
    ]


def resolve(
    session: Session, province_key: str, city_code: str
) -> Location:
    """Validate a form pair and return the names/codes worth storing.

    Raises ValidationError with the field the form should highlight when the
    province or city is not an exact member of the PSGC lists.
    """
    option = next(
        (row for row in province_options(session) if row.key == province_key),
        None,
    )
    if option is None:
        raise ValidationError(
            "Choose a province from the list.", field="province_code"
        )

    city = next(
        (
            row
            for row in municipalities(session, province_key)
            if row.code == city_code
        ),
        None,
    )
    if city is None:
        raise ValidationError(
            "Choose a city or municipality from the list.",
            field="city_municipality_code",
        )

    province_name = ""
    if option.key == NCR_KEY:
        province_name = NCR_NAME
    elif option.province_code:
        province_name = option.name

    return Location(
        region_code=city.region_code,
        province_code=option.province_code,
        province_name=province_name,
        city_code=city.code,
        city_name=city.name,
        city_kind=city.kind,
    )
