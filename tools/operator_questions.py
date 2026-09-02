"""The questions the operator actually typed at the reporting surface.

For this build I am the human here too. These are questions a seller doing a few
crore across four marketplaces would ask their reconciliation, written before the
registry was finalised rather than reverse-engineered from it -- which is why three
of them do not map cleanly, and those three are the useful ones.

Two are refused outright. One asks for profitability by SKU, which needs a product
master this system does not have; one asks for a forecast, and every metric in the
registry is a measurement of what happened. Neither is answered with an adjacent
chart, and the refusal text says which fact is missing rather than apologising.

One is ambiguous on purpose. "How are our fees trending?" is exactly the question a
person asks, and "fees" is exactly the word that means two different registry metrics
-- commission alone, or commission plus the tax withheld on top of it. The system asks
which, once, and computes nothing until it is told.

Pinned questions are marked. A pin keeps the *definition*, never the numbers, and it
recomputes with no model in the loop from then on.
"""

from __future__ import annotations

from dataclasses import dataclass

OPERATOR = "priya.n@demostore.in"
ASKED_AT = "2025-03-16"


@dataclass(frozen=True)
class AskedQuestion:
    """One question, and what the operator did with the answer."""

    question: str
    confirmed: bool                 # did they say yes to the restatement
    pin_as: str | None = None       # the name they pinned it under, if they pinned it


QUESTIONS: tuple[AskedQuestion, ...] = (
    AskedQuestion(
        "How much did we actually get paid by each channel?",
        confirmed=True,
    ),
    AskedQuestion(
        "Is Myntra taking a bigger cut than it used to?",
        confirmed=True,
        pin_as="Myntra take rate, week by week",
    ),
    AskedQuestion(
        "What share of gross are the platforms keeping across the board?",
        confirmed=True,
        pin_as="Effective take rate by channel",
    ),
    AskedQuestion(
        "Which causes are generating the most exceptions?",
        confirmed=True,
        pin_as="Exceptions by cause",
    ),
    AskedQuestion(
        "Is the manual review rate actually coming down?",
        confirmed=True,
        pin_as="Manual review rate",
    ),
    AskedQuestion(
        "How much money are we still chasing, by platform?",
        confirmed=True,
        pin_as="Open claim value",
    ),
    AskedQuestion(
        "How much have we lost to claims that expired before we filed them?",
        confirmed=True,
    ),
    AskedQuestion(
        "Show me net revenue by channel for the first four weeks only",
        confirmed=True,
    ),
    # -- the ambiguous one. Answered with a question, not a guess.
    AskedQuestion(
        "How are our fees trending?",
        confirmed=False,
    ),
    # -- the two refusals.
    AskedQuestion(
        "Which of our SKUs are least profitable?",
        confirmed=False,
    ),
    AskedQuestion(
        "What will next month's settlement come to?",
        confirmed=False,
    ),
)
