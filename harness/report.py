"""The plain-text report `make score` prints.

Fixed-width columns and no colour: this gets read in a terminal, pasted into a
commit message, and diffed against the previous run. The one thing it must never do
is round a number into looking better than it is.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from harness.attribution import CauseConfusion, SilentClears
from harness.claims import ClaimScore
from harness.cost import Pricing, to_paise
from harness.learning import Abstention, BatchLearningMetrics
from harness.metrics import BatchMetrics, QuarantineSummary, pct
from harness.reporting import ReportingScore
from harness.truth import AnswerKey
from pipeline.claims.queue import QueueView
from pipeline.metrics.registry import COUNT, INR, PERCENT, MetricResult
from pipeline.llm.usage import LlmUsage
from pipeline.rules.guardrails import ALWAYS_HUMAN_CLASSES, GuardrailConfig
from pipeline.rules.models import RuleState
from pipeline.learn import LearningRun
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


def auto_resolution_policy(cfg: GuardrailConfig, overridden: bool) -> list[str]:
    """The ceilings this run was scored under, printed above the numbers they produced.

    The report has always printed rupees auto-resolved beside rupees escalated so the
    ratio cannot be quietly inverted. Once the ceiling is a number the business sets,
    that ratio is only readable next to the policy that produced it -- so the policy
    is printed too, every run, default or not.
    """
    lines = _heading("auto-resolution policy — the ceilings a rule cannot out-confidence")
    source = "--max-variance-inr (a what-if; config/thresholds.yaml is unchanged)" \
        if overridden else "config/thresholds.yaml"
    lines.append(f"       default ceiling  ₹{cfg.default_ceiling.max_variance_inr}   [{source}]")
    if not cfg.overrides:
        lines.append("       no per-cause or per-channel ceilings set.")
    for ceiling in cfg.overrides:
        who = f"  (set by {ceiling.set_by})" if ceiling.set_by else ""
        lines.append(f"       ₹{str(ceiling.max_variance_inr):>12}  for {ceiling.scope}{who}")
        if ceiling.note:
            lines.append(f"                     {ceiling.note}")
    lines += [
        f"       never auto-resolved, whatever a rule believes: "
        f"{', '.join(sorted(cfg.never_auto_resolve_causes))}",
        f"       always human, by resolution class: {', '.join(sorted(ALWAYS_HUMAN_CLASSES))}",
    ]
    return lines


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


def evidence_gate(run: LearningRun, min_support: int) -> list[str]:
    """What the backtest threw away before a human was asked to look at it.

    Printed because it is the number that separates a system that induces rules from
    one that induces rules it can defend, and because it is the cost side of the
    ledger: most of what the ladder proposes is supposed to die here.
    """
    lines = _heading("evidence gate — candidates scored, admitted, discarded")
    lines.append(
        f"{'batch':>5} {'candidates':>11} {'admitted':>9} {'discarded':>10} {'demonstrations':>15}"
    )
    admitted = discarded = 0
    for batch in run.batches:
        cards, binned = len(batch.candidate_cards), len(batch.candidates_discarded)
        if cards == 0 and binned == 0:
            continue
        admitted += cards
        discarded += binned
        demos = sum(len(card.demonstration_ids) for card in batch.candidate_cards)
        lines.append(
            f"{batch.batch:>5} {cards + binned:>11} {cards:>9} {binned:>10} {demos:>15}"
        )
    total = admitted + discarded
    approved = sum(1 for rule in run.store.rules if rule.approved)
    lines += [
        RULE,
        f"       {total} candidates scored against the full history of resolved exceptions.",
        f"       {discarded} discarded below the {min_support}-demonstration support floor,",
        f"       {admitted} put in front of a human, {approved} approved.",
        "",
        "       Support counts distinct operator demonstrations, never rows. Eighty rows",
        "       cleared by one sentence is one piece of evidence, and a rule standing on",
        "       one demonstration backtests at 100% on the row it came from by construction.",
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


# --------------------------------------------------------------------------- #
# Checkpoint 4 — the claims queue
# --------------------------------------------------------------------------- #


def claims(score: ClaimScore, view: QueueView) -> list[str]:
    """What the register did, what it recovered, and what it let lapse."""
    lines = _heading("claims queue — opened, recovered, expired, and the money on each")
    lines.append(
        f"{'batch':>5} {'opened':>7} {'draft':>6} {'filed':>6} {'recov':>6} {'exp':>4}"
        f" {'₹ opened':>13} {'₹ recovered':>14} {'₹ expired':>12}"
        f" {'open':>5} {'₹ open':>12}"
    )
    for batch in score.batches:
        lines.append(
            f"{batch.batch:>5} {batch.opened:>7} {batch.drafted:>6} {batch.filed:>6}"
            f" {batch.recovered:>6} {batch.expired:>4} {'₹' + str(batch.rupees_opened):>13}"
            f" {'₹' + str(batch.rupees_recovered):>14} {'₹' + str(batch.rupees_expired):>12}"
            f" {batch.open_at_end:>5} {'₹' + str(batch.rupees_open_at_end):>12}"
        )
    lines += [
        RULE,
        f"       {score.opened} claims opened. {len(score.recovered)} recovered "
        f"(₹{score.rupees_recovered}), {len(score.expired)} expired "
        f"(₹{score.rupees_expired}), {len(score.still_open)} still open.",
        f"       recovery rate on settled claims: {score.recovery_rate}%. Open claims are "
        "not counted as either;",
        "       a claim inside its window is not yet a result.",
        "",
        f"       queue as it stands: {view.header}",
        "       Sorted by expiry, never by creation date. A claims list ordered by when it",
        "       was raised buries the one that stops being recoverable on Thursday.",
    ]
    return lines


def claim_recovery(score: ClaimScore) -> list[str]:
    """Every planted recovery pair, and what the register did about it."""
    lines = _heading("claim recovery — the planted reimbursements, one row each")
    lines.append(f"{'order':<14}{'credit':<12}{'claimed in':>11}{'paid in':>9}"
                 f"{'₹':>11}  outcome")
    for entry in score.planted:
        lines.append(
            f"{entry.order_id:<14}{entry.row_id:<12}{'batch ' + str(entry.claim_batch):>11}"
            f"{'batch ' + str(entry.recovery_batch):>9}{'₹' + str(entry.amount_inr):>11}"
            f"  {entry.outcome}"
        )
    lines += [
        RULE,
        f"       {score.planted_caught} of {len(score.planted)} planted pairs auto-closed "
        "against the credit that paid them.",
        "       The misses are not link failures. In both, the reimbursement arrived while "
        "the order was",
        "       still inside its settlement window, so the matcher never raised it and no "
        "claim was ever",
        "       opened to close. A claim the system had no cause to open is not a claim it "
        "failed to recover,",
        "       and it is reported as a miss anyway because excluding it would be marking "
        "its own homework.",
    ]
    return lines


def claim_attribution(score: ClaimScore) -> list[str]:
    """Whether the answer key agrees these were somebody else's problem."""
    lines = _heading("claim attribution — did the answer key agree these were claims")
    lines.append(
        f"{'cause claimed':<32}{'claims':>7}{'confirmed':>11}{'precision':>11}"
        f"{'self-closed misses':>20}"
    )
    for entry in score.attribution:
        precision = "—" if entry.precision is None else f"{entry.precision}%"
        lines.append(
            f"{entry.cause:<32}{entry.claims:>7}{entry.confirmed:>11}{precision:>11}"
            f"{entry.self_closed_misses:>20}"
        )
    total = sum(entry.claims for entry in score.attribution)
    confirmed = sum(entry.confirmed for entry in score.attribution)
    self_closed = sum(entry.self_closed_misses for entry in score.attribution)
    lines += [
        RULE,
        f"       {confirmed} of {total} claims ({pct(confirmed, total)}%) are confirmed by "
        "the answer key.",
        f"       {self_closed} of the {total - confirmed} that are not closed themselves "
        "when the money arrived,",
        "       with no operator ever filing them.",
        "",
        "       Read the missing_settlement_row row and do not look away from it. The queue",
        "       opens a claim whenever a payout is past its settlement window, and most of",
        "       those turn out to be settlements that were merely late. That is a deliberate",
        "       bias and the auto-close is what pays for it: chasing a late payout costs a",
        "       claim that closes itself, and not chasing a genuinely missing one costs the",
        "       whole payout once the filing window shuts. The bias is only affordable",
        "       because the recovery match exists, which is why both numbers are printed",
        "       side by side.",
    ]
    return lines


