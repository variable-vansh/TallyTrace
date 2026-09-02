"""Auto-closing a claim when the money comes back.

The part an email-drafting demo does not have. A claim is not closed by someone
ticking it off; it is closed by a credit turning up in a later settlement report and
being matched to it. That match is itself a reconciliation, so it is done the way
every other match in this repo is done: **an exact key plus an explicit tolerance
band.** No fuzzy linkage, no scoring, no "probably this one".

The key is the order id. A SAFE-T reimbursement, a Flipkart adjustment credit and a
Myntra settlement top-up all carry the order they are putting right, and joining on
it is the same join the order matcher already uses. The band is
``rounding_tolerance_inr`` from ``config/thresholds.yaml`` -- the same rupee
tolerance the matcher uses everywhere else, because a reimbursement that is a paise
out is still the reimbursement.

**What is deliberately not used:** the row's description. The generator writes
``CLAIM REIMBURSEMENT ord_000081`` on the rows it plants, and matching on that string
would make this module a detector for one generator's phrasing rather than a
reconciliation. It would also score perfectly and mean nothing.

**A claim with no order id cannot auto-close.** It stays open and a human closes it.
Amount-and-platform alone is not a key: two Amazon claims for ₹2,400 are two claims,
and picking one would be a coin toss with money on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from pipeline.claims.models import Claim
from pipeline.matcher.normalise import raw_net
from pipeline.models import SettlementRow

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class RecoveryMatch:
    """One claim, and the row that paid it back."""

    claim_id: str
    row_id: str
    amount_inr: Decimal
    delta_inr: Decimal        # how far the credit was from the amount claimed

    def to_json(self) -> dict[str, str]:
        return {
            "claim_id": self.claim_id,
            "row_id": self.row_id,
            "amount_inr": str(self.amount_inr),
            "delta_inr": str(self.delta_inr),
        }


def inbound_credits(rows: Iterable[SettlementRow]) -> list[tuple[SettlementRow, Decimal]]:
    """Rows that moved money *into* the bank, with what they moved.

    ``credit - debit`` in the matcher's own normalisation, so a channel that writes a
    credit as a negative amount and one that writes it in the credit column are read
    the same way. Rows with no order id are dropped here: there is no key to join on.
    """
    return [
        (row, raw_net(row))
        for row in rows
        if row.order_id and raw_net(row) > ZERO
    ]


def match_recoveries(
    claims: Iterable[Claim], rows: Iterable[SettlementRow], tolerance_inr: Decimal
) -> list[RecoveryMatch]:
    """Pair open claims with the credits that settle them.

    One row closes at most one claim and one claim is closed by at most one row: a
    single credit cannot honestly be counted against two different debts. Candidates
    are taken in row order and the closest claim by amount wins, so the pairing does
    not depend on which claim happens to be first in the register.
    """
    open_by_order: dict[str, list[Claim]] = {}
    for claim in claims:
        if claim.is_open and claim.order_key:
            open_by_order.setdefault(claim.order_key, []).append(claim)

    matches: list[RecoveryMatch] = []
    taken: set[str] = set()
    for row, credited in sorted(inbound_credits(rows), key=lambda pair: pair[0].entity_id):
        candidates = [
            (abs(credited - claim.amount_inr), claim)
            for claim in open_by_order.get(row.order_id or "", [])
            if claim.claim_id not in taken
            and abs(credited - claim.amount_inr) <= tolerance_inr
        ]
        if not candidates:
            continue
        delta, claim = min(candidates, key=lambda pair: (pair[0], pair[1].claim_id))
        taken.add(claim.claim_id)
        matches.append(
            RecoveryMatch(
                claim_id=claim.claim_id,
                row_id=row.entity_id,
                amount_inr=credited,
                delta_inr=delta,
            )
        )
    return matches
