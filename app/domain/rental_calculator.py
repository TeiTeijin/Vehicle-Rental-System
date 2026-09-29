from datetime import date
from decimal import Decimal

from app.utils.money import money


class RentalCalculator:
    """Prices a rental by asking the vehicle what it charges.

    The caller never knows which Vehicle subclass it holds -- the discount on
    a long car hire and the flat motorcycle multiplier are resolved inside
    calculate_rate(). That is the polymorphism the domain layer exists to show.
    """

    @staticmethod
    def rental_days(start_date: date, end_date: date) -> int:
        """Billable days for a half-open [start, end) range.

        Single source of truth for the range rule, so the cost calculation and
        the availability check cannot disagree about what counts as a day.
        """
        if end_date <= start_date:
            raise ValueError("end_date must be after start_date")
        return (end_date - start_date).days

    @staticmethod
    def total_cost(vehicle, start_date: date, end_date: date) -> Decimal:
        """Total charge for the range, quantised to the peso.

        Quantised here rather than at the database boundary because the long-
        term discount multiplies a rate by 0.90 and can leave sub-cent
        precision (₱999.99 x 7 x 0.90 = ₱6,299.937) that a DECIMAL(10,2) column
        would have to round away anyway.
        """
        days = RentalCalculator.rental_days(start_date, end_date)
        return money(vehicle.calculate_rate(days))
