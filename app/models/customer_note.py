from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.database import Base


class Customer_Note(Base):
    """Internal staff notes on a customer, with authorship."""

    __tablename__ = "CUSTOMER_NOTE"

    note_id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("USERS.user_id"), nullable=False)
    author_id = Column(Integer, ForeignKey("USERS.user_id"), nullable=True)
    note = Column(String(1000), nullable=False)
    created_at = Column(DateTime, nullable=False)

    customer = relationship(
        "Users", back_populates="customer_notes",
        foreign_keys=[customer_id],
    )
