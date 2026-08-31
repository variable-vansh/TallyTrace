"""Build ``ui/public/tallytrace.json`` -- everything the React app renders.

One file, produced from one scored run, so the dashboard, the queue, the rules page
and the decision-path view cannot disagree with each other or with ``make score``.

**The answer key crosses a boundary here too.** Each exception carries a ``trueCause``
so a scored run can point at its own false positives -- the two planted near-misses are
the most useful rows in the demo and hiding them would defeat the purpose. It arrives
through the harness, which is the only thing allowed to read ``data/truth``; the
pipeline never sees it, and the UI labels every place it appears as coming from the
answer key rather than from the system.

**Money crosses a boundary here, and it is the only place it does.** Everywhere else
in this repo a rupee is a ``Decimal`` and a float in a money path is a bug. JavaScript
has no Decimal, and the charts have to do arithmetic, so amounts are serialised as
JSON numbers *for the UI only*. ``data/score.json`` -- the artifact anyone would audit
-- keeps every amount as a string. The conversion happens in one function, below, so
"where could a float have got in?" has one answer.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from harness.learning import cause_by_key, true_cause_of
from harness.metrics import pct
from harness.score import Score, run
from pipeline.cases import ExceptionCase
from pipeline.config import REPO_ROOT, batch_window
from pipeline.learn import BatchLearning
from pipeline.matcher import BatchResult, Bucket, GroupFinding
from pipeline.rules.apply import AUTO_RESOLVED, Decision
from pipeline.rules.models import Rule
from pipeline.rules.resolutions import OperatorLog
from pipeline.rules import resolutions as operator_log

UI_JSON = REPO_ROOT / "ui" / "public" / "tallytrace.json"


def money(value: Decimal | str | None) -> float | None:
    """The one place a rupee becomes a float, and only on its way to a browser."""
    if value is None:
        return None
    return float(Decimal(str(value)))


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #


def _verdict_index(result: BatchResult) -> dict[tuple[str, str], Any]:
    return {(v.table, v.row_id): v for v in result.verdicts}


def transactions(result: BatchResult, batch: int) -> list[dict[str, Any]]:
    """Settlement rows with the verdict the matcher gave each one."""
    index = _verdict_index(result)
    rows = []
    for verdict in result.by_table("settlement_report"):
        rows.append(
            {
                "entityId": verdict.row_id,
                "orderId": verdict.order_id,
                "channel": verdict.channel,
                "status": verdict.bucket.value,
                "reason": verdict.reason.value,
                "impact": money(verdict.impact_inr),
                "detail": dict(verdict.detail),
                "batch": batch,
            }
        )
    return sorted(rows, key=lambda row: row["entityId"])


def ledger(result: BatchResult) -> list[dict[str, Any]]:
    return sorted(
        (
            {
                "orderId": verdict.row_id,
                "channel": verdict.channel,
                "status": verdict.bucket.value,
                "reason": verdict.reason.value,
                "impact": money(verdict.impact_inr),
                "expectedFee": money(verdict.detail.get("expected_fee")),
                "expectedNet": money(verdict.detail.get("expected_net")),
                "chargedFee": money(verdict.detail.get("charged_fee")),
                "settledNet": money(verdict.detail.get("settled_net")),
            }
            for verdict in result.by_table("internal_ledger")
        ),
        key=lambda row: row["orderId"],
    )


def bank(result: BatchResult) -> list[dict[str, Any]]:
    groups: dict[str, GroupFinding] = {group.utr: group for group in result.groups}
    rows = []
    for verdict in result.by_table("bank_statement"):
        group = groups.get(verdict.row_id)
        rows.append(
            {
                "utr": verdict.row_id,
                "status": verdict.bucket.value,
                "reason": verdict.reason.value,
                "amount": money(verdict.detail.get("bank_amount")),
                "settlementSum": money(verdict.detail.get("settlement_sum")),
                "shortfall": money(verdict.detail.get("shortfall")),
                "rowsInGroup": None if group is None else len(group.candidate_row_ids),
                "residualRowIds": [] if group is None else list(group.residual_row_ids),
                "tiesOut": None if group is None else group.ties_out,
            }
        )
    return sorted(rows, key=lambda row: row["utr"])


# --------------------------------------------------------------------------- #
# The queue
# --------------------------------------------------------------------------- #


def _exception(
    decision: Decision,
    case: ExceptionCase,
    hypothesis: Any,
    log: OperatorLog,
    true_cause: str | None,
) -> dict[str, Any]:
    """One card in the review queue, with its whole decision path attached."""
    provenance = decision.provenance
    resolution = log.by_case().get(case.case_id)
    return {
        "caseId": case.case_id,
        "batch": case.batch,
        "kind": case.kind,
        "key": case.key,
        "orderId": case.key if case.kind == "order" else None,
        "channel": case.channel,
        "reason": case.reason,
        "bucket": case.features.bucket,
        "impact": money(case.impact_inr),
        "settlementRowIds": list(case.settlement_row_ids),
        "features": {
            **case.features.to_json(),
            "variance_inr": money(case.features.variance_inr),
        },
        "hypothesis": None if hypothesis is None else {
            "cause": hypothesis.cause.value,
            "text": hypothesis.hypothesis,
            "confidence": float(hypothesis.confidence),
        },
        "outcome": provenance.outcome,
        "status": "auto_resolved" if decision.resolved else (
            "resolved" if resolution is not None else "pending"
        ),
        "ruleId": provenance.rule_id,
        "ruleState": provenance.rule_state_at_fire,
        "proposedCause": provenance.proposed_cause,
        "guardrails": list(provenance.guardrails_evaluated),
        "guardrailDetail": list(provenance.guardrail_detail),
        "decisionNote": provenance.note,
        "sourceResolutionId": provenance.source_resolution_id,
        "sourceOperator": provenance.source_operator,
        "humanResolution": None if resolution is None else {
            "id": resolution.resolution_id,
            "text": resolution.text,
            "operator": resolution.operator,
            "at": resolution.resolved_at,
        },
        "trueCause": true_cause,
        "verdicts": [verdict.to_json() for verdict in case.verdicts],
    }


def exceptions(batch: BatchLearning, log: OperatorLog, lookup: dict) -> list[dict[str, Any]]:
    by_id = {case.case_id: case for case in batch.cases}
    return [
        _exception(
            decision,
            by_id[decision.case.case_id],
            batch.hypotheses.get(decision.case.case_id),
            log,
            true_cause_of(decision.case.row_keys, lookup),
        )
        for decision in batch.decisions
    ]


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


def rule_json(rule: Rule, log: OperatorLog, true_precision: dict) -> dict[str, Any]:
    payload = rule.to_json()
    source = next(
        (r for r in log.resolutions if r.resolution_id == rule.source_resolution_id), None
    )
    precision, scored = true_precision.get(rule.rule_id, (None, 0))
    payload["descended_from"] = None if source is None else {
        "resolution_id": source.resolution_id,
        "text": source.text,
        "operator": source.operator,
        "at": source.resolved_at,
        "batch": source.batch,
        "case_id": source.case_id,
    }
    payload["true_precision_pct"] = None if precision is None else str(precision)
    payload["scored_auto_resolutions"] = scored
    return payload


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #


def week(score: Score, index: int, log: OperatorLog, lookup: dict) -> dict[str, Any]:
    batch = score.run.batches[index]
    result = score.results[index]
    metric = score.metrics[index]
    learned = score.learning.batches[index]
    start, end = batch_window(batch.batch)

    return {
        "week": batch.batch,
        "dateRange": {"from": start.isoformat(), "to": end.isoformat()},
        "stats": {
            "totalTransactions": metric.settlement_rows,
            "autoMatched": metric.matched,
            "autoResolved": metric.auto_resolved,
            "flaggedForReview": metric.flagged,
            "manualReviewRate": float(metric.net_review_rate),
            "matcherReviewRate": float(metric.review_rate),
            "touchpoints": learned.human_touchpoints,
            "touchpointRate": float(pct(learned.human_touchpoints, metric.settlement_rows)),
            "bulkFixOpportunities": learned.proposals,
            "quarantined": metric.quarantined,
            "carriedForward": metric.carried_forward,
            "rupeesAutoResolved": money(learned.rupees_auto_resolved),
            "rupeesEscalated": money(learned.rupees_escalated),
            "autoResolutionPrecision": (
                None if learned.auto_resolution_precision is None
                else float(learned.auto_resolution_precision)
            ),
            "rulesLearned": learned.rules_learned,
            "rulesPromoted": learned.rules_promoted,
            "rulesRetired": learned.rules_retired,
            "tokens": metric.usage.total_tokens,
            "costInr": money(metric.cost_inr),
            "costPerTransactionInr": money(metric.cost_per_transaction_inr),
        },
        "transactions": transactions(result, batch.batch),
        "ledger": ledger(result),
        "bank": bank(result),
        "exceptions": exceptions(batch, log, lookup),
        "proposals": [proposal.to_json() for proposal in batch.proposals],
        "resolutions": [r.to_json() for r in batch.resolutions],
    }


def build(score: Score) -> dict[str, Any]:
    log = operator_log.load()
    lookup = cause_by_key(score.key)
    true_precision = {rid: (p, n) for rid, p, n in score.learning.rule_truth}

    return {
        "generatedFrom": "make score",
        "model": score.pricing.model,
        "tokensEstimated": score.run.tokens_estimated,
        "weeks": [week(score, index, log, lookup) for index in range(len(score.results))],
        "reviewRateTrend": [float(m.net_review_rate) for m in score.metrics],
        "matcherReviewRateTrend": [float(m.review_rate) for m in score.metrics],
        "touchpointRateTrend": [
            float(pct(learned.human_touchpoints, metric.settlement_rows))
            for learned, metric in zip(score.learning.batches, score.metrics)
        ],
        "precisionTrend": [
            None if b.auto_resolution_precision is None else float(b.auto_resolution_precision)
            for b in score.learning.batches
        ],
        "rules": [rule_json(rule, log, true_precision) for rule in score.run.store.rules],
        "abstention": [entry.to_json() for entry in score.learning.abstentions],
        "overallPrecision": (
            None if score.learning.overall_precision is None
            else float(score.learning.overall_precision)
        ),
        "quarantine": [
            {
                "batch": result.batch,
                "rowId": verdict.row_id,
                "table": verdict.table,
                "reason": verdict.reason.value,
                "message": verdict.detail.get("message", ""),
            }
            for result in score.results
            for verdict in result.verdicts
            if verdict.bucket is Bucket.QUARANTINED
        ],
    }


def main() -> int:
    payload = build(run())
    UI_JSON.parent.mkdir(parents=True, exist_ok=True)
    UI_JSON.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    size = UI_JSON.stat().st_size / 1024
    print(f"{len(payload['weeks'])} weeks, {len(payload['rules'])} rules -> "
          f"{UI_JSON.relative_to(REPO_ROOT)} ({size:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
