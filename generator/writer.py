"""Emit the batches to disk.

CSV, because that is what platforms actually send and because a decimal written as
text stays exactly the decimal that was computed. The malformed rows are written
after the models, deliberately bypassing validation: the point is to hand the
pipeline input its models will refuse, so the quarantine path has something to do.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from pipeline.config import batch_for_date, generation
from pipeline.models import BankRow, LedgerRow, SettlementRow
from generator.world import TruthEntry, World

SETTLEMENT_COLUMNS = [
    "entity_id", "type", "channel", "order_id", "amount", "fee", "tax", "tcs", "tds",
    "debit", "credit", "settlement_id", "settlement_utr", "created_at", "settled_at",
    "on_hold", "dispute_id", "description",
]
BANK_COLUMNS = ["utr", "amount", "created_at", "status"]
LEDGER_COLUMNS = [
    "order_id", "channel", "order_value", "expected_commission_rate", "expected_fee",
    "expected_net", "status", "resolution_reason",
]


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (Decimal, date)):
        return str(value)
    if hasattr(value, "value"):          # enum
        return str(value.value)
    return str(value)


def write_csv(path: Path, columns: Sequence[str], rows: Sequence[Any], extra: Sequence[dict]) -> None:
    """Write validated rows, then any raw malformed rows, in that order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            dumped = row.model_dump()
            writer.writerow([_cell(dumped[column]) for column in columns])
        for raw in extra:
            writer.writerow([raw.get(column, "") for column in columns])


# --------------------------------------------------------------------------- #
# Malformed rows
# --------------------------------------------------------------------------- #

CORRUPTIONS: dict[str, dict[str, str]] = {
    # An order id the platform never populated.
    "missing_order_id": {"order_id": ""},
    # A date in a format nobody agreed to.
    "unparseable_date": {"settled_at": "31/02/2025"},
    # An amount that arrived as a formatted string.
    "amount_with_comma": {"amount": "1,299.00", "credit": "1,101.24"},
}


def malformed_rows(batch: int, template: SettlementRow) -> list[dict[str, str]]:
    """Raw settlement rows for a batch, broken in the ways real feeds break."""
    kinds = generation()["malformed_rows"].get(batch, [])
    rows: list[dict[str, str]] = []
    for index, kind in enumerate(kinds, start=1):
        if kind not in CORRUPTIONS:
            raise ValueError(f"unknown corruption kind {kind!r}")
        dumped = {column: _cell(template.model_dump()[column]) for column in SETTLEMENT_COLUMNS}
        dumped["entity_id"] = f"st_bad_{batch:02d}{index:02d}"
        dumped.update(CORRUPTIONS[kind])
        rows.append(dumped)
    return rows


# --------------------------------------------------------------------------- #
# Slicing and writing
# --------------------------------------------------------------------------- #


def slice_batches(
    world: World, bank: list[BankRow]
) -> dict[int, tuple[list[SettlementRow], list[BankRow], list[LedgerRow]]]:
    """Split the world into per-batch files.

    A settlement row belongs to the batch its payout landed in, a bank credit to
    the batch it was credited in, and a ledger row to the batch the order was
    booked in -- so an order booked in batch 3 and paid in batch 5 appears in two
    different files, which is exactly the cross-batch case the matcher must handle.
    """
    count = int(generation()["batch_count"])
    sliced: dict[int, tuple[list, list, list]] = {b: ([], [], []) for b in range(1, count + 1)}

    for row in sorted(world.settlements, key=lambda r: (r.settled_at, r.entity_id)):
        sliced[batch_for_date(row.settled_at)][0].append(row)
    for credit in sorted(bank, key=lambda r: (r.created_at, r.utr)):
        sliced[batch_for_date(credit.created_at)][1].append(credit)
    for order_id, meta in sorted(world.meta.items()):
        sliced[meta.ledger_batch][2].append(world.ledger[order_id])
    return sliced


def write_batches(world: World, bank: list[BankRow], out_dir: Path) -> dict[int, dict[str, int]]:
    """Write ``data/generated/batch_NN/``. Returns per-batch row counts."""
    sliced = slice_batches(world, bank)
    counts: dict[int, dict[str, int]] = {}

    for batch in sorted(sliced):
        settlements, credits, ledger = sliced[batch]
        if not settlements:
            raise ValueError(f"batch {batch} has no settlement rows")
        broken = malformed_rows(batch, settlements[0])
        folder = out_dir / f"batch_{batch:02d}"
        write_csv(folder / "settlement_report.csv", SETTLEMENT_COLUMNS, settlements, broken)
        write_csv(folder / "bank_statement.csv", BANK_COLUMNS, credits, [])
        write_csv(folder / "internal_ledger.csv", LEDGER_COLUMNS, ledger, [])
        counts[batch] = {
            "settlement_rows": len(settlements),
            "malformed_rows": len(broken),
            "bank_rows": len(credits),
            "ledger_rows": len(ledger),
        }
    return counts


# --------------------------------------------------------------------------- #
# Ground truth
# --------------------------------------------------------------------------- #


def write_truth(world: World, counts: dict[int, dict[str, int]], truth_dir: Path) -> None:
    """Write the answer key. Read only by /harness; never by /pipeline."""
    truth_dir.mkdir(parents=True, exist_ok=True)
    by_batch: dict[int, list[TruthEntry]] = {}
    for entry in world.truth:
        by_batch.setdefault(entry.batch, []).append(entry)

    manifest: dict[str, Any] = {"batches": {}, "recovery_pairs": [], "malformed_rows": {}}
    for batch in sorted(counts):
        entries = sorted(by_batch.get(batch, []), key=lambda e: (e.cause, e.affected_row_ids))
        payload = {
            "batch": batch,
            "row_counts": counts[batch],
            "injections": [entry.to_json() for entry in entries],
        }
        (truth_dir / f"batch_{batch:02d}.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        manifest["batches"][str(batch)] = {
            "row_counts": counts[batch],
            "injection_count": len(entries),
            "affected_rows": sum(len(e.affected_row_ids) for e in entries),
            "total_impact_inr": str(sum((e.true_impact_inr for e in entries), Decimal("0.00"))),
            "causes": sorted({e.cause for e in entries}),
        }
        manifest["malformed_rows"][str(batch)] = generation()["malformed_rows"].get(batch, [])

    for entry in world.truth:
        for recovery in entry.injector_params.get("recoveries", []):
            manifest["recovery_pairs"].append(
                {"cause": entry.cause, "claim_batch": entry.batch, **recovery}
            )

    (truth_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
