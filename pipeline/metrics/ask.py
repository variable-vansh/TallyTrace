"""Ask, confirm, compute, pin. Four steps, and the order is the safety.

The model is present in step one and absent from every step after it:

1. **Plan.** The question goes to :func:`pipeline.llm.intent.map_question` and comes
   back as an id, some parameters and a restatement. *Nothing is computed.* A plan is
   a proposal, and :class:`Plan` holds no data and can produce none.
2. **Confirm.** The restatement is shown and a human says yes. A plan that has not
   been confirmed cannot be executed -- :func:`execute` raises rather than assuming.
3. **Compute.** A pure function over the corpus. No model, no cache, no prompt.
4. **Pin.** A confirmed result can be kept by name. From then on it recomputes every
   batch through step three alone; the model that helped define it never runs again.

That last sentence is the claim worth making about this surface, so it is worth
saying precisely: **the model is present at the moment of definition and absent from
every run afterwards.** ``tests/test_pins.py`` asserts it by breaking the LLM client
and recomputing the whole dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from pipeline.llm.schemas import MetricIntent
from pipeline.metrics.corpus import Corpus
from pipeline.metrics.pins import Pin
from pipeline.metrics.registry import MetricParams, MetricResult, compute, get

MAPPED = "mapped"
CLARIFY = "clarify"
REFUSE = "refuse"


class NotConfirmed(RuntimeError):
    """Tried to compute a plan nobody confirmed. Refused: confirm-before-compute is the point."""


@dataclass(frozen=True)
class Plan:
    """What would be computed, if a human said yes. Holds no results and computes none."""

    question: str
    intent: MetricIntent

    @property
    def outcome(self) -> str:
        return self.intent.outcome

    @property
    def answerable(self) -> bool:
        return self.intent.outcome == MAPPED

    @property
    def restatement(self) -> str:
        return self.intent.restatement

    @property
    def params(self) -> MetricParams:
        metric = get(str(self.intent.metric_id))
        return MetricParams(
            from_batch=self.intent.from_batch,
            to_batch=self.intent.to_batch,
            channel=None if self.intent.channel is None else self.intent.channel.value,
            group_by=self.intent.group_by or metric.groupings[0],
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "outcome": self.outcome,
            "restatement": self.restatement,
            "metric_id": self.intent.metric_id,
            "clarifying_question": self.intent.clarifying_question,
            "refusal": self.intent.refusal,
            "params": self.params.to_json() if self.answerable else None,
        }


def plan_from(question: str, intent: MetricIntent) -> Plan:
    return Plan(question=question, intent=intent)


def execute(plan: Plan, corpus: Corpus, *, confirmed: bool) -> MetricResult:
    """Compute a confirmed plan. Pure, deterministic, and there is no model in this call."""
    if not confirmed:
        raise NotConfirmed(
            f"{plan.question!r} was mapped but not confirmed; the restatement is shown "
            "and answered before anything is computed"
        )
    if not plan.answerable:
        raise NotConfirmed(
            f"{plan.question!r} produced outcome {plan.outcome!r}, which is not a result"
        )
    return compute(str(plan.intent.metric_id), corpus, plan.params)


def pin_from(plan: Plan, name: str, pin_id: str, pinned_by: str, pinned_at: date) -> Pin:
    """Keep a confirmed result. What is stored is the definition, never the numbers."""
    if not plan.answerable:
        raise NotConfirmed(f"cannot pin a plan whose outcome is {plan.outcome!r}")
    return Pin(
        pin_id=pin_id,
        name=name,
        metric_id=str(plan.intent.metric_id),
        params=plan.params,
        pinned_by=pinned_by,
        pinned_at=pinned_at.isoformat(),
        source_question=plan.question,
    )
