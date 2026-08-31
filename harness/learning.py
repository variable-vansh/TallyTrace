"""Scoring the learning loop against the answer key.

Checkpoint 2 left seams for exactly these numbers and they are filled here rather
than inside ``pipeline/``, because every one of them needs the answer key and the
pipeline may never see it. The key arrives as an already-loaded :class:`AnswerKey`;
``harness/truth.py`` stays the only module in the repo that names the path.

Four things this module measures, and one it deliberately measures twice:

**Auto-resolution precision.** Of the cases a rule closed without a human, how many
carried the cause the rule claimed? Scored per batch, because a precision that holds
while the volume grows is a different claim from one averaged over ten weeks.

**Abstention correctness.** The held-out causes -- promotional co-funding from batch
7, chargebacks from batch 9 -- must not be auto-resolved on first sight. Refusing
correctly is the hardest behaviour to fake and it is measured as a rate, not asserted.

**Rule lifecycle.** Learned, promoted and retired per batch.

**Rupees.** Auto-resolved against escalated, because a system that automates a
thousand ten-rupee variances and escalates every four-figure one is behaving well and
a count alone would call it lazy.

The doubled measure is **live precision versus true precision**. A rule's live
precision comes from what the operator said; its true precision comes from the key. Where they differ, the operator and the system were fooled by the same row --
which is what the near-miss in the corpus exists to produce, and it is worth seeing
the two numbers apart rather than trusting either alone.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from harness.metrics import AutoResolution, pct
from harness.truth import AnswerKey
from pipeline.learn import BatchLearning, LearningRun
from pipeline.rules.apply import AUTO_RESOLVED, HELD

ZERO = Decimal("0.00")

def cause_by_key(key: AnswerKey) -> dict[tuple[str, str], str]:
    """``(table, row_id) -> true cause``, for every injected row and affected order."""
    lookup: dict[tuple[str, str], str] = {}
    for injection in key.injections:
        table = "bank_statement" if injection.is_bank_side else "settlement_report"
        for row_id in injection.affected_row_ids:
            lookup[(table, row_id)] = injection.cause
        for order_id in injection.affected_order_ids:
            lookup[("internal_ledger", order_id)] = injection.cause
    return lookup


def true_cause_of(case_row_keys: tuple[tuple[str, str], ...], lookup: dict) -> str | None:
    """The injected cause behind a case, or None where the key cannot name exactly one.

    Two ways of returning None, and they are different findings that happen to score
    the same way:

    - **No entry in the key at all.** The generator did not touch these rows, so the
      matcher's finding is its own. Calling that a precision miss would score real
      findings against a key with nothing to say about them.
    - **More than one injected cause across the case's rows.** No injector stacks two
      troubles on one order, so this does not happen in the shipped corpus -- 0 of 390
      attributable cases. If it ever does, the honest answer is to refuse to score it
      rather than take the alphabetically first cause: an arbitrary pick would flatter
      or penalise the run depending on nothing, and the count already surfaces as
      ``unscored_auto_resolutions``.
    """
    found = {lookup[key] for key in case_row_keys if key in lookup}
    return found.pop() if len(found) == 1 else None


# --------------------------------------------------------------------------- #
# Per-batch
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BatchLearningMetrics:
    """One batch's learning numbers."""

    batch: int
    queue_cases: int
    auto_resolved_cases: int
    auto_resolved_rows: int
    escalated_cases: int
    held_by_guardrail_cases: int
    correct_auto_resolutions: int
    scored_auto_resolutions: int
    unscored_auto_resolutions: int
    rupees_auto_resolved: Decimal
    rupees_escalated: Decimal
    rules_learned: int
    rules_promoted: int
    rules_retired: int
    proposals: int
    human_touchpoints: int

    @property
    def auto_resolution_precision(self) -> Decimal | None:
        if self.scored_auto_resolutions == 0:
            return None
        return pct(self.correct_auto_resolutions, self.scored_auto_resolutions)

    def to_json(self) -> dict[str, Any]:
        return {
            "batch": self.batch,
            "queue_cases": self.queue_cases,
            "auto_resolved_cases": self.auto_resolved_cases,
            "auto_resolved_rows": self.auto_resolved_rows,
            "escalated_cases": self.escalated_cases,
            "held_by_guardrail_cases": self.held_by_guardrail_cases,
            "auto_resolution_precision_pct": (
                None if self.auto_resolution_precision is None
                else str(self.auto_resolution_precision)
            ),
            "scored_auto_resolutions": self.scored_auto_resolutions,
            "unscored_auto_resolutions": self.unscored_auto_resolutions,
            "rupees_auto_resolved": str(self.rupees_auto_resolved),
            "rupees_escalated": str(self.rupees_escalated),
            "rules_learned": self.rules_learned,
            "rules_promoted": self.rules_promoted,
            "rules_retired": self.rules_retired,
            "proposals": self.proposals,
            "human_touchpoints": self.human_touchpoints,
        }


