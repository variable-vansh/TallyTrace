"""Trouble injectors -- one per cause in the frozen enum.

Each takes the clean world and a count, mutates rows, and appends to the ground
truth. They are independent and composable: an injector never assumes another has
or has not run, and it never targets an order another injector already touched.

A ground-truth entry records what was done and what it was worth. It records no
claim about whether a matcher *should* catch it. That is the harness's finding.
"""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal
from typing import Any, Callable

from generator.base import fee_breakdown, payout_dates_in, snap_to_payout
from generator.money import ZERO, apply_rate, inr
from generator.world import OrderMeta, World, describe, net_of
from pipeline.config import batch_window, channel_config, channels, generation
from pipeline.models import (
    BankRow,
    BankStatus,
    Channel,
    LedgerRow,
    SettlementRow,
    TransactionType,
)

LAST_BATCH = int(generation()["batch_count"])


class GenerationError(RuntimeError):
    """The plan asked for something the world cannot supply. Never swallowed."""


# --------------------------------------------------------------------------- #
# Selection helpers
# --------------------------------------------------------------------------- #


def take_orders(
    world: World,
    rng: random.Random,
    batch: int,
    count: int,
    *,
    channel: str | None = None,
    refunded: bool = False,
) -> list[OrderMeta]:
    """Claim ``count`` untouched orders settling in ``batch``.

    Claimed orders are marked so no two injectors stack on the same order, which
    is what keeps each ground-truth entry attributable to exactly one cause.
    """
    pool = [
        meta
        for _, meta in sorted(world.meta.items())
        if meta.settle_batch == batch
        and not meta.troubled
        and meta.refunded == refunded
        and (channel is None or meta.channel == channel)
    ]
    if len(pool) < count:
        raise GenerationError(
            f"batch {batch}: need {count} untouched {channel or 'any'} orders, found {len(pool)}. "
            "Lower the count in config/generation.yaml or raise settlements_per_batch."
        )
    picked = rng.sample(pool, count)
    for meta in picked:
        meta.troubled = True
    return sorted(picked, key=lambda m: m.order_id)


def payment_of(world: World, meta: OrderMeta) -> SettlementRow:
    for row in world.settlements:
        if row.entity_id == meta.payment_entity_id:
            return row
    raise GenerationError(f"payment row for {meta.order_id} has gone missing")


def payout_date_in(rng: random.Random, batch: int, channel: str) -> Any:
    cfg = channel_config(channel)
    dates = payout_dates_in(batch_window(min(batch, LAST_BATCH)), cfg)
    if not dates:
        raise GenerationError(f"{channel} has no payout date inside batch {batch}")
    return rng.choice(dates)


def extra_row(
    world: World,
    meta: OrderMeta,
    rng: random.Random,
    *,
    batch: int,
    amount_inr: Decimal,
    direction: str,
    tx_type: TransactionType,
    description: str,
    fee: Decimal = ZERO,
    tax: Decimal = ZERO,
    tcs: Decimal = ZERO,
    tds: Decimal = ZERO,
) -> SettlementRow:
    """Emit one extra settlement row against an existing order.

    ``direction`` is ``out`` for money the platform takes back and ``in`` for money
    it pays. Deductions follow the channel's own sign convention -- negated amount
    on Amazon/Myntra/website, positive amount against the debit column on
    Flipkart/POS -- because that inconsistency is real and the matcher has to
    normalise it.
    """
    cfg = channel_config(meta.channel)
    settled = payout_date_in(rng, batch, meta.channel)
    if direction == "in":
        amount, debit, credit = amount_inr, ZERO, amount_inr
    elif cfg["refund_sign_convention"] == "negative_amount":
        amount, debit, credit = inr(-amount_inr), ZERO, inr(-amount_inr)
    else:
        amount, debit, credit = amount_inr, amount_inr, ZERO

    row = SettlementRow(
        entity_id=world.next_entity_id(),
        type=tx_type,
        channel=Channel(meta.channel),
        order_id=meta.order_id,
        amount=amount,
        fee=fee,
        tax=tax,
        tcs=tcs,
        tds=tds,
        debit=debit,
        credit=credit,
        settlement_id="",
        settlement_utr="",
        created_at=meta.created_at,
        settled_at=settled,
        description=description,
    )
    world.settlements.append(row)
    return row


