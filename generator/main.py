"""``make generate``.

Builds the clean world, applies the injection plan, writes the ten batches and the
ground truth, then prints the per-batch summary. The summary is the point: after a
run you can say out loud, per batch, how many troubles were injected, of which
causes, worth how many rupees.
"""

from __future__ import annotations

import argparse
import shutil
from decimal import Decimal
from pathlib import Path

from generator.base import build_clean_world
from generator.injectors import run_plan
from generator.world import World, finalise
from generator.writer import write_batches, write_truth
from pipeline.config import REPO_ROOT, generation

DEFAULT_OUT = REPO_ROOT / "data" / "generated"
DEFAULT_TRUTH = REPO_ROOT / "data" / "truth"


def build(seed: int | None, *, inject: bool) -> tuple[World, list]:
    world, rng = build_clean_world(seed)
    if inject:
        run_plan(world, rng)
    return world, finalise(world)


def clear(directory: Path) -> None:
    """Wipe generated output so a run is never a merge of two runs."""
    if directory.exists():
        for child in sorted(directory.iterdir()):
            if child.name == ".gitkeep":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()


def print_summary(world: World, counts: dict[int, dict[str, int]]) -> None:
    by_batch: dict[int, list] = {}
    for entry in world.truth:
        by_batch.setdefault(entry.batch, []).append(entry)

    grand = Decimal("0.00")
    print(f"{'batch':>5} {'settle':>7} {'bank':>5} {'ledger':>7} {'bad':>4} {'troubles':>9} {'impact INR':>13}  causes")
    for batch in sorted(counts):
        entries = by_batch.get(batch, [])
        rows = sum(len(entry.affected_row_ids) for entry in entries)
        impact = sum((entry.true_impact_inr for entry in entries), Decimal("0.00"))
        grand += impact
        causes = ", ".join(f"{e.cause}x{len(e.affected_row_ids)}" for e in sorted(entries, key=lambda e: e.cause))
        counted = counts[batch]
        print(
            f"{batch:>5} {counted['settlement_rows']:>7} {counted['bank_rows']:>5} "
            f"{counted['ledger_rows']:>7} {counted['malformed_rows']:>4} {rows:>9} {impact:>13}  {causes}"
        )
    print(f"{'total':>5} {sum(c['settlement_rows'] for c in counts.values()):>7} "
          f"{sum(c['bank_rows'] for c in counts.values()):>5} "
          f"{sum(c['ledger_rows'] for c in counts.values()):>7} "
          f"{sum(c['malformed_rows'] for c in counts.values()):>4} "
          f"{sum(len(e.affected_row_ids) for e in world.truth):>9} {grand:>13}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Tallytrace batches and ground truth.")
    parser.add_argument("--seed", type=int, default=None, help="override config/generation.yaml seed")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--truth", type=Path, default=DEFAULT_TRUTH)
    parser.add_argument(
        "--no-injections", action="store_true",
        help="emit the clean base only; used to prove the base reconciles before anything is broken",
    )
    args = parser.parse_args()

    world, bank = build(args.seed, inject=not args.no_injections)
    clear(args.out)
    counts = write_batches(world, bank, args.out)
    if args.no_injections:
        print(f"clean base written to {args.out} (no injections, no ground truth)")
        return 0

    clear(args.truth)
    write_truth(world, counts, args.truth)
    print(f"seed {args.seed if args.seed is not None else generation()['seed']} -> {args.out}")
    print_summary(world, counts)
    print(f"ground truth -> {args.truth}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
