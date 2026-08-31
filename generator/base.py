"""Clean base generation.

Builds a fully consistent world: every order has a settlement, every settlement row
rolls up into a bank credit, every fee equals the ledger's expectation. Injection
happens afterwards, on top of this. If the clean base does not reconcile at 100%,
every number produced later is meaningless -- see ``generator/verify_clean.py``.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from generator.money import ZERO, apply_rate, inr
from generator.world import OrderMeta, World, describe
from pipeline.config import batch_for_date, batch_window, channel_config, channels, generation
from pipeline.models import Channel, LedgerRow, LedgerStatus, SettlementRow, TransactionType


def channel_pool() -> list[str]:
    """Channel draw pool, sized by the configured volume mix.

    A pool of repeated names rather than weighted sampling: it keeps the draw
    integer-only and therefore reproducible across Python versions.
    """
    mix = channels()["channel_mix"]
    pool: list[str] = []
    for name in sorted(mix):
        pool.extend([name] * int(Decimal(mix[name]) * 1000))
    if not pool:
        raise ValueError("channel_mix produced an empty pool")
    return pool


def order_value(rng: random.Random, cfg: dict) -> Decimal:
    """A plausible apparel ticket, occasionally with paise on it."""
    low, high = (int(x) for x in cfg["order_value_inr"])
    rupees = Decimal(rng.randint(low, high))
    if rng.randint(1, 100) <= 12:
        rupees += Decimal(rng.choice(["0.50", "0.99", "0.25"]))
    return inr(rupees)


def fee_breakdown(value: Decimal, rate: Decimal, *, marketplace: bool) -> dict[str, Decimal]:
    """Commission, GST on commission, TCS and TDS for one order."""
    tax_cfg = channels()["taxes"]
    fee = apply_rate(value, Decimal(rate))
    tax = apply_rate(fee, Decimal(tax_cfg["gst_on_commission_rate"]))
    tcs = apply_rate(value, Decimal(tax_cfg["tcs_rate"])) if marketplace else ZERO
    tds = apply_rate(value, Decimal(tax_cfg["tds_rate"])) if marketplace else ZERO
    net = inr(value - fee - tax - tcs - tds)
    return {"fee": fee, "tax": tax, "tcs": tcs, "tds": tds, "net": net}


def snap_to_payout(due: date, cfg: dict) -> date:
    """Move a due date onto the channel's next actual payout run.

    Marketplaces pay out on a fixed weekday, so a cycle's rows share one payout
    date and therefore one bank credit -- that is where the N:1 join comes from.
    Gateways settle daily, so their payouts stay small.
    """
    if cfg["payout_cadence"] == "daily":
        return due
    weekday = int(cfg["payout_weekday"])
    return due + timedelta(days=(weekday - due.weekday()) % 7)


def payout_dates_in(window: tuple[date, date], cfg: dict) -> list[date]:
    """The channel's payout dates falling inside a batch window."""
    start, end = window
    days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    if cfg["payout_cadence"] == "daily":
        return days
    return [day for day in days if day.weekday() == int(cfg["payout_weekday"])]


def _dates(rng: random.Random, cfg: dict, window: tuple[date, date]) -> tuple[date, date]:
    """Draw a payout date inside the window, then work the order date backwards.

    Generating from the settlement side rather than the order side is what makes
    each batch's size exact, and it produces the opening book -- orders created
    before batch 1 -- without a separate warm-up pass.
    """
    settled = rng.choice(payout_dates_in(window, cfg))
    lag_lo, lag_hi = (int(x) for x in cfg["settlement_lag_days"])
    return settled - timedelta(days=rng.randint(lag_lo, lag_hi)), settled


