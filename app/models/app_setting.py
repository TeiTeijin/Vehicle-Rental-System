from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from app.database import Base


class App_Setting(Base):
    __tablename__ = "APP_SETTINGS"

    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=False, default="")
    updated_by = Column(Integer, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)
