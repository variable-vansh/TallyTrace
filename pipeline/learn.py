"""The learning loop, one batch at a time.

The order inside a batch is the product, so it is worth stating plainly:

1. **Reconcile.** The matcher runs exactly as it did in checkpoint 2. Nothing in this
   module can change a bucket.
2. **Build the queue.** New findings only -- an order that has been overdue for four
   weeks is one problem, not four.
3. **Hypothesise.** Every queued case gets a cause and a plain-English explanation
   from the model, constrained to the frozen enum.
4. **Decide.** The rule store is consulted. Active rules may auto-resolve, subject to
   the guardrails; shadow rules predict and log; a case no rule matched goes to a
   human untouched.
5. **Card decisions.** What the operator did with last week's proposals is applied,
   judging the observations those rules made.
6. **Claims.** Everything the routing sends to a counterparty is handed to the claims
   register: this batch's credits close earlier claims, elapsed windows expire, new
   claims open and get a draft. The learning loop and the claims queue are two
   destinations off one routing decision, not two pipelines.
7. **Resolve.** The operator's free text for this batch is read, shadow predictions on
   those cases are judged against what the human actually said, and new rules are
   induced from any resolution that does not corroborate a rule already held.
8. **Advance.** Every rule's lifecycle state is recomputed from its record.

Step 4 before step 7 matters: a rule must predict *before* it is told the answer, or
its precision is a measure of nothing. Step 8 last, so a rule promoted this week
starts firing next week rather than retroactively.

Reads ``data/generated``, ``data/resolutions.json`` and the LLM cache. Never the
answer key -- ``tests/test_boundaries.py`` fails if this file so much as names it.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from pipeline.cases import ExceptionCase, FindingLog, build_cases
from pipeline.claims.deadlines import deadline_config_from
from pipeline.claims.drafting import drafter_for
from pipeline.claims.models import Claim
from pipeline.claims.queue import QueueView, build as build_queue
from pipeline.claims.register import BatchClaims, ClaimRegister
from pipeline.claims.routing import route
from pipeline.config import (
    CONFIG_DIR,
    REPO_ROOT,
    batch_window,
    generation,
    load_yaml,
    resolution_class_by_cause,
    thresholds,
)
from pipeline.llm.client import LlmClient, client_from
from pipeline.llm.drafts import narrate
from pipeline.llm.hypotheses import hypothesise
from pipeline.llm.induction import induce
from pipeline.llm.schemas import ClaimNarrative, Hypothesis, InducedRule
from pipeline.llm.usage import UsageLedger
from pipeline.loader import BatchTables, load_batch
from pipeline.matcher import BatchResult, Bucket, MatchConfig, match_config_from
from pipeline.rules import resolutions as resolution_log
from pipeline.rules import store as rule_store
from pipeline.rules.apply import AUTO_RESOLVED, SHADOWED, Decision, decide
from pipeline.rules.approvals import ApprovalLog
from pipeline.rules import approvals as approval_log
from pipeline.rules.backtest import Demonstration, ScoredCandidate, backtest, survivors
from pipeline.rules.candidates import ladder
from pipeline.rules.guardrails import GuardrailConfig, guardrail_config_from
from pipeline.rules.lifecycle import advance, lifecycle_config_from
from pipeline.rules.models import Rule, RuleState, rule_from
from pipeline.rules.proposals import (
    CandidateCard,
    Proposal,
    build as build_proposals,
    candidate_cards,
)
from pipeline.rules.resolutions import ACCEPT, DECLINE, OperatorLog, Resolution
from pipeline.rules.store import RuleStore
from pipeline.run import OpenBook, run_batch

LEARNING_JSON = REPO_ROOT / "data" / "learning.json"
ZERO = Decimal("0.00")


#: Turns (platform, cause, batch) into the words of a claim. The live implementation
#: asks the model through the cache; ``tools/write_llm_fixtures.py`` passes one that
#: records which shapes a real run asks for, which is how the fixture list stays
#: derived from the corpus rather than typed out by hand and quietly going stale.
Narrator = Callable[[str, str, int], ClaimNarrative]


def live_narrator(client: LlmClient) -> Narrator:
    def ask(platform: str, cause: str, batch: int) -> ClaimNarrative:
        return narrate(client, platform, cause, batch)

    return ask


def new_register() -> ClaimRegister:
    """A register wired from config. Both the runner and the harness build one this way."""
    cfg = thresholds()
    return ClaimRegister(
        deadlines=deadline_config_from(cfg),
        rounding_tolerance_inr=Decimal(cfg["matching"]["rounding_tolerance_inr"]),
    )


# --------------------------------------------------------------------------- #
# Per-batch record
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BatchLearning:
    """Everything the loop did in one batch."""

    batch: int
    result: BatchResult
    #: The rows this batch was reconciled from. Carried rather than reloaded, so the
    #: metric registry computes over exactly the rows the matcher bucketed. A second
    #: read of the CSVs would let the two halves disagree about which rows existed.
    tables: BatchTables
    cases: tuple[ExceptionCase, ...]
    hypotheses: dict[str, Hypothesis]
    decisions: tuple[Decision, ...]
    proposals: tuple[Proposal, ...]
    rules_learned: tuple[str, ...]
    rules_promoted: tuple[str, ...]
    rules_retired: tuple[str, ...]
    resolutions: tuple[Resolution, ...]
    claims: BatchClaims
    #: Candidates that cleared the support gate and are waiting on a human.
    candidate_cards: tuple[CandidateCard, ...] = ()
    #: Candidates the backtest threw away for want of demonstrations. Kept and
    #: counted: how much of what the model proposes is not worth acting on is a
    #: result, not an implementation detail.
    candidates_discarded: tuple[ScoredCandidate, ...] = ()

    @property
    def auto_resolved(self) -> tuple[Decision, ...]:
        return tuple(d for d in self.decisions if d.resolved)

    @property
    def escalated(self) -> tuple[Decision, ...]:
        return tuple(d for d in self.decisions if d.needs_human)

    @property
    def rupees_auto_resolved(self) -> Decimal:
        return sum((d.impact_inr for d in self.auto_resolved), ZERO)

    @property
    def rupees_escalated(self) -> Decimal:
        return sum((d.impact_inr for d in self.escalated), ZERO)

    def to_json(self) -> dict[str, Any]:
        return {
            "batch": self.batch,
            "cases": len(self.cases),
            "auto_resolved_cases": len(self.auto_resolved),
            "auto_resolved_rows": sum(len(d.case.settlement_row_ids) for d in self.auto_resolved),
            "escalated_cases": len(self.escalated),
            "rupees_auto_resolved": str(self.rupees_auto_resolved),
            "rupees_escalated": str(self.rupees_escalated),
            "rules_learned": list(self.rules_learned),
            "rules_promoted": list(self.rules_promoted),
            "rules_retired": list(self.rules_retired),
            "resolutions_captured": len(self.resolutions),
            "claims": self.claims.to_json(),
            "proposals": [proposal.to_json() for proposal in self.proposals],
            "candidate_cards": [card.to_json() for card in self.candidate_cards],
            "candidates_discarded": [
                candidate.to_json() for candidate in self.candidates_discarded
            ],
            "decisions": [
                {
                    **decision.provenance.to_json(),
                    "kind": decision.case.kind,
                    "key": decision.case.key,
                    "channel": decision.case.channel,
                    "reason": decision.case.reason,
                    "impact_inr": str(decision.case.impact_inr),
                    "settlement_row_ids": list(decision.case.settlement_row_ids),
                    "hypothesis": (
                        None
                        if decision.case.case_id not in self.hypotheses
                        else {
                            "cause": self.hypotheses[decision.case.case_id].cause.value,
                            "text": self.hypotheses[decision.case.case_id].hypothesis,
                            "confidence": str(self.hypotheses[decision.case.case_id].confidence),
                        }
                    ),
                }
                for decision in self.decisions
            ],
        }


@dataclass
class LearningRun:
    """The whole corpus, learned across."""

    batches: list[BatchLearning] = field(default_factory=list)
    store: RuleStore = field(default_factory=RuleStore)
    register: ClaimRegister = field(default_factory=lambda: new_register())
    ledger: UsageLedger = field(default_factory=UsageLedger)
    #: Every resolution the operator wrote, as the evidence candidates are scored on.
    history: list[Demonstration] = field(default_factory=list)
    #: Whether the token counts behind the cost report were metered by the API or
    #: estimated from a recorded transcript. Carried so the report can label them.
    tokens_estimated: bool = False

    @property
    def results(self) -> list[BatchResult]:
        return [b.result for b in self.batches]

    @property
    def claims(self) -> list[Claim]:
        return self.register.claims


# --------------------------------------------------------------------------- #
# Rule bookkeeping
# --------------------------------------------------------------------------- #


def signature(rule: Rule) -> tuple[Any, ...]:
    """What makes two rules the same rule.

    The conditions and the cause, not the sentence they came from. Six bookkeepers
    writing six notes about the same stale Myntra rate should produce one rule with
    six resolutions behind it, not six rules that each fire on the same rows and each
    claim the credit.
    """
    return (
        rule.cause,
        rule.channel,
        rule.reason_code,
        rule.transaction_type,
        rule.variance_band_pct,
        rule.net_variance_band_pct,
        rule.direction,
        rule.lag_window_days,
    )


def equivalent(store: RuleStore, candidate: Rule) -> Rule | None:
    target = signature(candidate)
    return next((rule for rule in store.rules if signature(rule) == target), None)


def _judge(store: RuleStore, case_id: str, correct: bool, source: str) -> None:
    for rule in list(store.rules):
        if any(o.case_id == case_id and o.correct is None for o in rule.observations):
            store.replace(rule.judging(case_id, correct, source))


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


def _hypothesise_all(
    client: LlmClient, cases: list[ExceptionCase]
) -> dict[str, Hypothesis]:
    """A hypothesis for every case the matcher could actually read.

    Quarantined rows are excluded, and the exclusion is a correctness decision
    rather than a saving. The frozen enum holds business causes; a row rejected
    because its date was ``31/02/2025`` has no business cause, and forcing a choice
    from an enum that cannot express "this file is malformed" would put an invented
    reconciliation finding next to a confidence score. The quarantine reason already
    says exactly what happened, which is more than a hypothesis could add.
    """
    return {
        case.case_id: hypothesise(client, case)
        for case in cases
        if case.features.bucket != Bucket.QUARANTINED.value
    }


def _apply_card_decisions(store: RuleStore, log: OperatorLog, batch: int) -> None:
    """Judge last batch's proposals by what the operator did with them.

    Accepting a card confirms every observation the rule made; declining it refutes
    them. Declining is the corrigibility path, and it is the mechanism by which a
    rule that over-matches gets retired by the person it is annoying rather than by
    a scheduled review nobody runs.
    """
    for rule_id, decision in sorted(log.decisions_for(batch).items()):
        if decision.decision not in (ACCEPT, DECLINE):
            continue
        correct = decision.decision == ACCEPT
        rule = store.get(rule_id)
        for observation in rule.observations:
            if observation.batch == batch and observation.correct is None:
                _judge(store, observation.case_id, correct, "operator_card")


@dataclass(frozen=True)
class Capture:
    """What this batch's resolutions did to the rule store."""

    learned: tuple[str, ...]
    cards: tuple[CandidateCard, ...]
    discarded: tuple[ScoredCandidate, ...]


