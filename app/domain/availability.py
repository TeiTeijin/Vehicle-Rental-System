from datetime import date


class AvailabilityChecker:
    @staticmethod
    def overlaps(
        start_date: date,
        end_date: date,
        other_start: date,
        other_end: date,
    ) -> bool:
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
        for booking in existing_bookings:
            if AvailabilityChecker.overlaps(
                start_date, end_date, booking.start_date, booking.end_date
            ):
                return booking
        return None
