"""The structured outputs the model is allowed to produce.

Four of them, one per LLM job: a hypothesis, an induced rule, a claim narrative and a
question-to-metric mapping. Every one is a pydantic model handed to the API as a JSON schema with the
frozen enum inlined, so ``cause`` is constrained *in the schema* rather than merely
requested in the prompt text. A cause outside the enum is a hard error at parse time
-- there is no fallback, no nearest-match, no "unknown" bucket. A reconciliation
system that invents a cause is worse than one that abstains.

The rule schema carries the constraint that matters most: **no identifier anywhere.**
A rule containing an order id or an entity id is a memorised transaction wearing a
rule's clothes, and it would score beautifully on the batch it came from and explain
nothing. It is rejected here by construction -- the schema has no field to put one in
-- and rejected again in ``pipeline/rules/models.py`` against the values, because a
field named ``description`` will happily hold ``ord_000019`` if nobody looks.

The claim narrative carries the negative constraint that matters most on its side of
the system: **no numerals at all.** Every rupee figure in a claim letter is
substituted by code from the matcher's verdicts, and the schema is what makes that
checkable rather than aspirational.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pipeline.models import Cause, Channel, ResolutionClass

#: Directions a rule can be about, mirroring ``pipeline.cases.CaseFeatures.direction``.
Direction = Literal["short", "over", "flat", "any"]

#: Row types a rule can be about. A clawback and a TCS recovery arrive on the same
#: reason code, days apart, both taking money back; the type is what separates them,
#: and without it the two rules would be equally specific and permanently in conflict.
TransactionKind = Literal["payment", "refund", "transfer", "adjustment"]

#: What accepting a rule does to the books. Deliberately a closed set: an action is
#: a thing the system will perform unattended, so the model may select one, never
#: describe one.
ActionType = Literal[
    "update_ledger_rate",       # the books' commission rate is out of date
    "accept_timing_difference", # the money is right, the cycle is not
    "write_off_variance",       # small and explained; close it
    "flag_for_claim",           # someone else owes this; checkpoint 4 picks it up
    "none",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Hypothesis(_Strict):
    """LLM job 1: why did this row fail to match?"""

    cause: Cause
    hypothesis: str = Field(
        min_length=10, max_length=400,
        description="One or two sentences a bookkeeper would recognise, in plain English.",
    )
    confidence: Decimal = Field(ge=0, le=1)

    @field_validator("confidence", mode="before")
    @classmethod
    def _no_floats(cls, value: Any) -> Decimal:
        """A confidence arriving as a float is fine; a *money* float is not.

        Confidence is not money, so it is allowed in from JSON as a number -- but it
        is still stored as Decimal so that comparing it to a configured threshold is
        an exact comparison rather than a binary-rounding one.
        """
        return Decimal(str(value))


class RuleAction(_Strict):
    """What the rule does when it fires."""

    type: ActionType
    field: str | None = Field(default=None, max_length=40)
    value: Decimal | None = None

    @field_validator("value", mode="before")
    @classmethod
    def _as_decimal(cls, value: Any) -> Decimal | None:
        return None if value is None else Decimal(str(value))


class InducedRule(_Strict):
    """LLM job 2: the human's sentence, read into a schema.

    Every field is a *property of the phenomenon*. There is nowhere to put a
    transaction id, which is the point.
    """

    channel: Channel | None = None
    cause: Cause
    reason_code: str | None = Field(
        default=None, max_length=60,
        description="The matcher reason code this rule is about, if the resolution named one.",
    )
    transaction_type: TransactionKind | None = Field(
        default=None,
        description="Set when the note distinguishes a refund from a tax adjustment.",
    )
    variance_band_pct: tuple[Decimal, Decimal] | None = Field(
        default=None,
        description="Inclusive [low, high] band on the fee variance percentage.",
    )
    net_variance_band_pct: tuple[Decimal, Decimal] | None = None
    direction: Direction = "any"
    lag_window_days: tuple[int, int] | None = Field(
        default=None,
        description="Inclusive [low, high] band on days between settlement and this row.",
    )
    resolution_class: ResolutionClass
    action: RuleAction
    plain_words: str = Field(
        min_length=10, max_length=200,
        description="The rule in one sentence, as it will be shown to the operator.",
    )

    @field_validator("variance_band_pct", "net_variance_band_pct", mode="before")
    @classmethod
    def _band_as_decimal(cls, value: Any) -> Any:
        if value is None:
            return None
        low, high = value
        return (Decimal(str(low)), Decimal(str(high)))


def json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The API-facing schema, with ``$ref``/``$defs`` flattened into enums inline.

    The Messages API accepts ``$defs``, but a flattened schema is what makes the
    "constrained in the schema, not in the prompt" claim checkable by reading one
    object rather than by chasing references.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if ref and ref.startswith("#/$defs/"):
                target = dict(defs[ref.split("/")[-1]])
                target.pop("title", None)
                merged = {k: v for k, v in node.items() if k != "$ref"}
                return {**resolve(target), **merged}
            return {key: resolve(value) for key, value in node.items()}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    resolved: dict[str, Any] = resolve(schema)
    return resolved


# --------------------------------------------------------------------------- #
# Checkpoint 4 -- claim drafting
# --------------------------------------------------------------------------- #

#: Any digit at all. See :class:`ClaimNarrative`.
DIGIT = re.compile(r"\d")


class ClaimNarrative(_Strict):
    """LLM job 4: the words of a claim, and only the words.

    A claim is decided on the quality of its evidence, not on the quality of its
    prose, so the model's contribution here is deliberately small: a subject line, a
    factual statement of what kind of discrepancy this is, and the request. The
    evidence block underneath -- order id, settlement rows, expected against
    received, the shortfall, the filing deadline -- is rendered by
    ``pipeline/claims/drafting.py`` straight from the matcher's verdicts.

    **The model may not write a number.** Every field here rejects digits outright.
    That is not stylistic: a figure a language model typed into a letter to Amazon is
    a figure nobody computed, and one wrong rupee in a claim is the whole claim. The
    constraint makes "every number in the draft came from the matcher" a property of
    the schema rather than a promise in a README, and ``tests/test_claims.py``
    asserts it against the drafts an actual run produced.
    """

    subject: str = Field(min_length=10, max_length=90)
    statement: str = Field(
        min_length=30, max_length=420,
        description="One or two sentences stating factually what the discrepancy is. "
                    "No rhetoric, no apology, no numerals of any kind.",
    )
    request: str = Field(
        min_length=15, max_length=260,
        description="What is being asked for, in one sentence. No numerals of any kind.",
    )

    @field_validator("subject", "statement", "request")
    @classmethod
    def _no_numerals(cls, value: str) -> str:
        found = DIGIT.search(value)
        if found:
            raise ValueError(
                f"a claim narrative may not contain a numeral (found {found.group(0)!r}); "
                "every figure in a draft is substituted from the matcher's verdicts"
            )
        return value


# --------------------------------------------------------------------------- #
# Checkpoint 4 -- intent mapping
# --------------------------------------------------------------------------- #

#: The registered metric ids, mirrored here the way :class:`~pipeline.models.Cause`
#: mirrors ``config/causes.yaml``. Written out rather than derived so that the schema
#: handed to the API is a literal object anyone can read, and ``tests/test_metrics.py``
#: asserts this tuple and ``pipeline.metrics.registry.REGISTRY`` agree. A model asked
#: for a metric can therefore only return one that exists -- not because the prompt
#: asked nicely, but because the schema has no other value to give.
MetricId = Literal[
    "net_revenue_by_channel",
    "gross_order_value",
    "effective_take_rate",
    "commission_share_of_gross",
    "exception_count_by_cause",
    "review_rate_trend",
    "auto_resolved_rows",
    "claim_recovery_rate",
    "open_claim_value",
    "rupees_expired_unrecovered",
]

#: How a result may be grouped. Each metric declares which of these it supports and
#: rejects the rest -- see ``pipeline.metrics.registry.Metric.run``.
Grouping = Literal["channel", "batch", "cause", "platform"]

#: What the mapping decided. Three outcomes, and two of them are not an answer.
IntentOutcome = Literal["mapped", "clarify", "refuse"]


class MetricIntent(_Strict):
    """LLM job 3: a plain-language question, mapped onto a registered metric.

    The model selects; it never computes and it never writes SQL. Enterprise
    text-to-SQL execution accuracy runs roughly 21-39% on realistic schemas, and the
    failures are not visible ones -- a plausible query returns a plausible number that
    happens to be wrong. Selecting from a closed registry has a different failure
    mode: it can only pick the wrong metric from a list of ten, and the restatement
    below puts that choice in front of a human before anything is computed.

    Three outcomes and only one of them is an answer:

    - ``mapped``  -- a registered metric answers this. ``metric_id`` is set.
    - ``clarify`` -- more than one metric could be meant. One question is asked, and
      nothing is computed until it is answered. Guessing here is how a dashboard
      quietly answers a question nobody asked.
    - ``refuse``  -- nothing in the registry answers this. The refusal says so
      plainly. It does not offer a plausible adjacent chart, which is the tempting
      failure and the dishonest one.
    """

    outcome: IntentOutcome
    metric_id: MetricId | None = None
    group_by: Grouping | None = None
    channel: Channel | None = None
    from_batch: int | None = Field(default=None, ge=1)
    to_batch: int | None = Field(default=None, ge=1)
    restatement: str = Field(
        min_length=15, max_length=240,
        description="What is about to be computed, in one sentence, for a human to confirm "
                    "before anything runs. Required on every outcome.",
    )
    clarifying_question: str | None = Field(default=None, max_length=200)
    refusal: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _outcome_carries_its_payload(self) -> "MetricIntent":
        """An outcome without the field it exists for is a half-answer. Reject it here.

        Cheaper than discovering it downstream: a ``mapped`` with no metric id would
        otherwise surface as a ``KeyError`` in the registry three calls away from the
        thing that produced it.
        """
        required = {
            "mapped": ("metric_id", self.metric_id),
            "clarify": ("clarifying_question", self.clarifying_question),
            "refuse": ("refusal", self.refusal),
        }[self.outcome]
        if not required[1]:
            raise ValueError(f"outcome {self.outcome!r} requires {required[0]}")
        if self.outcome != "mapped" and self.metric_id is not None:
            raise ValueError(f"outcome {self.outcome!r} must not name a metric")
        return self

