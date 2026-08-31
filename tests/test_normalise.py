"""Sign normalisation, in both conventions.

Amazon negates a refund's amount; Flipkart keeps it positive and puts it in the
debit column. The whole point of this module is that the matcher stops caring, so
every test here asserts the two conventions produce the same normalised numbers.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pipeline.matcher.normalise import charged_fee, normalise, raw_net, total_net
from pipeline.models import Channel, SettlementRow, TransactionType

D = Decimal


def row(**overrides: object) -> SettlementRow:
    """A settlement row with sensible defaults; override what the test is about."""
    fields: dict[str, object] = dict(
        entity_id="st_000001", type=TransactionType.PAYMENT, channel=Channel.AMAZON,
        order_id="ord_000001", amount=D("1000.00"), fee=D("180.00"), tax=D("32.40"),
        tcs=D("10.00"), tds=D("1.00"), debit=D("0.00"), credit=D("776.60"),
        settlement_id="AMZ-STL-1", settlement_utr="AXISN1", created_at=date(2025, 1, 1),
        settled_at=date(2025, 1, 8),
    )
    fields.update(overrides)
    return SettlementRow(**fields)  # type: ignore[arg-type]


def test_a_payment_nets_in_and_charges_its_fee_out() -> None:
    normalised = normalise(row())
    assert normalised.net == D("776.60")
    assert normalised.fee == D("-180.00"), "a fee charged is money leaving the seller"


def test_the_two_refund_conventions_normalise_identically() -> None:
    """Amazon negates the amount; Flipkart debits it. Same money either way."""
    amazon = normalise(row(type=TransactionType.REFUND, amount=D("-1000.00"),
                           fee=D("-180.00"), tax=D("-32.40"), credit=D("-776.60")))
    flipkart = normalise(row(channel=Channel.FLIPKART, type=TransactionType.REFUND,
                             amount=D("1000.00"), fee=D("180.00"), tax=D("32.40"),
                             debit=D("776.60"), credit=D("0.00")))
    assert amazon.net == flipkart.net == D("-776.60")
    assert amazon.fee == flipkart.fee == D("180.00"), "a reversed fee comes back"


def test_a_payment_and_its_refund_cancel_to_nothing() -> None:
    payment = normalise(row())
    refund = normalise(row(entity_id="st_000002", type=TransactionType.REFUND,
                           amount=D("-1000.00"), fee=D("-180.00"), tax=D("-32.40"),
                           credit=D("-776.60")))
    assert total_net([payment, refund]) == D("0.00")
    assert charged_fee([payment, refund]) == D("0.00")


def test_a_withheld_payment_still_reads_as_a_fee_charged() -> None:
    """Credit zeroed by a dispute hold: the platform kept the commission."""
    normalised = normalise(row(credit=D("0.00"), on_hold=True))
    assert normalised.net == D("0.00")
    assert normalised.fee == D("-180.00")


def test_charged_fee_is_returned_in_the_ledgers_convention() -> None:
    """``expected_fee`` is a positive cost, so the comparison reads the same way round."""
    assert charged_fee([normalise(row())]) == D("180.00")


def test_raw_net_never_produces_negative_zero() -> None:
    assert str(raw_net(row(credit=D("0.00"), debit=D("0.00")))) == "0.00"


@pytest.mark.parametrize("field", ["net", "fee", "tax", "tcs", "tds"])
def test_every_normalised_field_is_decimal(field: str) -> None:
    assert isinstance(getattr(normalise(row()), field), Decimal)
