"""The metric registry: what it computes, what it refuses, and what it never generates."""

from __future__ import annotations

from decimal import Decimal
from typing import get_args

import pytest

from pipeline.llm.schemas import Grouping, MetricId, MetricIntent
from pipeline.metrics.ask import NotConfirmed, execute, plan_from
from pipeline.metrics.corpus import BatchFacts, Corpus, facts_for, order_values
from pipeline.metrics.registry import (
    COUNT,
    INR,
    PERCENT,
    REGISTRY,
    MetricParams,
    UnknownMetric,
    UnsupportedGrouping,
    catalogue,
    compute,
    get,
)

ZERO = Decimal("0.00")


# --------------------------------------------------------------------------- #
# The registry is closed, and the schema mirrors it
# --------------------------------------------------------------------------- #


def test_the_schemas_metric_ids_are_exactly_the_registered_ones() -> None:
    """Mirrored the way ``Cause`` mirrors causes.yaml, and asserted for the same reason.

    The model can only return an id the schema permits. If the schema's list drifts
    from the registry, the model is either offered a metric that does not exist or
    denied one that does -- and both failures are silent at the call site.
    """
    assert sorted(get_args(MetricId)) == sorted(REGISTRY)


def test_every_metric_declares_a_grouping_it_actually_supports() -> None:
    valid = set(get_args(Grouping))
    for metric in REGISTRY.values():
        assert metric.groupings, f"{metric.metric_id} declares no grouping"
        assert set(metric.groupings) <= valid, metric.metric_id


def test_an_unregistered_metric_id_raises_rather_than_finding_a_near_match() -> None:
    with pytest.raises(UnknownMetric):
        get("revenue_by_sku")


def test_a_grouping_a_metric_does_not_support_is_refused(scored) -> None:
    """Refused, not silently ignored: an empty chart reads as an answer."""
    corpus = scored.reporting.corpus
    with pytest.raises(UnsupportedGrouping):
        compute("review_rate_trend", corpus, MetricParams(group_by="channel"))


def test_the_catalogue_the_prompt_is_built_from_comes_from_the_registry() -> None:
    ids = [entry["metric_id"] for entry in catalogue()]
    assert ids == [metric.metric_id for metric in REGISTRY.values()]


# --------------------------------------------------------------------------- #
# Arithmetic
# --------------------------------------------------------------------------- #


def test_the_take_rate_denominator_is_the_orders_the_rows_settle(scored) -> None:
    """Not the orders the batch's ledger booked. See pipeline/metrics/corpus.py.

    A batch is a settlement report, so its ledger file and its settlement rows describe
    different sets of orders. Taking the denominator off the ledger produced a take rate
    that climbed from 5% to 86% across the corpus purely because batch ten settles a
    great deal and books almost nothing.
    """
    corpus = scored.reporting.corpus
    result = compute("effective_take_rate", corpus, MetricParams(group_by="batch"))
    values = [point.value for point in result.points]
    assert all(Decimal("5") < value < Decimal("40") for value in values), values


def test_the_take_rate_is_the_commission_share_plus_the_tax_withheld(scored) -> None:
    corpus = scored.reporting.corpus
    take = compute("effective_take_rate", corpus, MetricParams(group_by="channel"))
    fee = compute("commission_share_of_gross", corpus, MetricParams(group_by="channel"))
    by_channel = {point.label: point.value for point in fee.points}
    for point in take.points:
        assert point.value >= by_channel[point.label], point.label


def test_a_marketplace_take_rate_is_far_above_an_own_channel_one(scored) -> None:
    """A sanity check with a real-world shape: a marketplace keeps a fifth, a gateway 2%."""
    values = {
        point.label: point.value
        for point in compute(
            "effective_take_rate", scored.reporting.corpus, MetricParams(group_by="channel")
        ).points
    }
    assert values["myntra"] > values["amazon"] > values["website"]
    assert values["website"] < Decimal("5")


def test_the_review_rate_metric_agrees_with_the_harness(scored) -> None:
    """Two implementations of one number is one too many, so this asserts they agree."""
    computed = compute(
        "review_rate_trend", scored.reporting.corpus, MetricParams(group_by="batch")
    )
    assert [point.value for point in computed.points] == [
        metric.net_review_rate for metric in scored.metrics
    ]


def test_a_date_range_narrows_the_window(scored) -> None:
    corpus = scored.reporting.corpus
    whole = compute("net_revenue_by_channel", corpus, MetricParams(group_by="channel"))
    early = compute(
        "net_revenue_by_channel", corpus, MetricParams(group_by="channel", from_batch=1, to_batch=4)
    )
    assert early.total < whole.total


def test_a_channel_filter_narrows_to_one_series(scored) -> None:
    result = compute(
        "effective_take_rate",
        scored.reporting.corpus,
        MetricParams(group_by="channel", channel="myntra"),
    )
    assert [point.label for point in result.points] == ["myntra"]


def test_a_percentage_metric_reports_no_total(scored) -> None:
    """Summing ten percentages produces a number, and it is not a number about anything."""
    result = compute(
        "effective_take_rate", scored.reporting.corpus, MetricParams(group_by="batch")
    )
    assert result.to_json()["total"] is None
    assert compute(
        "net_revenue_by_channel", scored.reporting.corpus, MetricParams(group_by="channel")
    ).to_json()["total"] is not None


