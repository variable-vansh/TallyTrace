"""`make demo` must produce identical numbers twice.

Determinism is not a nicety here: the review-rate curve is the headline claim, and
a curve that moves between runs is not a measurement. Everything downstream of the
seed -- the draw order, the payout grouping, the injection plan -- has to be stable.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from generator.main import build
from generator.writer import write_batches, write_truth


def fingerprint(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name != ".gitkeep"
    }


def generate_into(root: Path) -> Path:
    world, bank = build(None, inject=True)
    counts = write_batches(world, bank, root / "generated")
    write_truth(world, counts, root / "truth")
    return root


def test_two_runs_are_byte_identical(tmp_path: Path) -> None:
    first = fingerprint(generate_into(tmp_path / "one"))
    second = fingerprint(generate_into(tmp_path / "two"))
    assert first.keys() == second.keys()
    differing = sorted(name for name in first if first[name] != second[name])
    assert differing == [], f"these files changed between two seeded runs: {differing}"


def test_the_committed_output_matches_a_fresh_run(tmp_path: Path) -> None:
    """Guards against `data/generated` drifting away from the code that made it."""
    from pipeline.config import REPO_ROOT

    fresh = fingerprint(generate_into(tmp_path / "fresh") / "generated")
    committed = fingerprint(REPO_ROOT / "data" / "generated")
    if not committed:
        import pytest

        pytest.skip("run `make generate` first")
    assert fresh == committed, "data/generated is stale; re-run `make generate`"


def test_a_different_seed_produces_a_different_world() -> None:
    """Determinism must come from the seed, not from the generator ignoring it."""
    default, _ = build(None, inject=False)
    other, _ = build(99, inject=False)
    assert [r.amount for r in default.settlements] != [r.amount for r in other.settlements]
