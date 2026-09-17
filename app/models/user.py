from sqlalchemy import Column, Integer, String, Enum, Date, DateTime
from app.database import Base


class Users(Base):
    __tablename__ = "USERS"

    user_id = Column(Integer, primary_key=True, autoincrement=True)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, unique=True)
    phone = Column(String(255))
    passward_hash = Column(String(255), nullable=False)
    address = Column(String(255), nullable=False)
    linense_number = Column(String(12), nullable=False, unique=True)
    linense_expiry = Column(Date, nullable=False)
    role = Column(Enum('customer', 'staff', 'admin'), nullable=False, default='customer')
    created_at = Column(DateTime, nullable=False)