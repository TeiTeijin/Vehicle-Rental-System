from datetime import datetime

from sqlalchemy import Column, Integer, String, Enum, ForeignKey, DECIMAL, DateTime
from sqlalchemy.orm import relationship
from app.database import Base


class Payment(Base):
    __tablename__ = "PAYMENT"

    payment_id = Column(Integer, primary_key=True, autoincrement=True)
    booking_id = Column(Integer, ForeignKey("BOOKING.booking_id"), nullable=False)
    amount = Column(DECIMAL(10, 2), nullable=False)
    method = Column(Enum('cash', 'card', 'gcash'), nullable=False, default='cash')

    #: `pending`/`failed` rows have no paid_at.
    status = Column(Enum('pending', 'paid', 'failed', 'refunded'), nullable=False, default='pending')
    paid_at = Column(DateTime, nullable=True)

    #: Staff member who took the money (shift reconciliation).
    recorded_by = Column(Integer, ForeignKey("USERS.user_id"), nullable=True)

    # GCash ref, card auth code, or the printed receipt number.
    reference_no = Column(String(64), nullable=True)

    note = Column(String(255), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.now)

    booking = relationship("Booking", back_populates="payments")
    recorder = relationship("Users", foreign_keys=[recorded_by])
