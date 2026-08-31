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
from harness.learning import Abstention, BatchLearningMetrics
from harness.metrics import BatchMetrics, QuarantineSummary, pct
from harness.truth import AnswerKey
from pipeline.llm.usage import LlmUsage
from pipeline.rules.models import RuleState
from pipeline.rules.store import RuleStore

RULE = "-" * 78
ZERO = Decimal("0.00")


def _heading(title: str) -> list[str]:
    return ["", title.upper(), RULE]


def throughput(
    metrics: list[BatchMetrics], pricing: Pricing, tokens_estimated: bool = False
) -> list[str]:
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
        lines += [
            "       no model was called on this run, so token cost is ₹0.00 by fact rather "
            "than by rounding.",
            f"       rates for {pricing.model} are wired from config/pricing.yaml and "
            f"exercised by tests.",
        ]
        return lines

    lines.append(f"       model {pricing.model}, rates from config/pricing.yaml.")
    if tokens_estimated:
        lines += [
            "       TOKEN COUNTS ARE ESTIMATED. Some cached answers were recorded from a",
            "       transcript rather than metered by the API, so their token counts are",
            "       derived from character length (config/pricing.yaml: "
            "estimated_chars_per_token).",
            "       Recording zero instead would report a model-backed pipeline as free,",
            "       which is a more misleading number than an approximate one.",
        ]
    lines.append(
        "       Cache hits are billed at the cache-read rate rather than as free: the first"
    )
    lines.append(
        "       run paid for the answer, and a cost that only counts cold runs is not a cost."
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


# --------------------------------------------------------------------------- #
# Checkpoint 3 — the learning loop
# --------------------------------------------------------------------------- #


def learning(
    learning_metrics: list[BatchLearningMetrics],
    batch_metrics: list[BatchMetrics],
    overall: Decimal | None,
) -> list[str]:
    """What the loop did, batch by batch, with precision beside every decline."""
    lines = _heading("learning loop — what a rule closed, and whether it was right")
    lines.append(
        f"{'batch':>5} {'queue':>6} {'auto':>5} {'held':>5} {'esc':>5} {'precision':>10}"
        f" {'₹ auto-resolved':>16} {'₹ escalated':>14} {'learn':>6} {'prom':>5} {'ret':>4}"
        f" {'cards':>6} {'touch':>6} {'touch %':>8}"
    )
    for learned, metric in zip(learning_metrics, batch_metrics):
        precision = (
            "—" if learned.auto_resolution_precision is None
            else f"{learned.auto_resolution_precision}%"
        )
        lines.append(
            f"{learned.batch:>5} {learned.queue_cases:>6} {learned.auto_resolved_cases:>5}"
            f" {learned.held_by_guardrail_cases:>5} {learned.escalated_cases:>5}"
            f" {precision:>10} {'₹' + str(learned.rupees_auto_resolved):>16}"
            f" {'₹' + str(learned.rupees_escalated):>14} {learned.rules_learned:>6}"
            f" {learned.rules_promoted:>5} {learned.rules_retired:>4}"
            f" {learned.proposals:>6} {learned.human_touchpoints:>6}"
            f" {str(pct(learned.human_touchpoints, metric.settlement_rows)) + '%':>8}"
        )
    first, last = learning_metrics[0], learning_metrics[-1]
    first_rows, last_rows = batch_metrics[0], batch_metrics[-1]
    lines += [
        RULE,
        f"       overall auto-resolution precision "
        f"{'undefined' if overall is None else str(overall) + '%'} over "
        f"{sum(m.scored_auto_resolutions for m in learning_metrics)} scored resolutions.",
        "",
        "       Two review series, and they say different things. Both are printed because",
        "       reporting only the flattering one is the failure this harness exists to catch.",
        f"       net review rate (rows a human still owns) : "
        f"{first_rows.net_review_rate}%  ->  {last_rows.net_review_rate}%",
        f"       human touchpoints (decisions to make)     : "
        f"{pct(first.human_touchpoints, first_rows.settlement_rows)}%  ->  "
        f"{pct(last.human_touchpoints, last_rows.settlement_rows)}%",
        "",
        "       'held' is a case a rule matched and a guardrail refused to automate. Those",
        "       rows still belong to a human, and they are collapsed into one card rather",
        "       than N exceptions — which is why the two series diverge.",
    ]
    return lines


def abstention(entries: list[Abstention]) -> list[str]:
    lines = _heading("abstention — the causes held out of the corpus until late")
    lines.append(
        f"{'cause':<30}{'first seen':>11}{'cases then':>12}{'auto then':>11}"
        f"{'auto ever':>11}{'abstention':>12}"
    )
    for entry in entries:
        lines.append(
            f"{entry.cause:<30}{'batch ' + str(entry.first_batch):>11}"
            f"{entry.cases_on_first_sight:>12}{entry.auto_resolved_on_first_sight:>11}"
            f"{entry.auto_resolved_ever:>11}{str(entry.rate) + '%':>12}"
        )
    correct = all(entry.correct_on_first_sight for entry in entries)
    lines += [
        RULE,
        "       Correct abstention is refusing to automate a cause the system has never",
        "       been taught. It is measured here, not asserted.",
        f"       every held-out cause was correctly left to a human on first sight: {correct}",
    ]
    return lines


def rules(store: RuleStore, true_precision: list[tuple[str, Decimal | None, int]]) -> list[str]:
    """Every rule, its state, its record, and the resolution it descends from."""
    truth = {rule_id: (p, n) for rule_id, p, n in true_precision}
    lines = _heading("rules — every one, including the retired one")
    lines.append(
        f"{'id':<6}{'state':<10}{'born':>5}{'support':>9}{'+':>5}{'-':>4}"
        f"{'live prec':>11}{'true prec':>14}{'last fired':>12}  cause"
    )
    for rule in store.rules:
        live = "—" if rule.precision is None else f"{rule.precision * 100:.2f}%"
        true_p, scored = truth.get(rule.rule_id, (None, 0))
        true_label = "—" if true_p is None else f"{true_p}% ({scored})"
        lines.append(
            f"{rule.rule_id:<6}{rule.state.value:<10}{rule.created_batch:>5}"
            f"{rule.support:>9}{rule.confirmations:>5}{rule.refutations:>4}"
            f"{live:>11}{true_label:>14}"
            f"{('—' if rule.last_fired_batch is None else 'batch ' + str(rule.last_fired_batch)):>12}"
            f"  {rule.cause}"
        )
    retired = [rule for rule in store.rules if rule.state is RuleState.RETIRED]
    lines.append(RULE)
    if retired:
        lines.append("       Retired, and why — this is evidence the lifecycle works, not a defect:")
        for rule in retired:
            transition = rule.transitions[-1]
            lines.append(f"       {rule.rule_id}  {rule.plain_words}")
            lines.append(f"              retired in batch {transition.batch}: {transition.reason}")
    else:
        lines.append(
            "       No rule retired. That is worth checking rather than celebrating: a "
            "lifecycle"
        )
        lines.append("       that never demotes anything has not been tested by the data.")
    lines += [
        "       'live prec' is what the operator's own resolutions said. 'true prec' is what",
        "       the answer key says about the rows the rule closed unattended. Where they",
        "       differ, the operator and the rule were fooled by the same row.",
    ]
    return lines