def _reprice(ledger: LedgerRow, rate: Decimal, *, marketplace: bool) -> dict[str, Decimal]:
    """Restate the ledger's expectation at ``rate`` and return the parts."""
    parts = fee_breakdown(ledger.order_value, rate, marketplace=marketplace)
    ledger.expected_commission_rate = rate
    ledger.expected_fee = parts["fee"]
    ledger.expected_net = parts["net"]
    return parts


def _repay(row: SettlementRow, parts: dict[str, Decimal]) -> None:
    """Restate what the platform actually paid on a payment row."""
    row.fee = parts["fee"]
    row.tax = parts["tax"]
    row.credit = parts["net"]


# --------------------------------------------------------------------------- #
# internal_fix
# --------------------------------------------------------------------------- #


def commission_rate_stale(world: World, rng: random.Random, batch: int, count: int, params: dict) -> None:
    """The books still hold last quarter's rate; the platform charges the new one."""
    channel = params["channel"]
    ledger_rate, actual_rate = Decimal(params["ledger_rate"]), Decimal(params["actual_rate"])
    marketplace = bool(channel_config(channel)["marketplace"])
    impact, rows, orders = ZERO, [], []

    for meta in take_orders(world, rng, batch, count, channel=channel):
        ledger = world.ledger[meta.order_id]
        booked = _reprice(ledger, ledger_rate, marketplace=marketplace)
        charged = fee_breakdown(ledger.order_value, actual_rate, marketplace=marketplace)
        row = payment_of(world, meta)
        _repay(row, charged)
        impact += (charged["fee"] + charged["tax"]) - (booked["fee"] + booked["tax"])
        rows.append(row.entity_id)
        orders.append(meta.order_id)

    world.record(
        batch=batch, cause="commission_rate_stale", row_ids=rows, order_ids=orders, impact=impact,
        params={"channel": channel, "stale_rate": str(ledger_rate), "actual_rate": str(actual_rate)},
    )


def commission_slab_change(world: World, rng: random.Random, batch: int, count: int, params: dict) -> None:
    """The platform moved the item into a different commission slab."""
    channel = params["channel"]
    actual_rate = Decimal(params["actual_rate"])
    marketplace = bool(channel_config(channel)["marketplace"])
    impact, rows, orders = ZERO, [], []

    for meta in take_orders(world, rng, batch, count, channel=channel):
        ledger = world.ledger[meta.order_id]
        booked = fee_breakdown(ledger.order_value, ledger.expected_commission_rate, marketplace=marketplace)
        charged = fee_breakdown(ledger.order_value, actual_rate, marketplace=marketplace)
        row = payment_of(world, meta)
        _repay(row, charged)
        row.description = f"{row.description} SLAB REVISION {meta.category.upper()}"
        impact += (charged["fee"] + charged["tax"]) - (booked["fee"] + booked["tax"])
        rows.append(row.entity_id)
        orders.append(meta.order_id)

    world.record(
        batch=batch, cause="commission_slab_change", row_ids=rows, order_ids=orders, impact=impact,
        params={"channel": channel, "actual_rate": str(actual_rate)},
    )


def fee_mismatch_other(world: World, rng: random.Random, batch: int, count: int, params: dict) -> None:
    """A shipping / fulfilment / payment fee the ledger never expected."""
    channel = params["channel"]
    gst = Decimal(channels()["taxes"]["gst_on_commission_rate"])
    impact, rows, orders = ZERO, [], []

    for meta in take_orders(world, rng, batch, count, channel=channel):
        extra = inr(Decimal(rng.randint(35, 120)))
        extra_tax = apply_rate(extra, gst)
        row = payment_of(world, meta)
        row.fee = inr(row.fee + extra)
        row.tax = inr(row.tax + extra_tax)
        row.credit = inr(row.credit - extra - extra_tax)
        row.description = f"{row.description} SHIPPING FEE ADJ"
        impact += extra + extra_tax
        rows.append(row.entity_id)
        orders.append(meta.order_id)

    world.record(
        batch=batch, cause="fee_mismatch_other", row_ids=rows, order_ids=orders, impact=impact,
        params={"channel": channel},
    )


