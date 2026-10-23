"""Sign-up step 2: PSGC address pair, street line and licence number."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.models import PsgcCityMunicipality, PsgcProvince, Users
from app.services import auth_service, customer_service, location_service
from app.services.errors import ConflictError, ValidationError


def _seed_ilocos(session) -> None:
    session.add(
        PsgcProvince(
            code="012800000", name="Ilocos Norte", region_code="010000000"
        )
    )
    session.add_all(
        [
            PsgcCityMunicipality(
                code="012801000",
                name="Adams",
                kind="Mun",
                region_code="010000000",
                province_code="012800000",
            ),
            PsgcCityMunicipality(
                code="012810000",
                name="City of Laoag",
                kind="City",
                region_code="010000000",
                province_code="012800000",
            ),
            PsgcCityMunicipality(
                code="012815000",
                name="Pagudpud",
                kind="Mun",
                region_code="010000000",
                province_code="012800000",
            ),
        ]
    )
    session.flush()


def _register(session, **overrides):
    fields = {
        "full_name": "Maria Santos",
        "email": "maria@example.com",
        "phone": "09171234567",
        "password": "Strong-pass1!",
        "street_address": "123 Mabini St.",
        "province_key": "012800000",
        "city_municipality_code": "012801000",
        "license_number": "D03-1234-567",
    }
    fields.update(overrides)
    return auth_service.register_customer(session, **fields)


# ---------------------------------------------------------------------------
# Pure validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("D03-1234-567", "D03-1234-567"),
        ("d031234567", "D03-1234-567"),
        ("D03 1234 567", "D03-1234-567"),
        (" d03 - 1234 - 567 ", "D03-1234-567"),
    ],
)
def test_canonical_licence_accepts_typed_variants(typed, expected):
    assert customer_service.canonical_licence(typed) == expected


@pytest.mark.parametrize(
    "typed",
    ["", None, "random word", "D03-1234-56", "D03-1234-5678", "1234-567"],
)
def test_canonical_licence_rejects_anything_else(typed):
    assert customer_service.canonical_licence(typed) is None


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("123 Mabini St.", True),
        ("Unit 4, 12 Rizal Avenue", True),
        ("Mabini St.", False),  # no house/unit number
        ("12345", False),  # no street name
        ("   ", False),
    ],
)
def test_street_address_needs_a_number_and_a_name(typed, expected):
    assert auth_service.is_valid_street_address(typed) is expected


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("09171234567", True),
        (" 09171234567 ", True),
        ("0917123456", False),  # only 10 digits
        ("091712345678", False),  # 12 digits
        ("+639171234567", False),  # country code is not accepted
        ("08171234567", False),  # must begin with 09
        ("0917 123 4567", False),  # digits only
        ("", False),
        (None, False),
    ],
)
def test_mobile_number_is_exactly_11_digits_starting_09(typed, expected):
    assert auth_service.is_valid_mobile(typed) is expected


# ---------------------------------------------------------------------------
# PSGC lookups
# ---------------------------------------------------------------------------


def test_provinces_include_metro_manila_and_independent_cities(session):
    session.add(
        PsgcProvince(
            code="012800000", name="Ilocos Norte", region_code="010000000"
        )
    )
    session.add_all(
        [
            # NCR has no province row: its cities hang off the region.
            PsgcCityMunicipality(
                code="133900000",
                name="City of Manila",
                kind="City",
                region_code="130000000",
                province_code=None,
            ),
            PsgcCityMunicipality(
                code="129804000",
                name="City of Cotabato",
                kind="City",
                region_code="120000000",
                province_code=None,
            ),
        ]
    )
    session.flush()

    options = location_service.province_options(session)
    keys = {option.key: option for option in options}

    assert "012800000" in keys
    assert keys["NCR"].name == "Metro Manila"
    assert keys["NCR"].province_code is None
    assert keys["CITY:129804000"].name == "City of Cotabato (independent city)"

    ncr_cities = location_service.municipalities(session, "NCR")
    assert [city.name for city in ncr_cities] == ["City of Manila"]
    assert location_service.municipalities(session, "012800000") == []


def test_independent_city_resolves_without_a_province(session):
    session.add(
        PsgcCityMunicipality(
            code="099701000",
            name="City of Isabela",
            kind="City",
            region_code="090000000",
            province_code=None,
        )
    )
    session.flush()

    options = {option.key: option for option in location_service.province_options(session)}
    assert "CITY:099701000" in options

    cities = location_service.municipalities(session, "CITY:099701000")
    assert [city.name for city in cities] == ["City of Isabela"]

    location = location_service.resolve(session, "CITY:099701000", "099701000")
    assert location.province_code is None
    assert location.province_name == ""
    assert location.address_line("5 Rizal St.") == "5 Rizal St., City of Isabela"


def test_resolve_metro_manila_keeps_province_code_empty(session):
    session.add(
        PsgcCityMunicipality(
            code="133900000",
            name="City of Manila",
            kind="City",
            region_code="130000000",
            province_code=None,
        )
    )
    session.flush()

    location = location_service.resolve(session, "NCR", "133900000")

    assert location.province_code is None
    assert location.province_name == "Metro Manila"
    assert location.address_line("7 Mabini St.") == (
        "7 Mabini St., City of Manila, Metro Manila"
    )


# ---------------------------------------------------------------------------
# register_customer
# ---------------------------------------------------------------------------


def test_register_customer_stores_psgc_codes_and_address(session):
    _seed_ilocos(session)

    user = _register(session)

    assert user.address == "123 Mabini St., Adams, Ilocos Norte"
    assert user.phone == "09171234567"
    assert user.street_address == "123 Mabini St."
    assert user.region_code == "010000000"
    assert user.province_code == "012800000"
    assert user.city_municipality_code == "012801000"
    assert user.barangay_code is None
    # The typed lowercase/no-dash form is stored canonically.
    user2 = _register(
        session,
        email="jose@example.com",
        city_municipality_code="012810000",
        license_number="d031234568",
    )
    assert user2.license_number == "D03-1234-568"
    assert user2.address == "123 Mabini St., City of Laoag, Ilocos Norte"


def test_register_customer_rejects_a_city_outside_the_province(session):
    _seed_ilocos(session)
    session.add(
        PsgcProvince(
            code="072200000", name="Cebu", region_code="070000000"
        )
    )
    session.add(
        PsgcCityMunicipality(
            code="072201000",
            name="Cebu City",
            kind="City",
            region_code="070000000",
            province_code="072200000",
        )
    )
    session.flush()

    with pytest.raises(ValidationError) as excinfo:
        _register(session, city_municipality_code="072201000")

    assert excinfo.value.field == "city_municipality_code"


def test_register_customer_rejects_a_random_province(session):
    _seed_ilocos(session)

    with pytest.raises(ValidationError) as excinfo:
        _register(session, province_key="Not A Province")

    assert excinfo.value.field == "province_code"


def test_register_customer_rejects_bad_street_and_licence(session):
    _seed_ilocos(session)

    with pytest.raises(ValidationError) as excinfo:
        _register(session, street_address="Mabini St.")
    assert excinfo.value.field == "street_address"

    with pytest.raises(ValidationError) as excinfo:
        _register(session, license_number="random word")
    assert excinfo.value.field == "license_number"


@pytest.mark.parametrize("phone", ["+639171234567", "0917123456", "08171234567"])
def test_register_customer_rejects_mobile_outside_the_09_format(session, phone):
    _seed_ilocos(session)

    with pytest.raises(ValidationError) as excinfo:
        _register(session, phone=phone)
    assert excinfo.value.field == "phone"


def test_register_customer_rejects_a_duplicate_licence(session):
    _seed_ilocos(session)
    _register(session)

    with pytest.raises(ConflictError):
        _register(session, email="other@example.com", license_number="D031234567")


def test_register_customer_rejects_a_staff_licence(session):
    """The UNIQUE column is global, so staff rows collide during sign-up too."""
    _seed_ilocos(session)
    session.add(
        Users(
            full_name="Staff Member",
            email="staff@example.com",
            password_hash="h",
            address="1 Office St.",
            license_number="D03-9999-000",
            license_expiry=date(2030, 1, 1),
            role="staff",
            created_at=datetime(2026, 1, 1, 9, 0),
        )
    )
    session.flush()

    with pytest.raises(ConflictError):
        _register(session, license_number="d039999000")
