"""Scoring the claims queue against the answer key.

Four questions, and the third and fourth are the ones that matter:

**What did the register do?** Opened, drafted, filed, recovered, expired per batch,
with rupees against each. Volume, and it is the easy half.

**Was the money right?** Rupees recovered against rupees expired. A queue that
recovers ten small claims and lets one large one lapse is behaving badly and a count
would call it a success.

**Did the recoveries actually recover anything?** The generator planted claim
reimbursements in later batches and recorded them in the answer key's manifest, which
reaches this module already loaded -- ``harness/truth.py`` stays the only thing in the
repo that names the path. This module checks each one: was a claim ever opened for it, was it linked to the
right claim, and where it was missed, what happened instead. Two of the five planted
pairs are never claimed at all in this corpus -- the reimbursement arrives before the
settlement window elapses, so the system never had cause to chase -- and that is
reported as a miss rather than quietly excluded.

**Were the claims real?** A claim is scored against the class the answer key puts its
rows in: a counterparty claim is confirmed when the key agrees somebody else owes the
money, and a TCS discrepancy -- which is in the register only for its cutoff -- is
confirmed when the key agrees it is a tax-review item. This is the least flattering
number in the file and it is meant to be: the queue over-claims badly on late
payouts, and the number beside it says what that costs.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from harness.learning import cause_by_key, true_cause_of
from harness.metrics import pct
from harness.truth import AnswerKey
from pipeline.claims.models import Claim, ClaimStatus
from pipeline.config import resolution_class_by_cause
from pipeline.learn import LearningRun

ZERO = Decimal("0.00")


def _rupees(claims: list[Claim]) -> Decimal:
    return sum((claim.amount_inr for claim in claims), ZERO)


@dataclass(frozen=True)
class BatchClaimMetrics:
    """What the register did in one batch, with the money beside every count."""

    batch: int
    opened: int
    drafted: int
    filed: int
    recovered: int
    expired: int
    rupees_opened: Decimal
    rupees_recovered: Decimal
    rupees_expired: Decimal
    open_at_end: int
    rupees_open_at_end: Decimal

    def to_json(self) -> dict[str, Any]:
        return {
            "batch": self.batch,
            "opened": self.opened,
            "drafted": self.drafted,
            "filed": self.filed,
            "recovered": self.recovered,
            "expired": self.expired,
            "rupees_opened": str(self.rupees_opened),
            "rupees_recovered": str(self.rupees_recovered),
            "rupees_expired": str(self.rupees_expired),
            "open_at_end": self.open_at_end,
            "rupees_open_at_end": str(self.rupees_open_at_end),
        }


def batch_metrics(run: LearningRun) -> list[BatchClaimMetrics]:
    """One row per batch. ``open_at_end`` is the register's state after that batch ran."""
    by_id = {claim.claim_id: claim for claim in run.register.claims}
    metrics: list[BatchClaimMetrics] = []
    for record in run.register.batches:
        recovered = [by_id[match.claim_id] for match in record.recovered]
        expired = [by_id[claim_id] for claim_id in record.expired]
        opened = [by_id[claim_id] for claim_id in record.opened]
        still_open = [
            claim
            for claim in run.register.claims
            if claim.opened_batch <= record.batch and _open_after(claim, record.batch)
        ]
        metrics.append(
            BatchClaimMetrics(
                batch=record.batch,
                opened=len(opened),
                drafted=len(record.drafted),
                filed=len(record.filed),
                recovered=len(recovered),
                expired=len(expired),
                rupees_opened=_rupees(opened),
                rupees_recovered=_rupees(recovered),
                rupees_expired=_rupees(expired),
                open_at_end=len(still_open),
                rupees_open_at_end=_rupees(still_open),
            )
        )
    return metrics


def _open_after(claim: Claim, batch: int) -> bool:
    """Was this claim still open once ``batch`` had finished?

    Read off the transition log rather than off the final status, because the final
    status is the end of the corpus and this is a per-batch series.
    """
    closed = [
        transition.batch
        for transition in claim.transitions
        if transition.to_status in {s.value for s in (
            ClaimStatus.RECOVERED, ClaimStatus.EXPIRED, ClaimStatus.WRITTEN_OFF
        )}
    ]
    return not any(when <= batch for when in closed)


# --------------------------------------------------------------------------- #
# Recovery, against the planted pairs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PlantedRecovery:
    """One reimbursement the generator planted, and what the register did about it."""

    order_id: str
    row_id: str
    claim_batch: int
    recovery_batch: int
    amount_inr: Decimal
    claim_id: str | None            # the claim the register linked it to, if any
    claim_opened: bool              # was a claim ever opened on that order at all
    linked_correctly: bool

    @property
    def outcome(self) -> str:
        if self.linked_correctly:
            return "recovered"
        if not self.claim_opened:
            return "no claim was ever opened on this order"
        return "a claim was opened but the credit was not linked to it"

    def to_json(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "row_id": self.row_id,
            "claim_batch": self.claim_batch,
            "recovery_batch": self.recovery_batch,
            "amount_inr": str(self.amount_inr),
            "claim_id": self.claim_id,
            "claim_opened": self.claim_opened,
            "linked_correctly": self.linked_correctly,
            "outcome": self.outcome,
        }