def rto_reversal_later_cycle(world: World, rng: random.Random, batch: int, count: int, params: dict) -> None:
    """A sale settles, then the return deduction arrives cycles later.

    The trouble is observable in the batch the reversal lands in, so that is the
    batch the ground truth records.
    """
    channel = params["channel"]
    lands_in = min(batch + int(params["reversal_batch_offset"]), LAST_BATCH)
    if lands_in <= batch:
        raise GenerationError(f"rto reversal for batch {batch} would not cross a cycle")
    impact, rows, orders = ZERO, [], []

    for meta in take_orders(world, rng, batch, count, channel=channel):
        ledger = world.ledger[meta.order_id]
        row = extra_row(
            world, meta, rng, batch=lands_in, amount_inr=ledger.expected_net, direction="out",
            tx_type=TransactionType.REFUND,
            description=describe(channel, "refund", rng, order_id=meta.order_id) + " RTO",
        )
        impact += ledger.expected_net
        rows.append(row.entity_id)
        orders.append(meta.order_id)

    world.record(
        batch=lands_in, cause="rto_reversal_later_cycle", row_ids=rows, order_ids=orders, impact=impact,
        params={"channel": channel, "sale_batch": batch, "reversal_batch": lands_in},
    )


def refund_timing_lag(world: World, rng: random.Random, batch: int, count: int, params: dict) -> None:
    """The books write the refund off now; the platform deducts it next cycle."""
    channel = params["channel"]
    lands_in = min(batch + int(params["refund_batch_offset"]), LAST_BATCH)
    if lands_in <= batch:
        raise GenerationError(f"refund lag for batch {batch} would not cross a cycle")
    impact, rows, orders = ZERO, [], []

    for meta in take_orders(world, rng, batch, count, channel=channel):
        ledger = world.ledger[meta.order_id]
        owed = ledger.expected_net
        row = extra_row(
            world, meta, rng, batch=lands_in, amount_inr=owed, direction="out",
            tx_type=TransactionType.REFUND,
            description=describe(channel, "refund", rng, order_id=meta.order_id),
        )
        # The ledger already treats the order as fully reversed.
        ledger.expected_fee = ZERO
        ledger.expected_net = ZERO
        meta.refunded = True
        impact += owed
        rows.append(row.entity_id)
        orders.append(meta.order_id)

    world.record(
        batch=batch, cause="refund_timing_lag", row_ids=rows, order_ids=orders, impact=impact,
        params={"channel": channel, "booked_batch": batch, "deducted_batch": lands_in},
    )


def settlement_lag_crossing_batch(world: World, rng: random.Random, batch: int, count: int, params: dict) -> None:
    """The payout slips past the normal window into a later batch."""
    channel = params["channel"]
    cfg = channel_config(channel)
    extra_days = int(params["extra_lag_days"])
    impact, rows, orders = ZERO, [], []

    for meta in take_orders(world, rng, batch, count, channel=channel):
        row = payment_of(world, meta)
        moved = snap_to_payout(row.settled_at + timedelta(days=extra_days), cfg)
        if moved > batch_window(LAST_BATCH)[1]:
            raise GenerationError(
                f"batch {batch}: extra_lag_days={extra_days} pushes a settlement past the corpus"
            )
        row.settled_at = moved
        meta.settled_at = moved
        from pipeline.config import batch_for_date

        meta.settle_batch = batch_for_date(moved)
        impact += world.ledger[meta.order_id].expected_net
        rows.append(row.entity_id)
        orders.append(meta.order_id)

    world.record(
        batch=batch, cause="settlement_lag_crossing_batch", row_ids=rows, order_ids=orders, impact=impact,
        params={"channel": channel, "extra_lag_days": extra_days},
    )


def rounding_variance(world: World, rng: random.Random, batch: int, count: int, params: dict) -> None:
    """Paise-level drift. Inside tolerance, so it should cost nobody any attention."""
    impact, rows, orders = ZERO, [], []
    for meta in take_orders(world, rng, batch, count):
        drift = inr(Decimal(rng.randint(1, 99)) / Decimal(100))
        row = payment_of(world, meta)
        row.fee = inr(row.fee + drift)
        row.credit = inr(row.credit - drift)
        impact += drift
        rows.append(row.entity_id)
        orders.append(meta.order_id)

    world.record(
        batch=batch, cause="rounding_variance", row_ids=rows, order_ids=orders, impact=impact, params={},
    )


