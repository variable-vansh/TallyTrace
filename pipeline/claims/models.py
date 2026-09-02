"""The claim object and the states it moves through.

A claim is an exception that someone else has to pay. It is a different object from
a rule-resolvable exception and it needs different bookkeeping: an amount that has to
survive being quoted back to a platform, the rows that prove it, a clock, and a link
to the credit that eventually closes it.

Immutable, like :class:`~pipeline.rules.models.Rule`, and for the same reason: a
claim's history is evidence. Every transition returns a new claim and appends to
:attr:`Claim.transitions`, so "why is this expired?" is answered by reading the
object rather than by re-running the batch it expired in.

**The status set is closed.** ``open`` -> ``drafted`` -> ``filed`` are the states a
human moves it through; ``recovered``, ``expired`` and ``written_off`` are terminal.
Nothing invents a seventh.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from pipeline.claims.deadlines import Deadline

ZERO = Decimal("0.00")


class ClaimStatus(str, Enum):
    OPEN = "open"                 # raised by reconciliation, nothing sent yet
    DRAFTED = "drafted"           # a message exists, waiting for a human to send it
    FILED = "filed"               # the operator said to raise it, in their own words
    RECOVERED = "recovered"       # a later batch credited the money back
    EXPIRED = "expired"           # the filing window closed with no recovery
    WRITTEN_OFF = "written_off"   # the operator gave up on it


#: Once here, a claim stops being worked and stops appearing in the open queue.
TERMINAL = frozenset({ClaimStatus.RECOVERED, ClaimStatus.EXPIRED, ClaimStatus.WRITTEN_OFF})


@dataclass(frozen=True)
class Evidence:
    """One row that proves part of a claim. Table and id, the way a verdict addresses one."""

    table: str
    row_id: str

    def to_json(self) -> dict[str, str]:
        return {"table": self.table, "row_id": self.row_id}


@dataclass(frozen=True)
class ClaimTransition:
    """One status change, with the batch it happened in and why."""

    batch: int
    from_status: str
    to_status: str
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {
            "batch": self.batch, "from_status": self.from_status,
            "to_status": self.to_status, "reason": self.reason,
        }


@dataclass(frozen=True)
class Claim:
    """Money an external party owes, with a clock on it."""

    claim_id: str
    platform: str
    amount_inr: Decimal
    cause: str
    resolution_class: str
    order_key: str | None              # the order this is about; None when there is no order
    evidence: tuple[Evidence, ...]
    opened_at: date
    opened_batch: int
    deadline: Deadline
    status: ClaimStatus = ClaimStatus.OPEN
    draft: str | None = None
    recovery_row_id: str | None = None
    recovered_batch: int | None = None
    recovered_amount_inr: Decimal | None = None
    filed_batch: int | None = None
    source_resolution_id: str | None = None
    case_ids: tuple[str, ...] = ()
    #: What the cause came from: a learned rule's prediction, or the model's hypothesis.
    cause_source: str = "hypothesis"
    transitions: tuple[ClaimTransition, ...] = field(default_factory=tuple)

    # -- queries ----------------------------------------------------------- #

    @property
    def is_open(self) -> bool:
        return self.status not in TERMINAL

    @property
    def evidence_row_ids(self) -> tuple[str, ...]:
        return tuple(item.row_id for item in self.evidence)

    def days_remaining(self, as_of: date) -> int | None:
        return self.deadline.days_remaining(as_of)

    # -- transitions ------------------------------------------------------- #

    def moved(self, batch: int, status: ClaimStatus, reason: str, **changes: Any) -> "Claim":
        """A new claim in ``status``, with the move recorded. The only way status changes."""
        return replace(
            self,
            status=status,
            transitions=self.transitions
            + (ClaimTransition(batch, self.status.value, status.value, reason),),
            **changes,
        )

    def with_evidence(self, extra: tuple[Evidence, ...], case_id: str) -> "Claim":
        """Attach another case's rows to an existing claim rather than opening a second one.

        The same order can produce a finding in more than one batch -- a short payment
        in week 2 and a related adjustment in week 5 -- and they are one thing to
        chase, not two. The amount is deliberately *not* summed here: a claim is worth
        what the first finding said it was worth until a human says otherwise, and
        quietly growing the figure would put a number in a letter that nobody chose.
        """
        seen = {(item.table, item.row_id) for item in self.evidence}
        added = tuple(item for item in extra if (item.table, item.row_id) not in seen)
        if not added and case_id in self.case_ids:
            return self
        return replace(
            self,
            evidence=self.evidence + added,
            case_ids=self.case_ids + ((case_id,) if case_id not in self.case_ids else ()),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "platform": self.platform,
            "amount_inr": str(self.amount_inr),
            "cause": self.cause,
            "resolution_class": self.resolution_class,
            "order_key": self.order_key,
            "evidence_row_ids": list(self.evidence_row_ids),
            "evidence": [item.to_json() for item in self.evidence],
            "opened_at": self.opened_at.isoformat(),
            "opened_batch": self.opened_batch,
            "deadline": self.deadline.to_json(),
            "status": self.status.value,
            "draft": self.draft,
            "recovery_row_id": self.recovery_row_id,
            "recovered_batch": self.recovered_batch,
            "recovered_amount_inr": (
                None if self.recovered_amount_inr is None else str(self.recovered_amount_inr)
            ),
            "filed_batch": self.filed_batch,
            "source_resolution_id": self.source_resolution_id,
            "case_ids": list(self.case_ids),
            "cause_source": self.cause_source,
            "transitions": [t.to_json() for t in self.transitions],
        }
