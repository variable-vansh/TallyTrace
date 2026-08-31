"""The two structured outputs the model is allowed to produce.

Both are pydantic models and both are handed to the API as a JSON schema with the
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
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
