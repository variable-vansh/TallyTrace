"""The matcher end to end, over the real corpus.

These are the properties the rest of the build stands on: every row lands in exactly
one bucket, the same input produces the same numbers, and the matching functions
touch no file and no clock.
"""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from pipeline.config import REPO_ROOT, generation, thresholds
from pipeline.loader import load_batch
from pipeline.matcher import Bucket, MatchConfig, Reason, match_config_from, reconcile
from pipeline.matcher.verdicts import assert_one_bucket_each, detail
from pipeline.run import OpenBook, run_all, run_batch

BATCH_COUNT = int(generation()["batch_count"])


@pytest.fixture(scope="module")
def results(generated_dir: Path) -> list:
    return run_all(generated_dir)


@pytest.fixture(scope="module")
def config() -> MatchConfig:
    return match_config_from(thresholds())


# --------------------------------------------------------------------------- #
# The bucket contract
# --------------------------------------------------------------------------- #


def test_every_input_row_lands_in_exactly_one_bucket(results: list, generated_dir: Path) -> None:
    """Row count in equals row count out, per table, including the rejected rows."""
    for result in results:
        tables = load_batch(result.batch, generated_dir)
        assert_one_bucket_each(result.verdicts)
        rejected = Counter(record.table for record in tables.quarantined)
        assert len(result.by_table("settlement_report")) == len(tables.settlements) + rejected[
            "settlement_report"
        ]
        assert len(result.by_table("bank_statement")) == len(tables.bank) + rejected[
            "bank_statement"
        ]
        assert result.counts()["quarantined"] == len(tables.quarantined)


def test_every_verdict_carries_a_reason_code_from_the_frozen_enum(results: list) -> None:
    for result in results:
        for verdict in result.verdicts:
            assert isinstance(verdict.reason, Reason)
            assert isinstance(verdict.bucket, Bucket)


def test_every_ledger_row_ever_booked_receives_a_verdict(results: list, generated_dir: Path) -> None:
    """The open book is cumulative, so an order is accounted for in every batch
    from the one that booked it until the one that settles it."""
    for result in results:
        for order_id in {row.order_id for row in load_batch(result.batch, generated_dir).ledger}:
            assert any(v.row_id == order_id for v in result.by_table("internal_ledger"))


def test_no_money_field_on_a_verdict_is_a_float(results: list) -> None:
    for result in results:
        for verdict in result.verdicts:
            assert isinstance(verdict.impact_inr, Decimal)
        for group in result.groups:
            assert isinstance(group.settlement_sum, Decimal)
            assert isinstance(group.shortfall, Decimal)


def test_the_verdict_detail_refuses_a_float() -> None:
    with pytest.raises(TypeError):
        detail(expected_fee=1.5)


# --------------------------------------------------------------------------- #
# Determinism and purity
# --------------------------------------------------------------------------- #


def test_the_same_batch_reconciles_to_the_same_numbers_twice(generated_dir: Path) -> None:
    first = [r.to_json() for r in run_all(generated_dir)]
    second = [r.to_json() for r in run_all(generated_dir)]
    assert first == second


def test_reconcile_does_not_mutate_its_input(generated_dir: Path, config: MatchConfig) -> None:
    tables = load_batch(1, generated_dir)
    before = [row.model_dump() for row in tables.settlements]
    run_batch(tables, OpenBook.empty(), config)
    assert [row.model_dump() for row in tables.settlements] == before


IO_CALL = re.compile(r"\b(open\(|read_text|write_text|Path\(|datetime\.now|date\.today|random\.)")


def test_the_matcher_package_performs_no_io() -> None:
    """Pure functions: data and config in, results out. Asserted, not asserted-to."""
    offenders = [
        f"{path.name}:{index}: {line.strip()}"
        for path in sorted((REPO_ROOT / "pipeline" / "matcher").glob("*.py"))
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if IO_CALL.search(line) and not line.lstrip().startswith(("#", '"', "'"))
    ]
    assert offenders == [], f"I/O or non-determinism inside pipeline/matcher/: {offenders}"


# --------------------------------------------------------------------------- #
# What the corpus should produce
# --------------------------------------------------------------------------- #


def test_the_n_to_one_grouping_ties_out_for_the_clean_settlements(results: list) -> None:
    """Almost every payout should tie. The ones that do not are the injected ones."""
    groups = [group for result in results for group in result.groups]
    tying = [group for group in groups if group.ties_out]
    assert len(tying) / len(groups) > 0.9, "too many groups failing to tie means normalisation is off"


def test_a_group_that_does_not_tie_out_reports_its_shortfall_and_candidates(results: list) -> None:
    broken = [g for result in results for g in result.groups if not g.ties_out and g.candidate_row_ids]
    assert broken, "the duplicate-row injections should break at least one payout"
    for group in broken:
        assert group.shortfall != Decimal("0.00")
        assert group.candidate_row_ids
        assert group.residual_row_ids or group.search_exhausted


def test_the_largest_payout_really_is_an_n_to_one_join(results: list) -> None:
    widest = max(len(g.candidate_row_ids) for result in results for g in result.groups)
    assert widest > 20, "a subset search over three candidates is not the N:1 problem"


def test_batch_one_match_rate_is_plausible(results: list) -> None:
    """The checkpoint gate, kept as a test.

    Above ~85% means the tolerance is silently clearing real troubles; below ~60%
    means normalisation is broken. It is a measurement, not a target -- if this
    fails, the matcher is what changes, not the band.
    """
    counts = results[0].counts("settlement_report")
    rate = counts["matched"] / sum(counts.values())
    assert 0.60 <= rate <= 0.85, f"batch 1 matched {rate:.0%} of its settlement rows"


def test_settlement_lag_is_reported_as_late_rather_than_as_lost(results: list) -> None:
    """The cross-batch case: flagged on the clock, never on the money."""
    late = [
        v for result in results for v in result.by_table("internal_ledger")
        if v.reason is Reason.SETTLEMENT_OUTSIDE_DATE_WINDOW
    ]
    assert late, "the lag injections should surface"
    assert all(v.impact_inr == Decimal("0.00") for v in late), "late money is not missing money"


def test_orders_awaiting_settlement_are_carried_not_charged(results: list) -> None:
    waiting = [
        v for result in results for v in result.by_table("internal_ledger")
        if v.reason is Reason.AWAITING_SETTLEMENT_IN_WINDOW
    ]
    assert waiting
    assert all(v.impact_inr == Decimal("0.00") for v in waiting)


def test_an_order_settled_in_one_batch_is_not_reopened_in_the_next(generated_dir: Path) -> None:
    """The open book carries forward; a closed order stops consuming attention."""
    results = run_all(generated_dir)
    closed: set[str] = set()
    for result in results:
        reopened = closed & {v.row_id for v in result.by_table("internal_ledger")}
        assert not reopened, f"batch {result.batch} reopened {sorted(reopened)[:3]}"
        closed |= set(result.settled_orders)


def test_a_deduction_arriving_after_its_order_settled_is_named_as_late_not_unknown(
    results: list,
) -> None:
    """RTO reversals and lagged refunds land cycles after the sale they reverse."""
    reasons = {v.reason for result in results for v in result.by_table("settlement_report")}
    assert Reason.LATE_ROW_FOR_SETTLED_ORDER in reasons
    assert Reason.ROW_FOR_UNKNOWN_ORDER not in reasons, "every row should join to a known order"
