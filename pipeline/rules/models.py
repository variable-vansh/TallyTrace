"""Rules, their lifecycle states, and the one thing a rule may never contain.

A rule is a predicate over :class:`~pipeline.cases.CaseFeatures` plus the bookkeeping
that decides whether it is allowed to fire. It is a plain dataclass and it is
evaluated by plain comparisons -- there is no model anywhere in this package, by
design. Induction is language work; application is arithmetic, and money does not
want a probabilistic predicate.

**No identifiers.** A rule containing an order id, an entity id or a UTR is a
memorised transaction. It would explain every row it was induced from and nothing
else, and it would score beautifully. The schema handed to the model has no field
for one; :func:`assert_generalisable` checks the values as well, because
``plain_words`` is free text and free text will hold an order id quite happily.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable

from pipeline.llm.schemas import InducedRule, RuleAction

#: Identifier shapes this corpus uses. Prefix-underscore-digits covers ``ord_000019``
#: and ``st_001110``; the second pattern covers bank references like
#: ``HDFCN25010900011``. Percentages and rates do not match either, which is the
#: point -- a rule is allowed to say "24.2%" and not allowed to say "ord_000019".
IDENTIFIER_PATTERNS = (
    re.compile(r"\b[A-Za-z]{2,6}_\d{3,}\b"),
    re.compile(r"\b[A-Z]{4,}[0-9]{6,}[A-Z0-9]*\b"),
)


class RuleState(str, Enum):
    """Four states. The lag between them is what makes the decline earned."""

    PROPOSED = "proposed"     # just induced; never fires
    SHADOW = "shadow"         # predicts and logs; the human still sees the exception
    ACTIVE = "active"         # auto-resolves matching rows
    RETIRED = "retired"       # demoted on live precision; shown, not hidden


def contains_identifier(text: str) -> str | None:
    """The first identifier-shaped token in ``text``, or None."""
    for pattern in IDENTIFIER_PATTERNS:
        found = pattern.search(text)
        if found:
            return found.group(0)
    return None


def assert_generalisable(payload: Iterable[tuple[str, Any]]) -> None:
    """Raise if any field value looks like a transaction id."""
    for name, value in payload:
        if not isinstance(value, str):
            continue
        found = contains_identifier(value)
        if found:
            raise ValueError(
                f"rule field {name!r} names a transaction ({found!r}); "
                "a rule that memorises a row has learned nothing"
            )


@dataclass(frozen=True)
class Observation:
    """One prediction a rule made, and whether it turned out to be right.

    ``correct`` is None while nobody has said. In shadow that is the normal state
    until the human resolves the row; scoring against the answer key happens in the
    harness and never writes back here, because a rule that could see the answer key
    would be measuring itself.
    """

    batch: int
    case_id: str
    predicted_cause: str
    state_at_prediction: str
    correct: bool | None = None
    verdict_source: str = "pending"   # human_resolution | operator_card | pending

    def to_json(self) -> dict[str, Any]:
        return {
            "batch": self.batch,
            "case_id": self.case_id,
            "predicted_cause": self.predicted_cause,
            "state_at_prediction": self.state_at_prediction,
            "correct": self.correct,
            "verdict_source": self.verdict_source,
        }


@dataclass(frozen=True)
class Transition:
    """Every state change, with the reason. Retirement is evidence, not a secret."""

    batch: int
    from_state: str
    to_state: str
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {
            "batch": self.batch, "from_state": self.from_state,
            "to_state": self.to_state, "reason": self.reason,
        }


@dataclass(frozen=True)
class Rule:
    """A learned pattern, its conditions, and its record."""

    rule_id: str
    cause: str
    resolution_class: str
    plain_words: str
    channel: str | None = None
    reason_code: str | None = None
    transaction_type: str | None = None
    variance_band_pct: tuple[Decimal, Decimal] | None = None
    net_variance_band_pct: tuple[Decimal, Decimal] | None = None
    direction: str = "any"
    lag_window_days: tuple[int, int] | None = None
    action_type: str = "none"
    action_field: str | None = None
    action_value: Decimal | None = None

    state: RuleState = RuleState.PROPOSED
    created_batch: int = 0
    source_resolution_id: str = ""
    source_operator: str = ""
    enabled: bool = True

    #: Which rung of the specificity ladder this rule came off. See
    #: ``pipeline/rules/candidates.py``.
    level: str = "narrow"

    #: Every operator resolution this rule stands on, oldest first. **Distinct
    #: demonstrations, not rows.** Eighty rows cleared by one sentence is one
    #: demonstration, and the difference is the whole point of the gate: a rule with
    #: one of these explains the row it came from and has said nothing about the
    #: world yet. ``source_resolution_id`` is the first of them, kept as its own
    #: field because every provenance chain in the system already points at it.
    demonstration_ids: tuple[str, ...] = ()

    #: Set by a human on the candidate card. A rule nobody approved never predicts,
    #: whatever its backtest said -- see ``pipeline/rules/lifecycle.py``.
    approved: bool = False
    approved_by: str = ""

    #: How the candidate scored on the history that admitted it. Kept so the rules
    #: page can show what was known at approval time next to what has happened since.
    backtest_coverage: int | None = None
    backtest_precision: Decimal | None = None

    observations: tuple[Observation, ...] = field(default_factory=tuple)
    transitions: tuple[Transition, ...] = field(default_factory=tuple)
    last_fired_batch: int | None = None

    # -- record ------------------------------------------------------------ #

    @property
    def judged(self) -> tuple[Observation, ...]:
        return tuple(o for o in self.observations if o.correct is not None)

    @property
    def confirmations(self) -> int:
        return sum(1 for o in self.judged if o.correct)

    @property
    def refutations(self) -> int:
        return sum(1 for o in self.judged if not o.correct)

    @property
    def precision(self) -> Decimal | None:
        """Live precision over judged observations. None when nothing is judged yet."""
        judged = self.judged
        if not judged:
            return None
        return (Decimal(self.confirmations) / Decimal(len(judged))).quantize(Decimal("0.0001"))

    @property
    def support(self) -> int:
        """How many predictions this rule has made, judged or not.

        Deliberately *not* the number the promotion gate reads. This counts rows the
        rule spoke about; :attr:`demonstration_support` counts the times a human
        independently demonstrated the phenomenon. A rule can have eighty of these
        and one of those.
        """
        return len(self.observations)

    @property
    def demonstration_support(self) -> int:
        """How many distinct operator resolutions stand behind this rule."""
        return len(self.demonstration_ids)

    @property
    def fires(self) -> bool:
        return self.enabled and self.state is RuleState.ACTIVE

    # -- edits -------------------------------------------------------------- #

    def observing(self, observation: Observation) -> "Rule":
        return replace(self, observations=self.observations + (observation,))

    def demonstrated_by(self, resolution_id: str) -> "Rule":
        """Record another human demonstration of the same phenomenon.

        Idempotent by resolution id: the same note counted twice would let one
        sentence walk a rule through a gate that exists to require several.
        """
        if resolution_id in self.demonstration_ids:
            return self
        return replace(self, demonstration_ids=self.demonstration_ids + (resolution_id,))

    def approving(self, operator: str, batch: int, note: str = "") -> "Rule":
        """A human accepted the candidate card. Recorded; it does not move the state.

        Approval and promotion are separate on purpose. This says the rule is worth
        watching; ``lifecycle.advance`` is what decides it may start watching, and the
        thresholds it reads are nobody's opinion.
        """
        detail = f"{operator} approved the candidate card" + (f": {note}" if note else "")
        return replace(
            self,
            approved=True,
            approved_by=operator,
            transitions=self.transitions
            + (Transition(batch=batch, from_state=self.state.value,
                          to_state=self.state.value, reason=detail),),
        )

    def judging(self, case_id: str, correct: bool, source: str) -> "Rule":
        """Record the verdict on a prediction. Only the first verdict counts."""
        updated = tuple(
            replace(o, correct=correct, verdict_source=source)
            if o.case_id == case_id and o.correct is None
            else o
            for o in self.observations
        )
        return replace(self, observations=updated)

    def moving_to(self, state: RuleState, batch: int, reason: str) -> "Rule":
        return replace(
            self,
            state=state,
            transitions=self.transitions
            + (Transition(batch=batch, from_state=self.state.value, to_state=state.value, reason=reason),),
        )

    def narrowed(self, band: tuple[Decimal, Decimal], batch: int, note: str) -> "Rule":
        """The corrigibility path: an operator tightening an over-matching band."""
        if band[0] < (self.variance_band_pct or band)[0] or band[1] > (self.variance_band_pct or band)[1]:
            raise ValueError("narrowing must not widen the band")
        return replace(
            self,
            variance_band_pct=band,
            transitions=self.transitions
            + (Transition(batch=batch, from_state=self.state.value, to_state=self.state.value,
                          reason=f"operator narrowed variance band to {band[0]}..{band[1]}%: {note}"),),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "state": self.state.value,
            "enabled": self.enabled,
            "cause": self.cause,
            "resolution_class": self.resolution_class,
            "plain_words": self.plain_words,
            "conditions": {
                "channel": self.channel,
                "reason_code": self.reason_code,
                "transaction_type": self.transaction_type,
                "variance_band_pct": None if self.variance_band_pct is None
                else [str(self.variance_band_pct[0]), str(self.variance_band_pct[1])],
                "net_variance_band_pct": None if self.net_variance_band_pct is None
                else [str(self.net_variance_band_pct[0]), str(self.net_variance_band_pct[1])],
                "direction": self.direction,
                "lag_window_days": None if self.lag_window_days is None
                else [self.lag_window_days[0], self.lag_window_days[1]],
            },
            "action": {
                "type": self.action_type,
                "field": self.action_field,
                "value": None if self.action_value is None else str(self.action_value),
            },
            "created_batch": self.created_batch,
            "source_resolution_id": self.source_resolution_id,
            "source_operator": self.source_operator,
            "level": self.level,
            "demonstration_ids": list(self.demonstration_ids),
            "demonstration_support": self.demonstration_support,
            "approved": self.approved,
            "approved_by": self.approved_by,
            "backtest": {
                "coverage": self.backtest_coverage,
                "precision": (
                    None if self.backtest_precision is None else str(self.backtest_precision)
                ),
            },
            "support": self.support,
            "confirmations": self.confirmations,
            "refutations": self.refutations,
            "precision": None if self.precision is None else str(self.precision),
            "last_fired_batch": self.last_fired_batch,
            "observations": [o.to_json() for o in self.observations],
            "transitions": [t.to_json() for t in self.transitions],
        }


def rule_from(
    induced: InducedRule,
    *,
    rule_id: str,
    batch: int,
    resolution_id: str,
    operator: str,
    level: str = "narrow",
) -> Rule:
    """Build a stored rule from the model's structured output, refusing memorised ones."""
    action: RuleAction = induced.action
    assert_generalisable(
        [
            ("plain_words", induced.plain_words),
            ("reason_code", induced.reason_code or ""),
            ("action.field", action.field or ""),
        ]
    )
    return Rule(
        rule_id=rule_id,
        cause=induced.cause.value,
        resolution_class=induced.resolution_class.value,
        plain_words=induced.plain_words,
        channel=None if induced.channel is None else induced.channel.value,
        reason_code=induced.reason_code,
        transaction_type=induced.transaction_type,
        variance_band_pct=induced.variance_band_pct,
        net_variance_band_pct=induced.net_variance_band_pct,
        direction=induced.direction,
        lag_window_days=induced.lag_window_days,
        action_type=action.type,
        action_field=action.field,
        action_value=action.value,
        state=RuleState.PROPOSED,
        created_batch=batch,
        source_resolution_id=resolution_id,
        source_operator=operator,
        level=level,
        demonstration_ids=(resolution_id,) if resolution_id else (),
    )
