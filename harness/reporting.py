"""The reporting surface, replayed and recomputed as part of the scored run.

Two things happen here and they are deliberately different in kind.

**The questions are replayed through the model**, against the cached answers, so the
report shows what the mapping says today rather than what it said the day the log was
written. Their tokens are billed into the same ledger as the hypotheses and the
inductions, so ``₹/txn`` in the throughput table is the cost of the whole system and
not of a convenient subset of it.

**The pins are recomputed without it.** :func:`pipeline.metrics.pins.recompute` is a
pure function over the corpus; nothing in that call path constructs a client, reads
the cache or renders a prompt. That is the claim this surface is built to make and
``tests/test_pins.py`` asserts it by breaking the client first.

Nothing here reads the answer key. The registry computes over the same reconciled
rows the operator's dashboard shows, and a metric only a scored run could produce
would be a metric the product does not have.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pipeline.learn import LearningRun
from pipeline.llm.client import LlmClient
from pipeline.llm.intent import map_question
from pipeline.metrics import corpus_from
from pipeline.metrics.ask import MAPPED, Plan, execute, plan_from
from pipeline.metrics.corpus import Corpus
from pipeline.metrics.pins import Pin, load as load_pins, recompute
from pipeline.metrics.questions import AskedQuestion, load as load_questions
from pipeline.metrics.registry import MetricResult


@dataclass(frozen=True)
class AnsweredQuestion:
    """One question, its mapping, and the result if the operator confirmed one."""

    asked: AskedQuestion
    plan: Plan
    result: MetricResult | None

    @property
    def outcome(self) -> str:
        return self.plan.outcome

    def to_json(self) -> dict[str, Any]:
        return {
            **self.plan.to_json(),
            "asked_by": self.asked.asked_by,
            "asked_at": self.asked.asked_at,
            "confirmed": self.asked.confirmed,
            "pinned_as": self.asked.pin_as,
            "result": None if self.result is None else self.result.to_json(),
        }


@dataclass(frozen=True)
class ReportingScore:
    """The registry surface as it stands at the end of the run."""

    corpus: Corpus
    answers: tuple[AnsweredQuestion, ...]
    pins: tuple[tuple[Pin, MetricResult], ...]

    @property
    def mapped(self) -> int:
        return sum(1 for answer in self.answers if answer.outcome == MAPPED)

    @property
    def declined(self) -> int:
        return len(self.answers) - self.mapped

    def to_json(self) -> dict[str, Any]:
        return {
            "questions": [answer.to_json() for answer in self.answers],
            "mapped": self.mapped,
            "declined": self.declined,
            "pins": [
                {**pin.to_json(), "result": result.to_json()} for pin, result in self.pins
            ],
        }


def score(run: LearningRun, client: LlmClient, batch: int) -> ReportingScore:
    """Replay every logged question, then recompute every pin without a model."""
    corpus = corpus_from(run)
    answers: list[AnsweredQuestion] = []
    for asked in load_questions():
        plan = plan_from(asked.question, map_question(client, asked.question, batch))
        computed = (
            execute(plan, corpus, confirmed=True)
            if plan.answerable and asked.confirmed
            else None
        )
        answers.append(AnsweredQuestion(asked=asked, plan=plan, result=computed))

    return ReportingScore(
        corpus=corpus,
        answers=tuple(answers),
        pins=tuple(recompute(load_pins(), corpus)),
    )
