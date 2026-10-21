from datetime import datetime, time

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Time

from app.database import Base


def clock_label(value: time | None) -> str:
    """`time(7, 0)` -> '7:00 AM'. Display only, never stored."""
    if value is None:
        return ""
    hour = value.hour % 12 or 12
    meridiem = "AM" if value.hour < 12 else "PM"
    return f"{hour}:{value.minute:02d} {meridiem}"


class Branch(Base):
    """A pickup/return counter and the hours it is open.

    Times are naive local clock times on a single day: a branch closes the
    same day it opens, so a rental may never start or end outside
    ``[open_time, close_time]`` (see app/services/branch_service.py).
    """

    __tablename__ = "BRANCH"

    branch_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False)
    address = Column(String(255), nullable=False)
    open_time = Column(Time, nullable=False, default=time(7, 0))
    close_time = Column(Time, nullable=False, default=time(21, 0))
    #: Retire instead of delete: bookings keep the FK forever.
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    @property
    def hours_label(self) -> str:
        """'7:00 AM - 9:00 PM', as shown under the branch combos."""
        return f"{clock_label(self.open_time)} - {clock_label(self.close_time)}"

    @property
    def display_name(self) -> str:
        return f"{self.name} \u2014 {self.address}" if self.address else self.name
