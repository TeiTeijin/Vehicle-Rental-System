from sqlalchemy import Column, Integer, Enum, ForeignKey, DECIMAL, Date, DateTime
from app.database import Base


class Booking(Base):
    __tablename__ = "BOOKING"

    booking_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("USERS.user_id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("VEHICLE.vehicle_id"), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    actual_return_date = Column(Date, nullable=False)
    total_cost = Column(DECIMAL(10, 2), nullable=False)
    status = Column(Enum('pending', 'confirmed', 'ongoing', 'completed', 'cancelled'), nullable=False, default='pending')
    created_at = Column(DateTime, nullable=False)