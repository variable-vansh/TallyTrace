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
from decimal import Decimal
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
from pipeline.llm.schemas import ClaimNarrative, Hypothesis
from pipeline.llm.usage import UsageLedger
from pipeline.loader import BatchTables, load_batch
from pipeline.matcher import BatchResult, Bucket, MatchConfig, match_config_from
from pipeline.rules import resolutions as resolution_log
from pipeline.rules import store as rule_store
from pipeline.rules.apply import AUTO_RESOLVED, SHADOWED, Decision, decide
from pipeline.rules.guardrails import guardrail_config_from
from pipeline.rules.lifecycle import advance, lifecycle_config_from
from pipeline.rules.models import Rule, RuleState, rule_from
from pipeline.rules.proposals import Proposal, build as build_proposals
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


def _capture_resolutions(
    client: LlmClient,
    store: RuleStore,
    cases_by_id: dict[str, ExceptionCase],
    resolutions: list[Resolution],
) -> list[str]:
    """Induce rules from this batch's human resolutions; judge shadow predictions.

    Judging happens against the cause the human's own words induced to, never against
    the answer key. That is the product's signal and it is the honest one: the system
    finds out it was wrong the same way a colleague would, by being told.
    """
    learned: list[str] = []
    for resolution in resolutions:
        case = cases_by_id.get(resolution.case_id)
        if case is None:
            continue
        induced = induce(client, resolution.text, case.features, case.batch)
        candidate = rule_from(
            induced,
            rule_id=store.next_id(),
            batch=case.batch,
            resolution_id=resolution.resolution_id,
            operator=resolution.operator,
        )
        # Judge before storing: whatever a rule predicted on this case is now checked
        # against the cause the human's own words imply. Nothing predicted on it means
        # nothing to judge, and _judge quietly does nothing.
        was_right = _agrees(store, case.case_id, candidate.cause)
        _judge(store, case.case_id, was_right, "human_resolution")

        # Six notes about the same stale rate are one rule with six resolutions behind
        # it, not six rules that each fire on the same rows and each claim the credit.
        if equivalent(store, candidate) is None:
            store.add(candidate)
            learned.append(candidate.rule_id)
    return learned


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
    cases: list[ExceptionCase], store: RuleStore, batch: int
) -> list[Decision]:
    """Consult the rule store on every case, recording what each rule predicted.

    The observation is written here rather than inside ``decide`` because a rule is an
    immutable dataclass and the store is the thing that owns it. A rule that fired also
    has its ``last_fired_batch`` stamped, which is what the rules page shows.
    """
    guardrails = guardrail_config_from(thresholds())
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
) -> BatchLearning:
    """One batch, all eight steps, in order. See the module docstring for why that order."""
    result = run_batch(tables, book, cfg)
    batch = result.batch
    cases = build_cases(result, finding_log)

    hypotheses = _hypothesise_all(client, cases)
    decisions = _decide_all(cases, store, batch)
    proposals = build_proposals(batch, decisions, {r.rule_id: r for r in store.rules})

    _apply_card_decisions(store, log, batch)
    batch_resolutions = log.for_batch(batch)
    claims = _advance_claims(
        register, tables, cases, list(decisions), hypotheses, batch_resolutions,
        narrator or live_narrator(client),
    )
    learned = _capture_resolutions(
        client, store, {case.case_id: case for case in cases}, batch_resolutions
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
        rules_learned=tuple(learned),
        rules_promoted=tuple(promoted),
        rules_retired=tuple(retired),
        resolutions=tuple(batch_resolutions),
        claims=claims,
    )


def run(
    generated_dir: Path | None = None,
    *,
    operator_log: OperatorLog | None = None,
    cache_dir: Path | None = None,
    last_batch: int | None = None,
    narrator: Narrator | None = None,
    allow_network: bool = True,
) -> LearningRun:
    """Walk the corpus, learning as it goes."""
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
    book = OpenBook.empty()
    finding_log = FindingLog()
    store = RuleStore()
    register = new_register()
    run_record = LearningRun(store=store, register=register, ledger=ledger)

    count = last_batch or int(generation()["batch_count"])
    for batch in range(1, count + 1):
        run_record.batches.append(
            run_learning_batch(
                load_batch(batch, generated_dir), book, cfg, store, client, log,
                finding_log, register, narrator,
            )
        )
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
        f"learned {len(batch.rules_learned)} promoted {len(batch.rules_promoted)} "
        f"retired {len(batch.rules_retired)}  "
        f"claims +{len(batch.claims.opened)} "
        f"recovered {len(batch.claims.recovered)} expired {len(batch.claims.expired)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the learning loop across the corpus.")
    parser.add_argument(
        "--offline", action="store_true",
        help="never call the API, even with a key set; answer only from data/llm_cache",
    )
    args = parser.parse_args()
    record = run(allow_network=not args.offline)
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
