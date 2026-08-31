"""Money is Decimal. The models are where that is enforced, so test it there."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from pipeline.models import BankRow, LedgerRow, SettlementRow

BASE = dict(
    entity_id="st_000001", type="payment", channel="amazon", order_id="ord_000001",
    amount="1500.00", fee="270.00", tax="48.60", tcs="15.00", tds="1.50",
    debit="0.00", credit="1164.90", settlement_id="AMZ-STL-1", settlement_utr="AXISN1",
    created_at=date(2025, 1, 6), settled_at=date(2025, 1, 14),
)


def test_money_parses_to_decimal() -> None:
    row = SettlementRow(**BASE)
    for field in ("amount", "fee", "tax", "tcs", "tds", "debit", "credit"):
        assert isinstance(getattr(row, field), Decimal)
    assert row.amount == Decimal("1500.00")


def test_float_is_rejected_on_construction() -> None:
    with pytest.raises(ValidationError, match="float"):
        SettlementRow(**{**BASE, "amount": 1500.0})


def test_float_is_rejected_on_assignment() -> None:
    row = SettlementRow(**BASE)
    with pytest.raises(ValidationError, match="float"):
        row.credit = 12.5


def test_unparseable_amount_is_rejected_rather_than_coerced() -> None:
    """The comma-formatted amount in the malformed rows must not slip through."""
    with pytest.raises(ValidationError):
        SettlementRow(**{**BASE, "amount": "1,299.00"})


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SettlementRow(**{**BASE, "surprise": "x"})


def test_adjustment_rows_may_have_no_order_id() -> None:
    assert SettlementRow(**{**BASE, "type": "adjustment", "order_id": None}).order_id is None


def test_bank_and_ledger_money_is_decimal() -> None:
    bank = BankRow(utr="AXISN1", amount="10000.00", created_at=date(2025, 1, 14))
    ledger = LedgerRow(
        order_id="ord_000001", channel="amazon", order_value="1500.00",
        expected_commission_rate="0.18", expected_fee="270.00", expected_net="1164.90",
    )
    assert isinstance(bank.amount, Decimal)
    assert isinstance(ledger.expected_commission_rate, Decimal)
    assert isinstance(ledger.expected_net, Decimal)