def _demonstrate(
    client: LlmClient,
    store: RuleStore,
    cases_by_id: dict[str, ExceptionCase],
    resolutions: list[Resolution],
    history: list[Demonstration],
) -> list[tuple[Resolution, ExceptionCase, InducedRule]]:
    """Read each resolution into a rule, judge what predicted on it, record the evidence.

    Judging happens against the cause the human's own words induced to, never against
    the answer key. That is the product's signal and it is the honest one: the system
    finds out it was wrong the same way a colleague would, by being told.

    Every resolution becomes a :class:`~pipeline.rules.backtest.Demonstration` whether
    or not a rule ends up standing on it. The history is what candidates are scored
    against, so it has to be the whole record of what the operator did, not the subset
    that happened to induce cleanly.
    """
    read: list[tuple[Resolution, ExceptionCase, InducedRule]] = []
    for resolution in resolutions:
        case = cases_by_id.get(resolution.case_id)
        if case is None:
            continue
        induced = induce(client, resolution.text, case.features, case.batch)

        # Judge before anything is stored: whatever a rule predicted on this case is
        # now checked against the cause the human's own words imply. Nothing predicted
        # on it means nothing to judge, and _judge quietly does nothing.
        _judge(store, case.case_id, _agrees(store, case.case_id, induced.cause.value),
               "human_resolution")

        history.append(
            Demonstration(
                resolution_id=resolution.resolution_id,
                case_id=case.case_id,
                batch=case.batch,
                features=case.features,
                cause=induced.cause.value,
            )
        )
        read.append((resolution, case, induced))
    return read


