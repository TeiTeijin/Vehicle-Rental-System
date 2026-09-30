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

    # Which member of staff took this booking. An audit trail, not a
    # convenience: disputes about a rental start here.
    created_by = Column(Integer, ForeignKey("USERS.user_id"), nullable=True)

    # Why a booking was cancelled. "cancelled" on its own answers none of the
    # questions staff actually get asked about it.
    cancel_reason = Column(String(255), nullable=True)

    # Whether the rental was walked in at the counter or taken online. The
    # branch reports on both, and the split cannot be reconstructed from
    # anything else here: `created_by` says who took the booking, not where the
    # customer found us, and one member of staff takes both kinds.
    #
    # Nullable rather than defaulted, on purpose. This column did not exist
    # until the dashboard needed it, so every booking already on disk has no
    # value for it -- which is TRUE information (the branch was not recording
    # it), not a zero. Backfilling from nothing would invent history.
    channel = Column(Enum('walk_in', 'online'), nullable=True)

    user = relationship("Users", back_populates="bookings", foreign_keys=[user_id])
    vehicle = relationship("Vehicle", back_populates="bookings")

    # One-to-many, not one-to-one. A booking is settled in parts: a deposit on
    # the day it is taken, the balance on return, and sometimes a late fee on
    # top. A single `payment` attribute could only ever hold one of those.
    payments = relationship("Payment", back_populates="booking")
    inspections = relationship("Inspection_Report", back_populates="booking")
    penalties = relationship("Penalty", back_populates="booking")
    creator = relationship("Users", foreign_keys=[created_by])
