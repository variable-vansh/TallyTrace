"""The generator's own unit test: does the clean base reconcile at 100%?

This is deliberately written against the emitted world rather than reusing the code
that built it, so a bug in the builder shows up here instead of cancelling itself
out. If this does not come back clean, every number the pipeline produces later is
meaningless, and nothing downstream is worth debugging until it does.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Iterable

from generator.base import build_clean_world
from generator.money import ZERO, inr
from generator.world import World, finalise, net_of
from pipeline.models import BankRow


def check_orders(world: World) -> list[str]:
    """Every booked order is settled, and settled for exactly what the books expect."""
    problems: list[str] = []
    by_order: dict[str, list] = defaultdict(list)
    for row in world.settlements:
        if row.order_id is not None:
            by_order[row.order_id].append(row)

    for order_id, ledger in sorted(world.ledger.items()):
        rows = by_order.get(order_id, [])
        if not rows:
            problems.append(f"{order_id}: booked but never settled")
            continue
        settled = inr(sum((net_of(row) for row in rows), ZERO))
        if settled != ledger.expected_net:
            problems.append(f"{order_id}: settled {settled} against expected_net {ledger.expected_net}")
    return problems


def check_fees(world: World) -> list[str]:
    """The fee charged equals the fee the ledger derived from its own rate."""
    problems: list[str] = []
    for order_id, meta in sorted(world.meta.items()):
        if meta.refunded:
            continue                       # fully reversed: nothing left to compare
        ledger = world.ledger[order_id]
        for row in world.settlements:
            if row.entity_id == meta.payment_entity_id and row.fee != ledger.expected_fee:
                problems.append(f"{order_id}: fee {row.fee} against expected_fee {ledger.expected_fee}")
    return problems


def check_orphans(world: World) -> list[str]:
    """No settlement row points at an order that is not in the books."""
    return [
        f"{row.entity_id}: settles unknown order {row.order_id}"
        for row in world.settlements
        if row.order_id is not None and row.order_id not in world.ledger
    ]


def check_bank(world: World, bank: Iterable[BankRow]) -> list[str]:
    """Every payout group sums, N:1, to exactly one bank credit."""
    problems: list[str] = []
    credits = {row.utr: row for row in bank}
    grouped: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in world.settlements:
        grouped[row.settlement_utr] = inr(grouped[row.settlement_utr] + net_of(row))

    for utr in sorted(grouped):
        if utr not in credits:
            # A cycle that nets to zero -- a same-day capture and refund, say -- is
            # never wired, so no bank credit for it is the correct outcome.
            if grouped[utr] != ZERO:
                problems.append(f"{utr}: settlement group of {grouped[utr]} with no bank credit")
        elif credits[utr].amount != grouped[utr]:
            problems.append(f"{utr}: bank {credits[utr].amount} against settlement sum {grouped[utr]}")
    for utr in sorted(credits):
        if utr not in grouped:
            problems.append(f"{utr}: bank credit with no settlement group")
    return problems


def verify(seed: int | None = None) -> list[str]:
    """Build the clean base with injections disabled and reconcile it end to end."""
    world, _ = build_clean_world(seed)
    bank = finalise(world)
    return check_orders(world) + check_fees(world) + check_orphans(world) + check_bank(world, bank)


def main() -> int:
    problems = verify()
    if problems:
        print(f"clean base does NOT reconcile: {len(problems)} problem(s)")
        for line in problems[:20]:
            print(f"  - {line}")
        if len(problems) > 20:
            print(f"  ... and {len(problems) - 20} more")
        return 1
    print("clean base reconciles at 100%: orders, fees, orphans and bank all tie out")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
