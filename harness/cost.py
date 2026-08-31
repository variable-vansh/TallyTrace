"""What the LLM costs, in rupees, per reconciled transaction.

Zero today: no model is called anywhere in this build. The arithmetic exists now so
that checkpoint 3 adds a model and reads a number, rather than adding a model and a
number at the same time and having no way to tell which one moved the result.

Rates come from ``config/pricing.yaml`` and are Decimal from the moment they are
read, like every other money path in the repo.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pipeline.llm.usage import LlmUsage

MILLION = Decimal("1000000")
ZERO = Decimal("0.00")
PAISE = Decimal("0.01")


@dataclass(frozen=True)
class Pricing:
    """Per-million-token rates, plus the rate the report is denominated in."""

    model: str
    input_usd: Decimal
    output_usd: Decimal
    cache_read_usd: Decimal
    cache_write_usd: Decimal
    usd_to_inr: Decimal


def pricing_from(config: dict[str, Any]) -> Pricing:
    rates = config["usd_per_mtok"]
    return Pricing(
        model=str(config["model"]),
        input_usd=Decimal(rates["input"]),
        output_usd=Decimal(rates["output"]),
        cache_read_usd=Decimal(rates["cache_read"]),
        cache_write_usd=Decimal(rates["cache_write"]),
        usd_to_inr=Decimal(config["usd_to_inr"]),
    )


def cost_usd(usage: LlmUsage, pricing: Pricing) -> Decimal:
    """Unrounded, because rounding to paise before dividing by 1,500 transactions
    would quantize the per-transaction figure to zero and hide a real cost."""
    return (
        Decimal(usage.input_tokens) * pricing.input_usd
        + Decimal(usage.output_tokens) * pricing.output_usd
        + Decimal(usage.cache_read_input_tokens) * pricing.cache_read_usd
        + Decimal(usage.cache_creation_input_tokens) * pricing.cache_write_usd
    ) / MILLION


def cost_inr(usage: LlmUsage, pricing: Pricing) -> Decimal:
    return cost_usd(usage, pricing) * pricing.usd_to_inr


def per_transaction_inr(usage: LlmUsage, pricing: Pricing, transactions: int) -> Decimal:
    """Rupees of model spend per reconciled transaction. Zero over zero is zero."""
    if transactions <= 0:
        return ZERO
    return cost_inr(usage, pricing) / Decimal(transactions)


def to_paise(value: Decimal) -> Decimal:
    return value.quantize(PAISE, rounding=ROUND_HALF_UP)