def _pending_candidates(
    store: RuleStore, read: list[tuple[Resolution, ExceptionCase, InducedRule]]
) -> dict[tuple[Any, ...], tuple[str, Rule]]:
    """Ladder every reading; fold what already exists back into the rule it repeats.

    Six notes about the same stale rate are one rule with six demonstrations behind
    it, not six rules that each fire on the same rows and each claim the credit. The
    fold is also how support accumulates: a rule already in the store gains the new
    resolution id here, which is what eventually carries it past the support gate.
    """
    pending: dict[tuple[Any, ...], tuple[str, Rule]] = {}
    for resolution, case, induced in read:
        for rung in ladder(induced):
            provisional = rule_from(
                rung.rule,
                rule_id="",
                batch=case.batch,
                resolution_id=resolution.resolution_id,
                operator=resolution.operator,
                level=rung.level,
            )
            existing = equivalent(store, provisional)
            if existing is not None:
                store.replace(existing.demonstrated_by(resolution.resolution_id))
                continue
            key = signature(provisional)
            if key in pending:
                level, held = pending[key]
                pending[key] = (level, held.demonstrated_by(resolution.resolution_id))
                continue
            pending[key] = (rung.level, provisional)
    return pending


def _capture_resolutions(
    client: LlmClient,
    store: RuleStore,
    cases_by_id: dict[str, ExceptionCase],
    resolutions: list[Resolution],
    history: list[Demonstration],
    approvals: ApprovalLog,
    min_support: int,
    batch: int,
) -> Capture:
    """Turn this batch's resolutions into evidence, and evidence into candidates.

    The order is the gate:

    1. read every resolution and record the demonstration it constitutes;
    2. ladder each reading into up to three candidates of different reach;
    3. fold candidates that repeat a rule already in the store into that rule;
    4. backtest what is left against the *whole* history, not just this batch;
    5. discard anything below the support threshold -- and count it;
    6. admit the survivors as ``proposed``, and put a card in front of a human.

    Nothing here moves a rule into shadow. ``advance`` does that, at the end of the
    batch, and only for a rule that has both the demonstrations and the approval.
    """
    read = _demonstrate(client, store, cases_by_id, resolutions, history)
    pending = _pending_candidates(store, read)

    scored = [
        ScoredCandidate(level=level, rule=rule, score=backtest(rule, history, store.firing))
        for level, rule in pending.values()
    ]
    kept, discarded = survivors(scored, min_support)

    learned: list[str] = []
    admitted: list[ScoredCandidate] = []
    for candidate in kept:
        rule = replace(
            candidate.rule,
            rule_id=store.next_id(),
            demonstration_ids=candidate.score.supporting_resolution_ids,
            backtest_coverage=candidate.score.coverage,
            backtest_precision=candidate.score.precision,
        )
        verdict = approvals.verdict_for(rule)
        if verdict is not None and verdict.approves:
            rule = rule.approving(verdict.operator, rule.created_batch, verdict.note)
        store.add(rule)
        learned.append(rule.rule_id)
        admitted.append(replace(candidate, rule=rule))

    return Capture(
        learned=tuple(learned),
        cards=tuple(candidate_cards(batch, admitted)),
        discarded=tuple(discarded),
    )


