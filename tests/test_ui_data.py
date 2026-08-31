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
