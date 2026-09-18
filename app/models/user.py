from sqlalchemy import Column, Integer, String, Enum, Date, DateTime
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
    created_at = Column(DateTime, nullable=False)

    bookings = relationship("Booking", back_populates="user")
    inspections = relationship("Inspection_Report", back_populates="inspector")