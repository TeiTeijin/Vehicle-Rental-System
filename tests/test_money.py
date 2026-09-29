"""Tests for app.utils.money.

The behaviours asserted here are the ones a rental desk would notice: a blank
field must not become zero, a float must not leak binary noise into a stored
rate, and a balance must be the difference of what was charged and what was
actually taken.
"""

from decimal import Decimal

import pytest

from app.utils.money import (
    PESO_SIGN,
    InvalidAmount,
    ZERO,
    compact_pesos,
    is_positive,
    money,
    pesos,
    sum_money,
    to_decimal,
)


class TestToDecimal:
    def test_passes_decimal_through(self):
        assert to_decimal(Decimal("1250.00")) == Decimal("1250.00")

    def test_int(self):
        assert to_decimal(1000) == Decimal(1000)

    def test_float_goes_through_str(self):
        # Decimal(0.1) would be 0.1000000000000000055511151231257827...
        assert to_decimal(0.1) == Decimal("0.1")
        assert str(to_decimal(0.1)) == "0.1"

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("1250", "1250"),
            ("1250.50", "1250.50"),
            ("  1250.50  ", "1250.50"),
            ("1,250.50", "1250.50"),
            ("₱1,250.50", "1250.50"),
        ],
    )
    def test_strings(self, raw, expected):
        assert to_decimal(raw) == Decimal(expected)

    @pytest.mark.parametrize("raw", [None, "", "   ", "abc", "12.5.5", True, False, [1]])
    def test_rejects_garbage(self, raw):
        with pytest.raises(InvalidAmount):
            to_decimal(raw)

    def test_none_names_the_field(self):
        with pytest.raises(InvalidAmount, match="daily_rate"):
            to_decimal(None, field="daily_rate")


class TestMoney:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("1250.005", "1250.01"),   # half-up, not half-even
            ("1250.015", "1250.02"),
            ("1250.004", "1250.00"),
            ("0.1", "0.10"),
            (2, "2.00"),
        ],
    )
    def test_quantises_half_up(self, raw, expected):
        assert money(raw) == Decimal(expected)

    def test_half_even_would_have_given_a_different_answer(self):
        # Decimal's default rounding is ROUND_HALF_EVEN, which rounds 1250.005
        # to 1250.00. A person reading a receipt expects 1250.01.
        assert money("1250.005") == Decimal("1250.01")
        assert Decimal("1250.005").quantize(Decimal("0.01")) == Decimal("1250.00")


class TestPesos:
    def test_formats_with_thousands_and_sign(self):
        assert pesos(1250000) == "₱1,250,000.00"
        assert pesos("0") == "₱0.00"
        assert pesos(Decimal("999.5")) == "₱999.50"

    def test_renders_exactly_what_was_stored(self):
        # The string shown must equal money(), or a receipt and a screen can
        # disagree by a cent.
        assert pesos("1250.005") == f"{PESO_SIGN}{money('1250.005'):,.2f}"


class TestCompactPesos:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("9999.00", "₱9,999.00"),      # below the threshold: exact
            ("10000.00", "₱10.0K"),
            ("18500.00", "₱18.5K"),
            ("1250000", "₱1.3M"),
        ],
    )
    def test_shortens_large_amounts(self, raw, expected):
        assert compact_pesos(raw) == expected


class TestSumMoney:
    def test_sums_decimal_inputs_exactly(self):
        # 0.1 + 0.2 != 0.3 in float. It must be exact here.
        assert sum_money([Decimal("0.10"), Decimal("0.20")]) == Decimal("0.30")

    def test_sums_mixed_inputs(self):
        assert sum_money(["1250.50", 1000, Decimal("99.90")]) == Decimal("2350.40")

    def test_empty_is_zero(self):
        assert sum_money([]) == ZERO

    def test_quantises_the_total(self):
        assert sum_money(["0.005", "0.005"]) == Decimal("0.01")

    def test_rejects_a_blank_member(self):
        with pytest.raises(InvalidAmount):
            sum_money(["100.00", ""])


class TestIsPositive:
    @pytest.mark.parametrize(
        "raw, expected",
        [("0.00", False), ("0.01", True), (1, True), (0, False), ("-5", False)],
    )
    def test_strictly_greater_than_zero(self, raw, expected):
        assert is_positive(raw) is expected


class TestBalanceArithmetic:
    """The rule the whole payments screen depends on."""

    def test_balance_is_charge_plus_penalties_minus_payments(self):
        charge = money("12500.00")
        penalties = sum_money(["1000.00", "1500.00"])
        paid = sum_money(["12500.00"])
        assert money(charge + penalties - paid) == Decimal("2500.00")

    def test_no_float_ever_enters_the_chain(self):
        total = ZERO
        for value in ["0.10"] * 10:
            total += to_decimal(value)
        assert total == Decimal("1.00")
        assert money(total) == Decimal("1.00")
