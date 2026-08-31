"""The plain-text report `make score` prints.

Fixed-width columns and no colour: this gets read in a terminal, pasted into a
commit message, and diffed against the previous run. The one thing it must never do
is round a number into looking better than it is.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from harness.attribution import CauseConfusion, SilentClears
from harness.cost import Pricing, to_paise
from harness.metrics import BatchMetrics, QuarantineSummary, pct
from harness.truth import AnswerKey
from pipeline.llm.usage import LlmUsage

RULE = "-" * 78
ZERO = Decimal("0.00")


def _heading(title: str) -> list[str]:
    return ["", title.upper(), RULE]


def throughput(metrics: list[BatchMetrics], pricing: Pricing) -> list[str]:
    lines = _heading("throughput")
    lines.append(
        f"{'batch':>5}  {'records':>8}  {'settle':>7}  {'seconds':>8}  {'rec/s':>8}"
        f"  {'tokens':>8}  {'₹/txn':>8}"
    )
    for metric in metrics:
        lines.append(
            f"{metric.batch:>5}  {metric.records_processed:>8}  {metric.settlement_rows:>7}"
            f"  {metric.seconds:>8.3f}  {metric.records_per_second:>8}"
            f"  {metric.usage.total_tokens:>8}  {to_paise(metric.cost_per_transaction_inr):>8}"
        )
    total_records = sum(m.records_processed for m in metrics)
    total_seconds = sum(m.seconds for m in metrics)
    total_usage = _total_usage(metrics)
    lines += [
        RULE,
        f"{'all':>5}  {total_records:>8}  {sum(m.settlement_rows for m in metrics):>7}"
        f"  {total_seconds:>8.3f}  {int(total_records / total_seconds) if total_seconds else 0:>8}"
        f"  {total_usage.total_tokens:>8}",
    ]
    if total_usage.total_tokens == 0:
        lines.append(
            f"       no model is called anywhere in this build, so token cost is ₹0.00 "
            f"by fact rather than by rounding."
        )
        lines.append(
            f"       rates for {pricing.model} are wired from config/pricing.yaml and "
            f"exercised by tests."
        )
    return lines


def _total_usage(metrics: Iterable[BatchMetrics]) -> LlmUsage:
    total = LlmUsage()
    for metric in metrics:
        total = total + metric.usage
    return total


def accuracy(metrics: list[BatchMetrics]) -> list[str]:
    lines = _heading("accuracy — buckets and rates, as a percentage of batch total")
    lines.append(
        f"{'batch':>5} {'settle':>7} {'match':>6} {'var':>4} {'unmat':>6} {'quar':>5}"
        f" {'auto-match':>11} {'review':>8} {'auto':>5} {'net review':>11}"
        f" {'new':>4} {'aged':>5} {'carried':>8}"
    )
    for metric in metrics:
        lines.append(
            f"{metric.batch:>5} {metric.settlement_rows:>7} {metric.matched:>6}"
            f" {metric.variance:>4} {metric.unmatched:>6} {metric.quarantined:>5}"
            f" {str(metric.auto_match_rate) + '%':>11} {str(metric.review_rate) + '%':>8}"
            f" {metric.auto_resolved:>5} {str(metric.net_review_rate) + '%':>11}"
            f" {metric.review_queue:>4} {metric.aged_findings:>5} {metric.carried_forward:>8}"
        )
    first, last = metrics[0], metrics[-1]
    resolved = sum(metric.auto_resolved for metric in metrics)
    lines += [
        RULE,
        f"       batch 1 auto-match {first.auto_match_rate}%, review {first.review_rate}%"
        f"  ->  batch {last.batch} auto-match {last.auto_match_rate}%, "
        f"review {last.review_rate}%",
        "       review rate is a measurement, not a target. Nothing here is tuned to move it.",
        "       'review' is what the matcher alone leaves; 'net review' is what is left after",
        "       learned rules auto-resolve. Two columns, so a decline that came from widening",
        "       a tolerance cannot be mistaken for one that came from learning.",
    ]
    if resolved == 0:
        lines.append(
            "       nothing auto-resolves yet, so the two rates are equal by fact. "
            "Checkpoint 3 moves the second."
        )
    lines += [
        "       'new' is findings raised this batch across all three tables; 'aged' is the "
        "same problems still",
        "       open from earlier batches; 'carried' is orders inside their window, which "
        "are not exceptions.",
    ]
    return lines


def auto_resolution(precision: Decimal | None, attempts: int) -> list[str]:
    lines = _heading("auto-resolution")
    if precision is None:
        lines += [
            "       0 attempted, so precision is undefined rather than 100%.",
            "       Nothing auto-resolves until checkpoint 3; the scoring path is wired "
            "and tested.",
        ]
    else:
        lines.append(f"       {attempts} attempted, precision {precision}%")
    return lines


def confusion_table(table: list[CauseConfusion], key: AnswerKey) -> list[str]:
    lines = _heading("cause-level confusion — which bucket did each injected trouble land in")
    lines.append(f"{'cause':<32}{'class':<20}{'rows':>5}{'caught':>8}{'rate':>8}")
    for entry in sorted(table, key=lambda c: (-c.rows, c.cause)):
        lines.append(
            f"{entry.cause:<32}{entry.resolution_class:<20}{entry.rows:>5}"
            f"{entry.caught:>8}{str(pct(entry.caught, entry.rows)) + '%':>8}"
        )
        for label, count in entry.by_verdict:
            lines.append(f"    {count:>4}  {label}")
    caught = sum(entry.caught for entry in table)
    lines += [
        RULE,
        f"       {caught} of {key.affected_row_count} injected rows surfaced, "
        f"₹{key.total_impact_inr} of true impact in the corpus.",
        "       The answer key records no claim about what *should* be catchable. "
        "This table is the finding.",
    ]
    return lines


def silent_clear_table(table: list[SilentClears], key: AnswerKey) -> list[str]:
    lines = _heading("silent clears — injected troubles the matcher called clean")
    total = sum(entry.rows for entry in table)
    if not table:
        lines.append("       none.")
        return lines
    lines.append(
        f"{'cause':<32}{'rows':>6}{'largest Δ':>12}{'tightest headroom':>20}"
    )
    for entry in table:
        headroom = (
            "—" if entry.tightest_headroom_inr is None
            else f"₹{to_paise(entry.tightest_headroom_inr)} of ₹"
                 f"{to_paise(entry.tolerance_at_tightest_inr or ZERO)}"
        )
        lines.append(
            f"{entry.cause:<32}{entry.rows:>6}{'₹' + str(entry.largest_delta_inr):>12}"
            f"{headroom:>20}"
        )
    lines += [
        RULE,
        f"       {total} of {key.affected_row_count} injected rows "
        f"({pct(total, key.affected_row_count)}%).",
        "       'tightest headroom' is the smallest gap between a cleared row's deviation",
        "       and the band that permitted it. That is the number that says a band is "
        "too wide.",
    ]
    return lines


def honesty(quarantine: QuarantineSummary, open_count: int, impact: Decimal) -> list[str]:
    lines = _heading("honesty")
    lines.append(f"       {open_count} open exceptions, ₹{impact} in question. See EXCEPTIONS.md.")
    lines.append(f"       {quarantine.total} rows quarantined, none dropped:")
    for reason, count in quarantine.by_reason:
        lines.append(f"           {count:>3}  {reason}")
    if quarantine.by_batch:
        spread = ", ".join(f"batch {batch}: {count}" for batch, count in quarantine.by_batch)
        lines.append(f"       {spread}")
    return lines


def render(sections: list[list[str]]) -> str:
    lines = ["=" * 78, "TALLYTRACE — SCORE REPORT", "=" * 78]
    for section in sections:
        lines.extend(section)
    return "\n".join(lines) + "\n"
