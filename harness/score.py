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

import argparse
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from harness import aging as aging_module
from harness import claims as claims_module
from harness import report
from harness import reporting as reporting_module
from harness.aging import Aging
from harness.attribution import CauseConfusion, RowOutcome, attribute, confusion, silent_clears
from harness.claims import ClaimScore
from harness.reporting import ReportingScore
from harness.cost import Pricing, pricing_from
from harness.exceptions import open_exceptions, render as render_exceptions
from harness.learning import LearningScore, auto_resolutions
from harness.learning import score as score_learning
from harness.metrics import (
    AutoResolution,
    BatchMetrics,
    auto_resolution_precision,
    batch_metrics,
    pct,
    quarantine_summary,
    resolved_row_keys,
)
from harness.truth import AnswerKey, load_answer_key
from pipeline.cases import FindingLog
from pipeline.claims.queue import QueueView, build as build_queue
from pipeline.config import CONFIG_DIR, REPO_ROOT, batch_window, generation, load_yaml, thresholds
from pipeline.learn import LearningRun, run_learning_batch
from pipeline.llm.client import client_from
from pipeline.loader import load_batch
from pipeline.matcher import BatchResult, match_config_from
from pipeline.metrics.registry import REGISTRY
from pipeline.rules import resolutions as operator_log
from pipeline.rules import store as rule_store
from pipeline.rules.models import RuleState
from pipeline.run import OpenBook, run_batch

SCORE_JSON = REPO_ROOT / "data" / "score.json"
EXCEPTIONS_MD = REPO_ROOT / "EXCEPTIONS.md"
RESULTS_MD = REPO_ROOT / "RESULTS.md"
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
    learning: LearningScore
    claims: ClaimScore
    queue: QueueView
    reporting: ReportingScore
    run: LearningRun

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
    generated_dir: Path | None, cache_dir: Path | None, allow_network: bool
) -> tuple[LearningRun, list[tuple[int, float]], ReportingScore]:
    """Run the whole loop batch by batch, timing each.

    The learning loop *contains* the matcher -- it reconciles, queues, hypothesises,
    applies rules and captures resolutions in one pass -- so the harness drives that
    rather than reconciling separately and bolting the learning numbers on beside it.
    Two passes would let the two halves disagree about which rows existed.
    """
    record = LearningRun()
    timings: list[tuple[int, float]] = []
    cfg = match_config_from(thresholds())
    book = OpenBook.empty()
    finding_log = FindingLog()
    log = operator_log.load()

    pricing_cfg = load_yaml(CONFIG_DIR / "pricing.yaml")
    client = client_from(
        str(pricing_cfg["model"]),
        cache_dir=cache_dir,
        ledger=record.ledger,
        chars_per_token=Decimal(str(pricing_cfg["estimated_chars_per_token"])),
        allow_network=allow_network,
    )

    for batch in range(1, int(generation()["batch_count"]) + 1):
        tables = load_batch(batch, generated_dir)
        started = time.perf_counter()
        learned = run_learning_batch(
            tables, book, cfg, record.store, client, log, finding_log, record.register
        )
        timings.append((tables.rows_read, time.perf_counter() - started))
        record.batches.append(learned)
    # The reporting surface is replayed after the corpus is complete, and its intent
    # calls are billed to the last batch through the same ledger. A cost per
    # transaction that excluded the surface the operator actually types into would be
    # a cost for a subset of the system.
    surface = reporting_module.score(record, client, record.batches[-1].batch)
    record.tokens_estimated = client.tokens_estimated
    return record, timings, surface


