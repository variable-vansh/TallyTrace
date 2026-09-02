"""The reconciled corpus, in the one shape every metric reads.

Built once from a completed run and handed to the registry as data. Every metric is
a pure function over this object -- no I/O, no re-reading the CSVs, no second pass.
That matters for one specific reason: the rows the registry computes over have to be
the same rows the matcher bucketed, or the dashboard and the harness will quietly
disagree about how many settlement rows batch 7 had.

Money is normalised the way the matcher normalises it, through
``pipeline/matcher/normalise.py``, so a channel that writes a reversal as a negative
amount and one that writes it in the debit column are added up the same way. Nothing
here re-implements a sign convention.

The one number worth naming: **gross order value** is the ledger's ``order_value``
for the orders *this batch's settlement rows settle*, not for the orders this batch's
ledger booked. Those are different sets and the difference is not small -- a batch is
a settlement report, so an order booked in week three and paid in week five is in one
batch's ledger and a different batch's settlement rows.

Taking the denominator off the ledger file was the first version of this module and
it produced an effective take rate that climbed from five percent to eighty-six
across the corpus, because batch ten settles a great deal and books almost nothing.
A ratio whose numerator and denominator are drawn from different populations is not a
rate. So the denominator is looked up per settling order, deduplicated -- an order
that emits a payment and a refund in one batch contributes its value once.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from pipeline.claims.models import Claim
from pipeline.matcher.normalise import charged_fee, inr, normalise_all, total_net
from pipeline.models import LedgerRow, SettlementRow

#: order_id -> what the customer paid for it, from the seller's own books.
OrderValues = dict[str, Decimal]

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class BatchFacts:
    """One batch's money, already aggregated by channel.

    Aggregated at construction rather than on every metric call: eight metrics over
    ten batches would otherwise re-normalise the same twelve hundred rows eighty
    times to produce the same sums.
    """

    batch: int
    gross_order_value: dict[str, Decimal]     # from the ledger
    net_settled: dict[str, Decimal]           # what reached the bank, per channel
    fees_charged: dict[str, Decimal]          # platform + fulfilment fee kept
    taxes_withheld: dict[str, Decimal]        # GST on fee + TCS + TDS
    orders: dict[str, int]                    # distinct orders settled, per channel
    settlement_rows: dict[str, int]

    def total(self, field: dict[str, Decimal], channel: str | None) -> Decimal:
        if channel is not None:
            return field.get(channel, ZERO)
        return inr(sum(field.values(), ZERO))


def _by_channel(rows: Iterable[SettlementRow]) -> dict[str, list[SettlementRow]]:
    grouped: dict[str, list[SettlementRow]] = {}
    for row in rows:
        grouped.setdefault(row.channel.value, []).append(row)
    return grouped


def order_values(ledgers: Iterable[Iterable[LedgerRow]]) -> OrderValues:
    """Every order the books have ever held, and what the customer paid for it.

    Built across the whole corpus before any batch is aggregated, because the order a
    settlement row belongs to was very likely booked in an earlier batch's file.
    """
    values: OrderValues = {}
    for ledger in ledgers:
        for row in ledger:
            values[row.order_id] = row.order_value
    return values


def _gross_settled(
    rows: list[SettlementRow], values: OrderValues
) -> tuple[Decimal, int]:
    """Gross value of the distinct orders these rows settle, and how many there were.

    Deduplicated by order: a sale and its reversal in the same batch are two rows and
    one sale. An order with no ledger row -- a settlement line for something the books
    never had -- contributes nothing, because there is no sale value to attribute.
    """
    seen: dict[str, Decimal] = {}
    for row in rows:
        if row.order_id and row.order_id in values:
            seen[row.order_id] = values[row.order_id]
    return inr(sum(seen.values(), ZERO)), len(seen)


def facts_for(
    batch: int, settlements: list[SettlementRow], values: OrderValues
) -> BatchFacts:
    """Aggregate one batch's settlement rows into the sums every metric is built from."""
    settle = _by_channel(settlements)

    gross: dict[str, Decimal] = {}
    net: dict[str, Decimal] = {}
    fees: dict[str, Decimal] = {}
    taxes: dict[str, Decimal] = {}
    orders: dict[str, int] = {}
    counts: dict[str, int] = {}
    for channel, rows in settle.items():
        normalised = normalise_all(rows)
        gross[channel], orders[channel] = _gross_settled(rows, values)
        net[channel] = total_net(normalised)
        fees[channel] = charged_fee(normalised)
        taxes[channel] = inr(
            sum((abs(row.tax) + abs(row.tcs) + abs(row.tds) for row in rows), ZERO)
        )
        counts[channel] = len(rows)

    return BatchFacts(
        batch=batch,
        gross_order_value=gross,
        net_settled=net,
        fees_charged=fees,
        taxes_withheld=taxes,
        orders=orders,
        settlement_rows=counts,
    )


@dataclass(frozen=True)
class BatchQueue:
    """One batch's exception counts, the numbers the review-rate metrics read."""

    batch: int
    settlement_rows: int
    flagged_rows: int
    auto_resolved_rows: int
    cases_by_cause: dict[str, int]


@dataclass(frozen=True)
class Corpus:
    """Everything the registry may compute over. Immutable, and it holds no file handles."""

    facts: tuple[BatchFacts, ...]
    queues: tuple[BatchQueue, ...]
    claims: tuple[Claim, ...]

    @property
    def batches(self) -> tuple[int, ...]:
        return tuple(entry.batch for entry in self.facts)

    @property
    def channels(self) -> tuple[str, ...]:
        seen = {channel for entry in self.facts for channel in entry.gross_order_value}
        return tuple(sorted(seen))

    def window(self, from_batch: int | None, to_batch: int | None) -> "Corpus":
        """A narrower corpus. Metrics take date ranges as batch ranges; a batch is a week."""
        low = from_batch or min(self.batches, default=1)
        high = to_batch or max(self.batches, default=1)
        return Corpus(
            facts=tuple(f for f in self.facts if low <= f.batch <= high),
            queues=tuple(q for q in self.queues if low <= q.batch <= high),
            claims=tuple(c for c in self.claims if low <= c.opened_batch <= high),
        )
