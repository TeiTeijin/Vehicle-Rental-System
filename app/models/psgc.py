"""PSGC reference rows behind the customer address form.

The live database ships the full Philippine Standard Geographic Code tables
(regions, provinces, cities/municipalities, barangays). The sign-up form only
needs the top two levels, so only those two are mapped here; barangays are
left in the database untouched.
"""

from sqlalchemy import Column, String
from app.database import Base


class PsgcProvince(Base):
    __tablename__ = "PSGC_PROVINCES"

    code = Column(String(9), primary_key=True)
    name = Column(String(255), nullable=False)
    region_code = Column(String(9), nullable=False)
    island_group = Column(String(32), nullable=True)


class PsgcCityMunicipality(Base):
    __tablename__ = "PSGC_CITIES_MUNICIPALITIES"

    code = Column(String(9), primary_key=True)
    name = Column(String(255), nullable=False)
    #: "City" or "Mun" in the live PSGC seed.
    kind = Column(String(16), nullable=False)
    region_code = Column(String(9), nullable=False)
    #: NULL for Metro Manila cities and the independent cities.
    province_code = Column(String(9), nullable=True)
    district_code = Column(String(9), nullable=True)
