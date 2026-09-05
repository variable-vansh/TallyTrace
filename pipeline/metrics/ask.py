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
from pipeline.metrics.vocabulary import Refusal, check

MAPPED = "mapped"
CLARIFY = "clarify"
REFUSE = "refuse"


class NotConfirmed(RuntimeError):
    """Tried to compute a plan nobody confirmed. Refused: confirm-before-compute is the point."""


class Unsupported(RuntimeError):
    """A slot names something the registry does not have. Names the term; offers no substitute."""

    def __init__(self, refusal: Refusal) -> None:
        super().__init__(refusal.message)
        self.refusal = refusal


@dataclass(frozen=True)
class Plan:
    """What would be computed, if a human said yes. Holds no results and computes none."""

    question: str
    intent: MetricIntent

    @property
    def outcome(self) -> str:
        return self.intent.outcome

    @property
    def vocabulary_refusal(self) -> Refusal | None:
        """The first slot this plan fills with a term the registry does not have.

        Checked deterministically, by lookup, whatever the model said the outcome was.
        The schema already stops the model naming an unregistered metric, but the
        schema cannot stop a *grouping* a metric does not support, and it is not the
        thing that should be trusted to notice either way.
        """
        if self.intent.outcome != MAPPED:
            return None
        return check(
            self.intent.metric_id,
            self.intent.group_by,
            None if self.intent.channel is None else self.intent.channel.value,
            (self.intent.from_batch, self.intent.to_batch),
        )

    @property
    def answerable(self) -> bool:
        """Mapped by the model *and* every slot in vocabulary. Both, or it is refused."""
        return self.intent.outcome == MAPPED and self.vocabulary_refusal is None

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
        refusal = self.vocabulary_refusal
        return {
            "question": self.question,
            "outcome": self.outcome,
            "restatement": self.restatement,
            "metric_id": self.intent.metric_id,
            "clarifying_question": self.intent.clarifying_question,
            # The model's sentence where it wrote one; the lookup's where the model
            # mapped a question it should not have. The lookup wins.
            "refusal": refusal.message if refusal is not None else self.intent.refusal,
            "unsupported_term": None if refusal is None else refusal.to_json(),
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
    refusal = plan.vocabulary_refusal
    if refusal is not None:
        raise Unsupported(refusal)
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
        metric_version=get(str(plan.intent.metric_id)).version,
        params=plan.params,
        pinned_by=pinned_by,
        pinned_at=pinned_at.isoformat(),
        source_question=plan.question,
    )
