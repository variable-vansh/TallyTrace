"""The generator's own unit test.

If the clean base does not reconcile at 100%, a matcher bug and a generator bug are
indistinguishable, and every number produced after this point is meaningless.
"""

from __future__ import annotations

from decimal import Decimal

from generator import verify_clean
from generator.money import ZERO, inr
from generator.world import finalise, net_of


def test_clean_base_reconciles_completely() -> None:
    problems = verify_clean.verify()
    assert problems == [], "clean base does not reconcile:\n" + "\n".join(problems[:10])


def test_clean_base_has_no_injections(clean_world) -> None:
    world, _ = clean_world
    assert world.truth == []
    assert world.extra_bank_rows == []
    assert world.bank_excluded_entity_ids == set()


def test_every_settlement_row_belongs_to_a_payout(clean_world) -> None:
    world, _ = clean_world
    finalise(world)
    for row in world.settlements:
        assert row.settlement_id and row.settlement_utr


def test_bank_credits_aggregate_many_settlement_rows(clean_world) -> None:
    """The N:1 join has to be genuinely N:1, or checkpoint 2 has nothing to solve."""
    world, _ = clean_world
    finalise(world)
    per_utr: dict[str, int] = {}
    for row in world.settlements:
        per_utr[row.settlement_utr] = per_utr.get(row.settlement_utr, 0) + 1
    assert max(per_utr.values()) > 20


def test_fees_are_derived_from_the_ledgers_own_rate(clean_world) -> None:
    world, _ = clean_world
    for order_id, meta in world.meta.items():
        if meta.refunded:
            continue
        ledger = world.ledger[order_id]
        assert ledger.expected_fee == inr(ledger.order_value * ledger.expected_commission_rate)


def test_both_sign_conventions_are_present_and_mean_the_same_thing(clean_world) -> None:
    """Refunds are negative on some channels and a positive debit on others."""
    world, _ = clean_world
    negated = [r for r in world.settlements if r.type.value == "refund" and r.amount < ZERO]
    debited = [r for r in world.settlements if r.type.value == "refund" and r.debit > ZERO]
    assert negated and debited, "the matcher needs both conventions to normalise"
    for row in negated + debited:
        assert net_of(row) < ZERO, "either convention, the money still went out"


def test_money_in_the_world_is_never_float(clean_world) -> None:
    world, _ = clean_world
    for row in world.settlements:
        for field in ("amount", "fee", "tax", "tcs", "tds", "debit", "credit"):
            assert isinstance(getattr(row, field), Decimal)
    for ledger in world.ledger.values():
        for field in ("order_value", "expected_fee", "expected_net", "expected_commission_rate"):
            assert isinstance(getattr(ledger, field), Decimal)
