from abc import ABC, abstractmethod
from decimal import Decimal

from app.domain.pricing import PricingPolicy


class Vehicle(ABC):
    def __init__(self, make: str, model: str, year: int, daily_rate) -> None:
        self.make = make
        self.model = model
        self.year = year
        self.daily_rate = Decimal(str(daily_rate))

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.make} {self.model} {self.year})"

    @abstractmethod
    def calculate_rate(
        self, days: int, *, policy: PricingPolicy | None = None
    ) -> Decimal:
        raise NotImplementedError


class Car(Vehicle):
    def calculate_rate(
        self, days: int, *, policy: PricingPolicy | None = None
    ) -> Decimal:
        rules = policy or PricingPolicy()
        rate = self.daily_rate * Decimal(days)
        if days >= rules.long_term_days:
            rate *= rules.long_term_discount
        return rate


class Motorcycle(Vehicle):
    def calculate_rate(
        self, days: int, *, policy: PricingPolicy | None = None
    ) -> Decimal:
        rules = policy or PricingPolicy()
        return self.daily_rate * Decimal(days) * rules.moto_multiplier
