from datetime import date
from decimal import Decimal

from app.utils.money import money


class RentalCalculator:
    @staticmethod
    def rental_days(start_date: date, end_date: date) -> int:
        if end_date <= start_date:
            raise ValueError("end_date must be after start_date")
        return (end_date - start_date).days

    @staticmethod
    def total_cost(vehicle, start_date: date, end_date: date) -> Decimal:
        days = RentalCalculator.rental_days(start_date, end_date)
        return money(vehicle.calculate_rate(days))
