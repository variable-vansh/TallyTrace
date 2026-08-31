"""The features checkpoint 3 induces and evaluates rules on.

Checkpoint 3 turns a human's sentence into a structured rule and then evaluates that
rule as a predicate. Both halves read the matcher's verdict detail, so these tests
pin the properties they depend on:

- a rule's variance band is emitted by the matcher, once, so induction and
  application cannot drift apart on what "8.8% over" means;
- a lagged deduction carries *time*, not only a channel, so a rule can be about the
  phenomenon rather than about which channel this corpus happened to inject it on;
- the dataset's deliberate traps survive: the held-out cause sits outside the
  learnable band, and the near-miss sits inside it.

If one of these fails, checkpoint 3 will still produce a curve. It will just be a
curve about something else.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

from harness.attribution import VerdictIndex
from harness.score import Score, run
from pipeline.matcher import Reason

D = Decimal
LEARNABLE_RECURRING = {
    "commission_rate_stale",
    "rto_reversal_later_cycle",
    "refund_timing_lag",
    "settlement_lag_crossing_batch",
}


@pytest.fixture(scope="module")
def score(generated_dir: Path, truth_dir: Path) -> Score:
    return run(generated_dir, truth_dir)


def bands_by(score: Score, key: str) -> dict[tuple[str, str], list[Decimal]]:
    """Injected rows grouped by (cause, channel) with their variance band."""
    index = VerdictIndex(score.results)
    bands: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
    for injection in score.key.injections:
        for row_id in injection.affected_row_ids:
            found = index.find("settlement_report", row_id)
            if found and key in found[1].detail:
                bands[(injection.cause, found[1].channel or "")].append(
                    Decimal(found[1].detail[key])
                )
    return bands


# --------------------------------------------------------------------------- #
# Rule induction has something to induce on
# --------------------------------------------------------------------------- #


def test_a_stale_rate_produces_one_tight_band_per_channel(score: Score) -> None:
    """The signal the whole learning story rests on: systematic, not noisy.

    A stale rate is a fixed percentage error on every order it touches, so the band
    is a point rather than a spread. If this ever widens, the rate is no longer the
    only thing moving and a rule induced from it will over-match.
    """
    bands = bands_by(score, "fee_variance_pct")
    stale = {ch: v for (cause, ch), v in bands.items() if cause == "commission_rate_stale"}
    assert stale, "no stale-rate rows carried a variance band"
    for channel, values in stale.items():
        assert max(values) - min(values) <= D("0.5"), (
            f"{channel} stale-rate band spans {min(values)}%..{max(values)}%"
        )


def test_the_held_out_cause_sits_outside_the_learnable_band(score: Score) -> None:
    """Batch 7's promo co-funding must not look like a stale rate.

    Correct abstention is a graded behaviour and it is only possible if the two are
    actually separable in the features a rule can see.
    """
    bands = bands_by(score, "fee_variance_pct")
    stale = [v for (cause, _), values in bands.items() if cause == "commission_rate_stale"
             for v in values]
    promo = [v for (cause, _), values in bands.items()
             if cause == "promo_cofunding_deduction" for v in values]
    assert promo, "the held-out cause produced no bands to compare"
    assert min(promo) > max(stale), (
        f"promo co-funding at {min(promo)}% overlaps stale rate at {max(stale)}%"
    )


def test_the_near_miss_sits_inside_the_learnable_band_on_the_same_channel(
    score: Score,
) -> None:
    """The most valuable rows in the dataset, and they must stay indistinguishable.

    A stale-rate rule will fire on these and be wrong. That false positive is the
    thing checkpoint 3's precision number is supposed to catch, so a near-miss that
    a rule can trivially reject would test nothing.
    """
    bands = bands_by(score, "fee_variance_pct")
    myntra_stale = bands[("commission_rate_stale", "myntra")]
    near_miss = [
        value
        for value in bands[("short_payment_unexplained", "myntra")]
        if value > D("0")
    ]
    assert near_miss, "no near-miss row carried a fee variance band"
    assert min(myntra_stale) <= max(near_miss) <= max(myntra_stale), (
        "the near-miss is distinguishable from the rule it is supposed to trip"
    )


# --------------------------------------------------------------------------- #
# Lagged deductions carry time, not just a channel
# --------------------------------------------------------------------------- #


def test_a_late_deduction_says_how_late_it_is(score: Score) -> None:
    for result in score.results:
        for verdict in result.by_table("settlement_report"):
            if verdict.reason is Reason.LATE_ROW_FOR_SETTLED_ORDER:
                assert "days_after_settlement" in verdict.detail
                assert "days_since_order" in verdict.detail
                assert int(verdict.detail["days_after_settlement"]) > 0


def test_the_two_recurring_lag_causes_are_separable_by_something_other_than_channel(
    score: Score,
) -> None:
    """In this corpus each lag cause was injected on one channel, so a rule of the
    form "amazon + refund" would score perfectly while having learned nothing. The
    temporal features are what make a rule about the phenomenon possible instead."""
    index = VerdictIndex(score.results)
    lags: dict[str, list[int]] = defaultdict(list)
    for injection in score.key.injections:
        if injection.cause not in ("refund_timing_lag", "rto_reversal_later_cycle"):
            continue
        for row_id in injection.affected_row_ids:
            found = index.find("settlement_report", row_id)
            if found and "days_after_settlement" in found[1].detail:
                lags[injection.cause].append(int(found[1].detail["days_after_settlement"]))

    assert set(lags) == {"refund_timing_lag", "rto_reversal_later_cycle"}
    for values in lags.values():
        assert len(set(values)) > 1, "a constant lag is not a temporal feature"


# --------------------------------------------------------------------------- #
# There is something left to learn
# --------------------------------------------------------------------------- #


def test_most_of_the_queue_comes_from_the_recurring_learnable_causes(
    score: Score,
) -> None:
    """Checkpoint 3's review rate can only fall if the queue is mostly learnable.

    If this drops, the decline in that chart will be coming from somewhere other
    than the learning loop, and the curve stops meaning what it claims to.
    """
    cause_of: dict[tuple[str, str], str] = {}
    for injection in score.key.injections:
        table = "bank_statement" if injection.is_bank_side else "settlement_report"
        for row_id in injection.affected_row_ids:
            cause_of[(table, row_id)] = injection.cause
        for order_id in injection.affected_order_ids:
            cause_of[("internal_ledger", order_id)] = injection.cause

    for result in score.results:
        findings = score.new_findings(result)
        learnable = sum(
            1
            for verdict in findings
            if cause_of.get((verdict.table, verdict.row_id)) in LEARNABLE_RECURRING
        )
        assert learnable / len(findings) > D("0.4"), (
            f"batch {result.batch}: only {learnable}/{len(findings)} findings are learnable"
        )
