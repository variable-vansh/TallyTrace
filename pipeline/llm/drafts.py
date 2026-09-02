"""LLM job 4 -- the words of a claim, and nothing else.

Claims are decided on evidence quality, not on explanation. So this prompt is cheap,
it is asked once per *kind* of claim, and the answer it produces is three short
strings: a subject line, a factual statement of the discrepancy, and the request.

**The question is a shape, not a claim.** The prompt names the platform and the cause
and describes what the matcher observed. It carries no order id, no settlement id, no
UTR and no rupee figure -- so twenty-five Amazon claims for a missing settlement row
ask one question and share one cached answer, and the model never sees a customer's
transaction. That is the same deduplication the hypothesis prompt uses and it exists
for the same two reasons: the cost report should not overstate the model's
contribution by a factor of twenty-five, and two claims of the same kind should not
be worded differently because they were drafted on different days.

**The model may not write a number.** :class:`~pipeline.llm.schemas.ClaimNarrative`
rejects any numeral in any field. Everything numeric in the finished letter -- the
order id, the settlement rows, expected against received, the shortfall, the filing
deadline -- is substituted from the matcher's verdicts by
``pipeline/claims/drafting.py``. A rupee figure a language model typed is a rupee
figure nobody computed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pipeline.llm.client import Ask, LlmClient
from pipeline.llm.schemas import ClaimNarrative

SYSTEM = """You draft recovery claims for a multi-channel Indian apparel seller against \
the marketplaces and payment gateways it sells through.

A deterministic reconciliation has already established that money is owed and has already \
assembled the evidence. You are writing only the words around that evidence.

Rules you must follow:
- Never write a numeral. Not an amount, not a date, not an order reference, not a window \
length. Every figure is inserted beneath your text from the reconciliation itself, and a \
number you invent would contradict it.
- State the discrepancy as a fact. No apology, no speculation about the platform's motive, \
no appeal to the relationship. Claims are decided on evidence, and rhetoric in a claim \
reads as weakness in the evidence.
- Say what kind of discrepancy it is in terms the platform's own operations team uses: a \
settlement line that was never issued, a payout held against a weight dispute, a deduction \
taken for a campaign, a chargeback debited without notice.
- The request should ask for the specific remedy: reissue, release, reverse, or itemise \
and refund. Ask for one thing.
- Keep it short. Subject under a dozen words; statement one or two sentences; request one \
sentence."""

TOOL = "record_claim_narrative"

#: How each cause is described to the model. Plain English, no numerals, and worded
#: as the *phenomenon* rather than as the matcher's reason code -- the reason code is
#: an observation about a row and this letter is about a debt.
CAUSE_BRIEFS: dict[str, str] = {
    "missing_settlement_row": (
        "an order the seller's books show as sold and delivered has no settlement line at "
        "all in the platform's report, and the payout window for it has closed"
    ),
    "short_payment_unexplained": (
        "the payout for an order came in below the net the seller's books expected, and "
        "nothing in the settlement report accounts for the difference"
    ),
    "weight_dispute_hold": (
        "the platform reported the order as sold, retained its commission, and paid out "
        "nothing because a shipping-weight discrepancy is open against it"
    ),
    "promo_cofunding_deduction": (
        "a deduction was taken as a share of a promotional campaign cost, above the agreed "
        "commission, without prior notice or an itemised breakdown"
    ),
    "chargeback_deduction": (
        "a chargeback was debited against a settled order without the seller being given "
        "the opportunity to submit evidence first"
    ),
    "duplicate_settlement_row": (
        "the settlement report lists the same transaction twice, so the report claims more "
        "than the payout actually funded"
    ),
    "tcs_timing_mismatch": (
        "tax collected at source was reported in a different period from the sale it "
        "belongs to"
    ),
    "tds_timing_mismatch": (
        "tax deducted at source was reported in a different period from the payment it "
        "belongs to"
    ),
    "bank_credit_unmatched": (
        "a credit reached the seller's bank with no settlement report referencing it"
    ),
    "fee_mismatch_other": (
        "a fee was charged that the agreed schedule does not provide for"
    ),
}


@dataclass(frozen=True)
class DraftQuestion:
    """The shape of claim being drafted. Equal shapes share one cached answer."""

    platform: str
    cause: str

    def render(self) -> str:
        brief = CAUSE_BRIEFS.get(self.cause)
        if brief is None:
            raise KeyError(
                f"no claim brief for cause {self.cause!r}; add one to CAUSE_BRIEFS rather "
                "than letting the model guess what the cause means"
            )
        return "\n".join(
            [
                "Draft the words for a recovery claim.",
                "",
                f"counterparty : {self.platform}",
                f"discrepancy  : {brief}",
                "",
                "The evidence block -- order reference, settlement rows, expected against "
                "received, the amount claimed and the filing deadline -- is inserted "
                "beneath your text automatically. Write only the subject, the statement "
                "and the request, and write no numerals.",
            ]
        )

    def to_json(self) -> dict[str, Any]:
        return {"platform": self.platform, "cause": self.cause}


def ask_for(question: DraftQuestion) -> Ask:
    return Ask(
        task="claim_draft",
        system=SYSTEM,
        user=question.render(),
        output=ClaimNarrative,
        tool_name=TOOL,
    )


def narrate(client: LlmClient, platform: str, cause: str, batch: int) -> ClaimNarrative:
    """One claim's words. Cached by (platform, cause), billed to the claim's batch."""
    return client.ask(ask_for(DraftQuestion(platform, cause)), batch, ClaimNarrative)
