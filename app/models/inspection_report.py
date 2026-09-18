from sqlalchemy import Column, Integer, String, Enum, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.database import Base


class Inspection_Report(Base):
    __tablename__ = "INSPECTION_REPORT"

    inspection_id = Column(Integer, primary_key=True, autoincrement=True)
    booking_id = Column(Integer, ForeignKey("BOOKING.booking_id"), nullable=False)
    inspected_by = Column(Integer, ForeignKey("USERS.user_id"), nullable=False)
    inspection_type = Column(Enum('pre-rental', 'post-rental'), nullable=False, default='pre-rental')
    mileage_reading = Column(Integer, nullable=False)
    fuel_level = Column(Enum('empty', 'quarter', 'half', 'three_quarter', 'full'), nullable=False)
    damage_notes = Column(String(255), nullable=True)
    photo_url = Column(String(255), nullable=False)
    inspected_at = Column(DateTime, nullable=False)

    booking = relationship("Booking", back_populates="inspections")
    inspector = relationship("Users", back_populates="inspections")