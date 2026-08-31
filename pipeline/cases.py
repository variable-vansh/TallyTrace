"""One human-facing problem, assembled from the verdicts that describe it.

The matcher's unit is the row. A human's unit is not: a Myntra order billed at the
wrong rate produces a verdict on the settlement row *and* one on the ledger row, and
a bookkeeper works that once. So this module groups verdicts into cases and gives
each case the feature vector everything downstream reads.

Three consumers, one shape, deliberately:

- the LLM sees a case when it generates a hypothesis, so its input is the whole
  problem rather than half of it;
- a learned rule is a predicate over ``CaseFeatures`` and nothing else, which is what
  makes rule matching a comparison of numbers instead of a search over rows;
- the UI renders a case as one card.

**No identifier is a feature.** ``CaseFeatures`` carries a channel, a reason code,
percentages, rupees and day counts. It carries no order id and no entity id, so a
rule written against it cannot be a memorised transaction even by accident. That is
enforced again at rule validation, in two places on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping

from pipeline.matcher import BatchResult, Bucket, Reason, Verdict

ZERO = Decimal("0.00")

#: Findings that never reach a queue. An order inside its settlement window is
#: carried, not queued: nobody works a payout that is not due yet.
CARRIED = Reason.AWAITING_SETTLEMENT_IN_WINDOW

SHORT = "short"      # the seller received less than the books expected
OVER = "over"        # the seller received more
FLAT = "flat"        # a finding with no money on it (a late but correct payout)


def finding_key(verdict: Verdict) -> tuple[str, str, str]:
    """The identity of a *finding*: this row, carrying this reason.

    The one definition of "is this the same problem I saw last week?", shared by the
    queue here and by ``harness/aging.py``. An order that goes overdue in batch 5 and
    is never paid carries the same key in batches 5 through 10 -- one problem, six
    verdicts -- and both the queue and the harness have to agree about that or the
    review rate measures the counting rule instead of the system.
    """
    return (verdict.table, verdict.row_id, verdict.reason.value)


def is_open(verdict: Verdict) -> bool:
    return (
        verdict.bucket in (Bucket.VARIANCE, Bucket.UNMATCHED, Bucket.QUARANTINED)
        and verdict.reason is not CARRIED
    )


# --------------------------------------------------------------------------- #
# Features
# --------------------------------------------------------------------------- #


def _decimal(detail: Mapping[str, str], key: str) -> Decimal | None:
    raw = detail.get(key)
    return Decimal(raw) if raw not in (None, "") else None


def _int(detail: Mapping[str, str], key: str) -> int | None:
    raw = detail.get(key)
    return int(raw) if raw not in (None, "") else None


def _direction(detail: Mapping[str, str]) -> str:
    """Which way the money went, from the seller's side.

    Read off the net first and the fee second, because a fee is only ever evidence
    about the net: a platform that overcharged commission short-paid the seller by
    that amount. ``shortfall`` is the bank-side equivalent -- a payout claiming more
    than the credit that funded it.
    """
    net = _decimal(detail, "net_delta")
    if net is not None and net != ZERO:
        return SHORT if net < ZERO else OVER
    fee = _decimal(detail, "fee_delta")
    if fee is not None and fee != ZERO:
        return SHORT if fee > ZERO else OVER
    shortfall = _decimal(detail, "shortfall")
    if shortfall is not None and shortfall != ZERO:
        return SHORT if shortfall > ZERO else OVER
    row_net = _decimal(detail, "row_net")
    if row_net is not None and row_net != ZERO:
        return SHORT if row_net < ZERO else OVER
    return FLAT


@dataclass(frozen=True)
class CaseFeatures:
    """Everything a rule may look at. Ids are deliberately absent -- see the module docstring."""

    channel: str | None
    reason: str
    bucket: str
    transaction_type: str | None
    direction: str
    variance_inr: Decimal                 # magnitude, never signed
    fee_variance_pct: Decimal | None
    net_variance_pct: Decimal | None
    days_after_settlement: int | None     # a deduction arriving after its order closed
    days_since_order: int | None
    days_late: int | None                 # days past the settlement window

    def to_json(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "reason": self.reason,
            "bucket": self.bucket,
            "transaction_type": self.transaction_type,
            "direction": self.direction,
            "variance_inr": str(self.variance_inr),
            "fee_variance_pct": None if self.fee_variance_pct is None else str(self.fee_variance_pct),
            "net_variance_pct": None if self.net_variance_pct is None else str(self.net_variance_pct),
            "days_after_settlement": self.days_after_settlement,
            "days_since_order": self.days_since_order,
            "days_late": self.days_late,
        }


#: Keys that measure a *deviation*. Order value is not one of them.
DELTA_KEYS = ("net_delta", "fee_delta", "shortfall", "row_net")


def _variance_inr(detail: Mapping[str, str]) -> Decimal:
    """The rupees this case puts in question.

    The largest of the deviations the matcher measured, never the order value: this
    is the figure the ``max_variance_inr`` guardrail is applied to, and a guardrail
    reading ``expected_net`` would refuse to auto-resolve a ₹30 rate variance on a
    ₹4,000 order -- blocking on the size of the sale rather than the size of the
    error. It takes the worst of the available deltas rather than a preferred one,
    so the guardrail cannot be walked under by a case that reports two.

    ``expected_net`` is the fallback and only the fallback: an order that never
    settled has no delta, and there the whole expected payout is what is missing.
    """
    deltas = [
        abs(value)
        for key in DELTA_KEYS
        for value in [_decimal(detail, key)]
        if value is not None
    ]
    if deltas:
        return max(deltas)
    return abs(_decimal(detail, "expected_net") or ZERO)


def features_of(verdicts: Iterable[Verdict]) -> CaseFeatures:
    """Collapse a case's verdicts into one feature vector.

    The detail maps are merged rather than picked from: the ledger verdict carries
    the value comparison and the settlement verdict carries the temporal fields, and
    a case wants both. Where two verdicts disagree on a key the more severe one wins,
    which is the same precedence the matcher itself uses.
    """
    ordered = sorted(verdicts, key=lambda v: (v.bucket is Bucket.VARIANCE, v.table))
    detail: dict[str, str] = {}
    for verdict in ordered:
        detail.update(verdict.detail)

    lead = max(ordered, key=lambda v: (v.impact_inr, v.table))
    channel = next((v.channel for v in ordered if v.channel), None)
    return CaseFeatures(
        channel=channel,
        reason=lead.reason.value,
        bucket=lead.bucket.value,
        transaction_type=detail.get("type"),
        direction=_direction(detail),
        variance_inr=_variance_inr(detail),
        fee_variance_pct=_decimal(detail, "fee_variance_pct"),
        net_variance_pct=_decimal(detail, "net_variance_pct"),
        days_after_settlement=_int(detail, "days_after_settlement"),
        days_since_order=_int(detail, "days_since_order"),
        days_late=_int(detail, "days_late"),
    )


# --------------------------------------------------------------------------- #
# Cases
# --------------------------------------------------------------------------- #


def case_key(verdict: Verdict) -> tuple[str, str]:
    """Which problem this verdict belongs to.

    An order is the unit wherever there is one: its ledger row and every settlement
    row that settled it describe a single thing that went wrong. A bank credit that
    nothing explains is its own case, and so is a settlement row with no order to
    hang on -- an adjustment, or a row the payout never funded.
    """
    if verdict.table == "bank_statement":
        return ("bank_credit", verdict.row_id)
    if verdict.order_id:
        return ("order", verdict.order_id)
    return ("row", verdict.row_id)


@dataclass(frozen=True)
class ExceptionCase:
    """One problem in front of a human, and everything known about it."""

    case_id: str
    batch: int
    kind: str                              # order | bank_credit | row
    key: str                               # order id, UTR, or entity id
    verdicts: tuple[Verdict, ...]
    features: CaseFeatures
    impact_inr: Decimal

    @property
    def channel(self) -> str | None:
        return self.features.channel

    @property
    def reason(self) -> str:
        return self.features.reason

    @property
    def settlement_row_ids(self) -> tuple[str, ...]:
        """The settlement rows resolving this case would close.

        The review rate is quoted against the settlement report, so this is the set
        the harness subtracts when a rule fires. Ledger and bank verdicts describe
        the same problem but are not part of that denominator.
        """
        return tuple(v.row_id for v in self.verdicts if v.table == "settlement_report")

    @property
    def row_keys(self) -> tuple[tuple[str, str], ...]:
        return tuple((v.table, v.row_id) for v in self.verdicts)

    def to_json(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "batch": self.batch,
            "kind": self.kind,
            "key": self.key,
            "impact_inr": str(self.impact_inr),
            "features": self.features.to_json(),
            "verdicts": [verdict.to_json() for verdict in self.verdicts],
        }


class FindingLog:
    """Which findings have already been put in front of a human.

    The incremental half of ``harness/aging.py``: the runner walks the batches in
    order and cannot see the future, so it records first sightings as it goes. Both
    use :func:`finding_key`, because a queue and a review rate that disagree about
    what "the same problem" means produce a curve about the counting rule.
    """

    def __init__(self) -> None:
        self._seen: set[tuple[str, str, str]] = set()

    def is_new(self, verdict: Verdict) -> bool:
        return finding_key(verdict) not in self._seen

    def record(self, verdicts: Iterable[Verdict]) -> None:
        self._seen.update(finding_key(verdict) for verdict in verdicts)


def build_cases(result: BatchResult, log: FindingLog | None = None) -> list[ExceptionCase]:
    """Every problem this batch puts in front of a human, one case each.

    ``log`` suppresses findings an earlier batch already raised. Pass it and the
    queue is what is *new* this week; leave it out and the queue is everything still
    open, which is what the UI's "all open" view wants.
    """
    open_verdicts = [v for v in result.verdicts if is_open(v)]
    if log is not None:
        open_verdicts = [v for v in open_verdicts if log.is_new(v)]
        log.record(open_verdicts)

    grouped: dict[tuple[str, str], list[Verdict]] = {}
    for verdict in open_verdicts:
        grouped.setdefault(case_key(verdict), []).append(verdict)

    cases = [
        ExceptionCase(
            case_id=f"case-{result.batch:02d}-{key}",
            batch=result.batch,
            kind=kind,
            key=key,
            verdicts=tuple(sorted(members, key=lambda v: (v.table, v.row_id))),
            features=features_of(members),
            impact_inr=sum((v.impact_inr for v in members), ZERO),
        )
        for (kind, key), members in grouped.items()
    ]
    return sorted(cases, key=lambda case: case.case_id)
