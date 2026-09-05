"""Pinned metrics: the model is present at definition and absent from every run after.

That sentence is the entire claim of the reporting surface, so it is asserted rather
than written down. The client is broken first -- every construction path raises -- and
then the whole pinned dashboard is recomputed anyway.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pipeline.llm.schemas import MetricIntent
from pipeline.metrics import pins as pins_module
from pipeline.metrics.ask import NotConfirmed, pin_from, plan_from
from pipeline.metrics.pins import Pin, load, recompute, save
from pipeline.metrics.registry import MetricParams, UnknownMetric


class ModelWasCalled(AssertionError):
    """Raised by the poisoned client. Reaching it is the failure this file exists to catch."""


@pytest.fixture
def no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every route to the model raise, including the cache underneath it."""
    from pipeline.llm import cache as cache_module
    from pipeline.llm import client as client_module

    def poisoned(*args: object, **kwargs: object) -> None:
        raise ModelWasCalled("a pinned metric reached the model")

    monkeypatch.setattr(client_module, "client_from", poisoned)
    monkeypatch.setattr(client_module.LlmClient, "ask", poisoned)
    monkeypatch.setattr(client_module.LlmClient, "__init__", poisoned)
    monkeypatch.setattr(cache_module.ResponseCache, "get", poisoned)


def test_every_pinned_metric_recomputes_with_the_model_broken(scored, no_model) -> None:
    pins = load()
    assert pins, "no metrics are pinned; data/pins.json is empty"

    results = recompute(pins, scored.reporting.corpus)
    assert len(results) == len(pins)
    for pin, result in results:
        assert result.metric_id == pin.metric_id
        assert result.points, f"{pin.name} recomputed to nothing"


def test_recomputing_twice_gives_the_same_numbers(scored, no_model) -> None:
    first = recompute(load(), scored.reporting.corpus)
    second = recompute(load(), scored.reporting.corpus)
    assert [r.to_json() for _, r in first] == [r.to_json() for _, r in second]


def test_a_pin_stores_the_definition_and_never_the_numbers() -> None:
    payload = load()[0].to_json()
    assert set(payload) == {
        "pin_id", "name", "metric_id", "metric_version", "params", "pinned_by",
        "pinned_at", "source_question",
    }
    assert "points" not in payload and "value" not in payload


def test_a_pin_records_the_question_it_came_from() -> None:
    """Not for the computation -- it never reads it -- but for the person who asks why."""
    for pin in load():
        assert pin.source_question.strip(), pin.pin_id


def test_a_pin_naming_an_unregistered_metric_fails_at_load(tmp_path) -> None:
    path = tmp_path / "pins.json"
    save(
        [
            Pin(
                pin_id="pin_01", name="Revenue by SKU", metric_id="revenue_by_sku",
                params=MetricParams(), pinned_by="a@b.c", pinned_at="2025-03-16",
                source_question="which SKUs sell best",
            )
        ],
        path,
    )
    with pytest.raises(UnknownMetric):
        load(path)


def test_a_pin_round_trips_through_disk(tmp_path) -> None:
    original = load()
    path = tmp_path / "pins.json"
    save(original, path)
    assert [pin.to_json() for pin in load(path)] == [pin.to_json() for pin in original]


def test_only_a_confirmed_answerable_plan_can_be_pinned() -> None:
    refusal = plan_from(
        "which SKUs are least profitable",
        MetricIntent.model_validate({
            "outcome": "refuse",
            "refusal": "There is no product master in this reconciliation.",
            "restatement": "Nothing has been computed: no metric answers this.",
        }),
    )
    with pytest.raises(NotConfirmed):
        pin_from(refusal, "SKU profit", "pin_09", "a@b.c", date(2025, 3, 16))


def test_the_pinned_dashboard_matches_what_the_scored_run_reported(scored) -> None:
    """One number, one place. The harness's pinned section and a fresh recompute agree."""
    fresh = recompute(load(), scored.reporting.corpus)
    assert [(p.pin_id, r.to_json()) for p, r in fresh] == [
        (p.pin_id, r.to_json()) for p, r in scored.reporting.pins
    ]


def test_a_pinned_percentage_is_still_a_decimal(scored, no_model) -> None:
    for _, result in recompute(load(), scored.reporting.corpus):
        assert all(isinstance(point.value, Decimal) for point in result.points)


# --------------------------------------------------------------------------- #
# A model version change cannot move a pinned number
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "model_version",
    ["claude-opus-4-1-20250805", "claude-sonnet-4-5-20250929", "a-model-that-does-not-exist"],
)
def test_a_pinned_metric_is_identical_across_model_versions(
    scored, no_model, monkeypatch: pytest.MonkeyPatch, model_version: str
) -> None:
    """The acceptance check, and the sentence worth saying precisely: the model is
    present at the moment of definition and absent from every run afterwards.

    The pinned value is recomputed with ``config/pricing.yaml`` naming a different
    model each time, and with every route to the client poisoned. If a pinned figure
    could move when the model changed, the dashboard would be a thing that quietly
    re-answers last quarter's question with this quarter's model.
    """
    from pipeline import config as config_module

    baseline = {
        pin.pin_id: result.to_json() for pin, result in recompute(load(), scored.reporting.corpus)
    }

    real = config_module.load_yaml

    def with_model(path):
        loaded = real(path)
        if "model" in loaded and "estimated_chars_per_token" in loaded:
            return {**loaded, "model": model_version}
        return loaded

    monkeypatch.setattr(config_module, "load_yaml", with_model)
    config_module.thresholds.cache_clear()

    after = {
        pin.pin_id: result.to_json() for pin, result in recompute(load(), scored.reporting.corpus)
    }
    config_module.thresholds.cache_clear()

    assert after == baseline, f"a pinned metric moved when the model became {model_version}"


def test_a_pinned_result_reports_the_row_count_it_was_derived_from(scored, no_model) -> None:
    """A figure arrives with the size of the thing it was computed from."""
    for _pin, result in recompute(load(), scored.reporting.corpus):
        assert result.row_count > 0
        assert result.to_json()["row_count"] == result.row_count
