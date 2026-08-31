"""Quarantine: malformed rows are parked with a reason, never dropped.

The three corruptions the generator plants -- a missing order id, a date in a format
nobody agreed to, an amount that arrived as formatted text -- are exercised here
against the real files, and the classifier is tested on its own so a new corruption
gets a named reason rather than falling into the catch-all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.config import generation
from pipeline.loader import load_batch, prepare_settlement
from pipeline.matcher.quarantine import classify
from pipeline.matcher.reasons import Reason

CORRUPTED_BATCHES = sorted(generation()["malformed_rows"])


def test_classify_names_a_missing_join_key() -> None:
    errors = [{"loc": ("order_id",), "msg": "field required"}]
    assert classify(errors, "order_id: field required") is Reason.MALFORMED_MISSING_ORDER_ID


def test_classify_names_an_unparseable_date() -> None:
    errors = [{"loc": ("settled_at",), "msg": "invalid date format"}]
    assert classify(errors, "") is Reason.MALFORMED_UNPARSEABLE_DATE


def test_classify_names_an_unparseable_amount() -> None:
    errors = [{"loc": ("amount",), "msg": "not a decimal amount"}]
    assert classify(errors, "") is Reason.MALFORMED_UNPARSEABLE_AMOUNT


def test_classify_falls_back_to_a_named_schema_violation() -> None:
    errors = [{"loc": ("channel",), "msg": "not a valid enumeration member"}]
    assert classify(errors, "") is Reason.MALFORMED_SCHEMA_VIOLATION


def test_an_empty_nullable_column_becomes_none_not_an_empty_string() -> None:
    prepared = prepare_settlement({"order_id": "", "dispute_id": "", "description": ""})
    assert prepared["order_id"] is None and prepared["dispute_id"] is None
    assert prepared["description"] == ""


@pytest.mark.parametrize("batch", CORRUPTED_BATCHES)
def test_every_planted_corruption_is_quarantined_with_a_reason(batch: int, generated_dir: Path) -> None:
    tables = load_batch(batch, generated_dir)
    planted = generation()["malformed_rows"][batch]
    assert len(tables.quarantined) == len(planted)
    for record in tables.quarantined:
        assert record.reason.value.startswith("malformed_")
        assert record.message, "a quarantined row without a reason is a dropped row"
        assert record.raw, "the raw row is kept so a human can look at it"


def test_the_good_rows_of_a_corrupted_batch_still_load(generated_dir: Path) -> None:
    """One bad row must not take the table with it."""
    tables = load_batch(CORRUPTED_BATCHES[0], generated_dir)
    assert tables.settlements, "a whole-table parse would have lost every row here"
    assert tables.quarantined


def test_no_row_is_lost_between_the_file_and_the_tables(generated_dir: Path) -> None:
    """The count going in equals the count coming out. Every batch, every table."""
    import csv

    for batch in range(1, int(generation()["batch_count"]) + 1):
        tables = load_batch(batch, generated_dir)
        folder = generated_dir / f"batch_{batch:02d}"
        on_disk = sum(
            sum(1 for _ in csv.DictReader(path.open(encoding="utf-8")))
            for path in sorted(folder.glob("*.csv"))
        )
        assert tables.rows_read == on_disk, f"batch {batch} lost rows"