def duplicate_settlement_row(world: World, rng: random.Random, batch: int, count: int, params: dict) -> None:
    """The report emits the same transaction twice; the bank funded it once."""
    channel = params["channel"]
    impact, rows, orders = ZERO, [], []

    for meta in take_orders(world, rng, batch, count, channel=channel):
        original = payment_of(world, meta)
        clone = original.model_copy(deep=True)
        clone.entity_id = world.next_entity_id()
        world.settlements.append(clone)
        # The bank never funded the phantom, which is what makes the payout stop
        # tying out against the settlement group.
        world.bank_excluded_entity_ids.add(clone.entity_id)
        impact += net_of(clone)
        rows.append(clone.entity_id)
        orders.append(meta.order_id)

    world.record(
        batch=batch, cause="duplicate_settlement_row", row_ids=rows, order_ids=orders, impact=impact,
        params={"channel": channel},
    )


# --------------------------------------------------------------------------- #
# tax_review
# --------------------------------------------------------------------------- #


def _defer_tax(
    world: World, rng: random.Random, batch: int, count: int, params: dict, *, field: str, cause: str
) -> None:
    channel = params["channel"]
    if not channel_config(channel)["marketplace"]:
        raise GenerationError(f"{cause} needs a marketplace channel, got {channel}")
    lands_in = min(batch + int(params["defer_batches"]), LAST_BATCH)
    if lands_in <= batch:
        raise GenerationError(f"{cause} for batch {batch} would not cross a cycle")
    impact, rows, orders = ZERO, [], []

    for meta in take_orders(world, rng, batch, count, channel=channel):
        row = payment_of(world, meta)
        held = getattr(row, field)
        if held <= ZERO:
            raise GenerationError(f"{meta.order_id} carries no {field} to defer")
        # Paid out now, collected in a later cycle.
        setattr(row, field, ZERO)
        row.credit = inr(row.credit + held)
        later = extra_row(
            world, meta, rng, batch=lands_in, amount_inr=held, direction="out",
            tx_type=TransactionType.ADJUSTMENT,
            description=f"{field.upper()} RECOVERY {meta.order_id}",
            **{field: held},
        )
        impact += held
        rows.extend([row.entity_id, later.entity_id])
        orders.append(meta.order_id)

    world.record(
        batch=batch, cause=cause, row_ids=rows, order_ids=orders, impact=impact,
        params={"channel": channel, "collected_batch": lands_in},
    )


def tcs_timing_mismatch(world: World, rng: random.Random, batch: int, count: int, params: dict) -> None:
    _defer_tax(world, rng, batch, count, params, field="tcs", cause="tcs_timing_mismatch")


def tds_timing_mismatch(world: World, rng: random.Random, batch: int, count: int, params: dict) -> None:
    _defer_tax(world, rng, batch, count, params, field="tds", cause="tds_timing_mismatch")


# --------------------------------------------------------------------------- #
# counterparty_claim
# --------------------------------------------------------------------------- #


def weight_dispute_hold(world: World, rng: random.Random, batch: int, count: int, params: dict) -> None:
    """Reported as sold, paid as nothing, pending a weight dispute. Held, not lost."""
    channel = params["channel"]
    impact, rows, orders = ZERO, [], []

    for index, meta in enumerate(take_orders(world, rng, batch, count, channel=channel), start=1):
        row = payment_of(world, meta)
        withheld = row.credit
        row.credit = ZERO
        row.on_hold = True
        row.dispute_id = f"WD-{batch:02d}{index:03d}"
        row.description = f"{row.description} WEIGHT DISCREPANCY HOLD"
        impact += withheld
        rows.append(row.entity_id)
        orders.append(meta.order_id)

    world.record(
        batch=batch, cause="weight_dispute_hold", row_ids=rows, order_ids=orders, impact=impact,
        params={"channel": channel},
    )


def _plant_recovery(
    world: World, rng: random.Random, meta: OrderMeta, amount: Decimal, batch: int, offset: int
) -> tuple[str, int]:
    """Plant the credit that closes a claim, in a later batch."""
    lands_in = min(batch + offset, LAST_BATCH)
    if lands_in <= batch:
        raise GenerationError(f"recovery for batch {batch} would land in the same batch")
    row = extra_row(
        world, meta, rng, batch=lands_in, amount_inr=amount, direction="in",
        tx_type=TransactionType.ADJUSTMENT,
        description=f"CLAIM REIMBURSEMENT {meta.order_id}",
    )
    return row.entity_id, lands_in


