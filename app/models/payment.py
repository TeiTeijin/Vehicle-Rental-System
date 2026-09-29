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

    # pending / failed have no paid_at, so this used to be NOT NULL and was
    # unsatisfiable for two of the four statuses the same column allows.
    status = Column(Enum('pending', 'paid', 'failed', 'refunded'), nullable=False, default='pending')
    paid_at = Column(DateTime, nullable=True)

    # Who took the money. Without it a shift cannot be reconciled against the
    # drawer at the end of the day.
    recorded_by = Column(Integer, ForeignKey("USERS.user_id"), nullable=True)

    # GCash ref, card auth code, or the printed receipt number.
    reference_no = Column(String(64), nullable=True)

    note = Column(String(255), nullable=True)

    # Defaulted rather than required: this column did not exist until the staff
    # rebuild, and every pre-existing caller that constructs a Payment omits it.
    # A NOT NULL column with no default is a trap for the next writer.
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    booking = relationship("Booking", back_populates="payments")
    recorder = relationship("Users", foreign_keys=[recorded_by])
