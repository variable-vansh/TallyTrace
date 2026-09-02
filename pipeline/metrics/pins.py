"""Pinned metrics: the model is present at the moment of definition and absent afterwards.

A pin is a metric id, a set of parameters and a name, written to ``data/pins.json``
by a human who confirmed a result. From then on it recomputes every batch by calling
:func:`pipeline.metrics.registry.compute`, which is a pure function -- **no model is
constructed, no cache is read, no prompt is rendered.** That is the whole claim of
this surface and it is asserted in ``tests/test_pins.py`` by monkeypatching the LLM
client to raise and then recomputing every pin.

A pin records the question it came from. Not for the computation -- the computation
never looks at it -- but because "why is this on my dashboard?" is a question someone
asks six weeks later, and the honest answer is the sentence somebody typed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline.config import REPO_ROOT
from pipeline.metrics.corpus import Corpus
from pipeline.metrics.registry import MetricParams, MetricResult, compute, get

PINS_JSON = REPO_ROOT / "data" / "pins.json"


@dataclass(frozen=True)
class Pin:
    """One confirmed result, kept."""

    pin_id: str
    name: str
    metric_id: str
    params: MetricParams
    pinned_by: str
    pinned_at: str                 # ISO date, fixed in the file so a rerun is reproducible
    source_question: str           # what was typed. Never read by the computation.

    def to_json(self) -> dict[str, Any]:
        return {
            "pin_id": self.pin_id,
            "name": self.name,
            "metric_id": self.metric_id,
            "params": self.params.to_json(),
            "pinned_by": self.pinned_by,
            "pinned_at": self.pinned_at,
            "source_question": self.source_question,
        }


def _from_json(payload: dict[str, Any]) -> Pin:
    params = payload.get("params", {})
    return Pin(
        pin_id=str(payload["pin_id"]),
        name=str(payload["name"]),
        metric_id=str(payload["metric_id"]),
        params=MetricParams(
            from_batch=params.get("from_batch"),
            to_batch=params.get("to_batch"),
            channel=params.get("channel"),
            group_by=str(params.get("group_by", "channel")),
        ),
        pinned_by=str(payload["pinned_by"]),
        pinned_at=str(payload["pinned_at"]),
        source_question=str(payload.get("source_question", "")),
    )


def load(path: Path | None = None) -> list[Pin]:
    """Read the pinned metrics. A missing file is an empty dashboard, not an error."""
    source = path or PINS_JSON
    if not source.exists():
        return []
    payload = json.loads(source.read_text(encoding="utf-8"))
    pins = [_from_json(entry) for entry in payload.get("pins", [])]
    for pin in pins:
        get(pin.metric_id)      # a pin naming an unregistered metric fails loudly, at load
    return pins


def save(pins: list[Pin], path: Path | None = None) -> None:
    target = path or PINS_JSON
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"pins": [pin.to_json() for pin in pins]}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )


def recompute(pins: list[Pin], corpus: Corpus) -> list[tuple[Pin, MetricResult]]:
    """Every pinned metric, recomputed. Deterministic, and there is no model in this call."""
    return [(pin, compute(pin.metric_id, corpus, pin.params)) for pin in pins]
