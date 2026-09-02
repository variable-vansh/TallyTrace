"""The claims register: one batch at a time, in the order the money actually moves.

Called once per batch from the learning loop, after the matcher has run and the rules
have had their say. The order inside :meth:`ClaimRegister.advance` is the design:

1. **Recover.** Credits in this batch close claims opened in earlier ones. First,
   because a claim that has just been paid should never be shown as expiring.
2. **Expire.** Claims whose window closed without a recovery. Terminal, and recorded
   with the date rather than deleted -- rupees expired is the number that says
   whether the clock is worth having.
3. **Open.** This batch's counterparty exceptions become claims. After expiry, so a
   claim cannot be opened and closed in the same week by the row that created it.
4. **Draft.** A newly opened counterparty claim gets a message drafted immediately,
   so the draft is already waiting when the human first sees the queue. That is the
   only language work in the file and it is injected as a callable -- the register
   itself does no I/O and calls no model.
5. **File.** The operator's own resolution note on a claim's case moves it to
   ``filed``. That is a real human action with a real provenance chain behind it,
   not a simulated workflow step. It runs last so that a claim worked in the week it
   opened ends the batch as ``filed`` rather than as ``drafted``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping

from pipeline.cases import ExceptionCase
from pipeline.claims.deadlines import DeadlineConfig, deadline_for
from pipeline.claims.models import Claim, ClaimStatus, Evidence
from pipeline.claims.recovery import RecoveryMatch, match_recoveries
from pipeline.claims.routing import evidence_of
from pipeline.models import SettlementRow
from pipeline.rules.resolutions import Resolution

ZERO = Decimal("0.00")

#: Turns a claim into a message a human can send, or ``None`` where none may be sent.
Drafter = Callable[[Claim], str | None]


@dataclass(frozen=True)
class BatchClaims:
    """What the register did in one batch."""

    batch: int
    opened: tuple[str, ...]
    recovered: tuple[RecoveryMatch, ...]
    expired: tuple[str, ...]
    filed: tuple[str, ...]
    drafted: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "batch": self.batch,
            "opened": list(self.opened),
            "recovered": [match.to_json() for match in self.recovered],
            "expired": list(self.expired),
            "filed": list(self.filed),
            "drafted": list(self.drafted),
        }


class ClaimRegister:
    """Every claim the corpus has produced, and the batch-by-batch record of them."""

    def __init__(self, deadlines: DeadlineConfig, rounding_tolerance_inr: Decimal) -> None:
        self.deadlines = deadlines
        self.rounding_tolerance_inr = rounding_tolerance_inr
        self._claims: dict[str, Claim] = {}
        self._by_order: dict[tuple[str, str], str] = {}
        self.batches: list[BatchClaims] = []

    # -- access ------------------------------------------------------------ #

    @property
    def claims(self) -> list[Claim]:
        return [self._claims[claim_id] for claim_id in sorted(self._claims)]

    @property
    def open_claims(self) -> list[Claim]:
        return [claim for claim in self.claims if claim.is_open]

    def get(self, claim_id: str) -> Claim:
        return self._claims[claim_id]

    def _next_id(self) -> str:
        return f"CLM-{len(self._claims) + 1:04d}"

    def _replace(self, claim: Claim) -> None:
        self._claims[claim.claim_id] = claim

    # -- the batch --------------------------------------------------------- #

    def advance(
        self,
        batch: int,
        batch_end: date,
        settlements: Iterable[SettlementRow],
        routed: list[tuple[ExceptionCase, str, str]],
        resolution_class_by_cause: Mapping[str, str],
        resolutions: Iterable[Resolution] = (),
        drafter: Drafter | None = None,
    ) -> BatchClaims:
        """Run one batch through the register. See the module docstring for the order."""
        recovered = self._recover(batch, settlements)
        expired = self._expire(batch, batch_end)
        opened = self._open(batch, batch_end, routed, resolution_class_by_cause)
        drafted = self._draft(batch, opened, drafter)
        filed = self._file(batch, resolutions)

        record = BatchClaims(
            batch=batch, opened=tuple(opened), recovered=tuple(recovered),
            expired=tuple(expired), filed=tuple(filed), drafted=tuple(drafted),
        )
        self.batches.append(record)
        return record

    def _recover(self, batch: int, settlements: Iterable[SettlementRow]) -> list[RecoveryMatch]:
        """Close the claims this batch paid back. Only claims from an earlier batch."""
        eligible = [claim for claim in self.open_claims if claim.opened_batch < batch]
        matches = match_recoveries(eligible, settlements, self.rounding_tolerance_inr)
        for match in matches:
            claim = self._claims[match.claim_id]
            self._replace(
                claim.moved(
                    batch,
                    ClaimStatus.RECOVERED,
                    f"credit {match.row_id} of ₹{match.amount_inr} matched the claimed "
                    f"₹{claim.amount_inr} within ₹{self.rounding_tolerance_inr}",
                    recovery_row_id=match.row_id,
                    recovered_batch=batch,
                    recovered_amount_inr=match.amount_inr,
                )
            )
        return matches

    def _expire(self, batch: int, batch_end: date) -> list[str]:
        """Retire the claims whose window closed. Kept, not deleted -- see the harness."""
        expired: list[str] = []
        for claim in self.open_claims:
            if not claim.deadline.has_passed(batch_end):
                continue
            self._replace(
                claim.moved(
                    batch,
                    ClaimStatus.EXPIRED,
                    f"filing window closed on {claim.deadline.on} with no recovery "
                    f"({claim.deadline.basis})",
                )
            )
            expired.append(claim.claim_id)
        return expired

    def _open(
        self,
        batch: int,
        batch_end: date,
        routed: list[tuple[ExceptionCase, str, str]],
        resolution_class_by_cause: Mapping[str, str],
    ) -> list[str]:
        """Open a claim per routed case, or attach it to the one already open on that order."""
        opened: list[str] = []
        for case, cause, source in routed:
            platform = case.channel or "unknown"
            key = (platform, case.key)
            existing = self._by_order.get(key) if case.kind == "order" else None
            if existing is not None and self._claims[existing].is_open:
                self._replace(self._claims[existing].with_evidence(evidence_of(case), case.case_id))
                continue

            claim = Claim(
                claim_id=self._next_id(),
                platform=platform,
                amount_inr=case.impact_inr,
                cause=cause,
                resolution_class=resolution_class_by_cause[cause],
                order_key=case.key if case.kind == "order" else None,
                evidence=evidence_of(case),
                opened_at=batch_end,
                opened_batch=batch,
                deadline=deadline_for(cause, platform, batch_end, self.deadlines),
                case_ids=(case.case_id,),
                cause_source=source,
            )
            self._replace(claim)
            if claim.order_key:
                self._by_order[key] = claim.claim_id
            opened.append(claim.claim_id)
        return opened

    def _file(self, batch: int, resolutions: Iterable[Resolution]) -> list[str]:
        """A human wrote a note on a claim's case, so the claim is being raised.

        This is the whole filing signal, and it is deliberately not a simulated
        workflow button. The operator's note on a weight-dispute hold reads "Raise it
        with them, don't write it off"; that sentence is already in
        ``data/resolutions.json`` with an id, an author and a date, and pointing the
        claim at it gives filing the same provenance every rule in the system has.
        """
        by_case = {
            case_id: claim
            for claim in self.open_claims
            for case_id in claim.case_ids
        }
        filed: list[str] = []
        for resolution in sorted(resolutions, key=lambda r: r.resolution_id):
            claim = by_case.get(resolution.case_id)
            if claim is None or claim.status is ClaimStatus.FILED:
                continue
            self._replace(
                self._claims[claim.claim_id].moved(
                    batch,
                    ClaimStatus.FILED,
                    f"{resolution.operator} worked this exception in batch {batch} "
                    f"({resolution.resolution_id})",
                    filed_batch=batch,
                    source_resolution_id=resolution.resolution_id,
                )
            )
            filed.append(claim.claim_id)
        return filed

    def _draft(self, batch: int, opened: list[str], drafter: Drafter | None) -> list[str]:
        """Draft the counterparty claims this batch opened.

        ``drafter`` returning ``None`` is the normal path for a claim that must not be
        drafted -- a TCS discrepancy is in the register for its cutoff and nothing
        else, and generating a letter for it would be the system speaking on a matter
        the tax rules reserve for a human.
        """
        if drafter is None:
            return []
        drafted: list[str] = []
        for claim_id in opened:
            draft = drafter(self._claims[claim_id])
            if draft is None:
                continue
            self._replace(
                self._claims[claim_id].moved(
                    batch, ClaimStatus.DRAFTED, "a draft was generated for review", draft=draft
                )
            )
            drafted.append(claim_id)
        return drafted

    def to_json(self) -> dict[str, Any]:
        return {
            "claims": [claim.to_json() for claim in self.claims],
            "batches": [record.to_json() for record in self.batches],
        }
