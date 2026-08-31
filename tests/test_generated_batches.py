"""Tests against the emitted files, not the in-memory world.

These read `data/generated` and `data/truth` the way the pipeline and the harness
will, so a bug in serialisation cannot hide behind a correct world.
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline.config import generation
from pipeline.models import BankRow, LedgerRow, SettlementRow

BATCHES = list(range(1, int(generation()["batch_count"]) + 1))
MONEY_COLUMNS = ["amount", "fee", "tax", "tcs", "tds", "debit", "credit"]


def read_rows(directory: Path, batch: int, name: str) -> list[dict[str, str]]:
    path = directory / f"batch_{batch:02d}" / name
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_settlements(rows: list[dict[str, str]]) -> tuple[list[SettlementRow], list[dict]]:
    """Split a settlement file into rows the models accept and rows they refuse."""
    good, bad = [], []
    for row in rows:
        cleaned = {k: (v if v != "" else None) for k, v in row.items()}
        cleaned["on_hold"] = row["on_hold"] == "true"
        try:
            good.append(SettlementRow(**cleaned))
        except ValidationError:
            bad.append(row)
    return good, bad


# --------------------------------------------------------------------------- #
# Shape
# --------------------------------------------------------------------------- #


def test_ten_batches_exist_with_all_three_tables(generated_dir: Path) -> None:
    for batch in BATCHES:
        folder = generated_dir / f"batch_{batch:02d}"
        for name in ("settlement_report.csv", "bank_statement.csv", "internal_ledger.csv"):
            assert (folder / name).exists(), f"batch {batch} is missing {name}"
    assert sorted(p.name for p in generated_dir.iterdir() if p.is_dir()) == [
        f"batch_{b:02d}" for b in BATCHES
    ]


def test_batch_sizes_grow_from_about_sixty_to_about_one_eighty(generated_dir: Path) -> None:
    counts = [len(read_rows(generated_dir, b, "settlement_report.csv")) for b in BATCHES]
    assert counts == sorted(counts), f"batch sizes are not monotonic: {counts}"
    assert 55 <= counts[0] <= 70, counts[0]
    assert 170 <= counts[-1] <= 190, counts[-1]


def test_every_batch_clears_the_fifty_record_floor(generated_dir: Path) -> None:
    for batch in BATCHES:
        total = sum(
            len(read_rows(generated_dir, batch, name))
            for name in ("settlement_report.csv", "bank_statement.csv", "internal_ledger.csv")
        )
        assert total >= 50, f"batch {batch} has only {total} records"


# --------------------------------------------------------------------------- #
# Types
# --------------------------------------------------------------------------- #


def test_all_money_in_the_files_parses_to_decimal(generated_dir: Path) -> None:
    for batch in BATCHES:
        good, _ = parse_settlements(read_rows(generated_dir, batch, "settlement_report.csv"))
        for row in good:
            for column in MONEY_COLUMNS:
                assert isinstance(getattr(row, column), Decimal)

        for raw in read_rows(generated_dir, batch, "bank_statement.csv"):
            assert isinstance(BankRow(**raw).amount, Decimal)
        for raw in read_rows(generated_dir, batch, "internal_ledger.csv"):
            ledger = LedgerRow(**{k: (v if v != "" else None) for k, v in raw.items()})
            assert isinstance(ledger.expected_net, Decimal)


def test_money_columns_are_written_to_the_paise(generated_dir: Path) -> None:
    """Two decimal places, always. A bare integer in a money column is a bug."""
    for batch in BATCHES:
        good, _ = parse_settlements(read_rows(generated_dir, batch, "settlement_report.csv"))
        for row in good:
            for column in MONEY_COLUMNS:
                assert getattr(row, column).as_tuple().exponent == -2


# --------------------------------------------------------------------------- #
# Realistic mess
# --------------------------------------------------------------------------- #


def test_malformed_rows_exist_and_the_models_refuse_them(generated_dir: Path) -> None:
    planned = generation()["malformed_rows"]
    expected = sum(len(kinds) for kinds in planned.values())
    assert 3 <= expected <= 5, "the brief asks for 3-5 malformed rows across all batches"

    found = 0
    for batch in BATCHES:
        _, bad = parse_settlements(read_rows(generated_dir, batch, "settlement_report.csv"))
        assert len(bad) == len(planned.get(batch, [])), f"batch {batch}"
        found += len(bad)
    assert found == expected


def test_both_refund_sign_conventions_reach_the_files(generated_dir: Path) -> None:
    negated, debited = 0, 0
    for batch in BATCHES:
        good, _ = parse_settlements(read_rows(generated_dir, batch, "settlement_report.csv"))
        negated += sum(1 for r in good if r.type.value == "refund" and r.amount < 0)
        debited += sum(1 for r in good if r.type.value == "refund" and r.debit > 0)
    assert negated > 0 and debited > 0


def test_descriptions_look_like_a_platform_wrote_them(generated_dir: Path) -> None:
    good, _ = parse_settlements(read_rows(generated_dir, 4, "settlement_report.csv"))
    descriptions = {row.description for row in good}
    assert len(descriptions) > 10, "every row reading the same is not a settlement report"
    assert all(row.description for row in good)


# --------------------------------------------------------------------------- #
# Cross-batch behaviour
# --------------------------------------------------------------------------- #


def test_settlements_cross_batch_boundaries(generated_dir: Path) -> None:
    """A sale in batch N settling in batch N+k has to be in the data, not a story."""
    crossing = 0
    for batch in BATCHES:
        good, _ = parse_settlements(read_rows(generated_dir, batch, "settlement_report.csv"))
        crossing += sum(1 for r in good if (r.settled_at - r.created_at).days > 21)
    assert crossing >= 20, f"only {crossing} settlements cross the date window"


def test_an_order_can_be_booked_in_one_batch_and_paid_in_another(generated_dir: Path) -> None:
    booked: dict[str, int] = {}
    for batch in BATCHES:
        for raw in read_rows(generated_dir, batch, "internal_ledger.csv"):
            booked[raw["order_id"]] = batch

    crossings = 0
    for batch in BATCHES:
        good, _ = parse_settlements(read_rows(generated_dir, batch, "settlement_report.csv"))
        for row in good:
            if row.order_id and booked.get(row.order_id, batch) < batch:
                crossings += 1
    assert crossings > 100, "the ledger and the settlement report must not move in lockstep"