def _agrees(store: RuleStore, case_id: str, cause: str) -> bool:
    """Did the rule that predicted on this case predict the cause the human implied?

    False when nothing predicted on it, which is inert: :func:`_judge` finds no pending
    observation to write the verdict onto. A case no rule saw earns nobody a mark.
    """
    for rule in store.rules:
        for observation in rule.observations:
            if observation.case_id == case_id and observation.correct is None:
                return observation.predicted_cause == cause
    return False


def _advance_claims(
    register: ClaimRegister,
    tables: BatchTables,
    cases: list[ExceptionCase],
    decisions: list[Decision],
    hypotheses: dict[str, Hypothesis],
    resolutions: list[Resolution],
    narrator: Narrator,
) -> BatchClaims:
    """Hand this batch's counterparty exceptions to the register, and draft the new ones.

    The routing table is ``config/causes.yaml`` and nothing else: a cause's
    ``resolution_class`` decides whether it is a rule's problem or a platform's. The
    same decision list feeds both, so nothing can be in the learning loop and in the
    claims queue at once, and nothing can fall between them.
    """
    routes = resolution_class_by_cause()
    cases_by_id = {case.case_id: case for case in cases}

    def words(platform: str, cause: str) -> ClaimNarrative:
        return narrator(platform, cause, tables.batch)

    return register.advance(
        batch=tables.batch,
        batch_end=batch_window(tables.batch)[1],
        settlements=tables.settlements,
        routed=route(decisions, hypotheses, routes),
        resolution_class_by_cause=routes,
        resolutions=resolutions,
        drafter=drafter_for(words, cases_by_id),
    )


