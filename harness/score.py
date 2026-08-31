"""`make score` — the measurement harness.

One command: run the pipeline across all ten batches, score it against the answer
key, print a report, write the artifacts.

This exists *before* the learning loop on purpose. Built afterwards, a harness
becomes a thing that confirms what you hoped; built first, it is a thing that
catches what went wrong. Three of the four defects in FAILURES.md #7-#10 were found
by pointing this at a matcher that passed all its own tests.

The harness reads the answer key -- through ``harness/truth.py``, the one module
allowed to name its path. The pipeline never does.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from harness import aging as aging_module
from harness import report
from harness.attribution import CauseConfusion, RowOutcome, attribute, confusion, silent_clears
from harness.cost import Pricing, pricing_from
from harness.aging import Aging
from harness.exceptions import open_exceptions, render as render_exceptions
from harness.metrics import (
    AutoResolution,
    BatchMetrics,
    auto_resolution_precision,
    batch_metrics,
    quarantine_summary,
    resolved_row_keys,
)
from harness.truth import AnswerKey, load_answer_key
from pipeline.config import CONFIG_DIR, REPO_ROOT, generation, load_yaml, thresholds
from pipeline.llm.usage import UsageLedger
from pipeline.loader import load_batch
from pipeline.matcher import BatchResult, match_config_from
from pipeline.run import OpenBook, run_batch

SCORE_JSON = REPO_ROOT / "data" / "score.json"
EXCEPTIONS_MD = REPO_ROOT / "EXCEPTIONS.md"
ZERO = Decimal("0.00")


@dataclass(frozen=True)
class Score:
    """Everything one scored run produced."""

    results: list[BatchResult]
    metrics: list[BatchMetrics]
    outcomes: list[RowOutcome]
    confusion: list[CauseConfusion]
    aging: Aging
    key: AnswerKey
    pricing: Pricing
    proposals: list[AutoResolution]

    def new_findings(self, result: BatchResult) -> list[Any]:
        """Exceptions raised for the first time in this batch. See harness/aging.py."""
        return [v for v in open_exceptions(result) if self.aging.is_new(result.batch, v)]

    @property
    def open_exception_count(self) -> int:
        return sum(len(self.new_findings(result)) for result in self.results)

    @property
    def open_exception_impact(self) -> Decimal:
        return sum(
            (v.impact_inr for result in self.results for v in self.new_findings(result)),
            ZERO,
        )


def _cause_by_row(key: AnswerKey) -> dict[tuple[str, str], str]:
    """Answer-key lookup for scoring an auto-resolution's proposed cause."""
    table_of = {True: "bank_statement", False: "settlement_report"}
    return {
        (table_of[injection.is_bank_side], row_id): injection.cause
        for injection in key.injections
        for row_id in injection.affected_row_ids
    }


def _reconcile_all(
    generated_dir: Path | None,
) -> tuple[list[BatchResult], list[tuple[int, float]]]:
    """Reconcile every batch in order, timing each. Returns results and (rows, seconds)."""
    cfg = match_config_from(thresholds())
    book = OpenBook.empty()
    results: list[BatchResult] = []
    timings: list[tuple[int, float]] = []

    for batch in range(1, int(generation()["batch_count"]) + 1):
        tables = load_batch(batch, generated_dir)
        started = time.perf_counter()
        result = run_batch(tables, book, cfg)
        timings.append((tables.rows_read, time.perf_counter() - started))
        results.append(result)
    return results, timings


def run(
    generated_dir: Path | None = None,
    truth_dir: Path | None = None,
    ledger: UsageLedger | None = None,
    proposals: list[AutoResolution] | None = None,
) -> Score:
    """Reconcile every batch, timing each, then score against the answer key.

    ``ledger`` and ``proposals`` are the two seams checkpoint 3 fills: the first
    collects token usage from the LLM client, the second carries the rows a learned
    rule resolved without a human. Both are empty today and both are scored today,
    so neither arrives as an untested code path next to a new model.
    """
    pricing = pricing_from(load_yaml(CONFIG_DIR / "pricing.yaml"))
    usage = ledger or UsageLedger()
    results, timings = _reconcile_all(generated_dir)

    # Aging needs the whole corpus before any batch's queue can be sized: whether a
    # finding is new depends on whether an earlier batch already raised it.
    aging = aging_module.index(results)
    accepted = proposals or []
    metrics = [
        batch_metrics(
            result,
            records_processed=records,
            usage=usage.usage_for(result.batch),
            pricing=pricing,
            aging=aging,
            resolved=resolved_row_keys(
                [p for p in accepted if p.batch == result.batch]
            ),
            seconds=seconds,
        )
        for result, (records, seconds) in zip(results, timings)
    ]

    key = load_answer_key(truth_dir)
    outcomes = attribute(results, key)
    return Score(
        results=results,
        metrics=metrics,
        outcomes=outcomes,
        confusion=confusion(outcomes),
        aging=aging,
        key=key,
        pricing=pricing,
        proposals=accepted,
    )


