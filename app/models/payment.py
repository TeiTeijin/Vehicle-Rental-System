from sqlalchemy import Column, Integer, Enum, ForeignKey, DECIMAL, DateTime
from app.database import Base


class Payment(Base):
    __tablename__ = "PAYMENT"

    payment_id = Column(Integer, primary_key=True, autoincrement=True)
    booking_id = Column(Integer, ForeignKey("BOOKING.booking_id"), nullable=False)
    amount = Column(DECIMAL(10, 2), nullable=False)
    method = Column(Enum('cash', 'card', 'gcash'), nullable=False, default='cash')
    status = Column(Enum('pending', 'paid', 'failed', 'refunded'), nullable=False, default='pending')
    paid_at = Column(DateTime, nullable=False)