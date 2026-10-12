from sqlalchemy import Column, Integer, String, ForeignKey, DECIMAL, DateTime, Boolean
from sqlalchemy.orm import relationship
from app.database import Base


class Vehicle_Rate_Log(Base):
    """Audit trail for daily-rate changes.

    Extensions recompute at current rates by design, so without a log two
    identical extensions can cost different amounts with no explanation.
    Every per-vehicle and bulk rate write lands here in the same commit.
    """

    __tablename__ = "VEHICLE_RATE_LOG"

    rate_log_id = Column(Integer, primary_key=True, autoincrement=True)
    vehicle_id = Column(Integer, ForeignKey("VEHICLE.vehicle_id"), nullable=False)
    old_rate = Column(DECIMAL(10, 2), nullable=False)
    new_rate = Column(DECIMAL(10, 2), nullable=False)
    changed_by = Column(Integer, ForeignKey("USERS.user_id"), nullable=True)
    changed_at = Column(DateTime, nullable=False)
    is_bulk = Column(Boolean, nullable=False, default=False)
    note = Column(String(255), nullable=True)

    vehicle = relationship("Vehicle", back_populates="rate_logs")
