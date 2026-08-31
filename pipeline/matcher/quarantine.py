"""Quarantine, never drop.

A malformed row is not swallowed and it is not silently skipped. It is parked with
the reason it failed and counted, because a reconciliation tool that loses rows is
worse than useless -- the one number a bookkeeper checks first is whether the row
count going in matches the row count coming out.

Classification is pure so it can be tested without a file: hand it the pydantic
error list and it names the failure. The reading of the file happens in
``pipeline/loader.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from pipeline.matcher.reasons import Reason

DATE_FIELDS = frozenset({"created_at", "settled_at"})
MONEY_FIELDS = frozenset({"amount", "fee", "tax", "tcs", "tds", "debit", "credit",
                          "order_value", "expected_fee", "expected_net",
                          "expected_commission_rate"})


@dataclass(frozen=True)
class QuarantineRecord:
    """One row the models refused, kept with the reason they refused it."""

    table: str
    row_id: str
    reason: Reason
    message: str
    raw: Mapping[str, str] = field(default_factory=dict)


def _fields_in(errors: Sequence[Mapping[str, Any]]) -> set[str]:
    return {str(location[0]) for error in errors for location in [error.get("loc") or ("",)]}


def classify(errors: Sequence[Mapping[str, Any]], message: str) -> Reason:
    """Name the failure from the validation errors.

    Ordered by how a human would describe the row: a missing join key first, then a
    date nobody can parse, then an amount that arrived as formatted text. Anything
    else is a schema violation and says so rather than guessing.
    """
    fields = _fields_in(errors)
    if "order_id" in fields or "order_id" in message:
        return Reason.MALFORMED_MISSING_ORDER_ID
    if fields & DATE_FIELDS:
        return Reason.MALFORMED_UNPARSEABLE_DATE
    if fields & MONEY_FIELDS:
        return Reason.MALFORMED_UNPARSEABLE_AMOUNT
    return Reason.MALFORMED_SCHEMA_VIOLATION
