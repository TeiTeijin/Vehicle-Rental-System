from sqlalchemy import Column, Integer, String, Enum, Date, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class Users(Base):
    __tablename__ = "USERS"

    user_id = Column(Integer, primary_key=True, autoincrement=True)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, unique=True)
    phone = Column(String(255))
    password_hash = Column(String(255), nullable=False)
    address = Column(String(255), nullable=False)
    license_number = Column(String(12), nullable=False, unique=True)
    license_expiry = Column(Date, nullable=False)
    role = Column(Enum('customer', 'staff', 'admin'), nullable=False, default='customer')
    birthdate = Column(Date, nullable=True)
    emergency_contact = Column(String(255), nullable=True)
    #: Where the record came from. Walk-ins may never log in.
    source = Column(Enum('walk_in', 'online'), nullable=True)
    #: Deactivate instead of delete: bookings keep their FK forever.
    is_active = Column(Boolean, nullable=False, default=True)
    #: Staff flag / blacklist. Warns loudly in the wizard with a required
    #: override note; never silently blocks except via eligibility rules.
    flag_reason = Column(String(255), nullable=True)
    flagged_at = Column(DateTime, nullable=True)
    flagged_by = Column(Integer, ForeignKey("USERS.user_id"), nullable=True)
    #: Disk paths to licence front/back copies (never BLOBs in the DB).
    licence_front_path = Column(String(255), nullable=True)
    licence_back_path = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False)

    bookings = relationship("Booking", back_populates="user", foreign_keys="Booking.user_id")
    inspections = relationship("Inspection_Report", back_populates="inspector")
    payments_recorded = relationship("Payment", back_populates="recorder")
    customer_notes = relationship(
        "Customer_Note", back_populates="customer",
        foreign_keys="Customer_Note.customer_id",
    )