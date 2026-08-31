"""The synthetic world in flight, plus the ground-truth record.

The generator builds a clean, fully reconciling world, hands it to the injectors,
then calls :func:`finalise` to assign payout groupings and derive the bank
statement. Deriving the bank statement *after* injection is what keeps the bank
side honest: a credit is whatever the settlement rows actually add up to, unless an
injector deliberately says otherwise.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from generator.money import ZERO, inr
from pipeline.config import channel_config, resolution_class_by_cause
from pipeline.models import BankRow, BankStatus, Channel, LedgerRow, SettlementRow


@dataclass
class OrderMeta:
    """Generator-side bookkeeping that never reaches the emitted files."""

    order_id: str
    channel: str
    category: str
    commission_rate: Decimal
    created_at: date
    settled_at: date
    ledger_batch: int               # batch whose ledger file carries the order
    settle_batch: int               # batch whose settlement file carries the payment
    refunded: bool
    troubled: bool = False          # already carries an injected trouble
    payment_entity_id: str = ""


@dataclass
class TruthEntry:
    """One injected trouble.

    Records what was done and what it was worth. It records *no* claim about
    whether a matcher ought to catch it -- that is the harness's finding, not the
    dataset's assertion.
    """

    batch: int
    cause: str
    affected_row_ids: list[str]
    affected_order_ids: list[str]
    true_impact_inr: Decimal
    resolution_class: str
    injector_params: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "batch": self.batch,
            "cause": self.cause,
            "affected_row_ids": self.affected_row_ids,
            "affected_order_ids": self.affected_order_ids,
            "true_impact_inr": str(inr(self.true_impact_inr)),
            "resolution_class": self.resolution_class,
            "injector_params": _jsonable(self.injector_params),
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


@dataclass
class World:
    settlements: list[SettlementRow] = field(default_factory=list)
    ledger: dict[str, LedgerRow] = field(default_factory=dict)
    meta: dict[str, OrderMeta] = field(default_factory=dict)
    truth: list[TruthEntry] = field(default_factory=list)

    # Rows the platform reported but the bank never funded (duplicates, holds
    # already netted out elsewhere). Excluded when the bank statement is derived.
    bank_excluded_entity_ids: set[str] = field(default_factory=set)
    # Credits that arrived with no settlement counterpart at all.
    extra_bank_rows: list[BankRow] = field(default_factory=list)

    _entity_seq: int = 0

    def next_entity_id(self) -> str:
        self._entity_seq += 1
        return f"st_{self._entity_seq:06d}"

    def record(
        self,
        *,
        batch: int,
        cause: str,
        row_ids: list[str],
        order_ids: list[str],
        impact: Decimal,
        params: dict[str, Any],
    ) -> TruthEntry:
        entry = TruthEntry(
            batch=batch,
            cause=cause,
            affected_row_ids=sorted(row_ids),
            affected_order_ids=sorted(order_ids),
            true_impact_inr=inr(impact),
            resolution_class=resolution_class_by_cause()[cause],
            injector_params=params,
        )
        self.truth.append(entry)
        return entry

    def rows_for_order(self, order_id: str) -> list[SettlementRow]:
        return [row for row in self.settlements if row.order_id == order_id]


def net_of(row: SettlementRow) -> Decimal:
    """Signed rupees this row contributes to a bank payout."""
    return inr(row.credit - row.debit)


def finalise(world: World) -> list[BankRow]:
    """Assign payout groupings, then derive the bank statement from them.

    Payouts group by (channel, settled_at): that is how platforms actually batch a
    cycle's rows into one NEFT. Grouping happens after injection because injectors
    move ``settled_at`` around, and a moved row belongs in the payout it actually
    landed in.
    """
    groups: dict[tuple[str, date], list[SettlementRow]] = {}
    for row in world.settlements:
        groups.setdefault((row.channel.value, row.settled_at), []).append(row)

    bank: list[BankRow] = []
    for index, key in enumerate(sorted(groups), start=1):
        channel, settled_at = key
        cfg = channel_config(channel)
        settlement_id = f"{cfg['settlement_id_prefix']}-{settled_at:%Y%m%d}-{index:04d}"
        utr = f"{cfg['utr_prefix']}{settled_at:%y%m%d}{index:05d}"

        funded = ZERO
        for row in groups[key]:
            row.settlement_id = settlement_id
            row.settlement_utr = utr
            if row.entity_id not in world.bank_excluded_entity_ids:
                funded += net_of(row)

        funded = inr(funded)
        if funded != ZERO:
            bank.append(BankRow(utr=utr, amount=funded, created_at=settled_at, status=BankStatus.PROCESSED))

    bank.extend(world.extra_bank_rows)
    bank.sort(key=lambda r: (r.created_at, r.utr))
    return bank


def describe(
    channel: str, kind: str, rng: random.Random, *, order_id: str = "", settlement_id: str = ""
) -> str:
    """Pick a description in the shape the platform actually emits.

    ``ref`` is the bare numeric part of the order id, for the channels that print
    their own reference format (Flipkart's OD..., a POS terminal's TID...).
    """
    templates: list[str] = channel_config(channel)["descriptions"][kind]
    ref = order_id.rsplit("_", 1)[-1] if order_id else ""
    return rng.choice(templates).format(order_id=order_id, settlement_id=settlement_id, ref=ref)


CHANNEL_VALUES: list[str] = [c.value for c in Channel]
