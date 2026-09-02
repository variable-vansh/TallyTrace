"""The UI reads one scored run, and it has to be the same run the harness scored.

Two artifacts leave a `make demo`: ``data/score.json``, which anyone auditing this
would read, and ``ui/public/tallytrace.json``, which the browser reads. They are
built from the same :class:`Score`, and these tests hold them to it — a dashboard
quoting a different precision from the terminal is the exact failure the "one source"
design is meant to prevent.

The float boundary is asserted here too. Everywhere else in the repo a rupee is a
``Decimal``; the UI file is the single deliberate exception, and ``score.json`` must
stay string-typed so the audited artifact never inherits binary rounding.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from harness.score import to_json
from tools.build_ui_data import build, money

D = Decimal


@pytest.fixture(scope="module")
def artifacts(scored):
    return to_json(scored), build(scored)


# --------------------------------------------------------------------------- #
# The two artifacts describe one run
# --------------------------------------------------------------------------- #


def test_the_headline_numbers_match_between_the_terminal_and_the_browser(artifacts) -> None:
    score_json, ui = artifacts
    totals = score_json["totals"]
    assert D(str(ui["overallPrecision"])) == D(totals["auto_resolution_precision_pct"])
    assert len(ui["weeks"]) == len(score_json["batches"])
    assert len(ui["rules"]) == totals["rules_total"]


def test_every_review_series_matches_the_scored_one(artifacts) -> None:
    score_json, ui = artifacts
    totals = score_json["totals"]
    for ui_key, score_key in (
        ("reviewRateTrend", "review_rate_series_pct"),
        ("matcherReviewRateTrend", "matcher_review_rate_series_pct"),
        ("touchpointRateTrend", "touchpoint_rate_series_pct"),
    ):
        assert [D(str(v)) for v in ui[ui_key]] == [D(v) for v in totals[score_key]], ui_key


def test_each_week_reports_the_row_counts_the_harness_reported(artifacts) -> None:
    score_json, ui = artifacts
    for week, batch in zip(ui["weeks"], score_json["batches"]):
        assert week["week"] == batch["batch"]
        assert week["stats"]["totalTransactions"] == batch["settlement_rows"]
        assert week["stats"]["autoMatched"] == batch["buckets"]["matched"]
        assert week["stats"]["autoResolved"] == batch["auto_resolved"]
        assert len(week["transactions"]) == batch["settlement_rows"]


def test_the_abstention_result_is_not_restated_differently_for_the_browser(artifacts) -> None:
    score_json, ui = artifacts
    assert ui["abstention"] == score_json["learning"]["abstention"]


def test_the_model_and_its_provenance_travel_to_the_ui(artifacts) -> None:
    """If the token counts are estimated, the UI file has to know — a dashboard that
    quotes a cost without the caveat the terminal prints is the caveat going missing."""
    score_json, ui = artifacts
    assert ui["model"] == score_json["totals"]["llm_model"]
    assert ui["tokensEstimated"] == score_json["totals"]["llm_tokens_estimated"]


# --------------------------------------------------------------------------- #
# The float boundary
# --------------------------------------------------------------------------- #


def test_money_is_the_only_way_a_rupee_becomes_a_float() -> None:
    assert money(D("32.73")) == 32.73
    assert money("1698.04") == 1698.04
    assert money(None) is None


def test_the_audited_artifact_keeps_every_amount_as_a_string(artifacts) -> None:
    """``score.json`` is the file someone would check the numbers in. A float in it is
    a rounding error waiting to be quoted back."""
    score_json, _ = artifacts
    stripped = dict(score_json)
    stripped.pop("timings")          # wall clock is a float on purpose and is labelled
    floats: list[str] = []

    def walk(node, path="") -> None:
        if isinstance(node, float):
            floats.append(path)
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(stripped)
    assert floats == [], floats


def test_the_ui_file_carries_every_case_the_run_decided(artifacts) -> None:
    _, ui = artifacts
    for week in ui["weeks"]:
        outcomes = {e["outcome"] for e in week["exceptions"]}
        assert outcomes <= {
            "auto_resolved", "held_by_guardrail", "shadow_prediction",
            "rules_disagree", "no_rule_matched",
        }
        for exception in week["exceptions"]:
            if exception["outcome"] == "auto_resolved":
                assert len(exception["guardrails"]) == 3
                assert exception["ruleId"] and exception["sourceResolutionId"]


def test_the_ui_file_is_deterministic(scored) -> None:
    """Two builds from one score produce identical bytes, so a redeploy is a no-op
    unless something actually changed."""
    assert json.dumps(build(scored), sort_keys=True) == json.dumps(build(scored), sort_keys=True)


# --------------------------------------------------------------------------- #
# Checkpoint 4 — claims and the reporting surface
# --------------------------------------------------------------------------- #


def test_the_claims_the_browser_sees_are_the_claims_the_harness_scored(artifacts) -> None:
    score_json, ui = artifacts
    assert len(ui["claims"]) == score_json["totals"]["claims_opened"]
    assert ui["claimsQueue"]["header"] == score_json["totals"]["claims_queue_header"]
    statuses = [claim["status"] for claim in ui["claims"]]
    assert statuses.count("recovered") == score_json["totals"]["claims_recovered"]
    assert statuses.count("expired") == score_json["totals"]["claims_expired"]


def test_a_claims_rupee_survives_the_float_boundary(artifacts) -> None:
    """The UI gets JSON numbers because charts do arithmetic; score.json keeps strings."""
    _, ui = artifacts
    for claim in ui["claims"]:
        assert isinstance(claim["amount"], float)
        assert D(str(claim["amount"])) == D(claim["amount_inr"])


def test_the_pinned_metrics_the_browser_shows_are_the_ones_the_harness_recomputed(
    artifacts, scored
) -> None:
    _, ui = artifacts
    pins = ui["reporting"]["pins"]
    assert len(pins) == len(scored.reporting.pins)
    for shown, (pin, result) in zip(pins, scored.reporting.pins):
        assert shown["pin_id"] == pin.pin_id
        assert shown["result"]["metric_id"] == result.metric_id
        assert [D(str(p["value"])) for p in shown["result"]["points"]] == [
            point.value for point in result.points
        ]


def test_a_refused_question_reaches_the_browser_with_no_result(artifacts) -> None:
    """The refusal is shown, not hidden, and nothing plausible is shown beside it."""
    _, ui = artifacts
    declined = [q for q in ui["reporting"]["questions"] if q["outcome"] != "mapped"]
    assert declined
    for question in declined:
        assert question["result"] is None
        assert question["metric_id"] is None
        assert question["refusal"] or question["clarifying_question"]


def test_the_take_rate_chart_is_a_percentage_and_not_rupees(artifacts) -> None:
    """Checkpoint 4 Part B item 4. An absolute fee line rises because batches grow."""
    _, ui = artifacts
    for key in ("takeRateByBatch", "takeRateByChannel", "commissionShareByChannel"):
        assert ui["reporting"][key]["unit"] == "pct", key
        assert ui["reporting"][key]["total"] is None, key


def test_the_browsers_headline_totals_agree_with_the_terminals(artifacts) -> None:
    """One run, one set of numbers. The dashboard cannot quote a different figure."""
    score_json, ui = artifacts
    scored, shown = score_json["totals"], ui["totals"]
    for key in (
        "records_processed", "settlement_rows", "open_exceptions", "claims_opened",
        "claims_recovered", "claims_expired", "claims_open", "registered_metrics",
        "questions_asked", "questions_mapped", "questions_declined", "pinned_metrics",
    ):
        assert shown[key] == scored[key], key
    for key in ("rupees_recovered", "rupees_expired", "claim_recovery_rate_pct"):
        assert D(str(shown[key])) == D(scored[key]), key


def test_every_registered_metric_is_precomputed_for_the_browser(artifacts) -> None:
    """The ask surface renders a lookup, because the registry is Python and Decimal.

    A metric the operator can pick but the browser cannot render would be a dead button,
    so every id at every grouping it supports has to be in the payload.
    """
    from pipeline.metrics.registry import REGISTRY

    results = artifacts[1]["reporting"]["results"]
    expected = {
        f"{metric.metric_id}|{grouping}"
        for metric in REGISTRY.values()
        for grouping in metric.groupings
    }
    assert set(results) == expected
    for key, result in results.items():
        metric_id, grouping = key.split("|")
        assert result["metric_id"] == metric_id and result["group_by"] == grouping
        assert result["points"], f"{key} precomputed to nothing"


def test_a_precomputed_result_matches_a_fresh_computation(artifacts, scored) -> None:
    """The lookup the browser renders is the same number the registry produces."""
    from pipeline.metrics.registry import MetricParams, compute

    results = artifacts[1]["reporting"]["results"]
    for key, shown in results.items():
        metric_id, grouping = key.split("|")
        fresh = compute(metric_id, scored.reporting.corpus, MetricParams(group_by=grouping))
        assert [D(str(p["value"])) for p in shown["points"]] == [
            point.value for point in fresh.points
        ], key


def test_every_logged_question_carries_what_the_ask_screen_needs(artifacts) -> None:
    """The conversation renders from these fields; a missing one is a blank bubble."""
    for entry in artifacts[1]["reporting"]["questions"]:
        assert entry["question"] and entry["restatement"]
        assert entry["outcome"] in {"mapped", "clarify", "refuse"}
        if entry["outcome"] == "mapped":
            assert entry["metric_id"] and entry["params"]["group_by"]
        elif entry["outcome"] == "clarify":
            assert entry["clarifying_question"]
        else:
            assert entry["refusal"]
