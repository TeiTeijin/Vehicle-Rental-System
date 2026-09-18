from sqlalchemy import Column, Integer, String, Enum, ForeignKey, DECIMAL, DateTime
from sqlalchemy.orm import relationship
from app.database import Base


class Vehicle(Base):
    __tablename__ = "VEHICLE"

    vehicle_id = Column(Integer, primary_key=True, autoincrement=True)
    category_id = Column(Integer, ForeignKey("VEHICLE_CATEGORY.category_id"), nullable=False)
    make = Column(String(255), nullable=False)
    model = Column(String(255), nullable=False)
    year = Column(Integer, nullable=False)
    plate_number = Column(String(7), nullable=False, unique=True)
    daily_rate = Column(DECIMAL(10, 2), nullable=False)
    mileage = Column(Integer, nullable=False)
    status = Column(Enum('available', 'reserved', 'rented', 'maintenance'), nullable=False, default='available')
    created_at = Column(DateTime, nullable=False)

    category = relationship("Vehicle_Category", back_populates="vehicles")
    bookings = relationship("Booking", back_populates="vehicle")
    media = relationship("Vehicle_Media", back_populates="vehicle")
    maintenance_records = relationship("Maintenance_Record", back_populates="vehicle")