def missing_settlement_row(world: World, rng: random.Random, batch: int, count: int, params: dict) -> None:
    """The order is in the books and simply absent from the settlement report."""
    channel = params["channel"]
    recoveries = int(params.get("recoveries", 0))
    impact, rows, orders, planted = ZERO, [], [], []

    for index, meta in enumerate(take_orders(world, rng, batch, count, channel=channel)):
        row = payment_of(world, meta)
        owed = world.ledger[meta.order_id].expected_net
        world.settlements.remove(row)
        impact += owed
        rows.append(row.entity_id)
        orders.append(meta.order_id)
        if index < recoveries:
            entity_id, lands_in = _plant_recovery(
                world, rng, meta, owed, batch, int(params["recovery_batch_offset"])
            )
            planted.append({"order_id": meta.order_id, "row_id": entity_id, "batch": lands_in,
                            "amount_inr": str(owed)})

    world.record(
        batch=batch, cause="missing_settlement_row", row_ids=rows, order_ids=orders, impact=impact,
        params={"channel": channel, "rows_removed": True, "recoveries": planted},
    )


def short_payment_unexplained(world: World, rng: random.Random, batch: int, count: int, params: dict) -> None:
    """Net comes up short with nothing in the report that explains it."""
    channel = params["channel"]
    pct = Decimal(params["shortfall_pct"])
    recoveries = int(params.get("recoveries", 0))
    impact, rows, orders, planted = ZERO, [], [], []

    for index, meta in enumerate(take_orders(world, rng, batch, count, channel=channel)):
        row = payment_of(world, meta)
        shortfall = apply_rate(row.credit, pct)
        row.credit = inr(row.credit - shortfall)
        impact += shortfall
        rows.append(row.entity_id)
        orders.append(meta.order_id)
        if index < recoveries:
            entity_id, lands_in = _plant_recovery(
                world, rng, meta, shortfall, batch, int(params["recovery_batch_offset"])
            )
            planted.append({"order_id": meta.order_id, "row_id": entity_id, "batch": lands_in,
                            "amount_inr": str(shortfall)})

    world.record(
        batch=batch, cause="short_payment_unexplained", row_ids=rows, order_ids=orders, impact=impact,
        params={"channel": channel, "shortfall_pct": str(pct), "recoveries": planted},
    )


def chargeback_deduction(world: World, rng: random.Random, batch: int, count: int, params: dict) -> None:
    """A dispute deduction. Held out of the data until batch 9."""
    channel = params["channel"]
    impact, rows, orders = ZERO, [], []

    for index, meta in enumerate(take_orders(world, rng, batch, count, channel=channel), start=1):
        ledger = world.ledger[meta.order_id]
        deduction = inr(ledger.expected_net)
        row = extra_row(
            world, meta, rng, batch=batch, amount_inr=deduction, direction="out",
            tx_type=TransactionType.ADJUSTMENT,
            description=f"CHARGEBACK DEBIT {meta.order_id}",
        )
        row.dispute_id = f"CB-{batch:02d}{index:03d}"
        impact += deduction
        rows.append(row.entity_id)
        orders.append(meta.order_id)

    world.record(
        batch=batch, cause="chargeback_deduction", row_ids=rows, order_ids=orders, impact=impact,
        params={"channel": channel},
    )


def promo_cofunding_deduction(world: World, rng: random.Random, batch: int, count: int, params: dict) -> None:
    """The platform shares a promo cost by quietly widening the fee line.

    Held out of the data until batch 7. Nothing in the description announces it,
    which is the point: it looks like a fee movement and is not one.
    """
    channel = params["channel"]
    pct = Decimal(params["deduction_pct"])
    impact, rows, orders = ZERO, [], []

    for meta in take_orders(world, rng, batch, count, channel=channel):
        ledger = world.ledger[meta.order_id]
        deduction = apply_rate(ledger.order_value, pct)
        row = payment_of(world, meta)
        row.fee = inr(row.fee + deduction)
        row.credit = inr(row.credit - deduction)
        impact += deduction
        rows.append(row.entity_id)
        orders.append(meta.order_id)

    world.record(
        batch=batch, cause="promo_cofunding_deduction", row_ids=rows, order_ids=orders, impact=impact,
        params={"channel": channel, "deduction_pct": str(pct)},
    )


