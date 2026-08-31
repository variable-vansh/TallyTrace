"""The matcher's config object.

Built from ``config/thresholds.yaml`` by the caller and handed to the matching
functions as data, so that nothing under ``pipeline/matcher/`` reads a file. Every
number here is argued about somewhere, which is why none of them is a literal in the
matching code.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

HUNDRED = Decimal("100")


@dataclass(frozen=True)
class MatchConfig:
    """Tolerances and search bounds. All money is Decimal, all rates are fractions."""

    rounding_tolerance_inr: Decimal
    date_window_days: int
    fee_variance_tolerance_pct: Decimal
    subset_max_size: int
    subset_max_candidates: int

    def value_tolerance(self, expected_fee: Decimal) -> Decimal:
        """Rupee tolerance for one order's value comparison.

        Derived from the ledger's own expectation -- ``expected_fee`` is
        ``order_value * expected_commission_rate`` -- rather than from a flat rupee
        constant. That is what makes a stale rate produce a *systematic* variance
        proportional to the order, which is the signal checkpoint 3 generalises
        from. The rounding tolerance is a floor under it so that paise drift on a
        small order does not fire.
        """
        band = abs(expected_fee) * self.fee_variance_tolerance_pct / HUNDRED
        return max(self.rounding_tolerance_inr, band)


def match_config_from(thresholds: dict[str, Any]) -> MatchConfig:
    """Build the config from a parsed ``thresholds.yaml`` mapping."""
    matching = thresholds["matching"]
    subset = matching["subset_search"]
    return MatchConfig(
        rounding_tolerance_inr=Decimal(matching["rounding_tolerance_inr"]),
        date_window_days=int(matching["date_window_days"]),
        fee_variance_tolerance_pct=Decimal(matching["fee_variance_tolerance_pct"]),
        subset_max_size=int(subset["max_subset_size"]),
        subset_max_candidates=int(subset["max_candidates"]),
    )
