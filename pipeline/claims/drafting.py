"""Assembling the claim a human sends.

The model wrote three sentences and no numbers (see :mod:`pipeline.llm.drafts`).
Everything else on the page is built here, out of the matcher's own verdicts:

- the order reference and the settlement rows, from the claim's evidence;
- expected against received, from the verdict detail the value check produced;
- the amount claimed, from the case's impact;
- the deadline and its authority, from ``config/thresholds.yaml`` via the clock.

So the finished draft has exactly one author per line, and the rupee figures in it
are the same objects the reconciliation reported. ``tests/test_claims.py`` asserts
that every numeral appearing in a generated draft can be traced to the claim or its
evidence -- which is only checkable because the model was forbidden to write one.

**Only counterparty claims are drafted.** A TCS discrepancy sits in the register for
its cutoff and nothing else: it is a return correction a human files with their own
accountant, and a system-drafted letter about a tax position is exactly the kind of
confident automation this repo spends the rest of its time refusing to do.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Mapping

from pipeline.cases import ExceptionCase
from pipeline.claims.models import Claim
from pipeline.claims.routing import COUNTERPARTY_CLAIM
from pipeline.llm.schemas import ClaimNarrative

SIGNER = "TallyTrace reconciliation, Demo Store"

#: Verdict-detail keys worth quoting in a claim, in the order a reader wants them.
#: ``money`` is rendered with a rupee sign; everything else goes through as written.
EVIDENCE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("expected_net", "Net expected", "money"),
    ("settled_net", "Net received", "money"),
    ("expected_fee", "Commission expected", "money"),
    ("charged_fee", "Commission charged", "money"),
    ("row_net", "Amount on the disputed row", "money"),
    ("due_by", "Payout was due by", "text"),
    ("days_late", "Days past the settlement window", "text"),
)


def rupees(value: Decimal | str) -> str:
    """One money figure, grouped, to the paise. The only money formatter in a draft."""
    return f"₹{Decimal(str(value)):,.2f}"


@dataclass(frozen=True)
class DraftContext:
    """The facts a draft is allowed to quote, gathered from the matcher's verdicts."""

    order_reference: str
    settlement_rows: tuple[str, ...]
    detail: Mapping[str, str]


def context_for(claim: Claim, case: ExceptionCase) -> DraftContext:
    detail: dict[str, str] = {}
    for verdict in case.verdicts:
        detail.update(verdict.detail)
    return DraftContext(
        order_reference=claim.order_key or "not attributable to a single order",
        settlement_rows=tuple(
            item.row_id for item in claim.evidence if item.table == "settlement_report"
        ),
        detail=detail,
    )


def _evidence_lines(claim: Claim, context: DraftContext) -> list[str]:
    rows = ", ".join(context.settlement_rows) or "none issued"
    lines = [
        f"  {'Claim reference':<34}{claim.claim_id}",
        f"  {'Order reference':<34}{context.order_reference}",
        f"  {'Channel':<34}{claim.platform}",
        f"  {'Settlement rows on file':<34}{rows}",
    ]
    for key, label, kind in EVIDENCE_FIELDS:
        raw = context.detail.get(key)
        if raw in (None, ""):
            continue
        lines.append(f"  {label:<34}{rupees(raw) if kind == 'money' else raw}")
    lines.append(f"  {'Amount claimed':<34}{rupees(claim.amount_inr)}")
    lines.append(f"  {'Discrepancy raised on':<34}{claim.opened_at.isoformat()}")
    if claim.deadline.on is not None:
        lines.append(f"  {'Filing deadline':<34}{claim.deadline.on.isoformat()}")
    return lines


def render(claim: Claim, case: ExceptionCase, narrative: ClaimNarrative) -> str:
    """The finished draft: the model's three sentences around the matcher's evidence."""
    context = context_for(claim, case)
    body = [
        f"Subject: {narrative.subject} — {context.order_reference}",
        "",
        narrative.statement,
        "",
        "Evidence from our reconciliation:",
        "",
        *_evidence_lines(claim, context),
        "",
        narrative.request,
        "",
        f"Filing basis: {claim.deadline.basis}.",
        "",
        f"— {SIGNER}",
    ]
    return "\n".join(body)


def drafter_for(
    narrator: Callable[[str, str], ClaimNarrative],
    cases_by_id: Mapping[str, ExceptionCase],
) -> Callable[[Claim], str | None]:
    """A ``Drafter`` the register can call, closed over this batch's cases.

    Returns ``None`` for anything that is not a counterparty claim, and for a claim
    whose case is not in this batch -- which cannot happen through the register, and
    would be a silently wrong letter rather than a crash if it ever did.
    """

    def draft(claim: Claim) -> str | None:
        if claim.resolution_class != COUNTERPARTY_CLAIM:
            return None
        case = next((cases_by_id[c] for c in claim.case_ids if c in cases_by_id), None)
        if case is None:
            return None
        return render(claim, case, narrator(claim.platform, claim.cause))

    return draft
