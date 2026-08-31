"""Reading the batches off disk. The only I/O in the reconciliation path.

Everything under ``pipeline/matcher/`` is a pure function, so all the file handling
lives here. Rows are validated into the models one at a time rather than a table at
a time: a whole-table parse fails on the first bad row and takes the good ones with
it, which is exactly the "lost rows" failure quarantine exists to prevent.

CSV is what platforms actually send, and a decimal written as text stays exactly the
decimal that was written. Nothing here goes through pandas: a pandas column of
``Decimal`` is an object column with float coercion one careless operation away, and
the money-is-never-a-float rule is worth more than the ergonomics.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from pipeline.config import REPO_ROOT
from pipeline.models import BankRow, LedgerRow, SettlementRow
from pipeline.matcher.quarantine import QuarantineRecord, classify

GENERATED_DIR = REPO_ROOT / "data" / "generated"

Row = TypeVar("Row", bound=BaseModel)


class BatchTables:
    """The three tables of one batch, plus whatever the models refused."""

    def __init__(
        self,
        batch: int,
        settlements: list[SettlementRow],
        bank: list[BankRow],
        ledger: list[LedgerRow],
        quarantined: list[QuarantineRecord],
    ) -> None:
        self.batch = batch
        self.settlements = settlements
        self.bank = bank
        self.ledger = ledger
        self.quarantined = quarantined

    @property
    def rows_read(self) -> int:
        return len(self.settlements) + len(self.bank) + len(self.ledger) + len(self.quarantined)


def read_csv(path: Path) -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def _blank_to_none(raw: dict[str, str], nullable: Sequence[str]) -> dict[str, Any]:
    data: dict[str, Any] = dict(raw)
    for key in nullable:
        if not data.get(key):
            data[key] = None
    return data


def prepare_settlement(raw: dict[str, str]) -> dict[str, Any]:
    """CSV text to model input. Empty nullable columns become ``None``, not ``''``."""
    data = _blank_to_none(raw, ("order_id", "dispute_id"))
    data["description"] = raw.get("description") or ""
    data["on_hold"] = raw.get("on_hold") or "false"
    return data


def prepare_ledger(raw: dict[str, str]) -> dict[str, Any]:
    return _blank_to_none(raw, ("resolution_reason",))


def prepare_bank(raw: dict[str, str]) -> dict[str, Any]:
    """The bank statement has no nullable columns; it goes through as read."""
    return dict(raw)


def _parse_table(
    path: Path,
    table: str,
    model: type[Row],
    prepare: Callable[[dict[str, str]], dict[str, Any]],
    id_column: str,
) -> tuple[list[Row], list[QuarantineRecord]]:
    """Validate every row. Failures are parked with a reason, never dropped.

    ``ValidationError`` is caught and named. Nothing else is: an ``OSError`` on the
    file or a missing column is a broken run, not a broken row, and swallowing it
    would report a clean reconciliation over data that was never read.
    """
    rows: list[Row] = []
    rejects: list[QuarantineRecord] = []
    for index, raw in enumerate(read_csv(path), start=1):
        try:
            rows.append(model.model_validate(prepare(raw)))
        except ValidationError as error:
            rejects.append(
                QuarantineRecord(
                    table=table,
                    row_id=raw.get(id_column) or f"{table}_line_{index}",
                    reason=classify(error.errors(), str(error)),
                    message="; ".join(
                        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors()
                    ),
                    raw=dict(raw),
                )
            )
    return rows, rejects


def load_batch(batch: int, generated_dir: Path | None = None) -> BatchTables:
    """Load one batch's three tables."""
    folder = (generated_dir or GENERATED_DIR) / f"batch_{batch:02d}"
    settlements, bad_settlements = _parse_table(
        folder / "settlement_report.csv", "settlement_report", SettlementRow,
        prepare_settlement, "entity_id",
    )
    bank, bad_bank = _parse_table(
        folder / "bank_statement.csv", "bank_statement", BankRow, prepare_bank, "utr",
    )
    ledger, bad_ledger = _parse_table(
        folder / "internal_ledger.csv", "internal_ledger", LedgerRow, prepare_ledger, "order_id",
    )
    return BatchTables(
        batch=batch,
        settlements=settlements,
        bank=bank,
        ledger=ledger,
        quarantined=bad_settlements + bad_bank + bad_ledger,
    )
