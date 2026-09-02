"""`make ask q="..."` -- the reporting surface, on the command line.

Ask, confirm, compute. The three steps are visible here rather than hidden behind a
chat box, because the middle one is the design: the restatement is printed and
nothing is computed until it is accepted. ``--yes`` accepts on the caller's behalf,
which is what ``make demo`` uses; without it the command stops after the restatement
and says so.

A question the registry cannot answer prints the refusal and exits non-zero. It does
not fall back to a related chart.
"""

from __future__ import annotations

import argparse
from decimal import Decimal

from pipeline.config import CONFIG_DIR, generation, load_yaml
from pipeline.learn import run as run_learning
from pipeline.llm.cache import CacheMiss
from pipeline.llm.client import client_from
from pipeline.llm.intent import map_question
from pipeline.metrics import corpus_from
from pipeline.metrics.ask import execute, plan_from
from pipeline.metrics.registry import COUNT, INR, PERCENT, MetricResult

UNITS = {INR: "₹", PERCENT: "%", COUNT: ""}


def render(result: MetricResult) -> str:
    """One computed metric as a fixed-width table. Percentages never carry a total."""
    width = max((len(point.label) for point in result.points), default=10)
    lines = [f"{result.title}  [{result.group_by}]", "-" * 62]
    for point in result.points:
        value = (
            f"₹{point.value:,.2f}" if result.unit == INR
            else f"{point.value}%" if result.unit == PERCENT
            else str(point.value)
        )
        lines.append(f"  {point.label:<{width}}  {value:>16}")
    if result.unit == INR:
        lines += ["-" * 62, f"  {'total':<{width}}  {'₹' + format(result.total, ',.2f'):>16}"]
    elif result.unit == COUNT:
        lines += ["-" * 62, f"  {'total':<{width}}  {str(result.total):>16}"]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the metric registry a question.")
    parser.add_argument("question", help="the question, in plain language")
    parser.add_argument("--yes", action="store_true", help="accept the restatement and compute")
    parser.add_argument(
        "--offline", action="store_true",
        help="never call the API, even with a key set; answer only from data/llm_cache",
    )
    args = parser.parse_args()
    allow_network = not args.offline

    pricing = load_yaml(CONFIG_DIR / "pricing.yaml")
    client = client_from(
        str(pricing["model"]),
        chars_per_token=Decimal(str(pricing["estimated_chars_per_token"])),
        allow_network=allow_network,
    )
    last = int(generation()["batch_count"])
    try:
        intent = map_question(client, args.question, last)
    except CacheMiss:
        # Not a crash and not a refusal: a question nobody has asked before, on a machine
        # with nothing to ask with. Saying so beats a traceback and beats guessing.
        print(f'question    : "{args.question}"')
        print("outcome     : unasked")
        print(
            "\nThis question is not in the committed fixtures. `make ask` runs offline,\n"
            "so it answers the questions in tools/operator_questions.py and nothing else.\n"
            "Set ANTHROPIC_API_KEY and run without --offline to ask a new one."
        )
        return 4
    plan = plan_from(args.question, intent)

    print(f'question    : "{args.question}"')
    print(f"outcome     : {plan.outcome}")
    print(f"restatement : {plan.restatement}")

    if plan.outcome == "clarify":
        print(f"\nclarify     : {plan.intent.clarifying_question}")
        print("\nNothing was computed. One question is asked rather than a guess made.")
        return 2
    if plan.outcome == "refuse":
        print(f"\nrefused     : {plan.intent.refusal}")
        print("\nNothing was computed, and no adjacent chart was offered instead.")
        return 3

    if not args.yes:
        print("\nConfirm before compute: rerun with --yes to compute exactly the above.")
        return 0

    corpus = corpus_from(run_learning(allow_network=allow_network))
    print()
    print(render(execute(plan, corpus, confirmed=True)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
