"""What the operator decided about each candidate card.

A candidate card asks one question -- *may this rule start watching?* -- and this
file is where the answer lives. Without it the card would be a notification, and a
notification is not a gate.

**Why the key is the phenomenon and not the rule id.** Rule ids are handed out in
creation order, so keying a decision on ``R-14`` would mean an authored decision
silently attaching itself to a different rule the moment anything upstream changed
the candidate set. The key here is what the operator was actually looking at when
they decided: the cause, the channel, the matcher reason code, and which rung of the
specificity ladder they were shown. That tuple is stable across runs and legible in
a diff, which is what a record of a human decision has to be.

**A missing decision is not an approval.** ``verdict_for`` returns ``None`` for a
card nobody has answered, and :func:`pipeline.rules.lifecycle.advance` leaves such a
rule in ``proposed``. Defaulting the other way would make the gate open by omission,
which is the failure mode the gate exists to prevent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from pipeline.config import REPO_ROOT
from pipeline.rules.models import Rule

APPROVALS_JSON = REPO_ROOT / "data" / "approvals.json"

APPROVE = "approve"
REJECT = "reject"


@dataclass(frozen=True)
class CardVerdict:
    """One operator decision on one candidate card."""

    cause: str
    channel: str | None
    reason_code: str | None
    level: str
    decision: str            # approve | reject
    operator: str
    decided_at: str          # ISO date, fixed in the file so a rerun is reproducible
    note: str = ""

    @property
    def key(self) -> tuple[str | None, ...]:
        return (self.cause, self.channel, self.reason_code, self.level)

    @property
    def approves(self) -> bool:
        return self.decision == APPROVE

    def to_json(self) -> dict[str, Any]:
        return {
            "cause": self.cause,
            "channel": self.channel,
            "reason_code": self.reason_code,
            "level": self.level,
            "decision": self.decision,
            "operator": self.operator,
            "decided_at": self.decided_at,
            "note": self.note,
        }


def key_of(rule: Rule) -> tuple[str | None, ...]:
    """The card key a rule would be reviewed under."""
    return (rule.cause, rule.channel, rule.reason_code, rule.level)


@dataclass(frozen=True)
class ApprovalLog:
    """Every card decision the operator has made."""

    verdicts: tuple[CardVerdict, ...] = ()

    def verdict_for(self, rule: Rule) -> CardVerdict | None:
        """The decision on this rule's card, or None if nobody has answered it."""
        target = key_of(rule)
        return next((v for v in self.verdicts if v.key == target), None)

    def to_json(self) -> dict[str, Any]:
        return {"approvals": [v.to_json() for v in self.verdicts]}


def empty() -> ApprovalLog:
    return ApprovalLog()


def load(path: Path | None = None) -> ApprovalLog:
    """Read the decisions. A missing file is an empty log -- nothing is approved yet."""
    source = path or APPROVALS_JSON
    if not source.exists():
        return empty()
    payload = json.loads(source.read_text(encoding="utf-8"))
    return ApprovalLog(
        verdicts=tuple(
            CardVerdict(
                cause=str(entry["cause"]),
                channel=entry["channel"],
                reason_code=entry["reason_code"],
                level=str(entry["level"]),
                decision=str(entry["decision"]),
                operator=str(entry["operator"]),
                decided_at=str(entry["decided_at"]),
                note=str(entry.get("note", "")),
            )
            for entry in payload.get("approvals", [])
        )
    )


def save(log: ApprovalLog, path: Path | None = None) -> None:
    target = path or APPROVALS_JSON
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(log.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def of(verdicts: Iterable[CardVerdict]) -> ApprovalLog:
    return ApprovalLog(verdicts=tuple(verdicts))