def _touchpoints(batch: BatchLearning) -> int:
    """Distinct decisions a human has to make about this batch.

    A case a rule collapsed into a proposal card is one decision shared with every
    other case on that card. A case no rule matched is a decision on its own. This is
    a different measurement from the row-level review rate and it is reported beside
    it rather than instead of it -- twenty four-figure reversals a guardrail refused
    to automate are still twenty rows a human is accountable for, and also one
    question they answer once.
    """
    carded = {
        decision.case.case_id
        for decision in batch.decisions
        if decision.provenance.outcome in (HELD, AUTO_RESOLVED)
        and decision.provenance.rule_id is not None
    }
    cards = len({p.rule_id for p in batch.proposals if p.outcome in (HELD, AUTO_RESOLVED)})
    loose = sum(
        1
        for decision in batch.decisions
        if decision.case.case_id not in carded and decision.needs_human
    )
    return cards + loose


def batch_metrics(batch: BatchLearning, lookup: dict) -> BatchLearningMetrics:
    correct = scored = unscored = 0
    for decision in batch.auto_resolved:
        true_cause = true_cause_of(decision.case.row_keys, lookup)
        if true_cause is None:
            unscored += 1
            continue
        scored += 1
        correct += int(true_cause == decision.provenance.proposed_cause)

    return BatchLearningMetrics(
        batch=batch.batch,
        queue_cases=len(batch.cases),
        auto_resolved_cases=len(batch.auto_resolved),
        auto_resolved_rows=sum(len(d.case.settlement_row_ids) for d in batch.auto_resolved),
        escalated_cases=len(batch.escalated),
        held_by_guardrail_cases=sum(
            1 for d in batch.decisions if d.provenance.outcome == HELD
        ),
        correct_auto_resolutions=correct,
        scored_auto_resolutions=scored,
        unscored_auto_resolutions=unscored,
        rupees_auto_resolved=batch.rupees_auto_resolved,
        rupees_escalated=batch.rupees_escalated,
        rules_learned=len(batch.rules_learned),
        rules_promoted=len(batch.rules_promoted),
        rules_retired=len(batch.rules_retired),
        proposals=len(batch.proposals),
        human_touchpoints=_touchpoints(batch),
    )


# --------------------------------------------------------------------------- #
# Abstention
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Abstention:
    """Did the system correctly refuse to automate a cause it had never seen?"""

    cause: str
    first_batch: int
    cases_on_first_sight: int
    auto_resolved_on_first_sight: int
    auto_resolved_ever: int
    total_cases: int

    @property
    def correct_on_first_sight(self) -> bool:
        return self.auto_resolved_on_first_sight == 0

    @property
    def rate(self) -> Decimal:
        return pct(self.total_cases - self.auto_resolved_ever, self.total_cases)

    def to_json(self) -> dict[str, Any]:
        return {
            "cause": self.cause,
            "first_batch": self.first_batch,
            "cases_on_first_sight": self.cases_on_first_sight,
            "auto_resolved_on_first_sight": self.auto_resolved_on_first_sight,
            "auto_resolved_ever": self.auto_resolved_ever,
            "total_cases": self.total_cases,
            "abstention_rate_pct": str(self.rate),
            "correct": self.correct_on_first_sight,
        }


