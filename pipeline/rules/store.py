"""The rule set, on disk.

Rules are immutable dataclasses; the store is the mutable thing that holds them, so
every edit is a replacement and the history lives in each rule's ``transitions``
tuple rather than in a diff nobody kept.

Ids are assigned in creation order -- ``R-01``, ``R-02`` -- because a rule id shows
up in a provenance record a human reads, and a hash would make that record unreadable
for no gain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from pipeline.config import REPO_ROOT
from pipeline.rules.models import Observation, Rule, RuleState, Transition

RULES_JSON = REPO_ROOT / "data" / "rules.json"


@dataclass
class RuleStore:
    """Every rule the system has learned, in creation order."""

    rules: list[Rule] = field(default_factory=list)

    def next_id(self) -> str:
        return f"R-{len(self.rules) + 1:02d}"

    def add(self, rule: Rule) -> Rule:
        self.rules.append(rule)
        return rule

    def get(self, rule_id: str) -> Rule:
        for rule in self.rules:
            if rule.rule_id == rule_id:
                return rule
        raise KeyError(f"no such rule: {rule_id}")

    def replace(self, rule: Rule) -> None:
        for index, existing in enumerate(self.rules):
            if existing.rule_id == rule.rule_id:
                self.rules[index] = rule
                return
        raise KeyError(f"no such rule: {rule.rule_id}")

    def in_state(self, *states: RuleState) -> list[Rule]:
        return [rule for rule in self.rules if rule.state in states]

    @property
    def firing(self) -> list[Rule]:
        """Rules allowed to auto-resolve: active, enabled, nothing else."""
        return [rule for rule in self.rules if rule.fires]

    @property
    def predicting(self) -> list[Rule]:
        """Rules that evaluate at all -- shadow and active. Proposed and retired do not."""
        return [
            rule
            for rule in self.rules
            if rule.enabled and rule.state in (RuleState.SHADOW, RuleState.ACTIVE)
        ]

    def to_json(self) -> dict[str, Any]:
        return {"rules": [rule.to_json() for rule in self.rules]}


def _band(raw: Any) -> tuple[Decimal, Decimal] | None:
    return None if raw is None else (Decimal(raw[0]), Decimal(raw[1]))


def _days(raw: Any) -> tuple[int, int] | None:
    return None if raw is None else (int(raw[0]), int(raw[1]))


def _decimal(raw: Any) -> Decimal | None:
    return None if raw is None else Decimal(str(raw))


def _backtest(payload: dict[str, Any]) -> dict[str, Any]:
    """The backtest block, absent on rules written before the evidence gate existed."""
    block = payload.get("backtest") or {}
    return block if isinstance(block, dict) else {}


def _rule_from_json(payload: dict[str, Any]) -> Rule:
    conditions = payload["conditions"]
    action = payload["action"]
    return Rule(
        rule_id=str(payload["rule_id"]),
        cause=str(payload["cause"]),
        resolution_class=str(payload["resolution_class"]),
        plain_words=str(payload["plain_words"]),
        channel=conditions["channel"],
        reason_code=conditions["reason_code"],
        transaction_type=conditions.get("transaction_type"),
        variance_band_pct=_band(conditions["variance_band_pct"]),
        net_variance_band_pct=_band(conditions["net_variance_band_pct"]),
        direction=str(conditions["direction"]),
        lag_window_days=_days(conditions["lag_window_days"]),
        action_type=str(action["type"]),
        action_field=action["field"],
        action_value=None if action["value"] is None else Decimal(action["value"]),
        state=RuleState(payload["state"]),
        created_batch=int(payload["created_batch"]),
        source_resolution_id=str(payload["source_resolution_id"]),
        source_operator=str(payload["source_operator"]),
        enabled=bool(payload["enabled"]),
        level=str(payload.get("level", "narrow")),
        demonstration_ids=tuple(str(rid) for rid in payload.get("demonstration_ids", ())),
        approved=bool(payload.get("approved", False)),
        approved_by=str(payload.get("approved_by", "")),
        backtest_coverage=_backtest(payload).get("coverage"),
        backtest_precision=_decimal(_backtest(payload).get("precision")),
        observations=tuple(
            Observation(
                batch=int(o["batch"]), case_id=str(o["case_id"]),
                predicted_cause=str(o["predicted_cause"]),
                state_at_prediction=str(o["state_at_prediction"]),
                correct=o["correct"], verdict_source=str(o["verdict_source"]),
            )
            for o in payload["observations"]
        ),
        transitions=tuple(
            Transition(batch=int(t["batch"]), from_state=str(t["from_state"]),
                       to_state=str(t["to_state"]), reason=str(t["reason"]))
            for t in payload["transitions"]
        ),
        last_fired_batch=payload["last_fired_batch"],
    )


def load(path: Path | None = None) -> RuleStore:
    source = path or RULES_JSON
    if not source.exists():
        return RuleStore()
    payload = json.loads(source.read_text(encoding="utf-8"))
    return RuleStore(rules=[_rule_from_json(entry) for entry in payload["rules"]])


def save(store: RuleStore, path: Path | None = None) -> None:
    target = path or RULES_JSON
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(store.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
