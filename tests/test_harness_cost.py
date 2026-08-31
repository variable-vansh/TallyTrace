"""The LLM cost plumbing.

Nothing calls a model yet, so every number this produces in a real run is zero. That
is exactly why it is tested with non-zero usage here: a cost path that is only ever
exercised at zero is a cost path nobody has checked, and it would be checked for the
first time in the same commit that adds the model.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from harness.cost import Pricing, cost_inr, cost_usd, per_transaction_inr, pricing_from
from pipeline.config import CONFIG_DIR, load_yaml
from pipeline.llm.usage import LlmUsage, UsageLedger

D = Decimal
PRICING = Pricing(
    model="claude-opus-5", input_usd=D("5.00"), output_usd=D("25.00"),
    cache_read_usd=D("0.50"), cache_write_usd=D("6.25"), usd_to_inr=D("88.00"),
)


def test_the_shipped_pricing_file_parses_into_decimals() -> None:
    pricing = pricing_from(load_yaml(CONFIG_DIR / "pricing.yaml"))
    assert pricing.model
    for rate in (pricing.input_usd, pricing.output_usd, pricing.usd_to_inr):
        assert isinstance(rate, Decimal), "a float rate is a float in a money path"
        assert rate > 0


def test_a_million_input_tokens_costs_the_input_rate() -> None:
    assert cost_usd(LlmUsage(input_tokens=1_000_000), PRICING) == D("5.00")


def test_output_and_cache_tokens_price_at_their_own_rates() -> None:
    usage = LlmUsage(
        input_tokens=1_000_000, output_tokens=1_000_000,
        cache_read_input_tokens=1_000_000, cache_creation_input_tokens=1_000_000,
    )
    assert cost_usd(usage, PRICING) == D("36.75")
    assert cost_inr(usage, PRICING) == D("36.75") * D("88.00")


def test_cost_is_not_rounded_before_it_is_divided_per_transaction() -> None:
    """Rounding to paise first would quantize a real cost to zero across 1,200 rows."""
    usage = LlmUsage(input_tokens=1_000)          # $0.005, about 44 paise in total
    per_row = per_transaction_inr(usage, PRICING, 1_200)
    assert per_row > 0, "a real cost must not vanish into the per-transaction figure"
    assert per_row < D("0.01")


def test_no_transactions_means_no_cost_rather_than_a_division_error() -> None:
    assert per_transaction_inr(LlmUsage(input_tokens=5_000), PRICING, 0) == D("0.00")


def test_zero_usage_costs_zero() -> None:
    assert cost_inr(LlmUsage(), PRICING) == 0


# --------------------------------------------------------------------------- #
# The ledger
# --------------------------------------------------------------------------- #


def test_a_fresh_ledger_reports_zero_for_every_batch() -> None:
    ledger = UsageLedger()
    assert ledger.is_empty()
    assert ledger.usage_for(4) == LlmUsage()


def test_the_ledger_accumulates_per_batch_and_in_total() -> None:
    """The shape checkpoint 3's client records into, tested before it exists."""
    ledger = UsageLedger()
    ledger.record(1, input_tokens=100, output_tokens=10)
    ledger.record(1, input_tokens=200, output_tokens=20)
    ledger.record(2, input_tokens=50, output_tokens=5)

    assert ledger.usage_for(1) == LlmUsage(calls=2, input_tokens=300, output_tokens=30)
    assert ledger.total() == LlmUsage(calls=3, input_tokens=350, output_tokens=35)
    assert ledger.total().total_tokens == 385
    assert not ledger.is_empty()


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens", "cache_read_input_tokens"])
def test_usage_adds_field_by_field(field: str) -> None:
    combined = LlmUsage(**{field: 7}) + LlmUsage(**{field: 5})
    assert getattr(combined, field) == 12
