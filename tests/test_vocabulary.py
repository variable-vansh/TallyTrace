"""The closed vocabulary, and refusing by lookup rather than by asking the model nicely.

The interesting property is not that unsupported questions are refused -- the schema
already made most of that true. It is that the refusal is produced by deterministic
code, *names the term it could not honour*, and offers no substitute.
"""

from __future__ import annotations

from datetime import date

import pytest

from pipeline.llm.schemas import MetricIntent
from pipeline.metrics.ask import Unsupported, execute, plan_from
from pipeline.metrics.registry import REGISTRY
from pipeline.metrics.vocabulary import (
    CHANNEL,
    GROUPING,
    METRIC,
    Refusal,
    check,
    known_channels,
    known_groupings,
    known_metrics,
    metric_or_refusal,
    vocabulary,
)
from pipeline.models import Channel


def test_the_vocabulary_is_the_registry_and_the_frozen_enums() -> None:
    assert known_metrics() == tuple(sorted(REGISTRY))
    assert known_channels() == tuple(c.value for c in Channel)
    assert set(vocabulary()) == {METRIC, GROUPING, CHANNEL}


def test_an_unknown_metric_is_refused_and_named() -> None:
    """The acceptance check: refused, with the unsupported term named."""
    refusal = check("gross_margin_by_sku", None, None)
    assert refusal is not None
    assert refusal.slot == METRIC
    assert refusal.term == "gross_margin_by_sku"
    assert "gross_margin_by_sku" in refusal.message


def test_a_refusal_shows_what_is_supported_instead() -> None:
    refusal = check("gross_margin_by_sku", None, None)
    assert refusal is not None
    assert set(refusal.supported) == set(known_metrics())
    for metric_id in known_metrics():
        assert metric_id in refusal.message


def test_a_grouping_the_metric_does_not_support_is_refused_by_name() -> None:
    refusal = check("net_revenue_by_channel", "sku", None)
    assert refusal is not None
    assert refusal.slot == GROUPING
    assert refusal.term == "sku"
    assert set(refusal.supported) == set(known_groupings("net_revenue_by_channel"))


def test_an_unknown_channel_is_refused_by_name() -> None:
    refusal = check("net_revenue_by_channel", None, "shopify")
    assert refusal is not None
    assert refusal.slot == CHANNEL
    assert refusal.term == "shopify"


def test_a_batch_outside_the_corpus_is_refused_by_name() -> None:
    refusal = check("review_rate_trend", None, None, (99,), known_batches=(1, 2, 3))
    assert refusal is not None
    assert refusal.term == "99"


def test_everything_in_vocabulary_passes() -> None:
    assert check("net_revenue_by_channel", "channel", "amazon") is None


def test_nothing_is_offered_as_a_near_match() -> None:
    """The tempting failure and the dishonest one. A refusal names the gap and stops."""
    refusal = check("net_revenue_by_sku", None, None)
    assert refusal is not None
    assert "did you mean" not in refusal.message.lower()
    assert "closest" not in refusal.message.lower()
    assert "instead try" not in refusal.message.lower()


def test_a_lookup_never_returns_a_metric_for_an_unsupported_term() -> None:
    metric, refusal = metric_or_refusal("net_revenue_by_sku")
    assert metric is None and refusal is not None

    metric, refusal = metric_or_refusal("net_revenue_by_channel")
    assert metric is not None and refusal is None


# --------------------------------------------------------------------------- #
# The ask path refuses deterministically, whatever the model said
# --------------------------------------------------------------------------- #


def _intent(**kwargs) -> MetricIntent:
    defaults: dict = dict(
        outcome="mapped", metric_id="net_revenue_by_channel", group_by="channel",
        restatement="Net revenue settled per channel across all ten weeks.",
    )
    return MetricIntent(**{**defaults, **kwargs})


def test_a_plan_whose_grouping_is_out_of_vocabulary_is_not_answerable() -> None:
    """The schema constrains the metric id but cannot constrain a grouping *that
    metric* does not declare. The lookup is what catches it."""
    plan = plan_from("net revenue by sku", _intent(group_by="cause"))
    assert plan.vocabulary_refusal is not None
    assert plan.answerable is False


def test_executing_an_out_of_vocabulary_plan_raises_and_names_the_term() -> None:
    plan = plan_from("net revenue by sku", _intent(group_by="cause"))
    with pytest.raises(Unsupported) as caught:
        execute(plan, corpus=None, confirmed=True)  # type: ignore[arg-type]
    assert caught.value.refusal.term == "cause"
    assert "cause" in str(caught.value)


def test_the_refusal_in_the_payload_is_the_lookups_not_the_models() -> None:
    """A refusal written by the thing being refused is a courtesy, not a check."""
    plan = plan_from("net revenue by sku", _intent(group_by="cause"))
    payload = plan.to_json()
    assert payload["unsupported_term"]["term"] == "cause"
    assert "not a supported grouping" in payload["refusal"]


def test_a_plan_entirely_in_vocabulary_is_still_answerable() -> None:
    plan = plan_from("net revenue by channel", _intent())
    assert plan.vocabulary_refusal is None
    assert plan.answerable is True
