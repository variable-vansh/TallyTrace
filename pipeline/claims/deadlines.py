"""The deadline clock.

The drafting is not what matters. The deadlines are. Amazon's SAFE-T window closes
30 days after the event, and a TCS discrepancy has to be raised before the 10th of
the following month or the GSTR-8 correction misses its return. Sellers lose this
money because they only discover the loss at reconciliation time, which is already
late -- so the clock starts at the moment the reconciliation surfaces the problem,
and it is computed, not typed.

Two kinds of clock, and they are genuinely different shapes:

**A duration.** ``opened_at + N days``, N from ``config/thresholds.yaml`` per
platform. Days remaining is a subtraction.

**A statutory cutoff.** The 10th of the month *after* the one the claim opened in.
This is a calendar date rather than a duration, so a claim opened on the 2nd has
eight days and one opened on the 28th has thirteen. Forcing it into a days-remaining
model would put a number on screen that is wrong for eleven months of the year.

A platform with no configured window gets **no deadline at all**, not a default.
Inventing 30 days for the website gateway would put a countdown on screen that no
agreement backs, and a claim queue whose whole value is its clock cannot afford one
clock that is made up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Mapping

#: The config key that is a day-of-month, not a duration. Named separately because
#: reading it as a duration would be a ten-day window rather than a ten*th*-of-month.
TCS_KEY = "tcs_discrepancy"

#: The one non-counterparty cause that still has a filing deadline. TDS under 194-O
#: is reconciled against Form 26AS on a different cycle and no cutoff for it is
#: configured, so it gets no clock rather than this one.
STATUTORY_CAUSES = frozenset({"tcs_timing_mismatch"})

DURATION = "duration"
STATUTORY_CUTOFF = "statutory_cutoff"
UNCONFIGURED = "unconfigured"


@dataclass(frozen=True)
class DeadlineConfig:
    """Filing windows, straight from ``config/thresholds.yaml``."""

    days_by_platform: Mapping[str, int]
    tcs_cutoff_day_of_month: int


def deadline_config_from(thresholds: dict[str, Any]) -> DeadlineConfig:
    section = thresholds["claims"]["deadline_days"]
    return DeadlineConfig(
        days_by_platform={
            platform: int(days) for platform, days in section.items() if platform != TCS_KEY
        },
        tcs_cutoff_day_of_month=int(section[TCS_KEY]),
    )


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


def _tenth_of_next_month(when: date, day: int) -> date:
    """The cutoff for the return covering ``when``'s month."""
    year = when.year + when.month // 12
    month = when.month % 12 + 1
    return date(year, month, day)


def deadline_for(cause: str, platform: str, opened_at: date, cfg: DeadlineConfig) -> Deadline:
    """The clock this claim runs on.

    The statutory cutoff is checked first: a TCS discrepancy on Flipkart is on the
    GSTR-8 calendar, not on Flipkart's 30-day commercial window, and taking the
    platform's window because the platform is configured would quietly give it
    twenty extra days it does not have.
    """
    if cause in STATUTORY_CAUSES:
        cutoff = _tenth_of_next_month(opened_at, cfg.tcs_cutoff_day_of_month)
        return Deadline(
            kind=STATUTORY_CUTOFF,
            on=cutoff,
            basis=(
                f"GSTR-8 correction for {opened_at.strftime('%B %Y')} must be raised by the "
                f"{cfg.tcs_cutoff_day_of_month}th of the following month"
            ),
        )

    days = cfg.days_by_platform.get(platform)
    if days is None:
        return Deadline(
            kind=UNCONFIGURED,
            on=None,
            basis=f"no filing window is configured for {platform} in config/thresholds.yaml",
        )
    return Deadline(
        kind=DURATION,
        on=opened_at + timedelta(days=days),
        basis=f"{days}-day {platform} filing window from {opened_at.isoformat()}",
    )