def planted_recoveries(run: LearningRun, key: AnswerKey) -> list[PlantedRecovery]:
    """Every planted claim-recovery pair, scored."""
    claims = run.register.claims
    by_recovery_row = {c.recovery_row_id: c for c in claims if c.recovery_row_id}
    orders_claimed = {c.order_key for c in claims if c.order_key}

    out: list[PlantedRecovery] = []
    for pair in sorted(key.recovery_pairs, key=lambda p: str(p["row_id"])):
        order_id = str(pair["order_id"])
        linked = by_recovery_row.get(str(pair["row_id"]))
        out.append(
            PlantedRecovery(
                order_id=order_id,
                row_id=str(pair["row_id"]),
                claim_batch=int(pair["claim_batch"]),
                recovery_batch=int(pair["batch"]),
                amount_inr=Decimal(str(pair["amount_inr"])),
                claim_id=None if linked is None else linked.claim_id,
                claim_opened=order_id in orders_claimed,
                linked_correctly=linked is not None and linked.order_key == order_id,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Were the claims real?
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ClaimAttribution:
    """Per cause: how many claims the answer key agrees were somebody else's problem."""

    cause: str
    claims: int
    confirmed: int          # the key puts these rows in the class the claim assumed
    unattributable: int     # the key has nothing to say about these rows
    self_closed_misses: int # unconfirmed claims that recovered with no human filing

    @property
    def scored(self) -> int:
        return self.claims - self.unattributable

    @property
    def precision(self) -> Decimal | None:
        return None if self.scored == 0 else pct(self.confirmed, self.scored)

    def to_json(self) -> dict[str, Any]:
        return {
            "cause": self.cause,
            "claims": self.claims,
            "confirmed": self.confirmed,
            "unattributable": self.unattributable,
            "scored": self.scored,
            "precision_pct": None if self.precision is None else str(self.precision),
            "self_closed_misses": self.self_closed_misses,
        }


def attribution(run: LearningRun, key: AnswerKey) -> list[ClaimAttribution]:
    """Score every opened claim against the class the answer key puts its rows in.

    A claim is confirmed when the key's ``resolution_class`` for its evidence matches
    the class the claim was opened under -- ``counterparty_claim`` for a claim to be
    filed, ``tax_review`` for a TCS discrepancy that is in the register only for its
    cutoff. Comparing everything to ``counterparty_claim`` would score the tax rows as
    wrong for being exactly what they are.

    ``self_closed_misses`` is the mitigation, measured rather than argued: an
    unconfirmed claim that recovered on its own, with no operator ever filing it, cost
    nobody anything. It is what makes a queue biased towards opening affordable.
    """
    lookup = cause_by_key(key)
    routes = resolution_class_by_cause()
    claims: Counter[str] = Counter()
    confirmed: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    self_closed: Counter[str] = Counter()

    for claim in run.register.claims:
        claims[claim.cause] += 1
        rows = tuple((item.table, item.row_id) for item in claim.evidence)
        true_cause = true_cause_of(rows, lookup)
        if true_cause is None:
            unknown[claim.cause] += 1
            continue
        if routes.get(true_cause) == claim.resolution_class:
            confirmed[claim.cause] += 1
        elif claim.status is ClaimStatus.RECOVERED and claim.filed_batch is None:
            self_closed[claim.cause] += 1

    return [
        ClaimAttribution(
            cause=cause, claims=count,
            confirmed=confirmed[cause], unattributable=unknown[cause],
            self_closed_misses=self_closed[cause],
        )
        for cause, count in sorted(claims.items())
    ]


# --------------------------------------------------------------------------- #
# The whole run
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ClaimScore:
    """The claims queue, scored."""

    batches: tuple[BatchClaimMetrics, ...]
    planted: tuple[PlantedRecovery, ...]
    attribution: tuple[ClaimAttribution, ...]
    claims: tuple[Claim, ...]

    @property
    def opened(self) -> int:
        return len(self.claims)

    @property
    def recovered(self) -> list[Claim]:
        return [c for c in self.claims if c.status is ClaimStatus.RECOVERED]

    @property
    def expired(self) -> list[Claim]:
        return [c for c in self.claims if c.status is ClaimStatus.EXPIRED]

    @property
    def still_open(self) -> list[Claim]:
        return [c for c in self.claims if c.is_open]

    @property
    def rupees_recovered(self) -> Decimal:
        return _rupees(self.recovered)

    @property
    def rupees_expired(self) -> Decimal:
        return _rupees(self.expired)

    @property
    def recovery_rate(self) -> Decimal:
        """Share of *settled* claims that recovered. Open claims are not yet a result."""
        settled = len(self.recovered) + len(self.expired)
        return pct(len(self.recovered), settled)

    @property
    def planted_caught(self) -> int:
        return sum(1 for entry in self.planted if entry.linked_correctly)

    def to_json(self) -> dict[str, Any]:
        return {
            "batches": [batch.to_json() for batch in self.batches],
            "opened": self.opened,
            "recovered": len(self.recovered),
            "expired": len(self.expired),
            "open": len(self.still_open),
            "rupees_recovered": str(self.rupees_recovered),
            "rupees_expired": str(self.rupees_expired),
            "rupees_open": str(_rupees(self.still_open)),
            "recovery_rate_pct": str(self.recovery_rate),
            "planted_recovery_pairs": [entry.to_json() for entry in self.planted],
            "planted_caught": self.planted_caught,
            "attribution": [entry.to_json() for entry in self.attribution],
        }


def score(run: LearningRun, key: AnswerKey) -> ClaimScore:
    return ClaimScore(
        batches=tuple(batch_metrics(run)),
        planted=tuple(planted_recoveries(run, key)),
        attribution=tuple(attribution(run, key)),
        claims=tuple(run.register.claims),
    )