def abstentions(run: LearningRun, key: AnswerKey, causes: list[str]) -> list[Abstention]:
    """Abstention correctness for the held-out causes."""
    lookup = cause_by_key(key)
    out: list[Abstention] = []
    for cause in causes:
        first = min(
            (injection.batch for injection in key.injections if injection.cause == cause),
            default=0,
        )
        total = on_first = auto_first = auto_ever = 0
        for batch in run.batches:
            for decision in batch.decisions:
                if true_cause_of(decision.case.row_keys, lookup) != cause:
                    continue
                total += 1
                resolved = decision.resolved
                auto_ever += int(resolved)
                if batch.batch == first:
                    on_first += 1
                    auto_first += int(resolved)
        out.append(
            Abstention(
                cause=cause, first_batch=first, cases_on_first_sight=on_first,
                auto_resolved_on_first_sight=auto_first, auto_resolved_ever=auto_ever,
                total_cases=total,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Whole-run
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LearningScore:
    """The learning loop, scored."""

    batches: tuple[BatchLearningMetrics, ...]
    abstentions: tuple[Abstention, ...]
    rule_truth: tuple[tuple[str, Decimal | None, int], ...]   # rule_id, true precision, scored

    @property
    def overall_precision(self) -> Decimal | None:
        scored = sum(b.scored_auto_resolutions for b in self.batches)
        if scored == 0:
            return None
        return pct(sum(b.correct_auto_resolutions for b in self.batches), scored)

    def to_json(self) -> dict[str, Any]:
        return {
            "batches": [batch.to_json() for batch in self.batches],
            "overall_auto_resolution_precision_pct": (
                None if self.overall_precision is None else str(self.overall_precision)
            ),
            "abstention": [entry.to_json() for entry in self.abstentions],
            "rule_true_precision": [
                {"rule_id": rule_id, "true_precision_pct": None if p is None else str(p),
                 "scored_auto_resolutions": n}
                for rule_id, p, n in self.rule_truth
            ],
        }


def rule_true_precision(
    run: LearningRun, lookup: dict
) -> tuple[tuple[str, Decimal | None, int], ...]:
    """Per rule: how often the cause it claimed on an auto-resolution was the real one."""
    correct: Counter[str] = Counter()
    scored: Counter[str] = Counter()
    for batch in run.batches:
        for decision in batch.auto_resolved:
            rule_id = decision.provenance.rule_id
            true_cause = true_cause_of(decision.case.row_keys, lookup)
            if rule_id is None or true_cause is None:
                continue
            scored[rule_id] += 1
            correct[rule_id] += int(true_cause == decision.provenance.proposed_cause)
    return tuple(
        (rule_id, pct(correct[rule_id], scored[rule_id]), scored[rule_id])
        for rule_id in sorted(scored)
    )


def score(run: LearningRun, key: AnswerKey, held_out: list[str]) -> LearningScore:
    lookup = cause_by_key(key)
    return LearningScore(
        batches=tuple(batch_metrics(batch, lookup) for batch in run.batches),
        abstentions=tuple(abstentions(run, key, held_out)),
        rule_truth=rule_true_precision(run, lookup),
    )


def auto_resolutions(run: LearningRun) -> list[AutoResolution]:
    """The rows a learned rule closed, in the shape ``harness/metrics.py`` scores."""
    return [
        AutoResolution(
            batch=batch.batch,
            table="settlement_report",
            row_id=row_id,
            proposed_cause=decision.provenance.proposed_cause or "",
        )
        for batch in run.batches
        for decision in batch.auto_resolved
        for row_id in decision.case.settlement_row_ids
    ]
