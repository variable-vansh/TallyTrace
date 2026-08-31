"""Money arithmetic. Decimal only, rounded to paise, half-up.

Banker's rounding (Python's Decimal default) is wrong for Indian invoicing, so
every rounding point in the generator goes through :func:`inr`.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

PAISE = Decimal("0.01")
ZERO = Decimal("0.00")


def inr(value: Decimal | int | str) -> Decimal:
    """Quantize to paise, rounding half away from zero.

    Negative zero is normalised away: negating a zero tax line is legitimate
    arithmetic but ``-0.00`` in an emitted settlement file reads as a defect.
    """
    if isinstance(value, float):
        raise TypeError("float in a money path")
    result = Decimal(value).quantize(PAISE, rounding=ROUND_HALF_UP)
    return result + ZERO if result == ZERO else result


def apply_rate(amount: Decimal, rate: Decimal) -> Decimal:
    """``amount * rate`` rounded to paise."""
    return inr(amount * rate)


def total(values: list[Decimal]) -> Decimal:
    return inr(sum(values, ZERO))
