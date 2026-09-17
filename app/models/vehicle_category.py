from sqlalchemy import Column, Integer, Enum, DECIMAL
from app.database import Base


class Vehicle_Category(Base):
    __tablename__ = "VEHICLE_CATEGORY"

    category_id = Column(Integer, primary_key=True, autoincrement=True)
    category_name = Column(Enum('Car', 'Motorcycle', 'Truck'), nullable=False)
    base_rate_multiplier = Column(DECIMAL(10, 2), nullable=False)