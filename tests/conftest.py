"""Shared fixtures.

Generation is fast enough (under a tenth of a second) that every test gets a fresh
world rather than sharing a mutated one. Injectors mutate in place, so sharing would
make test order significant.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Callable

import pytest

from generator.base import build_clean_world
from generator.injectors import run_plan
from generator.world import World, finalise
from pipeline.config import REPO_ROOT
from pipeline.models import BankRow

CLEAN_SEED = 4242


@pytest.fixture
def clean_world() -> tuple[World, random.Random]:
    """A fully reconciling world with nothing injected."""
    return build_clean_world(CLEAN_SEED)


@pytest.fixture
def injected_world() -> tuple[World, list[BankRow]]:
    """The shipped world: the configured seed and the full injection plan."""
    world, rng = build_clean_world()
    run_plan(world, rng)
    return world, finalise(world)


@pytest.fixture
def run_injector(clean_world) -> Callable[..., World]:
    """Apply a single injector to an otherwise clean world."""
    world, rng = clean_world

    def run(injector: Callable, batch: int, count: int, **params) -> World:
        injector(world, rng, batch, count, params)
        finalise(world)
        return world

    return run


@pytest.fixture(scope="session")
def generated_dir() -> Path:
    directory = REPO_ROOT / "data" / "generated"
    if not (directory / "batch_01" / "settlement_report.csv").exists():
        pytest.skip("run `make generate` first")
    return directory


@pytest.fixture(scope="session")
def truth_dir() -> Path:
    directory = REPO_ROOT / "data" / "truth"
    if not (directory / "manifest.json").exists():
        pytest.skip("run `make generate` first")
    return directory