# --------------------------------------------------------------------------- #
# Artifacts
# --------------------------------------------------------------------------- #


def _confusion_json(score: Score) -> list[dict[str, Any]]:
    return [
        {
            "cause": entry.cause,
            "resolution_class": entry.resolution_class,
            "rows": entry.rows,
            "caught": entry.caught,
            "silently_cleared": entry.silently_cleared,
            "by_verdict": {label: count for label, count in entry.by_verdict},
        }
        for entry in score.confusion
    ]


def _silent_clear_json(score: Score) -> list[dict[str, Any]]:
    return [
        {
            "cause": entry.cause,
            "rows": entry.rows,
            "largest_delta_inr": str(entry.largest_delta_inr),
            "tightest_headroom_inr": (
                None if entry.tightest_headroom_inr is None
                else str(entry.tightest_headroom_inr)
            ),
        }
        for entry in silent_clears(score.outcomes)
    ]


def _quarantine_json(score: Score) -> dict[str, Any]:
    summary = quarantine_summary(score.results)
    return {"total": summary.total, "by_reason": dict(summary.by_reason)}


def to_json(score: Score) -> dict[str, Any]:
    """The artifact the UI and the chart consume.

    ``timings`` is split out and labelled because it is the one part of this file
    that cannot be reproduced: wall clock is not deterministic, and burying it among
    numbers that are would make "same input, same output" untestable.
    """
    precision = auto_resolution_precision(score.proposals, _cause_by_row(score.key))
    return {
        "batches": [metric.to_json() for metric in score.metrics],
        "totals": {
            "records_processed": sum(m.records_processed for m in score.metrics),
            "settlement_rows": sum(m.settlement_rows for m in score.metrics),
            "open_exceptions": score.open_exception_count,
            "aged_findings": sum(m.aged_findings for m in score.metrics),
            "open_exception_impact_inr": str(score.open_exception_impact),
            "injected_rows": score.key.affected_row_count,
            "injected_impact_inr": str(score.key.total_impact_inr),
            "silently_cleared_rows": sum(o.silently_cleared for o in score.outcomes),
            "auto_resolutions_attempted": len(score.proposals),
            "review_rate_series_pct": [str(m.net_review_rate) for m in score.metrics],
            "auto_resolution_precision_pct": None if precision is None else str(precision),
            "llm_model": score.pricing.model,
        },
        "cause_confusion": _confusion_json(score),
        "silent_clears": _silent_clear_json(score),
        "quarantine": _quarantine_json(score),
        "timings": {
            "reproducible": False,
            "seconds_by_batch": {str(m.batch): round(m.seconds, 4) for m in score.metrics},
        },
    }


def to_text(score: Score) -> str:
    quarantine = quarantine_summary(score.results)
    precision = auto_resolution_precision(score.proposals, _cause_by_row(score.key))
    return report.render(
        [
            report.throughput(score.metrics, score.pricing),
            report.accuracy(score.metrics),
            report.confusion_table(score.confusion, score.key),
            report.silent_clear_table(silent_clears(score.outcomes), score.key),
            report.auto_resolution(precision, len(score.proposals)),
            report.honesty(quarantine, score.open_exception_count, score.open_exception_impact),
        ]
    )


def main() -> int:
    score = run()
    print(to_text(score), end="")

    SCORE_JSON.write_text(json.dumps(to_json(score), indent=2) + "\n", encoding="utf-8")
    EXCEPTIONS_MD.write_text(render_exceptions(score.results, score.aging), encoding="utf-8")
    print(f"\nwrote {SCORE_JSON.relative_to(REPO_ROOT)} and EXCEPTIONS.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