def _decide_all(
    cases: list[ExceptionCase], store: RuleStore, batch: int, guardrails: GuardrailConfig
) -> list[Decision]:
    """Consult the rule store on every case, recording what each rule predicted.

    The observation is written here rather than inside ``decide`` because a rule is an
    immutable dataclass and the store is the thing that owns it. A rule that fired also
    has its ``last_fired_batch`` stamped, which is what the rules page shows.

    The guardrail policy is passed in rather than read here, because the ceiling is a
    number the business sets and a run has to be able to say which one it ran under.
    A module that reaches for the config mid-loop cannot be asked that question.
    """
    decisions: list[Decision] = []
    for case in cases:
        decision, observation = decide(case, store.predicting, guardrails)
        decisions.append(decision)
        rule_id = decision.provenance.rule_id
        if observation is None or rule_id is None:
            continue
        rule = store.get(rule_id)
        fired = decision.provenance.outcome == AUTO_RESOLVED
        store.replace(
            replace(
                rule.observing(observation),
                last_fired_batch=batch if fired else rule.last_fired_batch,
            )
        )
    return decisions


def _advance_all(store: RuleStore, batch: int) -> tuple[list[str], list[str]]:
    """Recompute every rule's lifecycle state from its record. Returns (promoted, retired).

    Last in the batch on purpose: a rule promoted this week starts firing next week
    rather than retroactively, and the lag is what makes the decline believable.
    """
    cfg = lifecycle_config_from(thresholds())
    promoted: list[str] = []
    retired: list[str] = []
    for rule in list(store.rules):
        moved = advance(rule, batch, cfg)
        if moved.state is rule.state:
            continue
        store.replace(moved)
        if moved.state is RuleState.ACTIVE:
            promoted.append(moved.rule_id)
        elif moved.state is RuleState.RETIRED:
            retired.append(moved.rule_id)
    return promoted, retired


