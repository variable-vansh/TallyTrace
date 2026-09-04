"""Score the corpus at a range of auto-resolution ceilings and write the curve.

The ceiling is a number the business sets, and a number nobody can evaluate is not
really settable. This scores the whole corpus once per candidate ceiling and records
what each one actually produced, so the threshold control in the UI is backed by real
runs rather than by an estimate the browser made up.

**Why two precision series and not one.** ``live`` is what the product can see: a rule
is judged against the cause the operator's own words imply. ``true`` is the harness's,
measured against the answer key the pipeline never reads. At the shipped ceiling they
agree exactly. They come apart as the ceiling rises, and the gap is the finding -- an
operator and a rule can be wrong in the same direction (FAILURES.md #22), and the
bigger the row, the more often they are. A control that showed only ``live`` would
recommend raising the ceiling forever.

Writes ``data/ceiling_scenarios.json``. Offline by default: every ceiling replays the
same committed fixtures, so the curve costs nothing and is reproducible.
"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from typing import Any

from harness.score import run, to_json
from pipeline.config import REPO_ROOT, thresholds
from pipeline.rules.guardrails import guardrail_config_from

SCENARIOS_JSON = REPO_ROOT / "data" / "ceiling_scenarios.json"

#: The candidates the control offers. Dense where the curve turns, sparse after it has
#: clearly turned -- past ₹2,000 every further rupee buys the same trade more loudly.
DEFAULT_CEILINGS = ("0", "250", "500", "600", "700", "800", "900", "1000", "1500", "2000", "3000")
ZERO = Decimal("0.00")


#: Rupees and paise, the grain money uses everywhere else. Quantised so a ceiling
#: written "500" here and "500.00" in config are the same key to everything that
#: joins the two -- the UI control looks itself up in this curve by that string.
PAISE = Decimal("0.01")


def scenario(ceiling: Decimal, allow_network: bool) -> dict[str, Any]:
    """One scored run at one ceiling, reduced to what a decision needs."""
    ceiling = ceiling.quantize(PAISE)
    policy = guardrail_config_from(thresholds()).with_default_ceiling(ceiling)
    payload = to_json(run(allow_network=allow_network, guardrails=policy))
    totals, learning = payload["totals"], payload["learning"]

    attempted = int(totals["auto_resolutions_attempted"])
    true_pct = Decimal(str(totals["auto_resolution_precision_pct"] or 0))
    live_raw = learning["overall_auto_resolution_precision_pct"]
    live_pct = None if live_raw is None else Decimal(str(live_raw))

    return {
        "ceiling_inr": str(ceiling),
        "auto_resolutions": attempted,
        # Rows the answer key says were closed with the wrong cause. The number a
        # bookkeeper would actually have to find and undo.
        "wrong": int(round(attempted * (100 - true_pct) / 100)),
        "true_precision_pct": str(true_pct),
        "live_precision_pct": None if live_pct is None else str(live_pct),
        "precision_gap_pct": None if live_pct is None else str(live_pct - true_pct),
        "rupees_auto_resolved": str(
            sum((Decimal(b["rupees_auto_resolved"]) for b in learning["batches"]), ZERO)
        ),
        "rupees_escalated": str(
            sum((Decimal(b["rupees_escalated"]) for b in learning["batches"]), ZERO)
        ),
        "review_rate_series_pct": list(totals["review_rate_series_pct"]),
        "touchpoint_rate_series_pct": list(totals["touchpoint_rate_series_pct"]),
        "final_review_rate_pct": totals["review_rate_series_pct"][-1],
        "open_exceptions": totals["open_exceptions"],
    }


def sweep(ceilings: tuple[str, ...], allow_network: bool) -> dict[str, Any]:
    configured = guardrail_config_from(thresholds()).default_ceiling.max_variance_inr.quantize(PAISE)
    return {
        "generatedFrom": "make ceilings",
        "configured_ceiling_inr": str(configured),
        "scenarios": [scenario(Decimal(c), allow_network) for c in ceilings],
    }


def summarise(payload: dict[str, Any]) -> str:
    head = (
        f"{'ceiling':>9} {'auto':>5} {'wrong':>6} {'true %':>7} {'live %':>7} "
        f"{'gap':>6} {'last review %':>14} {'₹ auto-resolved':>16}"
    )
    lines = [head, "-" * len(head)]
    for entry in payload["scenarios"]:
        marker = "*" if entry["ceiling_inr"] == payload["configured_ceiling_inr"] else " "
        lines.append(
            f"{marker}{'₹' + entry['ceiling_inr']:>8} {entry['auto_resolutions']:>5} "
            f"{entry['wrong']:>6} {entry['true_precision_pct']:>7} "
            f"{entry['live_precision_pct'] or '—':>7} {entry['precision_gap_pct'] or '—':>6} "
            f"{entry['final_review_rate_pct']:>14} "
            f"{'₹' + entry['rupees_auto_resolved']:>16}"
        )
    lines += [
        "-" * len(head),
        "* the ceiling in config/thresholds.yaml.",
        "",
        "  live %  what the system can see: the rule judged against the cause the",
        "          operator's own words imply. This is the product's signal.",
        "  true %  the same resolutions judged against the answer key, which the",
        "          pipeline never reads. This is the honest one.",
        "",
        "  The two agree at the shipped ceiling and come apart above it. A rule and an",
        "  operator can be wrong in the same direction, and the bigger the row the more",
        "  often they are -- so a ceiling chosen on live precision alone rises forever.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Score the corpus across a range of ceilings.")
    parser.add_argument(
        "--offline", action="store_true",
        help="never call the API, even with a key set; answer only from data/llm_cache",
    )
    parser.add_argument(
        "--ceilings", nargs="+", default=list(DEFAULT_CEILINGS), metavar="RUPEES",
        help="the ceilings to score (default: %(default)s)",
    )
    args = parser.parse_args()

    payload = sweep(tuple(args.ceilings), allow_network=not args.offline)
    print(summarise(payload), end="")
    SCENARIOS_JSON.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {SCENARIOS_JSON.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