def run(
    generated_dir: Path | None = None,
    truth_dir: Path | None = None,
    cache_dir: Path | None = None,
    allow_network: bool = True,
) -> Score:
    """Reconcile and learn across every batch, timing each, then score against the key.

    Token usage and auto-resolutions are read off the learning run rather than passed
    in: checkpoint 2 left both as parameters so the scoring path could be exercised
    before anything filled them, and this is the checkpoint that fills them.
    """
    pricing = pricing_from(load_yaml(CONFIG_DIR / "pricing.yaml"))
    record, timings, surface = _reconcile_all(generated_dir, cache_dir, allow_network)
    results = record.results
    usage = record.ledger
    accepted = auto_resolutions(record)

    # Aging needs the whole corpus before any batch's queue can be sized: whether a
    # finding is new depends on whether an earlier batch already raised it.
    aging = aging_module.index(results)
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
    held_out = sorted(generation()["held_out"])
    corpus_end = batch_window(results[-1].batch)[1]
    return Score(
        results=results,
        metrics=metrics,
        outcomes=outcomes,
        confusion=confusion(outcomes),
        aging=aging,
        key=key,
        pricing=pricing,
        proposals=accepted,
        learning=score_learning(record, key, held_out),
        claims=claims_module.score(record, key),
        queue=build_queue(record.register.claims, corpus_end),
        reporting=surface,
        run=record,
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


def _totals(score: Score, precision: Decimal | None) -> dict[str, Any]:
    """The single-number summary the UI header and the chart read."""
    return {
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
        "matcher_review_rate_series_pct": [str(m.review_rate) for m in score.metrics],
        "touchpoint_rate_series_pct": [
            str(pct(learned.human_touchpoints, metric.settlement_rows))
            for learned, metric in zip(score.learning.batches, score.metrics)
        ],
        "auto_resolution_precision_pct": None if precision is None else str(precision),
        "auto_resolution_precision_series_pct": [
            None if b.auto_resolution_precision is None else str(b.auto_resolution_precision)
            for b in score.learning.batches
        ],
        "rules_total": len(score.run.store.rules),
        "rules_active": sum(1 for r in score.run.store.rules if r.state is RuleState.ACTIVE),
        "rules_retired": sum(1 for r in score.run.store.rules if r.state is RuleState.RETIRED),
        "rupees_auto_resolved": str(
            sum((b.rupees_auto_resolved for b in score.learning.batches), ZERO)
        ),
        "rupees_escalated": str(
            sum((b.rupees_escalated for b in score.learning.batches), ZERO)
        ),
        "llm_model": score.pricing.model,
        "llm_tokens_estimated": score.run.tokens_estimated,
        "claims_opened": score.claims.opened,
        "claims_recovered": len(score.claims.recovered),
        "claims_expired": len(score.claims.expired),
        "claims_open": len(score.claims.still_open),
        "rupees_recovered": str(score.claims.rupees_recovered),
        "rupees_expired": str(score.claims.rupees_expired),
        "claim_recovery_rate_pct": str(score.claims.recovery_rate),
        "claims_queue_header": score.queue.header,
        "registered_metrics": len(REGISTRY),
        "questions_asked": len(score.reporting.answers),
        "questions_mapped": score.reporting.mapped,
        "questions_declined": score.reporting.declined,
        "pinned_metrics": len(score.reporting.pins),
    }


def to_json(score: Score) -> dict[str, Any]:
    """The artifact the UI and the chart consume.

    ``timings`` is split out and labelled because it is the one part of this file
    that cannot be reproduced: wall clock is not deterministic, and burying it among
    numbers that are would make "same input, same output" untestable.
    """
    precision = auto_resolution_precision(score.proposals, _cause_by_row(score.key))
    return {
        "batches": [metric.to_json() for metric in score.metrics],
        "totals": _totals(score, precision),
        "learning": score.learning.to_json(),
        "claims": score.claims.to_json(),
        "claims_queue": score.queue.to_json(),
        "reporting": score.reporting.to_json(),
        "rules": score.run.store.to_json()["rules"],
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
            report.throughput(score.metrics, score.pricing, score.run.tokens_estimated),
            report.accuracy(score.metrics),
            report.confusion_table(score.confusion, score.key),
            report.silent_clear_table(silent_clears(score.outcomes), score.key),
            report.auto_resolution(precision, len(score.proposals)),
            report.learning(
                list(score.learning.batches), score.metrics, score.learning.overall_precision
            ),
            report.abstention(list(score.learning.abstentions)),
            report.rules(score.run.store, list(score.learning.rule_truth)),
            report.claims(score.claims, score.queue),
            report.claim_recovery(score.claims),
            report.claim_attribution(score.claims),
            report.reporting(score.reporting, len(REGISTRY)),
            report.pinned(score.reporting),
            report.honesty(quarantine, score.open_exception_count, score.open_exception_impact),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Score the pipeline against the answer key.")
    parser.add_argument(
        "--offline", action="store_true",
        help="never call the API, even with a key set; answer only from data/llm_cache",
    )
    args = parser.parse_args()
    score = run(allow_network=not args.offline)
    report_text = to_text(score)
    print(report_text, end="")

    SCORE_JSON.write_text(json.dumps(to_json(score), indent=2) + "\n", encoding="utf-8")
    # The same report, verbatim, as a committed artifact. The README quotes figures out
    # of it; this is the file those figures are traceable to, and it is rewritten on
    # every run so it cannot quietly describe an older one.
    RESULTS_MD.write_text(
        "# RESULTS\n\nVerbatim output of `make score` over the ten shipped batches.\n"
        "Regenerated on every run; nothing here is typed by hand.\n\n```\n"
        + report_text
        + "```\n",
        encoding="utf-8",
    )
    EXCEPTIONS_MD.write_text(
        render_exceptions(
            score.results, score.aging, score.queue, list(score.claims.claims)
        ),
        encoding="utf-8",
    )
    # The rule store is written here as well as by `make learn`, so one command
    # produces every artifact the UI and the tests read and they all describe the
    # same run rather than two runs that happen to agree.
    rule_store.save(score.run.store)
    print(
        f"\nwrote {SCORE_JSON.relative_to(REPO_ROOT)}, data/rules.json, "
        "EXCEPTIONS.md and RESULTS.md"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