def run_learning_batch(
    tables: BatchTables,
    book: OpenBook,
    cfg: MatchConfig,
    store: RuleStore,
    client: LlmClient,
    log: OperatorLog,
    finding_log: FindingLog,
    register: ClaimRegister,
    narrator: Narrator | None = None,
    guardrails: GuardrailConfig | None = None,
    history: list[Demonstration] | None = None,
    approvals: ApprovalLog | None = None,
) -> BatchLearning:
    """One batch, all eight steps, in order. See the module docstring for why that order."""
    result = run_batch(tables, book, cfg)
    batch = result.batch
    cases = build_cases(result, finding_log)
    lifecycle = lifecycle_config_from(thresholds())

    hypotheses = _hypothesise_all(client, cases)
    decisions = _decide_all(
        cases, store, batch, guardrails or guardrail_config_from(thresholds())
    )
    proposals = build_proposals(batch, decisions, {r.rule_id: r for r in store.rules})

    _apply_card_decisions(store, log, batch)
    batch_resolutions = log.for_batch(batch)
    claims = _advance_claims(
        register, tables, cases, list(decisions), hypotheses, batch_resolutions,
        narrator or live_narrator(client),
    )
    capture = _capture_resolutions(
        client,
        store,
        {case.case_id: case for case in cases},
        batch_resolutions,
        history if history is not None else [],
        approvals or approval_log.empty(),
        lifecycle.min_support_demonstrations,
        batch,
    )
    promoted, retired = _advance_all(store, batch)

    return BatchLearning(
        batch=batch,
        result=result,
        tables=tables,
        cases=tuple(cases),
        hypotheses=hypotheses,
        decisions=tuple(decisions),
        proposals=tuple(proposals),
        rules_learned=capture.learned,
        rules_promoted=tuple(promoted),
        rules_retired=tuple(retired),
        resolutions=tuple(batch_resolutions),
        claims=claims,
        candidate_cards=capture.cards,
        candidates_discarded=capture.discarded,
    )


def run(
    generated_dir: Path | None = None,
    *,
    operator_log: OperatorLog | None = None,
    cache_dir: Path | None = None,
    last_batch: int | None = None,
    narrator: Narrator | None = None,
    allow_network: bool = True,
    guardrails: GuardrailConfig | None = None,
    approvals: ApprovalLog | None = None,
) -> LearningRun:
    """Walk the corpus, learning as it goes.

    ``guardrails`` defaults to the policy in ``config/thresholds.yaml``. A caller
    passes one to ask what a different ceiling would have done -- see the CLI below.
    """
    pricing = load_yaml(CONFIG_DIR / "pricing.yaml")
    ledger = UsageLedger()
    client = client_from(
        str(pricing["model"]),
        cache_dir=cache_dir,
        ledger=ledger,
        chars_per_token=Decimal(str(pricing["estimated_chars_per_token"])),
        allow_network=allow_network,
    )
    log = operator_log if operator_log is not None else resolution_log.load()

    cfg = match_config_from(thresholds())
    policy = guardrails or guardrail_config_from(thresholds())
    book = OpenBook.empty()
    finding_log = FindingLog()
    store = RuleStore()
    register = new_register()
    run_record = LearningRun(store=store, register=register, ledger=ledger)

    # Carried across batches, like the open book and the finding log: a candidate is
    # scored against everything the operator has ever resolved, not against the week
    # it happened to be induced in.
    history: list[Demonstration] = []
    decisions_log = approvals if approvals is not None else approval_log.load()

    count = last_batch or int(generation()["batch_count"])
    for batch in range(1, count + 1):
        run_record.batches.append(
            run_learning_batch(
                load_batch(batch, generated_dir), book, cfg, store, client, log,
                finding_log, register, narrator, policy, history, decisions_log,
            )
        )
    run_record.history = history
    run_record.tokens_estimated = client.tokens_estimated
    return run_record


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def to_json(run_record: LearningRun) -> dict[str, Any]:
    return {
        "batches": [batch.to_json() for batch in run_record.batches],
        "rules": run_record.store.to_json()["rules"],
        "claims": run_record.register.to_json()["claims"],
        "llm": run_record.ledger.total().to_json(),
    }


