"""The question log: what the operator typed at the reporting surface.

The counterpart of ``data/resolutions.json`` for the reporting side. It holds the
questions and what the operator did with each answer -- confirmed it, or pinned it --
and it holds no results. The mapping is replayed through the cached client on every
run rather than being recorded here, so the report shows what the model *actually*
says today about each question, not what it said the day the log was written.

Written by ``tools/write_reporting.py``; read by the harness and by the UI builder.
A missing file is an empty log, not an error: a fresh clone has asked nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline.config import REPO_ROOT

QUESTIONS_JSON = REPO_ROOT / "data" / "questions.json"


@dataclass(frozen=True)
class AskedQuestion:
    """One question, its author, and what became of the answer."""

    question: str
    asked_by: str
    asked_at: str                  # ISO date, fixed so a rerun is reproducible
    confirmed: bool                # did the operator accept the restatement
    pin_as: str | None = None      # the name they kept it under, if they kept it

    def to_json(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "asked_by": self.asked_by,
            "asked_at": self.asked_at,
            "confirmed": self.confirmed,
            "pin_as": self.pin_as,
        }


def load(path: Path | None = None) -> list[AskedQuestion]:
    source = path or QUESTIONS_JSON
    if not source.exists():
        return []
    payload = json.loads(source.read_text(encoding="utf-8"))
    return [
        AskedQuestion(
            question=str(entry["question"]),
            asked_by=str(entry["asked_by"]),
            asked_at=str(entry["asked_at"]),
            confirmed=bool(entry["confirmed"]),
            pin_as=entry.get("pin_as"),
        )
        for entry in payload.get("questions", [])
    ]


def save(questions: list[AskedQuestion], path: Path | None = None) -> None:
    target = path or QUESTIONS_JSON
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {"questions": [q.to_json() for q in questions]}, indent=2, ensure_ascii=False
        )
        + "\n",
        encoding="utf-8",
    )
