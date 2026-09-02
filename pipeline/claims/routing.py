"""Which exceptions become claims, and where their cause comes from.

``resolution_class`` does the routing and it is read from ``config/causes.yaml``,
never inferred:

- ``internal_fix``       -> the learning loop. Not here.
- ``counterparty_claim`` -> here, drafted and chased.
- ``tax_review``         -> here **only for its clock**, and only for
  ``tcs_timing_mismatch``. It is never drafted and never auto-closed; it stays a
  human's problem. It is in the register because the GSTR-8 cutoff is the hardest
  deadline in the whole corpus and the deadline is the thing this module exists for.
- ``investigate``        -> a human, with no clock. Nobody can file a claim for a
  bank credit whose sender is unknown.

Two filters, and both matter more than they look:

**Money in is not a claim.** A case whose direction is ``over`` is money arriving,
and the recovery credits planted in this corpus arrive as exactly that. Opening a
claim on one would open a claim for the money that just closed a claim.

**One claim per order.** The same order can surface in several batches. A second
finding attaches as evidence to the open claim rather than opening a second one, so
the queue's rupee total is a total of distinct money owed.
"""

from __future__ import annotations

from typing import Mapping

from pipeline.cases import ExceptionCase
from pipeline.claims.deadlines import STATUTORY_CAUSES
from pipeline.claims.models import Evidence
from pipeline.llm.schemas import Hypothesis
from pipeline.rules.apply import Decision

COUNTERPARTY_CLAIM = "counterparty_claim"

#: Directions that can be owed to the seller. ``over`` is money the seller received
#: and ``flat`` is an order that never paid at all -- no delta because there is no
#: settlement row to take a delta against.
CLAIMABLE_DIRECTIONS = frozenset({"short", "flat"})

FROM_RULE = "rule"
FROM_HYPOTHESIS = "hypothesis"


def cause_of(decision: Decision, hypothesis: Hypothesis | None) -> tuple[str | None, str]:
    """The cause this case is being routed on, and which of the two produced it.

    A learned rule's prediction wins over the model's hypothesis wherever there is
    one, because the rule is a deterministic predicate with a precision history
    behind it and the hypothesis is a one-shot reading of a single row. Where no rule
    matched, the hypothesis is all there is -- and every claim records which of the
    two it came from, so a queue can be read without guessing.
    """
    proposed = decision.provenance.proposed_cause
    if proposed:
        return proposed, FROM_RULE
    if hypothesis is not None:
        return hypothesis.cause.value, FROM_HYPOTHESIS
    return None, FROM_HYPOTHESIS


def is_claimable(cause: str | None, resolution_class: str | None, case: ExceptionCase) -> bool:
    """Does this case belong in the claims register?"""
    if cause is None or resolution_class is None:
        return False
    if case.features.direction not in CLAIMABLE_DIRECTIONS:
        return False
    return resolution_class == COUNTERPARTY_CLAIM or cause in STATUTORY_CAUSES


def evidence_of(case: ExceptionCase) -> tuple[Evidence, ...]:
    """The rows that prove the claim: every row the matcher's verdicts named."""
    return tuple(
        Evidence(table=verdict.table, row_id=verdict.row_id) for verdict in case.verdicts
    )


def route(
    decisions: list[Decision],
    hypotheses: Mapping[str, Hypothesis],
    resolution_class_by_cause: Mapping[str, str],
) -> list[tuple[ExceptionCase, str, str]]:
    """Every case in this batch that belongs in the register: (case, cause, source).

    Stable order, by case id, because the claim ids are handed out in the order this
    returns and a claim id that moves between runs is not an identifier.
    """
    routed: list[tuple[ExceptionCase, str, str]] = []
    for decision in sorted(decisions, key=lambda d: d.case.case_id):
        case = decision.case
        cause, source = cause_of(decision, hypotheses.get(case.case_id))
        klass = None if cause is None else resolution_class_by_cause.get(cause)
        if cause is not None and is_claimable(cause, klass, case):
            routed.append((case, cause, source))
    return routed