def queue_view(run_record: LearningRun) -> QueueView:
    """The claims queue as it stands at the end of the run, sorted by expiry."""
    last = run_record.batches[-1].batch if run_record.batches else 1
    return build_queue(run_record.register.claims, batch_window(last)[1])


def summarise(batch: BatchLearning) -> str:
    return (
        f"batch {batch.batch:>2}  "
        f"queue {len(batch.cases):>3}  "
        f"auto-resolved {len(batch.auto_resolved):>3} "
        f"(₹{batch.rupees_auto_resolved:>10})  "
        f"escalated {len(batch.escalated):>3} (₹{batch.rupees_escalated:>10})  "
        f"cards {len(batch.proposals):>2}  "
        f"candidates {len(batch.candidate_cards):>2}/"
        f"{len(batch.candidate_cards) + len(batch.candidates_discarded):<2} "
        f"learned {len(batch.rules_learned)} promoted {len(batch.rules_promoted)} "
        f"retired {len(batch.rules_retired)}  "
        f"claims +{len(batch.claims.opened)} "
        f"recovered {len(batch.claims.recovered)} expired {len(batch.claims.expired)}"
    )


def rupees(raw: str) -> Decimal:
    """Parse a ceiling off the command line at the paise grain money uses everywhere."""
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"{raw!r} is not an amount in rupees") from None
    if value < ZERO:
        raise argparse.ArgumentTypeError("a variance ceiling cannot be negative")
    return value.quantize(Decimal("0.01"))


def ceiling_argument(parser: argparse.ArgumentParser) -> None:
    """``--max-variance-inr``, shared by ``make learn`` and ``make score``.

    A what-if, not a setting. The standing policy is ``config/thresholds.yaml``; this
    flag answers "what would a ₹2,000 default have closed, and at what precision?"
    without editing the file, and every run that uses it says so in its output.
    """
    parser.add_argument(
        "--max-variance-inr", type=rupees, default=None, metavar="RUPEES",
        help=(
            "run with a different default auto-resolution ceiling. Per-cause and "
            "per-channel overrides in config/thresholds.yaml still apply."
        ),
    )


def policy_from(args: argparse.Namespace) -> tuple[GuardrailConfig, bool]:
    """The guardrail policy for this run, and whether the CLI moved it."""
    configured = guardrail_config_from(thresholds())
    if args.max_variance_inr is None:
        return configured, False
    return configured.with_default_ceiling(args.max_variance_inr), True


def describe_policy(policy: GuardrailConfig, overridden: bool) -> list[str]:
    """The ceilings in force, printed before the numbers they produced.

    Printed on every run, not only an overridden one. A reader who has to go and look
    up which ceiling a report was produced under is a reader who will assume the
    default, and the assumption is the thing that goes stale.
    """
    source = "--max-variance-inr" if overridden else "config/thresholds.yaml"
    lines = [
        f"auto-resolution ceiling: ₹{policy.default_ceiling.max_variance_inr} by default"
        f"  [{source}]"
    ]
    for ceiling in policy.overrides:
        who = f" — set by {ceiling.set_by}" if ceiling.set_by else ""
        lines.append(f"    ₹{ceiling.max_variance_inr:>12} for {ceiling.scope}{who}")
        if ceiling.note:
            lines.append(f"                 {ceiling.note}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the learning loop across the corpus.")
    parser.add_argument(
        "--offline", action="store_true",
        help="never call the API, even with a key set; answer only from data/llm_cache",
    )
    ceiling_argument(parser)
    args = parser.parse_args()
    policy, overridden = policy_from(args)
    for line in describe_policy(policy, overridden):
        print(line)
    print()

    record = run(allow_network=not args.offline, guardrails=policy)
    for batch in record.batches:
        print(summarise(batch))

    view = queue_view(record)
    print(f"\nclaims queue at the end of the corpus: {view.header}")
    rule_store.save(record.store)
    LEARNING_JSON.write_text(
        json.dumps(to_json(record), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\n{len(record.store.rules)} rules -> data/rules.json")
    print(f"wrote {LEARNING_JSON.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
