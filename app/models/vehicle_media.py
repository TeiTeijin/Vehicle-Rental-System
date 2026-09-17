from sqlalchemy import Column, Integer, String, ForeignKey, Boolean, DateTime
from app.database import Base


class Vehicle_Media(Base):
    __tablename__ = "VEHICLE_MEDIA"

    media_id = Column(Integer, primary_key=True, autoincrement=True)
    vehicle_id = Column(Integer, ForeignKey("VEHICLE.vehicle_id"), nullable=False)
    source = Column(String(255), nullable=False)
    view_angle = Column(String(255), nullable=False)
    image_url = Column(String(255), nullable=False)
    model_3d_url = Column(String(255), nullable=False)
    is_watermarked = Column(Boolean, default=False)
    created_at = Column(DateTime, nullable=False)