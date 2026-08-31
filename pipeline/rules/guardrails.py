"""The thresholds a rule cannot out-confidence.

Guardrails run **after** a rule matches and they override it. That ordering is the
point of the whole design: a rule's confidence is an opinion about a pattern, and a
threshold is a decision about risk. The opinion never wins.

Three of them, all from ``config/thresholds.yaml``:

1. **max_variance_inr** -- above this many rupees, never auto-resolve. Size of the
   error, not size of the sale.
2. **never_auto_resolve_causes** -- TCS and TDS timing and chargebacks, regardless of
   how well a rule predicts them.
3. **resolution class** -- ``tax_review`` and ``investigate`` are always human. A
   ``counterparty_claim`` is not auto-*resolved* either; it is routed to the claims
   queue, because closing a row someone else owes money on is not a resolution, it is
   a write-off nobody authorised.

Every evaluation is recorded, pass or fail, and travels with the decision. "Which
guardrails did you check?" is a question an auditor asks about the resolutions that
went through, not only the ones that were held.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pipeline.cases import CaseFeatures
from pipeline.rules.models import Rule

PASS = "pass"
HOLD = "hold"

#: Classes that never auto-resolve whatever a rule believes. ``counterparty_claim``
#: is here because money owed by someone else is a claim to be worked, not a
#: difference to be closed.
ALWAYS_HUMAN_CLASSES = frozenset({"tax_review", "investigate", "counterparty_claim"})


@dataclass(frozen=True)
class GuardrailConfig:
    max_variance_inr: Decimal
    never_auto_resolve_causes: frozenset[str]


def guardrail_config_from(thresholds: dict[str, Any]) -> GuardrailConfig:
    section = thresholds["auto_resolution"]
    return GuardrailConfig(
        max_variance_inr=Decimal(section["max_variance_inr"]),
        never_auto_resolve_causes=frozenset(section["never_auto_resolve_causes"]),
    )


@dataclass(frozen=True)
class GuardrailCheck:
    name: str
    outcome: str          # pass | hold
    detail: str

    def render(self) -> str:
        return f"{self.name}:{self.outcome}"


@dataclass(frozen=True)
class GuardrailResult:
    checks: tuple[GuardrailCheck, ...]

    @property
    def held(self) -> bool:
        return any(check.outcome == HOLD for check in self.checks)

    @property
    def held_by(self) -> tuple[GuardrailCheck, ...]:
        return tuple(check for check in self.checks if check.outcome == HOLD)

    @property
    def rendered(self) -> tuple[str, ...]:
        return tuple(check.render() for check in self.checks)

    @property
    def reason(self) -> str:
        return "; ".join(check.detail for check in self.held_by)


def evaluate(rule: Rule, features: CaseFeatures, cfg: GuardrailConfig) -> GuardrailResult:
    """Run every guardrail. All of them, always -- a short circuit loses the record."""
    over = features.variance_inr > cfg.max_variance_inr
    blocked = rule.cause in cfg.never_auto_resolve_causes
    human = rule.resolution_class in ALWAYS_HUMAN_CLASSES

    return GuardrailResult(
        checks=(
            GuardrailCheck(
                "max_variance_inr",
                HOLD if over else PASS,
                f"₹{features.variance_inr} is above the ₹{cfg.max_variance_inr} auto-resolution "
                f"ceiling" if over else f"₹{features.variance_inr} is within the "
                f"₹{cfg.max_variance_inr} ceiling",
            ),
            GuardrailCheck(
                "never_auto_resolve",
                HOLD if blocked else PASS,
                f"{rule.cause} is on the never-auto-resolve list" if blocked
                else f"{rule.cause} is not on the never-auto-resolve list",
            ),
            GuardrailCheck(
                "resolution_class",
                HOLD if human else PASS,
                f"{rule.resolution_class} is always human" if human
                else f"{rule.resolution_class} may be automated",
            ),
        )
    )
