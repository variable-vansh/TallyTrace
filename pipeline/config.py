"""Config loading.

Any number that could be argued about lives in ``config/``. This module is the only
way those files are read, and it parses every YAML float as ``Decimal`` so that a
rate or a tolerance can never enter the system as binary floating point.
"""

from __future__ import annotations

import functools
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"


class _DecimalLoader(yaml.SafeLoader):
    """SafeLoader that yields Decimal for YAML floats, preserving the literal text."""


def _construct_decimal(loader: yaml.Loader, node: yaml.Node) -> Decimal:
    return Decimal(str(node.value))


_DecimalLoader.add_constructor("tag:yaml.org,2002:float", _construct_decimal)


def load_yaml(path: Path) -> dict[str, Any]:
    """Read one YAML file. Floats come back as Decimal."""
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.load(handle, Loader=_DecimalLoader)
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not parse to a mapping")
    return data


@functools.lru_cache(maxsize=None)
def thresholds() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "thresholds.yaml")


@functools.lru_cache(maxsize=None)
def channels() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "channels.yaml")


@functools.lru_cache(maxsize=None)
def causes() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "causes.yaml")


@functools.lru_cache(maxsize=None)
def generation() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "generation.yaml")


@functools.lru_cache(maxsize=None)
def resolution_class_by_cause() -> dict[str, str]:
    """cause -> resolution_class, straight from the frozen enum file."""
    entries: list[dict[str, str]] = causes()["causes"]
    return {entry["cause"]: entry["resolution_class"] for entry in entries}


def channel_config(channel: str) -> dict[str, Any]:
    try:
        cfg: dict[str, Any] = channels()["channels"][channel]
    except KeyError as exc:
        raise KeyError(f"unknown channel {channel!r} in config/channels.yaml") from exc
    return cfg


def batch_window(batch: int) -> tuple[date, date]:
    """Inclusive [start, end] dates covered by a batch."""
    gen = generation()
    if not 1 <= batch <= int(gen["batch_count"]):
        raise ValueError(f"batch {batch} outside 1..{gen['batch_count']}")
    length = int(gen["batch_length_days"])
    start = gen["first_batch_start"] + timedelta(days=(batch - 1) * length)
    return start, start + timedelta(days=length - 1)


def batch_for_date(when: date) -> int:
    """Batch index containing ``when``, clamped to the corpus edges.

    Clamping is deliberate: an order created before batch 1 belongs on batch 1's
    opening book, and a settlement falling after batch 10 still has to be written
    somewhere. Both are recorded with their true dates; only the file they land in
    is clamped.
    """
    gen = generation()
    count = int(gen["batch_count"])
    length = int(gen["batch_length_days"])
    first: date = gen["first_batch_start"]
    index = (when - first).days // length + 1
    return max(1, min(count, index))
