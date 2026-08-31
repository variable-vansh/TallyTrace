"""What the human actually did, in their own words.

This file is the root of every provenance chain in the system. A rule points at a
resolution; a resolution points at a case, an operator and a timestamp; the UI walks
back along that chain when you click a transaction.

Two kinds of record, because the human acts in two ways:

**A resolution** is free text typed while clearing one exception. No dropdowns, no
rule builder -- the premise of the whole loop is that rules come out of work someone
was doing anyway. The text in ``data/resolutions.json`` was written the way a
bookkeeper writes: "Myntra is billing 27.2% but our master rate says 25%, they moved
outerwear to a new slab in January." Deliberately not in the shape the rule engine
wants. Text engineered to induce cleanly would test nothing.

**A card decision** is what the operator did with a batch proposal: accepted it,
opened it row by row, or declined it. Declining is not a no-op -- it records a
negative observation against the rule, which moves its live precision and can retire
it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from pipeline.config import REPO_ROOT

RESOLUTIONS_JSON = REPO_ROOT / "data" / "resolutions.json"

ACCEPT = "accept_all"
REVIEW = "review_individually"
DECLINE = "not_this_time"


@dataclass(frozen=True)
class Resolution:
    """One exception, cleared by a person, in their own words."""

    resolution_id: str
    batch: int
    case_id: str
    operator: str
    resolved_at: str          # ISO date; fixed in the log so a rerun is reproducible
    text: str

    def to_json(self) -> dict[str, Any]:
        return {
            "resolution_id": self.resolution_id,
            "batch": self.batch,
            "case_id": self.case_id,
            "operator": self.operator,
            "resolved_at": self.resolved_at,
            "text": self.text,
        }


@dataclass(frozen=True)
class CardDecision:
    """What the operator did with one batch proposal card."""

    batch: int
    rule_id: str
    decision: str             # accept_all | review_individually | not_this_time
    operator: str
    note: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "batch": self.batch, "rule_id": self.rule_id, "decision": self.decision,
            "operator": self.operator, "note": self.note,
        }


@dataclass(frozen=True)
class OperatorLog:
    """Everything the human did, indexed the two ways the runner needs it."""

    resolutions: tuple[Resolution, ...]
    decisions: tuple[CardDecision, ...]

    def for_batch(self, batch: int) -> list[Resolution]:
        return [r for r in self.resolutions if r.batch == batch]

    def by_case(self) -> dict[str, Resolution]:
        return {r.case_id: r for r in self.resolutions}

    def decisions_for(self, batch: int) -> dict[str, CardDecision]:
        return {d.rule_id: d for d in self.decisions if d.batch == batch}

    def to_json(self) -> dict[str, Any]:
        return {
            "resolutions": [r.to_json() for r in self.resolutions],
            "card_decisions": [d.to_json() for d in self.decisions],
        }


def empty() -> OperatorLog:
    return OperatorLog(resolutions=(), decisions=())


def load(path: Path | None = None) -> OperatorLog:
    """Read the operator log. A missing file is an empty log, not an error --
    a fresh clone has done no work yet, and that is a legitimate state."""
    source = path or RESOLUTIONS_JSON
    if not source.exists():
        return empty()
    payload = json.loads(source.read_text(encoding="utf-8"))
    return OperatorLog(
        resolutions=tuple(
            Resolution(
                resolution_id=str(entry["resolution_id"]),
                batch=int(entry["batch"]),
                case_id=str(entry["case_id"]),
                operator=str(entry["operator"]),
                resolved_at=str(entry["resolved_at"]),
                text=str(entry["text"]),
            )
            for entry in payload.get("resolutions", [])
        ),
        decisions=tuple(
            CardDecision(
                batch=int(entry["batch"]),
                rule_id=str(entry["rule_id"]),
                decision=str(entry["decision"]),
                operator=str(entry["operator"]),
                note=str(entry.get("note", "")),
            )
            for entry in payload.get("card_decisions", [])
        ),
    )


def save(log: OperatorLog, path: Path | None = None) -> None:
    target = path or RESOLUTIONS_JSON
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(log.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def appended(log: OperatorLog, resolutions: Iterable[Resolution]) -> OperatorLog:
    return OperatorLog(
        resolutions=log.resolutions + tuple(resolutions), decisions=log.decisions
    )
