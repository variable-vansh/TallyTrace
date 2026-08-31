"""The answer key.

This is the only module in the repo that reads ``data/truth``. The pipeline never
does -- ``tests/test_boundaries.py`` fails if anything under ``pipeline/`` so much as
names the path -- because a matcher with access to the answers is a matcher whose
score means nothing.

The key records what was done to the data and what it was worth. It records no claim
about whether a matcher *should* have caught it; that is what the confusion table is
for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from pipeline.config import REPO_ROOT

TRUTH_DIR = REPO_ROOT / "data" / "truth"


@dataclass(frozen=True)
class Injection:
    """One injected trouble, exactly as the generator recorded it."""

    batch: int
    cause: str
    resolution_class: str
    affected_row_ids: tuple[str, ...]
    affected_order_ids: tuple[str, ...]
    true_impact_inr: Decimal
    injector_params: dict[str, Any]

    @property
    def is_bank_side(self) -> bool:
        """A credit with no settlement counterpart is keyed by UTR, not entity id."""
        return self.cause == "bank_credit_unmatched"


@dataclass(frozen=True)
class AnswerKey:
    """Every injection across the corpus, plus the generator's own row counts."""

    injections: tuple[Injection, ...]
    row_counts: dict[int, dict[str, int]]
    malformed_rows: dict[int, list[str]]
    recovery_pairs: tuple[dict[str, Any], ...]

    @property
    def affected_row_count(self) -> int:
        return sum(len(injection.affected_row_ids) for injection in self.injections)

    @property
    def total_impact_inr(self) -> Decimal:
        return sum((i.true_impact_inr for i in self.injections), Decimal("0.00"))

    def for_batch(self, batch: int) -> list[Injection]:
        return [injection for injection in self.injections if injection.batch == batch]


def _injection(payload: dict[str, Any]) -> Injection:
    return Injection(
        batch=int(payload["batch"]),
        cause=str(payload["cause"]),
        resolution_class=str(payload["resolution_class"]),
        affected_row_ids=tuple(payload["affected_row_ids"]),
        affected_order_ids=tuple(payload["affected_order_ids"]),
        true_impact_inr=Decimal(payload["true_impact_inr"]),
        injector_params=dict(payload["injector_params"]),
    )


def load_answer_key(truth_dir: Path | None = None) -> AnswerKey:
    """Read every per-batch key plus the manifest."""
    directory = truth_dir or TRUTH_DIR
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))

    injections: list[Injection] = []
    row_counts: dict[int, dict[str, int]] = {}
    for path in sorted(directory.glob("batch_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        row_counts[int(payload["batch"])] = dict(payload["row_counts"])
        injections.extend(_injection(entry) for entry in payload["injections"])

    return AnswerKey(
        injections=tuple(injections),
        row_counts=row_counts,
        malformed_rows={int(k): list(v) for k, v in manifest["malformed_rows"].items()},
        recovery_pairs=tuple(manifest["recovery_pairs"]),
    )
