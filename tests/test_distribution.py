"""The distribution across batches is the whole learning story.

Get it wrong and the review-rate curve in checkpoint 3 is fake. These tests assert
the properties the story depends on: recurring causes really recur, held-out causes
really are absent until their batch, the near-misses really are ambiguous, and the
claim recoveries really are planted in later batches.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from pipeline.config import generation

BATCHES = list(range(1, int(generation()["batch_count"]) + 1))
RECURRING = {
    "commission_rate_stale",
    "rto_reversal_later_cycle",
    "refund_timing_lag",
    "settlement_lag_crossing_batch",
}


def truth_for(truth_dir: Path, batch: int) -> dict:
    return json.loads((truth_dir / f"batch_{batch:02d}.json").read_text(encoding="utf-8"))


def causes_in(truth_dir: Path, batch: int) -> Counter:
    counts: Counter = Counter()
    for injection in truth_for(truth_dir, batch)["injections"]:
        counts[injection["cause"]] += len(injection["affected_row_ids"])
    return counts


# --------------------------------------------------------------------------- #
# Recurring
# --------------------------------------------------------------------------- #


def test_every_batch_carries_troubles(truth_dir: Path) -> None:
    for batch in BATCHES:
        assert sum(causes_in(truth_dir, batch).values()) > 0, f"batch {batch} is clean"


def test_recurring_causes_appear_in_most_batches(truth_dir: Path) -> None:
    for cause in RECURRING:
        present = [b for b in BATCHES if causes_in(truth_dir, b).get(cause, 0) > 0]
        assert len(present) >= 6, f"{cause} appears in only {len(present)} batches: {present}"


def test_recurring_causes_never_reuse_an_order(truth_dir: Path) -> None:
    """Recurrence has to be a repeated pattern, not a repeated row."""
    seen: set[str] = set()
    for batch in BATCHES:
        for injection in truth_for(truth_dir, batch)["injections"]:
            for order_id in injection["affected_order_ids"]:
                assert order_id not in seen, f"{order_id} carries two injected troubles"
                seen.add(order_id)


def test_batch_one_is_not_flattered(truth_dir: Path) -> None:
    """Batch 1's review rate is the starting point the decline is measured from."""
    troubles = sum(causes_in(truth_dir, 1).values())
    rows = truth_for(truth_dir, 1)["row_counts"]["settlement_rows"]
    assert troubles / rows > 0.15, f"batch 1 has only {troubles} troubles in {rows} rows"


def test_one_off_causes_persist_to_the_last_batches(truth_dir: Path) -> None:
    """Why the review rate plateaus above zero instead of reaching it."""
    for batch in (9, 10):
        counts = causes_in(truth_dir, batch)
        assert set(counts) - RECURRING, f"batch {batch} has nothing but learnable causes"


# --------------------------------------------------------------------------- #
# Held out
# --------------------------------------------------------------------------- #


def test_held_out_causes_are_absent_before_their_batch(truth_dir: Path) -> None:
    for cause, first_batch in generation()["held_out"].items():
        for batch in range(1, first_batch):
            assert causes_in(truth_dir, batch).get(cause, 0) == 0, \
                f"{cause} leaked into batch {batch}, before its first appearance in {first_batch}"
        assert causes_in(truth_dir, first_batch).get(cause, 0) > 0, \
            f"{cause} does not appear in batch {first_batch}"


def test_chargeback_rows_are_absent_from_the_data_too(generated_dir: Path) -> None:
    """Held out of the answer key is not enough; it has to be out of the files."""
    first_batch = int(generation()["held_out"]["chargeback_deduction"])
    for batch in range(1, first_batch):
        path = generated_dir / f"batch_{batch:02d}" / "settlement_report.csv"
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                assert "CHARGEBACK" not in row["description"].upper()
                assert not row["dispute_id"].startswith("CB-")


# --------------------------------------------------------------------------- #
# Near-miss
# --------------------------------------------------------------------------- #


