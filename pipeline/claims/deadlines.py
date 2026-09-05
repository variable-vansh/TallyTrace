"""The deadline clock, driven by a policy table rather than by a conditional.

The drafting is not what matters. The deadlines are. Amazon's SAFE-T window closes
30 days after the event, and a TCS discrepancy has to be raised before the 10th of
the following month or the GSTR-8 correction misses its return. Sellers lose this
money because they only discover the loss at reconciliation time, which is already
late -- so the clock starts at the moment the reconciliation surfaces the problem,
and it is computed, not typed.

**The kind of clock is data too.** Two shapes exist and they are genuinely different:
a *duration* (``opened_at + N days``, where days remaining is a subtraction) and a
*statutory cutoff* (a calendar date in the following month, so a claim opened on the
2nd has eight days and one opened on the 28th has thirteen). Which shape applies used
to be an ``if`` in this file naming one cause. It is now a ``rule`` column in
``config/thresholds.yaml``, because a filing window is a commercial fact that changes
without the code changing, and adding a channel should be adding a row.

Most specific scope wins: a row naming a ``claim_type`` beats one naming only a
``channel``, so a TCS discrepancy on Flipkart runs on the GSTR-8 calendar rather than
quietly borrowing Flipkart's 30-day commercial window and gaining twenty days it does
not have.

A scope with no row gets **no deadline at all**, not a default. Inventing 30 days for
the website gateway would put a countdown on screen that no agreement backs, and a
claims queue whose whole value is its clock cannot afford one clock that is made up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

#: The two shapes of clock. ``rule`` in the policy table is one of these.
DAYS_FROM_EVENT = "days_from_event"
DAY_OF_NEXT_MONTH = "day_of_next_month"
RULES = (DAYS_FROM_EVENT, DAY_OF_NEXT_MONTH)

#: What :attr:`Deadline.kind` reports, kept as the vocabulary the UI already renders.
DURATION = "duration"
STATUTORY_CUTOFF = "statutory_cutoff"
UNCONFIGURED = "unconfigured"

KIND_OF_RULE = {DAYS_FROM_EVENT: DURATION, DAY_OF_NEXT_MONTH: STATUTORY_CUTOFF}


class DeadlinePolicyError(ValueError):
    """The policy table is malformed. Loud at load, never at claim-opening time."""


@dataclass(frozen=True)
class DeadlineRule:
    """One row of the policy table: a scope, a clock shape, and a number."""

    rule: str
    value: int
    channel: str | None = None
    claim_type: str | None = None
    note: str = ""

    @property
    def specificity(self) -> int:
        """How many dimensions this row names. The most specific match governs."""
        return sum(1 for scope in (self.channel, self.claim_type) if scope is not None)

    @property
    def scope_key(self) -> tuple[str | None, str | None]:
        """What makes two rows the same row: the scope, never the number."""
        return (self.channel, self.claim_type)

    def matches(self, cause: str, channel: str) -> bool:
        return (self.channel is None or self.channel == channel) and (
            self.claim_type is None or self.claim_type == cause
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "rule": self.rule, "value": self.value, "channel": self.channel,
            "claim_type": self.claim_type, "note": self.note,
        }


@dataclass(frozen=True)
class DeadlineConfig:
    """The filing windows, straight from ``config/thresholds.yaml``."""

    rules: tuple[DeadlineRule, ...]

    def governing(self, cause: str, channel: str) -> DeadlineRule | None:
        """The row that governs this claim, or None where nothing does.

        More scopes named wins. On a tie -- a row naming a channel against one naming
        a claim_type -- the claim_type wins, because what the claim *is* determines
        the calendar it runs on and where it happened does not. Without that, a TCS
        discrepancy on Flipkart would take Flipkart's 30-day commercial window and
        quietly gain twenty days the GSTR-8 return does not give it.
        """
        hits = [row for row in self.rules if row.matches(cause, channel)]
        if not hits:
            return None
        return max(hits, key=lambda row: (row.specificity, row.claim_type is not None))

    @property
    def clocked_claim_types(self) -> frozenset[str]:
        """Causes the table gives a clock to by name.

        Read by the routing: a cause that is not a counterparty claim but *does* have
        a filing window still belongs in the register, for its clock and nothing else.
        Derived from the table so that giving a new cause a deadline is one row here
        rather than a row here and a constant in ``pipeline/claims/routing.py``.
        """
        return frozenset(row.claim_type for row in self.rules if row.claim_type is not None)


def deadline_config_from(thresholds: dict[str, Any]) -> DeadlineConfig:
    """Load and validate the policy table. A malformed row fails here, not later."""
    rows = thresholds["claims"]["deadline_policy"]
    rules: list[DeadlineRule] = []
    seen: set[tuple[str | None, str | None]] = set()
    for row in rows:
        rule = str(row["rule"])
        if rule not in RULES:
            raise DeadlinePolicyError(
                f"unknown deadline rule {rule!r}; the table supports {', '.join(RULES)}"
            )
        entry = DeadlineRule(
            rule=rule,
            value=int(row["value"]),
            channel=row.get("channel"),
            claim_type=row.get("claim_type"),
            note=str(row.get("note", "")),
        )
        if entry.specificity == 0:
            raise DeadlinePolicyError(
                "a deadline row must name a channel, a claim_type or both; a row that "
                "scopes nothing would put a made-up clock on every claim"
            )
        if entry.scope_key in seen:
            raise DeadlinePolicyError(f"two deadline rows for the same scope: {entry.scope_key}")
        seen.add(entry.scope_key)
        rules.append(entry)
    return DeadlineConfig(rules=tuple(rules))


@dataclass(frozen=True)
class Deadline:
    """When this claim stops being worth filing, and on what authority."""

    kind: str                 # duration | statutory_cutoff | unconfigured
    on: date | None
    basis: str                # the sentence shown beside the countdown

    def days_remaining(self, as_of: date) -> int | None:
        """Days left on the clock. Negative once it has passed; None with no clock."""
        return None if self.on is None else (self.on - as_of).days

    def has_passed(self, as_of: date) -> bool:
        """A claim with no configured window never expires. It also never closes itself."""
        return self.on is not None and as_of > self.on

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "on": None if self.on is None else self.on.isoformat(),
            "basis": self.basis,
        }


def _day_of_next_month(when: date, day: int) -> date:
    """The cutoff for the return covering ``when``'s month."""
    year = when.year + when.month // 12
    month = when.month % 12 + 1
    return date(year, month, day)


def deadline_for(cause: str, platform: str, opened_at: date, cfg: DeadlineConfig) -> Deadline:
    """The clock this claim runs on, looked up rather than branched on."""
    governing = cfg.governing(cause, platform)
    if governing is None:
        return Deadline(
            kind=UNCONFIGURED,
            on=None,
            basis=(
                f"no filing window is configured for {platform}/{cause} in "
                "config/thresholds.yaml"
            ),
        )

    if governing.rule == DAY_OF_NEXT_MONTH:
        return Deadline(
            kind=STATUTORY_CUTOFF,
            on=_day_of_next_month(opened_at, governing.value),
            basis=(
                governing.note
                or f"must be raised by the {governing.value}th of the following month"
            )
            + f" (for {opened_at.strftime('%B %Y')})",
        )

    return Deadline(
        kind=DURATION,
        on=opened_at + timedelta(days=governing.value),
        basis=f"{governing.value}-day {platform} filing window from {opened_at.isoformat()}",
    )
