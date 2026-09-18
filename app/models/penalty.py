from sqlalchemy import Column, Integer, String, Enum, ForeignKey, DECIMAL, DateTime
from sqlalchemy.orm import relationship
from app.database import Base


class Penalty(Base):
    __tablename__ = "PENALTY"

    penalty_id = Column(Integer, primary_key=True, autoincrement=True)
    booking_id = Column(Integer, ForeignKey("BOOKING.booking_id"), nullable=False)
    penalty_type = Column(Enum('late_return', 'damage', 'cleaning', 'other'), nullable=False)
    amount = Column(DECIMAL(10, 2), nullable=False)
    description = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False)

    booking = relationship("Booking", back_populates="penalties")