from sqlalchemy import Column, Integer, String, Text, ForeignKey, Boolean, DateTime
from sqlalchemy.orm import relationship
from app.database import Base


class Vehicle_Media(Base):
    __tablename__ = "VEHICLE_MEDIA"

    media_id = Column(Integer, primary_key=True, autoincrement=True)
    vehicle_id = Column(Integer, ForeignKey("VEHICLE.vehicle_id"), nullable=False)
    source = Column(String(255), nullable=False)
    view_angle = Column(String(255), nullable=False)
    # NOTE: signed URLs embed the API key -- treat as credential data.
    # Text rather than VARCHAR(255): CarImages signs a long query string.
    image_url = Column(Text, nullable=False)
    model_3d_url = Column(Text, nullable=False)
    is_watermarked = Column(Boolean, default=False)
    cached_at = Column(DateTime, nullable=False)

    vehicle = relationship("Vehicle", back_populates="media")