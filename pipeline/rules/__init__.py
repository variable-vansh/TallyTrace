"""The learning loop's deterministic half.

Induction is language work and lives in ``pipeline/llm/``. Everything here --
matching a rule to a row, deciding whether it may fire, moving it between lifecycle
states, recording what it did -- is arithmetic over a dataclass, and there is no
model import anywhere in this package.
"""

from pipeline.rules.apply import (
    AUTO_RESOLVED,
    CONFLICTED,
    HELD,
    SHADOWED,
    UNMATCHED,
    Decision,
    Provenance,
    decide,
)
from pipeline.rules.guardrails import GuardrailConfig, guardrail_config_from
from pipeline.rules.lifecycle import LifecycleConfig, advance, lifecycle_config_from
from pipeline.rules.models import Observation, Rule, RuleState, Transition, rule_from
from pipeline.rules.predicates import matches, select, specificity
from pipeline.rules.proposals import Proposal
from pipeline.rules.store import RuleStore

__all__ = [
    "AUTO_RESOLVED", "CONFLICTED", "Decision", "GuardrailConfig", "HELD", "LifecycleConfig",
    "Observation", "Proposal", "Provenance", "Rule", "RuleState", "RuleStore", "SHADOWED",
    "Transition", "UNMATCHED", "advance", "decide", "guardrail_config_from",
    "lifecycle_config_from", "matches", "rule_from", "select", "specificity",
]
