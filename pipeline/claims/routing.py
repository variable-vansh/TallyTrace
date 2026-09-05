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
from pipeline.claims.deadlines import DeadlineConfig, deadline_config_from
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


def is_claimable(
    cause: str | None,
    resolution_class: str | None,
    case: ExceptionCase,
    clocked: frozenset[str] | None = None,
) -> bool:
    """Does this case belong in the claims register?

    ``clocked`` is the set of causes the deadline policy gives a window to by name --
    a ``tax_review`` cause is not a claim anyone files, but if the table puts a
    statutory cutoff on it then it belongs in the register for that clock and nothing
    else. Passed in rather than imported as a constant so that giving a cause a
    deadline is one row in ``config/thresholds.yaml``. Defaults to the configured
    policy, which is what every caller in the pipeline passes anyway.
    """
    if cause is None or resolution_class is None:
        return False
    if case.features.direction not in CLAIMABLE_DIRECTIONS:
        return False
    if resolution_class == COUNTERPARTY_CLAIM:
        return True
    return cause in (_configured_clocked() if clocked is None else clocked)


def _configured_clocked() -> frozenset[str]:
    """The clocked causes from the shipped policy. Imported late to keep this pure-ish."""
    from pipeline.config import thresholds

    return deadline_config_from(thresholds()).clocked_claim_types


def evidence_of(case: ExceptionCase) -> tuple[Evidence, ...]:
    """The rows that prove the claim: every row the matcher's verdicts named."""
    return tuple(
        Evidence(table=verdict.table, row_id=verdict.row_id) for verdict in case.verdicts
    )


def route(
    decisions: list[Decision],
    hypotheses: Mapping[str, Hypothesis],
    resolution_class_by_cause: Mapping[str, str],
    deadlines: DeadlineConfig | None = None,
) -> list[tuple[ExceptionCase, str, str]]:
    """Every case in this batch that belongs in the register: (case, cause, source).

    Stable order, by case id, because the claim ids are handed out in the order this
    returns and a claim id that moves between runs is not an identifier.
    """
    clocked = None if deadlines is None else deadlines.clocked_claim_types
    routed: list[tuple[ExceptionCase, str, str]] = []
    for decision in sorted(decisions, key=lambda d: d.case.case_id):
        case = decision.case
        cause, source = cause_of(decision, hypotheses.get(case.case_id))
        klass = None if cause is None else resolution_class_by_cause.get(cause)
        if cause is not None and is_claimable(cause, klass, case, clocked):
            routed.append((case, cause, source))
    return routed
