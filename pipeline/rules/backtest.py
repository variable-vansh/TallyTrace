"""Scoring a candidate against everything the operator has already resolved.

Deterministic, pure, and there is no model anywhere in this file. Induction is
language work; asking "how would this rule have done on the last six weeks?" is
counting, and counting is the only thing allowed to decide whether a rule earns the
right to predict.

Four numbers, and they answer four different objections:

- **coverage** -- how many resolved cases it fires on. A rule that fires on nothing
  explains nothing, however elegant its band.
- **precision** -- of the cases it fires on, how often its cause is the cause the
  human's own words implied. Measured against the operator, never against the
  generator's answer key: that key belongs to the harness, and a rule with sight of
  it would be marking its own homework.
- **conflicts** -- cases where it fires on a record an active rule already claims,
  with a different action. Two rules that disagree about what to *do* with a row is
  not a tie to be broken by precision; it is a case the system does not understand.
- **support** -- how many *distinct demonstrations* are consistent with it. This is
  the number the gate is on, and it is deliberately not coverage: a rule that fires
  on eighty rows the operator resolved with one sentence has one demonstration behind
  it, not eighty. Eighty rows of the same thing is one piece of evidence.

The support gate is what makes the difference between a system that induces rules and
one that induces rules it can defend. A single demonstration is an anecdote: it is
exactly enough to write a rule that explains the row it came from and nothing else,
and it will backtest at 100% precision on that row while being wrong about the world.
``config/thresholds.yaml`` sets how many are required and nothing in this file
assumes a value.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Sequence

from pipeline.cases import CaseFeatures
from pipeline.rules.models import Rule
from pipeline.rules.predicates import matches, specificity


@dataclass(frozen=True)
class Demonstration:
    """One exception a human resolved, and what their words turned out to mean.

    ``cause`` is the cause the resolution *induced to* -- the same signal
    :func:`pipeline.learn._agrees` judges shadow predictions against. It is what the
    operator implied, not what the answer key says, because the product only ever
    finds out it was wrong the way a colleague would: by being told.
    """

    resolution_id: str
    case_id: str
    batch: int
    features: CaseFeatures
    cause: str

    def to_json(self) -> dict[str, Any]:
        return {
            "resolution_id": self.resolution_id,
            "case_id": self.case_id,
            "batch": self.batch,
            "cause": self.cause,
        }


def _action(rule: Rule) -> tuple[str, str | None, Decimal | None]:
    """What a rule does, as the thing two rules can disagree about."""
    return (rule.action_type, rule.action_field, rule.action_value)


@dataclass(frozen=True)
class BacktestScore:
    """How a candidate would have done, and on which records.

    ``fired_on`` carries the actual case ids rather than a count, because the card a
    human approves has to be able to show them the rows it would have acted on. A
    number they cannot open is not evidence.
    """

    coverage: int
    agreements: int
    conflicts: int
    support: int
    fired_on: tuple[str, ...]
    supporting_resolution_ids: tuple[str, ...]

    @property
    def precision(self) -> Decimal | None:
        """Agreement rate over the cases it fired on. None when it fired on nothing."""
        if self.coverage == 0:
            return None
        return (Decimal(self.agreements) / Decimal(self.coverage)).quantize(Decimal("0.0001"))

    def to_json(self) -> dict[str, Any]:
        return {
            "coverage": self.coverage,
            "agreements": self.agreements,
            "conflicts": self.conflicts,
            "support": self.support,
            "precision": None if self.precision is None else str(self.precision),
            "fired_on": list(self.fired_on),
            "supporting_resolution_ids": list(self.supporting_resolution_ids),
        }


def _conflicts_with_active(
    features: CaseFeatures, candidate: Rule, active: Sequence[Rule]
) -> bool:
    """Does an active rule claim this record and want something else done to it?"""
    return any(
        matches(rule, features) and _action(rule) != _action(candidate)
        for rule in active
    )


def backtest(
    candidate: Rule, history: Sequence[Demonstration], active: Sequence[Rule] = ()
) -> BacktestScore:
    """Score one candidate over the full history of resolved exceptions.

    Pure: it reads the candidate, the history and the active rule set, and returns
    numbers. It writes nothing, ranks nothing against anything else, and decides
    nothing -- :func:`survivors` applies the threshold and a human approves the card.
    """
    fired_on: list[str] = []
    supporting: list[str] = []
    agreements = 0
    conflicts = 0
    seen_resolutions: set[str] = set()

    for demonstration in history:
        if not matches(candidate, demonstration.features):
            continue
        fired_on.append(demonstration.case_id)
        agreed = candidate.cause == demonstration.cause
        if agreed:
            agreements += 1
            if demonstration.resolution_id not in seen_resolutions:
                seen_resolutions.add(demonstration.resolution_id)
                supporting.append(demonstration.resolution_id)
        if _conflicts_with_active(demonstration.features, candidate, active):
            conflicts += 1

    return BacktestScore(
        coverage=len(fired_on),
        agreements=agreements,
        conflicts=conflicts,
        support=len(supporting),
        fired_on=tuple(fired_on),
        supporting_resolution_ids=tuple(supporting),
    )


@dataclass(frozen=True)
class ScoredCandidate:
    """A candidate, the level it sits at on the ladder, and how it did."""

    level: str
    rule: Rule
    score: BacktestScore

    def to_json(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "rule_id": self.rule.rule_id,
            "plain_words": self.rule.plain_words,
            "cause": self.rule.cause,
            "specificity": specificity(self.rule),
            **self.score.to_json(),
        }


def survivors(
    scored: Iterable[ScoredCandidate], min_support: int
) -> tuple[list[ScoredCandidate], list[ScoredCandidate]]:
    """Split scored candidates into (kept, discarded) on the support threshold.

    Returns both halves rather than filtering silently. What was thrown away and why
    is reportable -- a run that discarded nine candidates to keep two has said
    something about how much of what the model proposes is worth acting on, and that
    number belongs in the harness rather than in a comment.

    Ordering is deterministic: most supported first, then most precise, then most
    specific, then by ladder rung and plain words. Deliberately not by rule id --
    candidates are scored before any id is handed out, so an id-based tiebreak would
    order them by an identifier that does not exist yet. Nothing here selects a
    winner: the order is what a card renders in, and a human still chooses.
    """
    kept: list[ScoredCandidate] = []
    discarded: list[ScoredCandidate] = []
    for candidate in scored:
        (kept if candidate.score.support >= min_support else discarded).append(candidate)

    def order(item: ScoredCandidate) -> tuple[Any, ...]:
        precision = item.score.precision or Decimal(0)
        return (
            -item.score.support, -precision, -specificity(item.rule),
            item.level, item.rule.plain_words,
        )

    kept.sort(key=order)
    discarded.sort(key=order)
    return kept, discarded
