"""One test per injector.

Each asserts three things: the world changed the way the cause describes, the
ground truth records it, and the recorded rupee impact is the real difference --
not a number the injector made up.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from generator import injectors as inj
from generator.money import ZERO, inr
from generator.world import net_of
from pipeline.config import resolution_class_by_cause
from pipeline.models import BankStatus, TransactionType


def only_entry(world, cause: str):
    entries = [e for e in world.truth if e.cause == cause]
    assert len(entries) == 1, f"expected exactly one {cause} entry, got {len(entries)}"
    entry = entries[0]
    assert entry.resolution_class == resolution_class_by_cause()[cause]
    assert entry.true_impact_inr >= ZERO
    return entry


def row_by_id(world, entity_id: str):
    return next(r for r in world.settlements if r.entity_id == entity_id)


# --------------------------------------------------------------------------- #
# internal_fix
# --------------------------------------------------------------------------- #


def test_commission_rate_stale(run_injector) -> None:
    world = run_injector(inj.commission_rate_stale, 3, 4, channel="myntra",
                         ledger_rate="0.25", actual_rate="0.272")
    entry = only_entry(world, "commission_rate_stale")
    assert len(entry.affected_order_ids) == 4

    for order_id in entry.affected_order_ids:
        ledger = world.ledger[order_id]
        assert ledger.expected_commission_rate == Decimal("0.25")
        charged = next(r for r in world.rows_for_order(order_id) if r.type == TransactionType.PAYMENT)
        # The platform charged the new rate; the books still hold the old one.
        assert charged.fee > ledger.expected_fee
        assert charged.fee == inr(ledger.order_value * Decimal("0.272"))


def test_commission_slab_change(run_injector) -> None:
    world = run_injector(inj.commission_slab_change, 3, 2, channel="flipkart", actual_rate="0.24")
    entry = only_entry(world, "commission_slab_change")
    for order_id in entry.affected_order_ids:
        row = next(r for r in world.rows_for_order(order_id) if r.type == TransactionType.PAYMENT)
        assert row.fee == inr(world.ledger[order_id].order_value * Decimal("0.24"))
        assert "SLAB REVISION" in row.description


def test_fee_mismatch_other(run_injector) -> None:
    world = run_injector(inj.fee_mismatch_other, 3, 2, channel="website")
    entry = only_entry(world, "fee_mismatch_other")
    for order_id in entry.affected_order_ids:
        row = next(r for r in world.rows_for_order(order_id) if r.type == TransactionType.PAYMENT)
        # A fee the ledger never derived from any rate.
        assert row.fee > world.ledger[order_id].expected_fee
        assert "SHIPPING FEE ADJ" in row.description


def test_rto_reversal_lands_in_a_later_batch(run_injector) -> None:
    world = run_injector(inj.rto_reversal_later_cycle, 2, 3, channel="flipkart",
                         reversal_batch_offset=3)
    entry = only_entry(world, "rto_reversal_later_cycle")
    assert entry.batch == 5, "the trouble is observable where the reversal lands"
    assert entry.injector_params["sale_batch"] == 2

    for order_id in entry.affected_order_ids:
        rows = world.rows_for_order(order_id)
        sale = next(r for r in rows if r.type == TransactionType.PAYMENT)
        reversal = next(r for r in rows if r.type == TransactionType.REFUND)
        assert reversal.settled_at > sale.settled_at
        # The books still expect the sale: nothing told them about the return.
        assert world.ledger[order_id].expected_net > ZERO


def test_refund_timing_lag_writes_the_books_off_before_the_deduction(run_injector) -> None:
    world = run_injector(inj.refund_timing_lag, 2, 3, channel="amazon", refund_batch_offset=2)
    entry = only_entry(world, "refund_timing_lag")
    assert entry.injector_params["deducted_batch"] == 4

    for order_id in entry.affected_order_ids:
        assert world.ledger[order_id].expected_net == ZERO
        rows = world.rows_for_order(order_id)
        deduction = next(r for r in rows if r.type == TransactionType.REFUND)
        sale = next(r for r in rows if r.type == TransactionType.PAYMENT)
        assert deduction.settled_at > sale.settled_at


def test_settlement_lag_pushes_the_payout_past_the_date_window(run_injector) -> None:
    world = run_injector(inj.settlement_lag_crossing_batch, 2, 4, channel="amazon",
                         extra_lag_days=17)
    entry = only_entry(world, "settlement_lag_crossing_batch")
    for order_id in entry.affected_order_ids:
        row = next(r for r in world.rows_for_order(order_id) if r.type == TransactionType.PAYMENT)
        assert (row.settled_at - row.created_at).days > 21


def test_rounding_variance_stays_inside_tolerance(run_injector) -> None:
    from pipeline.config import thresholds

    world = run_injector(inj.rounding_variance, 3, 5)
    entry = only_entry(world, "rounding_variance")
    tolerance = thresholds()["matching"]["rounding_tolerance_inr"]
    for order_id in entry.affected_order_ids:
        row = next(r for r in world.rows_for_order(order_id) if r.type == TransactionType.PAYMENT)
        drift = row.fee - world.ledger[order_id].expected_fee
        assert ZERO < drift < tolerance, "paise drift must not exceed the rounding tolerance"


def test_duplicate_settlement_row_is_not_funded_by_the_bank(run_injector) -> None:
    world = run_injector(inj.duplicate_settlement_row, 3, 1, channel="flipkart")
    entry = only_entry(world, "duplicate_settlement_row")
    clone_id = entry.affected_row_ids[0]
    clone = row_by_id(world, clone_id)
    twin = next(
        r for r in world.rows_for_order(clone.order_id)
        if r.entity_id != clone_id and r.type == TransactionType.PAYMENT
    )
    assert clone.amount == twin.amount and clone.credit == twin.credit
    # The report says it twice; the bank paid once, so the payout stops tying out.
    assert clone_id in world.bank_excluded_entity_ids
    assert entry.true_impact_inr == net_of(clone)


# --------------------------------------------------------------------------- #
# tax_review
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "injector,cause,field",
    [
        (inj.tcs_timing_mismatch, "tcs_timing_mismatch", "tcs"),
        (inj.tds_timing_mismatch, "tds_timing_mismatch", "tds"),
    ],
)
def test_tax_timing_moves_the_collection_to_a_later_cycle(run_injector, injector, cause, field) -> None:
    world = run_injector(injector, 3, 1, channel="myntra", defer_batches=1)
    entry = only_entry(world, cause)
    order_id = entry.affected_order_ids[0]
    rows = world.rows_for_order(order_id)
    sale = next(r for r in rows if r.type == TransactionType.PAYMENT)
    later = next(r for r in rows if r.type == TransactionType.ADJUSTMENT)

    assert getattr(sale, field) == ZERO, "nothing collected at the sale"
    assert getattr(later, field) == entry.true_impact_inr
    assert later.settled_at > sale.settled_at


def test_tax_timing_refuses_a_non_marketplace_channel(run_injector) -> None:
    with pytest.raises(inj.GenerationError, match="marketplace"):
        run_injector(inj.tcs_timing_mismatch, 3, 1, channel="website", defer_batches=1)


# --------------------------------------------------------------------------- #
# counterparty_claim
# --------------------------------------------------------------------------- #


def test_weight_dispute_holds_the_money_without_losing_it(run_injector) -> None:
    world = run_injector(inj.weight_dispute_hold, 3, 2, channel="flipkart")
    entry = only_entry(world, "weight_dispute_hold")
    for order_id in entry.affected_order_ids:
        row = next(r for r in world.rows_for_order(order_id) if r.type == TransactionType.PAYMENT)
        assert row.on_hold is True and row.dispute_id
        assert row.credit == ZERO
        # Reported as sold, so the sale is still visible: held, not lost.
        assert row.amount > ZERO


def test_missing_settlement_row_removes_the_row_entirely(run_injector) -> None:
    world = run_injector(inj.missing_settlement_row, 3, 2, channel="myntra",
                         recoveries=1, recovery_batch_offset=3)
    entry = only_entry(world, "missing_settlement_row")
    recovered = {r["order_id"] for r in entry.injector_params["recoveries"]}
    assert len(recovered) == 1

    for order_id in entry.affected_order_ids:
        payments = [r for r in world.rows_for_order(order_id) if r.type == TransactionType.PAYMENT]
        assert payments == [], "the settlement report simply does not mention the order"
        assert world.ledger[order_id].expected_net > ZERO


def test_short_payment_reduces_the_credit_and_nothing_else(run_injector) -> None:
    world = run_injector(inj.short_payment_unexplained, 3, 2, channel="flipkart",
                         shortfall_pct="0.12", recoveries=1, recovery_batch_offset=2)
    entry = only_entry(world, "short_payment_unexplained")
    for order_id in entry.affected_order_ids:
        row = next(r for r in world.rows_for_order(order_id) if r.type == TransactionType.PAYMENT)
        ledger = world.ledger[order_id]
        # Fee and tax are untouched, so nothing in the report explains the shortfall.
        assert row.fee == ledger.expected_fee
        assert row.credit < ledger.expected_net


def test_chargeback_deduction_carries_a_dispute_id(run_injector) -> None:
    world = run_injector(inj.chargeback_deduction, 3, 2, channel="website")
    entry = only_entry(world, "chargeback_deduction")
    for row_id in entry.affected_row_ids:
        row = row_by_id(world, row_id)
        assert row.type == TransactionType.ADJUSTMENT
        assert row.dispute_id and row.dispute_id.startswith("CB-")
        assert net_of(row) < ZERO


def test_promo_cofunding_hides_inside_the_fee_line(run_injector) -> None:
    world = run_injector(inj.promo_cofunding_deduction, 3, 3, channel="myntra",
                         deduction_pct="0.08")
    entry = only_entry(world, "promo_cofunding_deduction")
    for order_id in entry.affected_order_ids:
        row = next(r for r in world.rows_for_order(order_id) if r.type == TransactionType.PAYMENT)
        ledger = world.ledger[order_id]
        assert row.fee - ledger.expected_fee == inr(ledger.order_value * Decimal("0.08"))
        # Nothing in the description announces it. That is the whole problem.
        assert "PROMO" not in row.description.upper()


def test_near_miss_is_indistinguishable_from_a_stale_rate(run_injector) -> None:
    """The most valuable rows in the dataset: right signature, wrong cause."""
    world = run_injector(inj.near_miss_fee_variance, 3, 1, channel="myntra",
                         ledger_rate="0.25", actual_rate="0.272")
    entry = only_entry(world, "short_payment_unexplained")
    assert entry.injector_params["near_miss"] is True
    assert entry.injector_params["looks_like"] == "commission_rate_stale"
    # Routed to the claims queue, not the learning loop -- which is exactly what a
    # rule induced from the stale-rate exceptions would get wrong.
    assert entry.resolution_class == "counterparty_claim"

    order_id = entry.affected_order_ids[0]
    row = next(r for r in world.rows_for_order(order_id) if r.type == TransactionType.PAYMENT)
    ledger = world.ledger[order_id]
    assert ledger.expected_commission_rate == Decimal("0.25")
    assert row.fee == inr(ledger.order_value * Decimal("0.272"))


# --------------------------------------------------------------------------- #
# investigate
# --------------------------------------------------------------------------- #


def test_bank_credit_unmatched_has_no_settlement_counterpart(run_injector) -> None:
    world = run_injector(inj.bank_credit_unmatched, 3, 3)
    entry = only_entry(world, "bank_credit_unmatched")
    assert entry.affected_order_ids == []
    utrs = {row.utr for row in world.extra_bank_rows}
    assert set(entry.affected_row_ids) == utrs
    assert not [r for r in world.settlements if r.settlement_utr in utrs]
    for row in world.extra_bank_rows:
        assert row.status in {BankStatus.PROCESSED, BankStatus.REVERSED}


# --------------------------------------------------------------------------- #
# Shared behaviour
# --------------------------------------------------------------------------- #


def test_injectors_do_not_stack_on_the_same_order(clean_world) -> None:
    world, rng = clean_world
    inj.commission_rate_stale(world, rng, 3, 4, {"channel": "myntra", "ledger_rate": "0.25",
                                                 "actual_rate": "0.272"})
    inj.promo_cofunding_deduction(world, rng, 3, 3, {"channel": "myntra", "deduction_pct": "0.08"})
    first = set(world.truth[0].affected_order_ids)
    second = set(world.truth[1].affected_order_ids)
    assert first.isdisjoint(second), "one order, one cause: otherwise the truth is unattributable"


def test_an_impossible_request_raises_rather_than_quietly_doing_less(clean_world) -> None:
    world, rng = clean_world
    with pytest.raises(inj.GenerationError, match="need 999"):
        inj.commission_rate_stale(world, rng, 3, 999, {"channel": "myntra", "ledger_rate": "0.25",
                                                       "actual_rate": "0.272"})


def test_every_frozen_cause_has_an_injector() -> None:
    from pipeline.models import Cause

    covered = {
        "short_payment_unexplained" if name == "near_miss_fee_variance" else name
        for name in inj.INJECTORS
    }
    assert covered == {c.value for c in Cause}
