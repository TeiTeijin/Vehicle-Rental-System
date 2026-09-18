from abc import ABC, abstractmethod
from decimal import Decimal


class Vehicle(ABC):
    def __init__(self, make: str, model: str, year: int, daily_rate) -> None:
        self.make = make
        self.model = model
        self.year = year
        self.daily_rate = Decimal(str(daily_rate))

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.make} {self.model} {self.year})"

    @abstractmethod
    def calculate_rate(self, days: int) -> Decimal:
        raise NotImplementedError


class Car(Vehicle):
    LONG_TERM_DAYS = 7
    LONG_TERM_DISCOUNT = Decimal("0.90")

    def calculate_rate(self, days: int) -> Decimal:
        rate = self.daily_rate * Decimal(days)
        if days >= self.LONG_TERM_DAYS:
            rate *= self.LONG_TERM_DISCOUNT
        return rate


class Motorcycle(Vehicle):
    MULTIPLIER = Decimal("0.70")

    def calculate_rate(self, days: int) -> Decimal:
        return self.daily_rate * Decimal(days) * self.MULTIPLIER