"""Order-level matching, and the date window that keeps lag from reading as loss.

``settlement_report.order_id`` to ``internal_ledger.order_id``, exact key. No fuzzy
matching, no scoring, no second-best candidate -- see the README.

The date window is the whole reason this module is more than a dictionary lookup. A
sale booked this week and paid three weeks from now is not missing money, it is a
settlement cycle. So an order with no settlement row is only an exception once the
channel's window has actually elapsed; before that it is carried forward, and the
reason code says so.

The ledger carries no order date -- the brief's schema has ``order_value`` and rates
but nothing temporal -- so the window is measured from the end of the batch the
order was *booked* in. That is the latest date the order could have been created,
which makes the check conservative in the right direction: it will call a settlement
late rather than call a normal cycle missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from pipeline.matcher.normalise import NormalisedRow, ZERO
from pipeline.matcher.reasons import Bucket, Reason
from pipeline.matcher.settings import MatchConfig
from pipeline.matcher.variance import ValueCheck, check_value
from pipeline.models import LedgerRow


@dataclass(frozen=True)
class OpenOrder:
    """A booked order still awaiting reconciliation, with the week it was booked in."""

    ledger: LedgerRow
    booked_batch: int
    booked_window_end: date

    @property
    def order_id(self) -> str:
        return self.ledger.order_id


@dataclass(frozen=True)
class OrderFinding:
    """One order's verdict, shared by its ledger row and its settlement rows."""

    order_id: str
    channel: str
    bucket: Bucket
    reason: Reason
    impact: Decimal
    row_ids: list[str] = field(default_factory=list)
    value: ValueCheck | None = None
    days_late: int = 0
    due_by: date | None = None
    settled_on: date | None = None   # latest payout date among this order's rows
    settled: bool = False            # closed by this run; the runner stops carrying it


def due_by(order: OpenOrder, cfg: MatchConfig) -> date:
    """The date after which a missing settlement stops being a normal cycle."""
    return order.booked_window_end + timedelta(days=cfg.date_window_days)


def settlement_delay_days(rows: list[NormalisedRow], cfg: MatchConfig) -> int:
    """How far past the window the slowest of these rows settled. Zero if none did.

    Measured on the rows themselves, from ``created_at`` to ``settled_at``, so this
    is the exact figure rather than the batch-level approximation ``due_by`` has to
    use for an order that has not settled at all.

    A payout that arrives late but correct is not a clean match. The money is right
    and the cash was not where the books said it would be, which is a real finding
    to a bookkeeper and -- because the delay is systematic per channel -- a
    learnable one. Reporting it costs nothing: no clean row in the corpus exceeds
    the window, because the window is set above every channel's own stated lag.
    """
    delays = [(row.row.settled_at - row.row.created_at).days for row in rows]
    return max(max(delays, default=0) - cfg.date_window_days, 0)


def _unsettled(order: OpenOrder, batch_end: date, cfg: MatchConfig) -> OrderFinding:
    """No settlement row this batch: still inside the window, or genuinely overdue."""
    deadline = due_by(order, cfg)
    overdue = batch_end > deadline
    return OrderFinding(
        order_id=order.order_id,
        channel=order.ledger.channel.value,
        bucket=Bucket.UNMATCHED,
        reason=Reason.SETTLEMENT_OVERDUE if overdue else Reason.AWAITING_SETTLEMENT_IN_WINDOW,
        impact=order.ledger.expected_net if overdue else ZERO,
        due_by=deadline,
    )


def match_orders(
    open_orders: list[OpenOrder],
    rows_by_order: dict[str, list[NormalisedRow]],
    batch_end: date,
    cfg: MatchConfig,
) -> list[OrderFinding]:
    """Join every open order to its settlement rows in this batch.

    ``rows_by_order`` holds only rows the bank actually funded; a row the payout
    never covered is not money the platform paid, so it is excluded upstream and
    reported on its own rather than double-counted into the order's net.
    """
    findings: list[OrderFinding] = []
    for order in sorted(open_orders, key=lambda o: o.order_id):
        rows = rows_by_order.get(order.order_id, [])
        if not rows:
            findings.append(_unsettled(order, batch_end, cfg))
            continue
        findings.append(_settled(order, rows, cfg))
    return findings


def _settled(order: OpenOrder, rows: list[NormalisedRow], cfg: MatchConfig) -> OrderFinding:
    """An order with settlement rows this batch: check the money, then the clock."""
    value = check_value(order.ledger, rows, cfg)
    late = settlement_delay_days(rows, cfg)
    bucket, reason = value.bucket, value.reason
    if bucket is Bucket.MATCHED and late:
        bucket, reason = Bucket.VARIANCE, Reason.SETTLEMENT_OUTSIDE_DATE_WINDOW
    return OrderFinding(
        order_id=order.order_id,
        channel=order.ledger.channel.value,
        bucket=bucket,
        reason=reason,
        impact=value.impact,
        row_ids=sorted(row.entity_id for row in rows),
        value=value,
        days_late=late,
        settled_on=max(row.row.settled_at for row in rows),
        settled=True,
    )