def test_at_least_two_near_misses_are_planted(truth_dir: Path) -> None:
    near_misses = [
        injection
        for batch in BATCHES
        for injection in truth_for(truth_dir, batch)["injections"]
        if injection["injector_params"].get("near_miss")
    ]
    assert len(near_misses) >= 2, "the false positive to feature in the video is missing"
    for injection in near_misses:
        # Looks like the learnable internal fix, is actually money someone owes you.
        assert injection["cause"] == "short_payment_unexplained"
        assert injection["resolution_class"] == "counterparty_claim"
        assert injection["injector_params"]["looks_like"] == "commission_rate_stale"


def test_a_near_miss_is_numerically_indistinguishable_from_the_rule_it_trips(truth_dir: Path) -> None:
    """Same channel, same variance band. Nothing on the surface separates them."""
    stale_rates, near_miss_rates = set(), set()
    for batch in BATCHES:
        for injection in truth_for(truth_dir, batch)["injections"]:
            params = injection["injector_params"]
            if params.get("near_miss"):
                near_miss_rates.add((params["channel"], params["charged_rate"]))
            elif injection["cause"] == "commission_rate_stale":
                stale_rates.add((params["channel"], params["actual_rate"]))
    assert near_miss_rates & stale_rates, f"{near_miss_rates} shares nothing with {stale_rates}"


# --------------------------------------------------------------------------- #
# Claim recovery
# --------------------------------------------------------------------------- #


def test_at_least_three_claim_recoveries_are_planted(truth_dir: Path) -> None:
    pairs = json.loads((truth_dir / "manifest.json").read_text(encoding="utf-8"))["recovery_pairs"]
    assert len(pairs) >= 3, f"only {len(pairs)} recovery pairs; checkpoint 4 auto-close needs them"
    for pair in pairs:
        assert pair["cause"] in {"missing_settlement_row", "short_payment_unexplained"}
        assert pair["batch"] > pair["claim_batch"], "a recovery must land in a later batch"
        assert Decimal(pair["amount_inr"]) > 0


def test_each_planted_recovery_credit_is_actually_in_the_later_batch(
    truth_dir: Path, generated_dir: Path
) -> None:
    pairs = json.loads((truth_dir / "manifest.json").read_text(encoding="utf-8"))["recovery_pairs"]
    for pair in pairs:
        path = generated_dir / f"batch_{pair['batch']:02d}" / "settlement_report.csv"
        with path.open(encoding="utf-8", newline="") as handle:
            rows = {row["entity_id"]: row for row in csv.DictReader(handle)}
        credit = rows.get(pair["row_id"])
        assert credit is not None, f"recovery {pair['row_id']} is not in batch {pair['batch']}"
        assert Decimal(credit["credit"]) == Decimal(pair["amount_inr"])
        assert credit["order_id"] == pair["order_id"]


# --------------------------------------------------------------------------- #
# Truth discipline
# --------------------------------------------------------------------------- #


def test_truth_records_cause_and_impact_but_never_difficulty(truth_dir: Path) -> None:
    """The dataset records what was done. Whether a matcher should catch it is the
    harness's finding, not the dataset's assertion."""
    forbidden = {"difficulty", "solvable", "matcher", "expected_bucket", "should_catch"}
    for batch in BATCHES:
        for injection in truth_for(truth_dir, batch)["injections"]:
            assert set(injection) == {
                "batch", "cause", "affected_row_ids", "affected_order_ids",
                "true_impact_inr", "resolution_class", "injector_params",
            }
            assert not forbidden & set(injection["injector_params"])
            assert Decimal(injection["true_impact_inr"]) > 0, injection["cause"]


def test_every_recorded_cause_is_in_the_frozen_enum(truth_dir: Path) -> None:
    from pipeline.models import Cause

    valid = {c.value for c in Cause}
    for batch in BATCHES:
        for injection in truth_for(truth_dir, batch)["injections"]:
            assert injection["cause"] in valid
