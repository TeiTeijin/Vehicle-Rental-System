from datetime import date


class AvailabilityChecker:
    @staticmethod
    def is_available(
        vehicle,
        start_date: date,
        end_date: date,
        existing_bookings,
    ) -> bool:
        if end_date < start_date:
            raise ValueError("end_date must not be before start_date")

        for booking in existing_bookings:
            if start_date <= booking.end_date and booking.start_date <= end_date:
                return False
        return True

    @staticmethod
    def find_conflict(
        start_date: date,
        end_date: date,
        existing_bookings,
    ):
        for booking in existing_bookings:
            if start_date <= booking.end_date and booking.start_date <= end_date:
                return booking
        return None