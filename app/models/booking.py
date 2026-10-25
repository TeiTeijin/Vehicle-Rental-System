from sqlalchemy import Column, Integer, String, Enum, ForeignKey, DECIMAL, Date, DateTime
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

    #: Staff member who took the booking (audit trail).
    created_by = Column(Integer, ForeignKey("USERS.user_id"), nullable=True)

    #: Free-text reason the booking was cancelled.
    cancel_reason = Column(String(255), nullable=True)

    #: Flag override audit trail: staff-typed reason for proceeding past a
    #: customer flag, plus who/when. NULL means no flag was overridden.
    flag_override_note = Column(String(255), nullable=True)
    flag_override_by = Column(Integer, ForeignKey("USERS.user_id"), nullable=True)
    flag_override_at = Column(DateTime, nullable=True)

    #: Walk-in vs online; NULL means never recorded, not walk-in.
    channel = Column(Enum('walk_in', 'online'), nullable=True)

    # -- customer app (online) booking fields -------------------------------
    #: Pickup/return with the clock time the customer chose. `start_date` /
    #: `end_date` stay authoritative for the counter and keep the two apps in
    #: step: they are always the date parts of these two.
    pickup_dt = Column(DateTime, nullable=True)
    return_dt = Column(DateTime, nullable=True)
    pickup_branch_id = Column(Integer, ForeignKey("BRANCH.branch_id"), nullable=True)
    return_branch_id = Column(Integer, ForeignKey("BRANCH.branch_id"), nullable=True)

    #: How the customer paid (NULL for a counter booking).
    payment_method = Column(Enum('cash', 'card', 'gcash'), nullable=True)
    #: Last four digits only. The full number and the CVV are never stored.
    card_last4 = Column(String(4), nullable=True)
    #: Short code quoted to the customer ("RW-7QK4ZP").
    reference_code = Column(String(16), nullable=True)

    #: Price breakdown behind `total_cost`, which stays the grand total so
    #: every counter-side balance calculation keeps working unchanged.
    subtotal = Column(DECIMAL(10, 2), nullable=True)
    insurance_fee = Column(DECIMAL(10, 2), nullable=True)
    service_fee = Column(DECIMAL(10, 2), nullable=True)
    vat = Column(DECIMAL(10, 2), nullable=True)
    total = Column(DECIMAL(10, 2), nullable=True)

    user = relationship("Users", back_populates="bookings", foreign_keys=[user_id])
    vehicle = relationship("Vehicle", back_populates="bookings")
    pickup_branch = relationship("Branch", foreign_keys=[pickup_branch_id])
    return_branch = relationship("Branch", foreign_keys=[return_branch_id])

    #: One-to-many: a booking is settled in several parts.
    payments = relationship("Payment", back_populates="booking")
    inspections = relationship("Inspection_Report", back_populates="booking")
    penalties = relationship("Penalty", back_populates="booking")
    creator = relationship("Users", foreign_keys=[created_by])
