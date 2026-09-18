from sqlalchemy import Column, Integer, String, Enum, ForeignKey, DECIMAL, Date
from sqlalchemy.orm import relationship
from app.database import Base


class Maintenance_Record(Base):
    __tablename__ = "MAINTENANCE_RECORD"

    maintenance_id = Column(Integer, primary_key=True, autoincrement=True)
    vehicle_id = Column(Integer, ForeignKey("VEHICLE.vehicle_id"), nullable=False)
    description = Column(String(255), nullable=False)
    cost = Column(DECIMAL(10, 2), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    status = Column(Enum('scheduled', 'ongoing', 'completed'), nullable=False, default='scheduled')

    vehicle = relationship("Vehicle", back_populates="maintenance_records")