def test_every_computed_value_is_a_decimal(scored) -> None:
    """Money is Decimal everywhere, and a metric is money more often than not."""
    corpus = scored.reporting.corpus
    for metric in REGISTRY.values():
        result = compute(metric.metric_id, corpus, MetricParams(group_by=metric.groupings[0]))
        assert all(isinstance(point.value, Decimal) for point in result.points)
        assert metric.unit in (INR, PERCENT, COUNT)


def test_a_channel_with_no_rows_divides_to_zero_rather_than_raising() -> None:
    empty = Corpus(facts=(facts_for(1, [], {}),), queues=(), claims=())
    result = compute("effective_take_rate", empty, MetricParams(group_by="batch"))
    assert [point.value for point in result.points] == [ZERO]


# --------------------------------------------------------------------------- #
# Ask, confirm, compute
# --------------------------------------------------------------------------- #


def _intent(**changes) -> MetricIntent:
    payload = {
        "outcome": "mapped",
        "metric_id": "net_revenue_by_channel",
        "group_by": "channel",
        "restatement": "Net revenue settled per channel across the whole corpus.",
    }
    payload.update(changes)
    return MetricIntent.model_validate(payload)


def test_nothing_is_computed_until_a_human_confirms(scored) -> None:
    plan = plan_from("how much did we get paid", _intent())
    with pytest.raises(NotConfirmed):
        execute(plan, scored.reporting.corpus, confirmed=False)
    assert execute(plan, scored.reporting.corpus, confirmed=True).points


def test_a_clarification_is_not_a_result(scored) -> None:
    plan = plan_from(
        "how are our fees trending",
        _intent(outcome="clarify", metric_id=None,
                clarifying_question="Commission alone, or every deduction?",
                restatement="Two metrics answer this, so nothing has been computed."),
    )
    assert plan.answerable is False
    with pytest.raises(NotConfirmed):
        execute(plan, scored.reporting.corpus, confirmed=True)


def test_a_refusal_is_not_a_result(scored) -> None:
    plan = plan_from(
        "which SKUs are least profitable",
        _intent(outcome="refuse", metric_id=None,
                refusal="There is no product master in this reconciliation.",
                restatement="Nothing has been computed: no metric answers this."),
    )
    assert plan.answerable is False
    with pytest.raises(NotConfirmed):
        execute(plan, scored.reporting.corpus, confirmed=True)


def test_an_outcome_without_its_payload_is_rejected_by_the_schema() -> None:
    with pytest.raises(ValueError, match="requires metric_id"):
        MetricIntent.model_validate(
            {"outcome": "mapped", "restatement": "Net revenue per channel, whole corpus."}
        )
    with pytest.raises(ValueError, match="requires refusal"):
        MetricIntent.model_validate(
            {"outcome": "refuse", "restatement": "Nothing in the registry answers this."}
        )


def test_a_declined_outcome_may_not_also_name_a_metric() -> None:
    """Refusing and answering at the same time is the failure this surface exists to avoid."""
    with pytest.raises(ValueError, match="must not name a metric"):
        MetricIntent.model_validate({
            "outcome": "refuse",
            "metric_id": "net_revenue_by_channel",
            "refusal": "no product master",
            "restatement": "Nothing in the registry answers this question.",
        })


def test_the_real_run_refuses_and_clarifies_rather_than_guessing(scored) -> None:
    """Three of eleven logged questions are not answered, and none of them is fudged."""
    outcomes = {answer.asked.question: answer for answer in scored.reporting.answers}
    declined = [a for a in outcomes.values() if a.outcome != "mapped"]
    assert len(declined) == 3
    for answer in declined:
        assert answer.result is None
        assert answer.plan.intent.metric_id is None


def test_a_platform_with_nothing_settled_is_omitted_not_plotted_at_zero(scored) -> None:
    """Six website chargebacks are open and none has settled either way.

    "0% recovery on website" would put a failure on screen where there is only an
    unfinished filing window. A metric with no denominator has no value, and on a bar
    chart the honest way to say that is to have no bar.
    """
    from pipeline.claims.models import ClaimStatus

    result = compute(
        "claim_recovery_rate", scored.reporting.corpus, MetricParams(group_by="platform")
    )
    plotted = {point.label for point in result.points}
    for platform in {claim.platform for claim in scored.claims.claims}:
        claims = [c for c in scored.claims.claims if c.platform == platform]
        settled = [c for c in claims if c.status in (ClaimStatus.RECOVERED, ClaimStatus.EXPIRED)]
        assert (platform in plotted) == bool(settled), platform
    assert "website" not in plotted


def test_a_windowed_series_never_reports_a_batch_outside_the_window(scored) -> None:
    """A claim opened in the window can expire outside it; that rupee belongs to no bar."""
    result = compute(
        "rupees_expired_unrecovered",
        scored.reporting.corpus,
        MetricParams(group_by="batch", from_batch=1, to_batch=5),
    )
    assert [point.label for point in result.points] == [f"batch {n}" for n in range(1, 6)]


def test_the_whole_corpus_series_still_totals_every_expired_rupee(scored) -> None:
    """The window guard must not have quietly dropped money from the full-corpus view."""
    result = compute(
        "rupees_expired_unrecovered", scored.reporting.corpus, MetricParams(group_by="batch")
    )
    assert result.total == scored.claims.rupees_expired
