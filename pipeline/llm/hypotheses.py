"""LLM job 1 -- why did this row fail to match?

The model is asked about a *question*, not about a row. Two Myntra orders both
billed 8.8% over the books' expectation, both short, both under the same reason
code, are the same question asked twice; the corpus asks 400 exceptions' worth of
them and only 35-odd distinct ones. Deduplicating is not a shortcut:

- asking the identical question 89 times produces 89 identical answers and a cost
  report that overstates the model's contribution by two orders of magnitude;
- a hypothesis that differs between two numerically identical rows is a
  non-determinism, not an insight.

So the prompt is built from a normalised signature -- channel, reason code,
direction, variance percentages, lag band, transaction type -- and the case's own
rupee figures are shown beside the hypothesis in the UI, straight from the matcher's
verdict detail, where they are exact rather than paraphrased.

The cause is constrained by the schema, not by the prompt. If the model returns
something outside the frozen enum, validation raises and the run stops.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pipeline.cases import CaseFeatures, ExceptionCase
from pipeline.llm.client import Ask, LlmClient
from pipeline.llm.schemas import Hypothesis

TENTH = Decimal("0.1")

SYSTEM = """You are the reconciliation analyst for a multi-channel Indian apparel seller \
selling on Amazon, Flipkart, Myntra, its own website and offline POS.

A deterministic matcher has already compared the platform settlement report, the bank \
statement and the seller's internal ledger. It has told you what it observed. Your only \
job is to say what most likely caused it, in words a bookkeeper would recognise.

Rules you must follow:
- Choose exactly one cause from the enum in the schema. Never invent one.
- The matcher's reason code is an observation, not a cause. "fee variance outside \
tolerance" is what was seen; "the ledger's commission rate is out of date" is a cause.
- A percentage that is identical across many orders on one channel is systematic, not \
random. A percentage that varies order by order is not a rate problem.
- Money arriving in a different settlement cycle than the books expected is a timing \
difference, not a loss.
- Be honest about confidence. If the observation is consistent with more than one cause, \
say so in the hypothesis and lower the confidence. Overconfidence on an ambiguous row is \
worse than abstention."""

TOOL = "record_hypothesis"


def _bucket(days: int | None) -> str | None:
    """Day counts as bands. A rule is about a lag, not about a specific Tuesday."""
    if days is None:
        return None
    for low, high in ((1, 7), (8, 14), (15, 21)):
        if low <= days <= high:
            return f"{low}-{high}"
    return "0" if days <= 0 else "22+"


def _pct(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(TENTH))


@dataclass(frozen=True)
class Question:
    """The normalised thing being asked. Equal questions share one cached answer."""

    reason: str
    channel: str | None
    direction: str
    fee_variance_pct: str | None
    net_variance_pct: str | None
    days_after_settlement: str | None
    days_late: str | None
    transaction_type: str | None

    def render(self) -> str:
        lines = [
            "A reconciliation exception from this week's batch.",
            "",
            f"matcher reason code : {self.reason}",
            f"channel             : {self.channel or 'not channel-specific'}",
            f"money direction     : {self.direction} "
            "(short = the seller received less than the books expected)",
        ]
        if self.fee_variance_pct is not None:
            lines.append(
                f"fee variance        : {self.fee_variance_pct}% of the fee the ledger expected"
            )
        if self.net_variance_pct is not None:
            lines.append(
                f"net variance        : {self.net_variance_pct}% of the net the ledger expected"
            )
        if self.days_after_settlement is not None:
            lines.append(
                f"arrived             : {self.days_after_settlement} days after this order "
                "had already settled and closed"
            )
        if self.days_late is not None:
            lines.append(
                f"payout lateness     : {self.days_late} days past the 21-day settlement window"
            )
        if self.transaction_type is not None:
            lines.append(f"row type            : {self.transaction_type}")
        lines += ["", "What caused this?"]
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "channel": self.channel,
            "direction": self.direction,
            "fee_variance_pct": self.fee_variance_pct,
            "net_variance_pct": self.net_variance_pct,
            "days_after_settlement": self.days_after_settlement,
            "days_late": self.days_late,
            "transaction_type": self.transaction_type,
        }


def question_for(features: CaseFeatures) -> Question:
    return Question(
        reason=features.reason,
        channel=features.channel,
        direction=features.direction,
        fee_variance_pct=_pct(features.fee_variance_pct),
        net_variance_pct=_pct(features.net_variance_pct),
        days_after_settlement=_bucket(features.days_after_settlement),
        days_late=_bucket(features.days_late),
        transaction_type=features.transaction_type,
    )


def ask_for(question: Question) -> Ask:
    return Ask(
        task="hypothesis",
        system=SYSTEM,
        user=question.render(),
        output=Hypothesis,
        tool_name=TOOL,
    )


def hypothesise(client: LlmClient, case: ExceptionCase) -> Hypothesis:
    """One case's hypothesis. Cached by question, billed to the case's batch."""
    return client.ask(ask_for(question_for(case.features)), case.batch, Hypothesis)


def questions_in(cases: list[ExceptionCase]) -> list[Question]:
    """Every distinct question a queue asks, in a stable order."""
    seen: dict[Question, None] = {}
    for case in cases:
        seen.setdefault(question_for(case.features), None)
    return list(seen)
