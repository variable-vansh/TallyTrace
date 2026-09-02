"""Build ``data/questions.json`` and ``data/pins.json`` -- the reporting surface's log.

Both are produced by walking the real path. A question goes through intent mapping;
the restatement comes back; the operator confirms it or does not; a confirmed result
may be pinned. What gets written is the *definition* and the human's decision -- never
a computed number, because the numbers are recomputed every run with no model in the
loop, which is the entire claim of this surface.

The pin policy lives beside the questions in ``tools/operator_questions.py``, so this
file is mechanism and contains no data.
"""

from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal

from pipeline.config import CONFIG_DIR, generation, load_yaml
from pipeline.llm.client import client_from
from pipeline.llm.intent import map_question
from pipeline.metrics.ask import Plan, pin_from, plan_from
from pipeline.metrics.pins import Pin, save as save_pins
from pipeline.metrics.questions import AskedQuestion, save as save_questions
from tools.operator_questions import ASKED_AT, OPERATOR, QUESTIONS


def plans(allow_network: bool = True) -> list[tuple[Plan, str | None, bool]]:
    """Every question, mapped once, with the operator's decision beside it."""
    pricing = load_yaml(CONFIG_DIR / "pricing.yaml")
    client = client_from(
        str(pricing["model"]),
        chars_per_token=Decimal(str(pricing["estimated_chars_per_token"])),
        allow_network=allow_network,
    )
    last = int(generation()["batch_count"])
    return [
        (plan_from(a.question, map_question(client, a.question, last)), a.pin_as, a.confirmed)
        for a in QUESTIONS
    ]


def build(allow_network: bool = True) -> tuple[list[AskedQuestion], list[Pin], int]:
    asked: list[AskedQuestion] = []
    pins: list[Pin] = []
    mapped = 0
    for plan, pin_as, confirmed in plans(allow_network):
        asked.append(
            AskedQuestion(
                question=plan.question, asked_by=OPERATOR, asked_at=ASKED_AT,
                confirmed=confirmed, pin_as=pin_as,
            )
        )
        mapped += int(plan.answerable)
        if pin_as is None:
            continue
        if not (plan.answerable and confirmed):
            raise ValueError(
                f"{plan.question!r} is marked as pinned but its outcome is {plan.outcome!r}; "
                "only a confirmed, mapped result can be pinned"
            )
        pins.append(
            pin_from(
                plan, name=pin_as, pin_id=f"pin_{len(pins) + 1:02d}",
                pinned_by=OPERATOR, pinned_at=date.fromisoformat(ASKED_AT),
            )
        )
    return asked, pins, mapped


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the reporting surface's log.")
    parser.add_argument(
        "--offline", action="store_true",
        help="never call the API, even with a key set; answer only from data/llm_cache",
    )
    args = parser.parse_args()

    asked, pins, mapped = build(allow_network=not args.offline)
    save_questions(asked)
    save_pins(pins)
    print(f"{len(asked)} questions -> data/questions.json "
          f"({mapped} mapped, {len(asked) - mapped} clarified or refused)")
    print(f"{len(pins)} pinned metrics -> data/pins.json")
    for pin in pins:
        print(f"  {pin.pin_id}  {pin.name:<34} {pin.metric_id} by {pin.params.group_by}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