def payment_row(
    world: World, meta: OrderMeta, value: Decimal, parts: dict[str, Decimal], rng: random.Random
) -> SettlementRow:
    row = SettlementRow(
        entity_id=world.next_entity_id(),
        type=TransactionType.PAYMENT,
        channel=Channel(meta.channel),
        order_id=meta.order_id,
        amount=value,
        fee=parts["fee"],
        tax=parts["tax"],
        tcs=parts["tcs"],
        tds=parts["tds"],
        debit=ZERO,
        credit=parts["net"],
        settlement_id="",
        settlement_utr="",
        created_at=meta.created_at,
        settled_at=meta.settled_at,
        description=describe(meta.channel, "payment", rng, order_id=meta.order_id),
    )
    meta.payment_entity_id = row.entity_id
    return row


def refund_row(
    world: World,
    meta: OrderMeta,
    value: Decimal,
    parts: dict[str, Decimal],
    rng: random.Random,
    *,
    settled_at: date | None = None,
) -> SettlementRow:
    """A reversal, emitted in whichever sign convention the channel uses.

    Amazon, Myntra and the website negate the amount; Flipkart and the POS report a
    positive amount against the debit column. Either way ``credit - debit`` is the
    same money. Normalising this is the matcher's job.
    """
    cfg = channel_config(meta.channel)
    negative = cfg["refund_sign_convention"] == "negative_amount"
    sign = Decimal(-1) if negative else Decimal(1)
    return SettlementRow(
        entity_id=world.next_entity_id(),
        type=TransactionType.REFUND,
        channel=Channel(meta.channel),
        order_id=meta.order_id,
        amount=inr(value * sign),
        fee=inr(parts["fee"] * sign),
        tax=inr(parts["tax"] * sign),
        tcs=inr(parts["tcs"] * sign),
        tds=inr(parts["tds"] * sign),
        debit=ZERO if negative else parts["net"],
        credit=inr(-parts["net"]) if negative else ZERO,
        settlement_id="",
        settlement_utr="",
        created_at=meta.created_at,
        settled_at=settled_at or meta.settled_at,
        description=describe(meta.channel, "refund", rng, order_id=meta.order_id),
    )


def _make_order(
    world: World, rng: random.Random, pool: list[str], seq: int, window: tuple[date, date]
) -> None:
    channel = rng.choice(pool)
    cfg = channel_config(channel)
    category = rng.choice(sorted(cfg["categories"]))
    rate = Decimal(cfg["categories"][category])
    value = order_value(rng, cfg)
    created, settled = _dates(rng, cfg, window)
    parts = fee_breakdown(value, rate, marketplace=bool(cfg["marketplace"]))
    refunded = rng.randint(1, 1000) <= int(Decimal(generation()["clean_refund_rate"]) * 1000)

    order_id = f"ord_{seq:06d}"
    meta = OrderMeta(
        order_id=order_id,
        channel=channel,
        category=category,
        commission_rate=rate,
        created_at=created,
        settled_at=settled,
        ledger_batch=batch_for_date(created),
        settle_batch=batch_for_date(settled),
        refunded=refunded,
    )
    world.meta[order_id] = meta
    world.ledger[order_id] = LedgerRow(
        order_id=order_id,
        channel=Channel(channel),
        order_value=value,
        expected_commission_rate=rate,
        # A refunded order is fully reversed: the platform gives the commission back
        # too, so the books expect nothing from it.
        expected_fee=ZERO if refunded else parts["fee"],
        expected_net=ZERO if refunded else parts["net"],
        status=LedgerStatus.BOOKED,
    )
    world.settlements.append(payment_row(world, meta, value, parts, rng))
    if refunded:
        world.settlements.append(refund_row(world, meta, value, parts, rng))


def build_clean_world(seed: int | None = None) -> tuple[World, random.Random]:
    """Generate the fully reconciling base world across all batches."""
    gen = generation()
    rng = random.Random(seed if seed is not None else int(gen["seed"]))
    pool = channel_pool()
    world = World()
    seq = 0

    for batch, count in enumerate(gen["settlements_per_batch"], start=1):
        window = batch_window(batch)
        for _ in range(int(count)):
            seq += 1
            _make_order(world, rng, pool, seq, window)

    return world, rng
