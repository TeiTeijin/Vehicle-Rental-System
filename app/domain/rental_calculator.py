from datetime import date
from decimal import Decimal


class RentalCalculator:
    @staticmethod
    def total_cost(vehicle, start_date: date, end_date: date) -> Decimal:
        days = (end_date - start_date).days
        if days <= 0:
            raise ValueError("end_date must be after start_date")
        return vehicle.calculate_rate(days)