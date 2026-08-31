"""LLM job 2 -- turn what the human wrote into a rule.

The premise of the whole learning loop is that rules are induced from work someone
was doing anyway. So the input is a sentence a bookkeeper typed while clearing an
exception, plus the features of the row they were looking at, and the output is a
schema-shaped predicate.

The model's only job here is **interpreting language into a schema.** It does not
decide whether the rule may fire, it does not set its state, and it does not evaluate
it against anything. Those are threshold and predicate work, and they live in
``pipeline/rules/`` with no model anywhere near them.

The hard constraint is negative: a rule may not contain an identifier. The schema
gives it nowhere to put one, and ``pipeline/rules/models.py`` re-checks the values,
because a free-text ``plain_words`` field will hold ``ord_000019`` quite happily if
nobody looks. Both checks exist on purpose -- a rule that names a transaction is a
memorised transaction, and it would score perfectly on the batch it came from while
generalising to nothing.
"""

from __future__ import annotations

from decimal import Decimal

from pipeline.cases import CaseFeatures
from pipeline.llm.hypotheses import question_for
from pipeline.llm.client import Ask, LlmClient
from pipeline.llm.schemas import InducedRule

SYSTEM = """You turn a bookkeeper's plain-language note into a structured, generalisable rule.

You are given (a) the note they typed while resolving one reconciliation exception and \
(b) the features of the row they were looking at. Return the rule their note implies.

Rules you must follow:
- The rule must describe a *pattern*, never a transaction. Never reference an order id, \
an entity id, a UTR or a settlement id anywhere, including in plain_words.
- Choose the cause from the enum in the schema. Never invent one.
- resolution_class must be the class that cause belongs to: internal_fix for the seller's \
own books being wrong or behind, counterparty_claim when an external party owes money, \
tax_review for TCS/TDS, investigate when nobody can yet say.
- Set a band only where the note supports one. A note about a rate that is out by a fixed \
percentage implies a tight variance band around that percentage. A note about a deduction \
arriving a cycle or two later implies a lag_window_days band and no variance band at all.
- Bands should be tight enough to exclude a different phenomenon and wide enough to cover \
normal rounding. Prefer a band that would not fire on a number twice the size.
- action.type: update_ledger_rate when the seller's master rate is stale and the note says \
what it should be; accept_timing_difference when the money is right and only the cycle is \
wrong; write_off_variance for small explained differences; flag_for_claim when an external \
party owes money; none when the note does not imply an action.
- plain_words is what the operator will see on the rules page. One sentence, no ids."""

TOOL = "record_rule"


def magnitude(rupees: Decimal) -> str:
    """The size of the money, as a band rather than to the paise.

    A rule induced from "₹32.73 in question" and one induced from "₹33.10 in
    question" are the same rule, and keying the model cache on the paise would ask
    the identical question 195 times. The band is also the honest level of detail:
    what a rule should learn from is that the money was tens of rupees rather than
    thousands, and the exact figure is on the case where it is exact.
    """
    for ceiling, label in (
        (Decimal("100"), "tens of rupees"),
        (Decimal("1000"), "hundreds of rupees"),
        (Decimal("10000"), "thousands of rupees"),
    ):
        if abs(rupees) < ceiling:
            return label
    return "tens of thousands of rupees"


def render(resolution_text: str, features: CaseFeatures) -> str:
    """The prompt, built from the case's *shape* rather than its identifiers.

    Same normalisation as the hypothesis prompt -- percentages to one decimal, day
    counts as bands -- so the two jobs agree about what makes two cases alike, and
    so an identical note about an identical shape is one question and one cached
    answer rather than a hundred.
    """
    question = question_for(features)
    lines = [
        "The bookkeeper wrote this while resolving an exception:",
        "",
        f'    "{resolution_text.strip()}"',
        "",
        "The kind of row they were looking at:",
        "",
        f"matcher reason code : {question.reason}",
        f"channel             : {question.channel or 'not channel-specific'}",
        f"money direction     : {question.direction}",
        f"money in question   : {magnitude(features.variance_inr)}",
    ]
    if question.fee_variance_pct is not None:
        lines.append(f"fee variance        : {question.fee_variance_pct}%")
    if question.net_variance_pct is not None:
        lines.append(f"net variance        : {question.net_variance_pct}%")
    if question.days_after_settlement is not None:
        lines.append(f"days after settlement: {question.days_after_settlement}")
    if question.days_late is not None:
        lines.append(f"days past the settlement window: {question.days_late}")
    if question.transaction_type is not None:
        lines.append(f"row type            : {question.transaction_type}")
    lines += ["", "What is the generalisable rule?"]
    return "\n".join(lines)


def ask_for(resolution_text: str, features: CaseFeatures) -> Ask:
    return Ask(
        task="induction",
        system=SYSTEM,
        user=render(resolution_text, features),
        output=InducedRule,
        tool_name=TOOL,
    )


def induce(
    client: LlmClient, resolution_text: str, features: CaseFeatures, batch: int
) -> InducedRule:
    """One resolution, read into a rule. Cached by (text, features)."""
    return client.ask(ask_for(resolution_text, features), batch, InducedRule)
