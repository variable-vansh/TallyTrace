"""One reading of a sentence, laddered into three candidates of different reach.

The model interprets the bookkeeper's note exactly once, in
``pipeline/llm/induction.py``, and that reading is the *narrow* candidate: every
constraint the note supported, as the model read it. The two candidates above it are
produced here, deterministically, by dropping constraints in a fixed order.

**Why the ladder is not a model call.** Interpreting "they moved outerwear to a new
slab" into a cause, a band and an action is language work and the model does it.
Asking "what would this same rule look like without the net-variance band?" is not:
it is a field deletion with a documented order, and it has exactly one right answer.
Generating the ladder in code rather than asking for three readings keeps the
specificity spread reproducible, keeps the LLM cache stable, and — the part that
matters for the invariants — means no model output ever chooses how far a rule
reaches. The model proposes one reading; the ladder proposes the alternatives; the
backtest, which is arithmetic over history, decides which of them survives.

**The ladder only ever widens.** A candidate above the model's reading fires on more
rows, never on fewer, so the ladder cannot invent a constraint the note did not
support. Widening is the risky direction and that is the point: a general candidate
that over-matches shows up in ``pipeline/rules/backtest.py`` as low precision or as a
conflict with an active rule, and is discarded on the evidence rather than on taste.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pipeline.llm.schemas import InducedRule

#: The three levels, narrowest first. A card names the level so an operator can see
#: which of the three they are being asked to approve.
NARROW = "narrow"
MEDIUM = "medium"
GENERAL = "general"

LEVELS = (NARROW, MEDIUM, GENERAL)

#: Constraints dropped at ``medium``: a secondary band and a row-type qualifier. Both
#: are things a note can imply incidentally -- the operator was looking at a refund,
#: so the row was a refund -- without the phenomenon being about them.
MEDIUM_DROPS = ("net_variance_band_pct", "transaction_type")

#: Constraints dropped at ``general``: every number and the direction, leaving the
#: categorical shape of the case -- its channel and the matcher reason code. This is
#: the widest a candidate is allowed to get. ``cause`` is never dropped, because a
#: rule with no cause is not a generalisation of anything; neither is ``channel``,
#: because a rule that crosses channels is a different claim about the world rather
#: than a looser version of the same one.
GENERAL_DROPS = MEDIUM_DROPS + ("variance_band_pct", "lag_window_days", "direction")

#: What ``direction`` means when it constrains nothing. Every other relaxable field
#: relaxes to ``None``.
ANY_DIRECTION = "any"

#: The fields a ladder rung is compared on when de-duplicating. Two rungs that
#: constrain the same things are one candidate, not two.
CONSTRAINED_FIELDS = (
    "cause", "channel", "reason_code", "transaction_type",
    "variance_band_pct", "net_variance_band_pct", "direction", "lag_window_days",
)


@dataclass(frozen=True)
class Candidate:
    """One rung: the level it sits at, and the rule it proposes."""

    level: str
    rule: InducedRule

    @property
    def signature(self) -> tuple[Any, ...]:
        return tuple(getattr(self.rule, name) for name in CONSTRAINED_FIELDS)


def _relaxed(induced: InducedRule, drop: tuple[str, ...]) -> InducedRule:
    """``induced`` with ``drop`` unconstrained. Never tightens anything."""
    changes: dict[str, Any] = {
        field: ANY_DIRECTION if field == "direction" else None for field in drop
    }
    return induced.model_copy(update=changes)


def ladder(induced: InducedRule) -> tuple[Candidate, ...]:
    """The model's reading and the two generalisations of it, narrowest first.

    Fewer than three come back when the note was already general -- a rule that only
    ever constrained a channel and a reason code has nothing left to drop, so its
    three rungs collapse into one. Padding that back out to three would mean
    inventing a constraint to relax, which is the one thing the ladder must not do.
    """
    rungs = (
        Candidate(NARROW, induced),
        Candidate(MEDIUM, _relaxed(induced, MEDIUM_DROPS)),
        Candidate(GENERAL, _relaxed(induced, GENERAL_DROPS)),
    )
    seen: set[tuple[Any, ...]] = set()
    distinct: list[Candidate] = []
    for rung in rungs:
        if rung.signature in seen:
            continue
        seen.add(rung.signature)
        distinct.append(rung)
    return tuple(distinct)
