from datetime import date, timedelta


def _window(start_date: date, end_date: date) -> tuple[date, date]:
    """Half-open occupancy window. A same-day pick-up and return still
    occupies the whole day, so `[d, d]` reads as `[d, d + 1 day]`."""
    if end_date < start_date:
        raise ValueError("end_date cannot be before start_date")
    if end_date == start_date:
        return start_date, start_date + timedelta(days=1)
    return start_date, end_date


class AvailabilityChecker:
    @staticmethod
    def overlaps(
        start_date: date,
        end_date: date,
        other_start: date,
        other_end: date,
    ) -> bool:
        start_date, end_date = _window(start_date, end_date)
        other_start, other_end = _window(other_start, other_end)
        return start_date < other_end and other_start < end_date

    @staticmethod
    def is_available(
        vehicle,
        start_date: date,
        end_date: date,
        existing_bookings,
    ) -> bool:
        if end_date < start_date:
            raise ValueError("end_date cannot be before start_date")

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
        for booking in existing_bookings:
            if AvailabilityChecker.overlaps(
                start_date, end_date, booking.start_date, booking.end_date
            ):
                return booking
        return None
