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

    #: What the money is: the rental charges, or the refundable security
    #: deposit held against damage/late return. Deposits are absorbed credit,
    #: never revenue -- see payment_service.booking_balance. The server
    #: default keeps raw-SQL inserts and table rebuilds honest: anything
    #: written before the column existed was rental money.
    kind = Column(Enum('rental', 'deposit'), nullable=False, default='rental', server_default='rental')

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
