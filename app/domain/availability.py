from datetime import date


class AvailabilityChecker:
    """Date-overlap checks against a set of existing bookings.

    Bookings are half-open intervals: a rental running 1 Sep to 5 Sep occupies
    1, 2, 3 and 4 Sep, and 5 Sep is the day the car comes back. Two bookings
    therefore touch without conflicting -- a car returned on the 5th can be
    re-rented on the 5th, which is what actually happens at a rental desk.

    The previous inclusive test (`start <= b.end and b.start <= end`) treated
    that shared day as a conflict and blocked every same-day turnaround.
    """

    @staticmethod
    def overlaps(
        start_date: date,
        end_date: date,
        other_start: date,
        other_end: date,
    ) -> bool:
        """True when [start, end) and [other_start, other_end) share a day."""
        return start_date < other_end and other_start < end_date

    @staticmethod
    def is_available(
        vehicle,
        start_date: date,
        end_date: date,
        existing_bookings,
    ) -> bool:
        if end_date <= start_date:
            raise ValueError("end_date must be after start_date")

        for booking in existing_bookings:
            if AvailabilityChecker.overlaps(
                start_date, end_date, booking.start_date, booking.end_date
            ):
                return False
        return True

    @staticmethod
    def find_conflict(
        start_date: date,
        end_date: date,
        existing_bookings,
    ):
        """The first booking that blocks this range, or None.

        Useful for telling staff *why* a vehicle is unavailable instead of just
        showing an empty result list.
        """
        for booking in existing_bookings:
            if AvailabilityChecker.overlaps(
                start_date, end_date, booking.start_date, booking.end_date
            ):
                return booking
        return None
