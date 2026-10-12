from sqlalchemy import Column, Integer, String, Enum, ForeignKey, DECIMAL, Date, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class Maintenance_Record(Base):
    """The service log for the fleet.

    A vehicle counts as in the shop while it has a record that is
    ``in_progress``, or ``scheduled`` with a start date that has arrived
    (see ``maintenance_service.sync_vehicle_status``) -- the status is
    derived from these rows, never typed separately in Fleet.
    """

    __tablename__ = "MAINTENANCE_RECORD"

    maintenance_id = Column(Integer, primary_key=True, autoincrement=True)
    vehicle_id = Column(Integer, ForeignKey("VEHICLE.vehicle_id"), nullable=False)
    description = Column(String(255), nullable=False)
    maintenance_type = Column(
        Enum(
            "routine",
            "repair",
            "tire_brake",
            "inspection_registration",
            "cleaning",
            "damage_repair",
            name="maintenance_type",
        ),
        nullable=False,
        default="routine",
        server_default="routine",
    )
    #: Final amount. Blank (NULL) until the work is done; `estimate_cost`
    #: carries the quote while a job is in progress.
    cost = Column(DECIMAL(10, 2), nullable=True)
    estimate_cost = Column(DECIMAL(10, 2), nullable=True)
    start_date = Column(Date, nullable=False)
    #: Blank (NULL) until the work is done.
    end_date = Column(Date, nullable=True)
    status = Column(
        Enum(
            "scheduled",
            "in_progress",
            "completed",
            "cancelled",
            name="maintenance_status",
        ),
        nullable=False,
        default="scheduled",
    )
    #: Odometer when the work was done (needed for mileage schedules).
    odometer = Column(Integer, nullable=True)
    #: Next service: a date, an odometer reading, or both.
    next_due_date = Column(Date, nullable=True)
    next_due_odometer = Column(Integer, nullable=True)
    #: Shop or mechanic who did the work.
    shop = Column(String(128), nullable=True)
    #: Staff member who logged the row (audit habit, like Payments).
    logged_by = Column(Integer, ForeignKey("USERS.user_id"), nullable=True)
    #: Repair caused by an incident: ties the cost back to the rental.
    booking_id = Column(Integer, ForeignKey("BOOKING.booking_id"), nullable=True)
    cancel_reason = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    vehicle = relationship("Vehicle", back_populates="maintenance_records")
    logger = relationship("Users", foreign_keys=[logged_by])
    booking = relationship("Booking", foreign_keys=[booking_id])
