from sqlalchemy import Column, Integer, Enum, ForeignKey, DECIMAL, Date, DateTime
from sqlalchemy.orm import relationship
from app.database import Base


class Booking(Base):
    __tablename__ = "BOOKING"

    booking_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("USERS.user_id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("VEHICLE.vehicle_id"), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    actual_return_date = Column(Date, nullable=True)
    total_cost = Column(DECIMAL(10, 2), nullable=False)
    status = Column(Enum('pending', 'confirmed', 'ongoing', 'completed', 'cancelled'), nullable=False, default='pending')
    created_at = Column(DateTime, nullable=False)

    user = relationship("Users", back_populates="bookings")
    vehicle = relationship("Vehicle", back_populates="bookings")
    payment = relationship("Payment", back_populates="booking", uselist=False)
    inspections = relationship("Inspection_Report", back_populates="booking")
    penalties = relationship("Penalty", back_populates="booking")