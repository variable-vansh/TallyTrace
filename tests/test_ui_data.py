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
import re
from decimal import Decimal

import pytest

from harness.metrics import pct
from harness.score import to_json
from pipeline.config import REPO_ROOT
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


def test_the_weekly_automation_split_partitions_the_batch_exactly(artifacts) -> None:
    """The dashboard's three-way split is a real part-to-whole.

    ``AutomationSplit`` draws auto-matched / AI-resolved / manual as one 100% bar,
    both for the selected week and stacked per week. A part-to-whole is only honest
    if the three add to every settlement row -- no residual quietly dropped, no row
    counted in two slices -- and the component derives the manual slice by
    subtraction, so this is the assertion that stops a subtraction that goes
    negative or a total that does not close.
    """
    for week in artifacts[1]["weeks"]:
        stats = week["stats"]
        human = stats["flaggedForReview"] - stats["autoResolved"]
        assert human >= 0, f"week {week['week']} resolved more rows than it flagged"
        assert stats["autoMatched"] + stats["flaggedForReview"] == stats["totalTransactions"], (
            f"week {week['week']} does not partition: "
            f"{stats['autoMatched']} + {stats['flaggedForReview']} "
            f"!= {stats['totalTransactions']}"
        )
        # The manual share of the bar is the review rate the hero figure shows.
        assert pct(human, stats["totalTransactions"]) == D(str(stats["manualReviewRate"]))


def test_the_threshold_control_is_backed_by_real_scored_runs(artifacts) -> None:
    """The ceiling control in the UI offers numbers, so those numbers have to be measured.

    ``tools/ceiling_sweep.py`` scores the whole corpus once per candidate ceiling and
    writes the curve; the UI renders it and refuses to interpolate a ceiling that is not
    on it. If the file is absent the control degrades to showing the ceiling in force,
    which is why this skips rather than fails.
    """
    policy = artifacts[1]["autoResolutionPolicy"]
    sweep = policy.get("scenarios")
    if sweep is None:
        pytest.skip("run `make ceilings` first")

    assert sweep["configured_ceiling_inr"] == policy["default"]["max_variance_inr"]
    ceilings = [D(s["ceiling_inr"]) for s in sweep["scenarios"]]
    assert ceilings == sorted(ceilings), "the curve is rendered in order and must be stored in it"
    assert D(policy["default"]["max_variance_inr"]) in ceilings, (
        "the ceiling in force is not on the curve, so the control has nothing to compare to"
    )


def test_the_two_precision_series_agree_at_the_shipped_ceiling_and_only_there(artifacts) -> None:
    """The finding the control exists to show, asserted rather than described.

    Live precision is judged against the operator's own words; true precision against
    the answer key. They agree at the shipped ceiling. They come apart as it rises,
    because a rule and an operator can be wrong in the same direction and the bigger the
    row, the more often they are — so a ceiling chosen on live precision alone rises
    forever. If this ever stops being true the README's argument is wrong and the
    control is recommending the wrong thing.
    """
    sweep = artifacts[1]["autoResolutionPolicy"].get("scenarios")
    if sweep is None:
        pytest.skip("run `make ceilings` first")

    shipped = sweep["configured_ceiling_inr"]
    by_ceiling = {s["ceiling_inr"]: s for s in sweep["scenarios"]}
    assert D(by_ceiling[shipped]["precision_gap_pct"]) == D("0.00")

    # The claim is a trend, not a step-by-step ordering: ₹900 sits a hundredth below
    # ₹800 because a different rule fires there, and asserting strict monotonicity
    # would make this test about that accident rather than about the finding.
    above = [s for s in sweep["scenarios"] if D(s["ceiling_inr"]) > D(shipped)]
    gaps = [D(s["precision_gap_pct"]) for s in above]
    assert all(gap >= 0 for gap in gaps), (
        f"true precision should never beat live precision above the ceiling: {gaps}"
    )
    assert gaps[-1] > D("5"), "the highest ceiling should show a large gap, or the point is lost"
    assert gaps[-1] > gaps[0], f"the gap should be wider at the top of the range: {gaps}"

    # And the thing the gap costs, stated in rows rather than in points.
    shipped_wrong = by_ceiling[shipped]["wrong"]
    assert above[-1]["wrong"] > shipped_wrong * 5, (
        "the top of the range should close many more rows with the wrong cause"
    )


def test_the_deployed_intent_mapper_mirrors_the_python_registry() -> None:
    """`ui/api/ask.js` holds a copy of the registry, and a copy is a thing that rots.

    The deployed build answers a question outside the fixtures by mapping it onto a
    metric id, and it does that in Node against a different provider — so it cannot
    import :mod:`pipeline.metrics.registry` and has to mirror it. A mirror that drifts
    is worse than no mirror: the model would be offered an id the registry cannot
    compute, or denied one it can, and the failure would surface as a refusal that
    looks like honest behaviour.

    Asserted both ways, on ids and on the groupings each metric supports, so neither a
    new metric nor a widened grouping can ship without the deployed prompt learning
    about it.
    """
    from pipeline.metrics.registry import catalogue

    source = (REPO_ROOT / "ui" / "api" / "ask.js").read_text(encoding="utf-8")

    mirrored = re.findall(
        r"\['([a-z_]+)',\s*'(?:inr|pct|count)',\s*\[([^\]]*)\]", source
    )
    assert mirrored, "could not find the mirrored registry in ui/api/ask.js"

    js = {
        metric_id: sorted(g.strip().strip("'") for g in groupings.split(","))
        for metric_id, groupings in mirrored
    }
    py = {entry["metric_id"]: sorted(entry["groupings"]) for entry in catalogue()}

    assert js == py, (
        "ui/api/ask.js has drifted from pipeline/metrics/registry.py.\n"
        f"  only in the deployed mirror: {sorted(set(js) - set(py))}\n"
        f"  only in the Python registry: {sorted(set(py) - set(js))}\n"
        f"  grouping mismatches: "
        f"{ {k: (js[k], py[k]) for k in set(js) & set(py) if js[k] != py[k]} }"
    )
