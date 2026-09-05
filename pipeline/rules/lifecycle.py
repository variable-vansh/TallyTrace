"""proposed -> shadow -> active -> retired, and the thresholds that move a rule.

The lag is the product. A rule that automates the moment it is induced has learned
nothing you can trust: it has one example. Shadow mode is where it earns the right
to fire, by predicting on batches nobody has resolved yet and being checked against
what the human went on to do.

Every threshold here comes from ``config/thresholds.yaml``. None of them is a
literal in this file, and none of them was chosen to make a curve look better --
tuning a promotion threshold to raise the auto-resolution rate is the same failure as
widening a matcher tolerance to raise the match rate, one layer up.

Retirement is not a failure to hide. A rule that stops being right and says so is the
strongest evidence the lifecycle is real.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pipeline.rules.models import Rule, RuleState


@dataclass(frozen=True)
class LifecycleConfig:
    """The numbers that move a rule between states."""

    promotion_min_confirmations: int
    promotion_min_precision: Decimal
    retirement_precision_floor: Decimal
    retirement_min_observations: int
    #: Distinct operator demonstrations a rule needs before it may predict at all.
    #: The gate on the *entrance* to the lifecycle, where the other four govern
    #: movement inside it.
    min_support_demonstrations: int


def lifecycle_config_from(thresholds: dict[str, Any]) -> LifecycleConfig:
    section = thresholds["rule_lifecycle"]
    return LifecycleConfig(
        promotion_min_confirmations=int(section["promotion_min_confirmations"]),
        promotion_min_precision=Decimal(section["promotion_min_precision"]),
        retirement_precision_floor=Decimal(section["retirement_precision_floor"]),
        retirement_min_observations=int(section["retirement_min_observations"]),
        min_support_demonstrations=int(section["min_support_demonstrations"]),
    )


def _admit(rule: Rule, batch: int, cfg: LifecycleConfig) -> Rule:
    """proposed -> shadow, but only on evidence and only with a human's say-so.

    Two conditions, and they fail for different reasons worth keeping apart:

    **Support.** A rule standing on one demonstration is an anecdote. It backtests
    perfectly on the row it was induced from and has said nothing about the world, so
    it waits in ``proposed`` until the operator has independently demonstrated the
    same phenomenon ``min_support_demonstrations`` times. This is a floor, not a
    tunable: no backtest precision and no amount of specificity in the note buys a way
    past it, because both are computed from the single row in question.

    **Approval.** Even a well-supported candidate waits for a person to accept its
    card. Shadow mode costs an operator nothing -- it acts on nothing -- but a rule
    nobody looked at is a rule nobody can be asked about, and every rule that reaches
    ``active`` reaches it through here.

    A rule that fails either condition stays ``proposed`` silently. That is a normal
    state, not an error: most candidates the ladder produces are supposed to die here.
    """
    if rule.demonstration_support < cfg.min_support_demonstrations:
        return rule
    if not rule.approved:
        return rule
    return rule.moving_to(
        RuleState.SHADOW, batch,
        f"{rule.demonstration_support} operator demonstrations at or above the "
        f"{cfg.min_support_demonstrations} required, approved by {rule.approved_by}; "
        "predicts and logs, does not fire",
    )


def advance(rule: Rule, batch: int, cfg: LifecycleConfig) -> Rule:
    """One rule's state at the end of a batch, given everything it has been told.

    Evaluated in severity order: retirement is checked before promotion, so a rule
    whose record qualifies it for both goes to retired. A rule that is doing badly
    enough to retire must not be promoted by the same numbers.
    """
    if rule.state is RuleState.RETIRED:
        return rule

    precision = rule.precision
    judged = len(rule.judged)

    if (
        judged >= cfg.retirement_min_observations
        and precision is not None
        and precision < cfg.retirement_precision_floor
    ):
        return rule.moving_to(
            RuleState.RETIRED, batch,
            f"live precision {precision:.2%} over {judged} judged observations is below the "
            f"{cfg.retirement_precision_floor:.0%} floor",
        )

    if rule.state is RuleState.PROPOSED:
        return _admit(rule, batch, cfg)

    if rule.state is RuleState.SHADOW:
        if rule.confirmations >= cfg.promotion_min_confirmations and (
            precision is not None and precision >= cfg.promotion_min_precision
        ):
            return rule.moving_to(
                RuleState.ACTIVE, batch,
                f"{rule.confirmations} confirmations at {precision:.2%} precision, at or above "
                f"the {cfg.promotion_min_precision:.0%} bar",
            )
    return rule