def near_miss_fee_variance(world: World, rng: random.Random, batch: int, count: int, params: dict) -> None:
    """The most valuable rows in the dataset.

    Surface signature identical to ``commission_rate_stale`` on the same channel --
    same ledger rate, same charged rate, same variance band -- but the money was
    never a commission. The platform short-paid and buried it in the fee line, so
    the true cause is ``short_payment_unexplained`` and the true routing is the
    claims queue, not the learning loop. A rule induced from the stale-rate
    exceptions will fire on these, and it will be wrong.
    """
    channel = params["channel"]
    ledger_rate, actual_rate = Decimal(params["ledger_rate"]), Decimal(params["actual_rate"])
    marketplace = bool(channel_config(channel)["marketplace"])
    impact, rows, orders = ZERO, [], []

    # Repriced to the same pair of rates the stale-rate injector uses, so the
    # variance lands in the same band rather than merely near it. Left to the
    # order's own category rate, a high-commission item would come out negative
    # and give the row away.
    for meta in take_orders(world, rng, batch, count, channel=channel):
        ledger = world.ledger[meta.order_id]
        booked = _reprice(ledger, ledger_rate, marketplace=marketplace)
        charged = fee_breakdown(ledger.order_value, actual_rate, marketplace=marketplace)
        row = payment_of(world, meta)
        _repay(row, charged)
        impact += (charged["fee"] + charged["tax"]) - (booked["fee"] + booked["tax"])
        rows.append(row.entity_id)
        orders.append(meta.order_id)

    world.record(
        batch=batch, cause="short_payment_unexplained", row_ids=rows, order_ids=orders, impact=impact,
        params={"channel": channel, "near_miss": True, "looks_like": "commission_rate_stale",
                "stale_rate": str(ledger_rate), "charged_rate": str(actual_rate)},
    )


# --------------------------------------------------------------------------- #
# investigate
# --------------------------------------------------------------------------- #


def bank_credit_unmatched(world: World, rng: random.Random, batch: int, count: int, params: dict) -> None:
    """A credit lands with nothing in any settlement report to explain it."""
    start, end = batch_window(batch)
    impact, rows = ZERO, []

    for index in range(count):
        amount = inr(Decimal(rng.randint(4_000, 42_000)))
        utr = f"UNKN{batch:02d}{index:03d}{rng.randint(10_000, 99_999)}"
        # One of these arrives and is pulled back, which is what the reversed
        # status on a bank row is for.
        status = BankStatus.REVERSED if rng.randint(1, 4) == 1 else BankStatus.PROCESSED
        world.extra_bank_rows.append(
            BankRow(
                utr=utr,
                amount=amount,
                created_at=start + timedelta(days=rng.randint(0, (end - start).days)),
                status=status,
            )
        )
        impact += amount
        rows.append(utr)

    world.record(
        batch=batch, cause="bank_credit_unmatched", row_ids=rows, order_ids=[], impact=impact, params={},
    )


INJECTORS: dict[str, Callable[[World, random.Random, int, int, dict], None]] = {
    "commission_rate_stale": commission_rate_stale,
    "commission_slab_change": commission_slab_change,
    "fee_mismatch_other": fee_mismatch_other,
    "rto_reversal_later_cycle": rto_reversal_later_cycle,
    "refund_timing_lag": refund_timing_lag,
    "settlement_lag_crossing_batch": settlement_lag_crossing_batch,
    "rounding_variance": rounding_variance,
    "duplicate_settlement_row": duplicate_settlement_row,
    "tcs_timing_mismatch": tcs_timing_mismatch,
    "tds_timing_mismatch": tds_timing_mismatch,
    "weight_dispute_hold": weight_dispute_hold,
    "missing_settlement_row": missing_settlement_row,
    "short_payment_unexplained": short_payment_unexplained,
    "chargeback_deduction": chargeback_deduction,
    "promo_cofunding_deduction": promo_cofunding_deduction,
    "near_miss_fee_variance": near_miss_fee_variance,
    "bank_credit_unmatched": bank_credit_unmatched,
}


def run_plan(world: World, rng: random.Random) -> None:
    """Apply the configured injection plan, batch by batch, in file order."""
    plan = generation()["injections"]
    for batch in sorted(plan):
        for entry in plan[batch]:
            name = entry["injector"]
            if name not in INJECTORS:
                raise GenerationError(f"unknown injector {name!r}; the cause enum is frozen")
            INJECTORS[name](world, rng, int(batch), int(entry["count"]), dict(entry.get("params") or {}))