# --------------------------------------------------------------------------- #
# Checkpoint 4 — the reporting surface
# --------------------------------------------------------------------------- #


def _value(result: MetricResult, value: Decimal) -> str:
    if result.unit == INR:
        return f"₹{value:,.2f}"
    if result.unit == PERCENT:
        return f"{value}%"
    return str(value)


def _points(result: MetricResult, indent: str = "           ") -> list[str]:
    width = max((len(point.label) for point in result.points), default=8)
    return [
        f"{indent}{point.label:<{width}}  {_value(result, point.value):>14}"
        for point in result.points
    ]


def reporting(score: ReportingScore, registry_size: int) -> list[str]:
    """Every question asked, and what the registry did with it."""
    lines = _heading("reporting — what the registry answered, and what it would not")
    lines.append(
        f"       {registry_size} registered metrics. {score.mapped} of "
        f"{len(score.answers)} questions mapped; {score.declined} were clarified or refused."
    )
    lines.append(
        "       No SQL is generated anywhere in this system. Enterprise text-to-SQL "
        "execution accuracy"
    )
    lines.append(
        "       runs roughly 21-39% on realistic schemas, and its failures are silent: a "
        "valid query"
    )
    lines.append(
        "       returns a plausible wrong number. A closed registry can only pick the wrong "
        "id out of"
    )
    lines.append("       ten, and the restatement puts that choice in front of a human first.")
    lines.append("")
    for answer in score.answers:
        lines.append(f"       [{answer.outcome:<8}] {answer.asked.question}")
        lines.append(f"                  -> {answer.plan.restatement}")
        if answer.outcome == "clarify":
            lines.append(f"                  ?  {answer.plan.intent.clarifying_question}")
        elif answer.outcome == "refuse":
            lines.append(f"                  ✗  {answer.plan.intent.refusal}")
    lines += [
        RULE,
        "       A refusal is the feature. The tempting failure on this surface is to answer",
        "       an unanswerable question with a nearby chart, and a nearby chart carries the",
        "       same authority as a correct one.",
    ]
    return lines


def pinned(score: ReportingScore) -> list[str]:
    """Every pinned metric, recomputed. No model was constructed to produce these."""
    lines = _heading("pinned metrics — recomputed with no model in the loop")
    if not score.pins:
        lines.append("       nothing pinned.")
        return lines
    for pin, result in score.pins:
        lines.append(f"       {pin.name}  [{result.metric_id} by {result.group_by}]")
        lines.append(f'           pinned by {pin.pinned_by} on {pin.pinned_at}, from: '
                     f'"{pin.source_question}"')
        lines.extend(_points(result))
        lines.append("")
    lines += [
        RULE,
        "       The model was present at the moment of definition and is absent from every",
        "       run afterwards. What is stored in data/pins.json is a metric id and its",
        "       parameters -- never a number -- so these recompute from the reconciled data",
        "       every batch. pipeline/metrics/pins.py:recompute constructs no client, reads",
        "       no cache and renders no prompt; tests/test_pins.py asserts it by breaking",
        "       the client first and recomputing the whole dashboard anyway.",
    ]
    return lines

