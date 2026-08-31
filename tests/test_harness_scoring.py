"""The harness scoring the real corpus.

The harness is the instrument. An instrument nobody checks reads whatever you hoped,
so these tests assert the properties the score depends on: that attribution finds
every injected row, that the confusion table is exhaustive, that silent clears are
counted rather than explained away, and that the artifacts are byte-identical run to
run apart from the wall clock, which is labelled as such.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from harness import aging as aging_module
from harness.attribution import attribute, confusion, silent_clears
from harness.exceptions import open_exceptions, render as render_exceptions
from harness.metrics import (
    AutoResolution,
    auto_resolution_precision,
    batch_metrics,
    pct,
    quarantine_summary,
    resolved_row_keys,
)
from harness.score import Score, run, to_json, to_text
from harness.truth import load_answer_key
from pipeline.config import generation
from pipeline.matcher import Bucket, Reason

BATCH_COUNT = int(generation()["batch_count"])
D = Decimal


@pytest.fixture(scope="module")
def score(generated_dir: Path, truth_dir: Path) -> Score:
    return run(generated_dir, truth_dir)


# --------------------------------------------------------------------------- #
# Coverage
# --------------------------------------------------------------------------- #


def test_the_run_covers_every_batch(score: Score) -> None:
    assert [metric.batch for metric in score.metrics] == list(range(1, BATCH_COUNT + 1))


def test_every_injected_row_in_the_answer_key_is_attributed(score: Score) -> None:
    assert len(score.outcomes) == score.key.affected_row_count
    assert all(outcome.bucket is not None for outcome in score.outcomes), (
        "an injected row with no verdict anywhere means the harness lost it, "
        "not that the matcher missed it"
    )


def test_the_confusion_table_accounts_for_every_injected_row(score: Score) -> None:
    assert sum(entry.rows for entry in score.confusion) == score.key.affected_row_count
    for entry in score.confusion:
        assert sum(count for _, count in entry.by_verdict) == entry.rows
        assert entry.caught + entry.silently_cleared == entry.rows


def test_every_cause_in_the_answer_key_appears_in_the_confusion_table(score: Score) -> None:
    assert {entry.cause for entry in score.confusion} == {i.cause for i in score.key.injections}


def test_records_processed_matches_the_generators_own_row_counts(score: Score) -> None:
    """The harness counts what was read; the answer key counts what was written."""
    for metric in score.metrics:
        written = score.key.row_counts[metric.batch]
        assert metric.records_processed == sum(written.values())


# --------------------------------------------------------------------------- #
# Rates
# --------------------------------------------------------------------------- #


def test_auto_match_and_review_rates_account_for_the_whole_batch(score: Score) -> None:
    for metric in score.metrics:
        assert metric.auto_match_rate + metric.review_rate == D("100.00")


def test_batch_one_lands_in_the_band_the_checkpoint_gate_expects(score: Score) -> None:
    rate = score.metrics[0].auto_match_rate
    assert D("60") <= rate <= D("85"), f"batch 1 auto-matched {rate}%"


def test_orders_inside_their_window_are_carried_and_not_queued(score: Score) -> None:
    """118 orders awaiting settlement in batch 1 are not 118 exceptions."""
    first = score.metrics[0]
    assert first.carried_forward > first.review_queue
    assert first.review_queue < first.settlement_rows


def test_a_finding_is_counted_in_the_batch_it_was_raised_and_not_again(score: Score) -> None:
    seen: set[tuple[str, str, str]] = set()
    for result in score.results:
        for verdict in score.new_findings(result):
            key = (verdict.table, verdict.row_id, verdict.reason.value)
            assert key not in seen, f"{key} was queued twice"
            seen.add(key)


def test_aged_findings_are_reported_rather_than_dropped(score: Score) -> None:
    """An overdue order stays unmatched for the rest of the corpus; say so."""
    assert sum(metric.aged_findings for metric in score.metrics) > 0


def test_pct_of_nothing_is_zero_not_an_exception() -> None:
    assert pct(0, 0) == D("0.00")


# --------------------------------------------------------------------------- #
# Silent clears
# --------------------------------------------------------------------------- #


def test_silent_clears_are_counted_not_excused(score: Score) -> None:
    counted = sum(entry.rows for entry in silent_clears(score.outcomes))
    assert counted == sum(1 for outcome in score.outcomes if outcome.silently_cleared)


def test_nothing_is_silently_cleared_outside_a_configured_band(score: Score) -> None:
    """The real "near zero": every cleared row is inside a band, on money and on time.

    A row cleared with a deviation *larger* than the tolerance recorded against it
    would be a matcher bug, not a tolerance judgement.
    """
    offenders = [
        outcome
        for outcome in score.outcomes
        if outcome.silently_cleared
        and (outcome.observed_delta_inr > outcome.tolerance_inr or outcome.days_late > 0)
    ]
    assert offenders == [], f"cleared outside its own band: {offenders[:3]}"


def test_the_silent_clear_report_states_how_close_a_band_came_to_firing(score: Score) -> None:
    for entry in silent_clears(score.outcomes):
        if entry.tightest_headroom_inr is not None:
            assert entry.tightest_headroom_inr >= 0
            assert entry.tolerance_at_tightest_inr is not None


# --------------------------------------------------------------------------- #
# Auto-resolution plumbing
# --------------------------------------------------------------------------- #


def test_precision_over_no_attempts_is_undefined_not_perfect() -> None:
    """A precision of 1.0 over nothing is the most flattering way to say nothing
    happened. Checkpoint 3 filled this path with real auto-resolutions, so the empty
    case is asserted on the function rather than on a run that no longer has one."""
    assert auto_resolution_precision([], {}) is None


def test_the_scored_run_now_has_auto_resolutions_to_score(score: Score) -> None:
    """The seam checkpoint 2 left is filled. If this ever empties again, every
    precision number in the report silently becomes 'undefined' rather than wrong,
    which is exactly the kind of quiet regression the harness is for."""
    assert score.proposals, "the learning loop resolved nothing"
    assert to_json(score)["totals"]["auto_resolution_precision_pct"] is not None


def test_the_net_review_rate_falls_when_a_rule_resolves_rows(
    generated_dir: Path, truth_dir: Path
) -> None:
    """The series checkpoint 3's chart plots, driven at the unit that computes it.

    ``review_rate`` is the matcher's own number and must not move; ``net_review_rate``
    is what is left after learned rules fire and must. Two columns is the point: a
    decline produced by widening a tolerance looks identical to one produced by
    learning if there is only one.

    Driven through ``batch_metrics`` with a hand-built resolved set rather than
    through a full run, so it keeps testing the arithmetic even as the learning loop
    that feeds it changes.
    """
    baseline = run(generated_dir, truth_dir)
    result = baseline.results[3]
    metric = baseline.metrics[3]
    flagged = [
        verdict.row_id
        for verdict in result.by_table("settlement_report")
        if verdict.reason is Reason.FEE_OUTSIDE_TOLERANCE
    ]
    assert flagged, "nothing to resolve, so the test would pass vacuously"

    proposals = [
        AutoResolution(result.batch, "settlement_report", row_id, "commission_rate_stale")
        for row_id in flagged
    ]
    resolved = batch_metrics(
        result,
        records_processed=metric.records_processed,
        usage=metric.usage,
        pricing=baseline.pricing,
        aging=baseline.aging,
        resolved=resolved_row_keys(proposals),
        seconds=metric.seconds,
    )
    assert resolved.review_rate == metric.review_rate, "the matcher's number is untouched"
    assert resolved.auto_resolved == len(flagged)
    assert resolved.net_review_rate < resolved.review_rate


def test_the_review_rate_series_is_published_for_the_chart(score: Score) -> None:
    series = to_json(score)["totals"]["review_rate_series_pct"]
    assert len(series) == BATCH_COUNT
    assert series == [str(metric.net_review_rate) for metric in score.metrics]


def test_precision_scores_a_proposed_cause_against_the_answer_key() -> None:
    key = {("settlement_report", "st_1"): "commission_rate_stale",
           ("settlement_report", "st_2"): "short_payment_unexplained"}
    proposals = [
        AutoResolution(1, "settlement_report", "st_1", "commission_rate_stale"),
        AutoResolution(1, "settlement_report", "st_2", "commission_rate_stale"),
    ]
    assert auto_resolution_precision(proposals, key) == D("50.00"), (
        "the near-miss row is the one a stale-rate rule gets wrong"
    )


# --------------------------------------------------------------------------- #
# Honesty
# --------------------------------------------------------------------------- #


def test_every_quarantined_row_is_counted_with_its_reason(score: Score) -> None:
    summary = quarantine_summary(score.results)
    planted = sum(len(kinds) for kinds in score.key.malformed_rows.values())
    assert summary.total == planted
    assert sum(count for _, count in summary.by_reason) == planted


def test_the_exceptions_file_itemises_what_the_run_could_not_resolve(score: Score) -> None:
    rendered = render_exceptions(score.results, score.aging)
    assert rendered.startswith("# EXCEPTIONS")
    for result in score.results:
        for verdict in score.new_findings(result)[:5]:
            assert verdict.row_id in rendered


def test_the_exceptions_file_never_lists_an_order_that_is_merely_waiting(score: Score) -> None:
    rendered = render_exceptions(score.results, score.aging)
    assert "awaiting_settlement_in_window" not in rendered


def test_open_exceptions_exclude_clean_matches(score: Score) -> None:
    for result in score.results:
        assert all(v.bucket is not Bucket.MATCHED for v in open_exceptions(result))


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_the_json_artifact_is_identical_run_to_run(generated_dir: Path, truth_dir: Path) -> None:
    first = to_json(run(generated_dir, truth_dir))
    second = to_json(run(generated_dir, truth_dir))
    assert first.pop("timings")["reproducible"] is False
    second.pop("timings")
    assert first == second


def test_the_text_report_is_identical_run_to_run_apart_from_the_clock(
    generated_dir: Path, truth_dir: Path
) -> None:
    def without_timings(text: str) -> str:
        start = text.index("ACCURACY")
        return text[start:]

    first = to_text(run(generated_dir, truth_dir))
    second = to_text(run(generated_dir, truth_dir))
    assert without_timings(first) == without_timings(second)


def test_no_money_value_reaches_the_artifact_as_a_float(score: Score) -> None:
    """Money is Decimal everywhere, so it goes to disk as text.

    The only floats in the file are the wall-clock seconds under ``timings``, which
    is the block labelled as the one part that does not reproduce.
    """

    def floats_in(node: object, path: str = "") -> list[str]:
        if isinstance(node, float):
            return [path]
        if isinstance(node, dict):
            return [p for k, v in node.items() for p in floats_in(v, f"{path}.{k}")]
        if isinstance(node, list):
            return [p for i, v in enumerate(node) for p in floats_in(v, f"{path}[{i}]")]
        return []

    payload = to_json(score)
    timings = payload.pop("timings")
    assert floats_in(payload) == []
    assert json.dumps(payload), "serialises with no custom encoder"
    assert payload["totals"]["injected_impact_inr"] == str(score.key.total_impact_inr)
    assert floats_in(timings), "seconds are floats, and they live only here"


def test_aging_marks_the_first_sighting_and_only_the_first(score: Score) -> None:
    index = aging_module.index(score.results)
    for result in score.results:
        for verdict in result.verdicts:
            key = (verdict.table, verdict.row_id, verdict.reason.value)
            assert index.first_seen[key] <= result.batch
