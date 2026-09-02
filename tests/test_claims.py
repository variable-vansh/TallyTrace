"""The claims queue: routing, the two clocks, recovery, and what a draft may contain."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest

from pipeline.claims.deadlines import (
    DURATION,
    STATUTORY_CUTOFF,
    UNCONFIGURED,
    deadline_config_from,
    deadline_for,
)
from pipeline.claims.models import Claim, ClaimStatus, Evidence
from pipeline.claims.queue import build as build_queue
from pipeline.claims.recovery import match_recoveries
from pipeline.claims.routing import cause_of, is_claimable
from pipeline.config import resolution_class_by_cause, thresholds
from pipeline.models import Channel, SettlementRow, TransactionType

ZERO = Decimal("0.00")
DIGIT = re.compile(r"\d")
#: A numeral as it appears in a rendered draft: digits, with grouping and paise kept
#: together so that ``2,399.73`` is one token rather than three.
NUMERIC_TOKEN = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


@pytest.fixture
def deadlines():
    return deadline_config_from(thresholds())


# --------------------------------------------------------------------------- #
# The clock
# --------------------------------------------------------------------------- #


def test_a_platform_window_is_a_duration_from_the_day_the_claim_opened(deadlines) -> None:
    opened = date(2025, 1, 19)
    clock = deadline_for("missing_settlement_row", "amazon", opened, deadlines)
    assert clock.kind == DURATION
    assert clock.on == date(2025, 2, 18)         # 30-day SAFE-T window, from config
    assert clock.days_remaining(opened) == 30


def test_a_tcs_discrepancy_uses_the_tenth_of_the_following_month(deadlines) -> None:
    """A day-of-month cutoff, not a duration. GSTR-8 for January is filed by 10 February."""
    early = deadline_for("tcs_timing_mismatch", "flipkart", date(2025, 1, 2), deadlines)
    late = deadline_for("tcs_timing_mismatch", "flipkart", date(2025, 1, 28), deadlines)

    assert early.kind == STATUTORY_CUTOFF
    assert early.on == date(2025, 2, 10) == late.on
    # The same cutoff is 39 days away from one claim and 13 from the other. That is the
    # whole reason it is not modelled as a duration.
    assert early.days_remaining(date(2025, 1, 2)) == 39
    assert late.days_remaining(date(2025, 1, 28)) == 13


def test_the_statutory_cutoff_rolls_over_a_year_boundary(deadlines) -> None:
    clock = deadline_for("tcs_timing_mismatch", "myntra", date(2025, 12, 20), deadlines)
    assert clock.on == date(2026, 1, 10)


def test_a_tcs_claim_does_not_borrow_the_platform_window(deadlines) -> None:
    """Flipkart has a 30-day window configured. A TCS discrepancy must not inherit it."""
    tcs = deadline_for("tcs_timing_mismatch", "flipkart", date(2025, 1, 19), deadlines)
    commercial = deadline_for("short_payment_unexplained", "flipkart", date(2025, 1, 19), deadlines)
    assert tcs.on == date(2025, 2, 10)
    assert commercial.on == date(2025, 2, 18)


def test_an_unconfigured_platform_gets_no_clock_rather_than_a_default(deadlines) -> None:
    """Inventing a window would put a countdown on screen that no agreement backs."""
    clock = deadline_for("chargeback_deduction", "website", date(2025, 1, 19), deadlines)
    assert clock.kind == UNCONFIGURED
    assert clock.on is None
    assert clock.days_remaining(date(2025, 3, 1)) is None
    assert clock.has_passed(date(2030, 1, 1)) is False


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


def _features(direction: str):
    from pipeline.cases import CaseFeatures

    return CaseFeatures(
        channel="amazon", reason="settlement_overdue_beyond_window", bucket="unmatched",
        transaction_type=None, direction=direction, variance_inr=Decimal("100.00"),
        fee_variance_pct=None, net_variance_pct=None, days_after_settlement=None,
        days_since_order=None, days_late=3,
    )


def _case(direction: str):
    from pipeline.cases import ExceptionCase

    return ExceptionCase(
        case_id="case-01-ord_000001", batch=1, kind="order", key="ord_000001",
        verdicts=(), features=_features(direction), impact_inr=Decimal("100.00"),
    )


@pytest.mark.parametrize(
    "cause,expected",
    [
        ("missing_settlement_row", True),      # counterparty_claim
        ("weight_dispute_hold", True),
        ("tcs_timing_mismatch", True),         # tax_review, but it has a statutory cutoff
        ("tds_timing_mismatch", False),        # tax_review with no configured cutoff
        ("commission_rate_stale", False),      # internal_fix -> the learning loop
        ("bank_credit_unmatched", False),      # investigate -> a human, no clock
    ],
)
def test_only_counterparty_causes_and_tcs_reach_the_register(cause: str, expected: bool) -> None:
    routes = resolution_class_by_cause()
    assert is_claimable(cause, routes[cause], _case("short")) is expected


def test_money_arriving_never_opens_a_claim() -> None:
    """The planted recovery credits arrive as `over`. Claiming one would claim the payment."""
    routes = resolution_class_by_cause()
    assert is_claimable("short_payment_unexplained", routes["short_payment_unexplained"],
                        _case("over")) is False
    assert is_claimable("short_payment_unexplained", routes["short_payment_unexplained"],
                        _case("short")) is True


def test_a_rules_prediction_wins_over_the_models_hypothesis() -> None:
    from pipeline.rules.apply import UNMATCHED, Decision, Provenance
    from pipeline.llm.schemas import Hypothesis

    hypothesis = Hypothesis(
        cause="weight_dispute_hold", confidence=Decimal("0.8"),
        hypothesis="The payout is held pending a weight dispute.",
    )
    case = _case("short")
    with_rule = Decision(
        case=case,
        provenance=Provenance(
            case_id=case.case_id, batch=1, outcome="shadow_prediction", rule_id="R-01",
            rule_state_at_fire="shadow", source_resolution_id=None, source_operator=None,
            proposed_cause="missing_settlement_row", guardrails_evaluated=(),
            guardrail_detail=(), note="",
        ),
        rule=None, guardrails=None,
    )
    without = Decision(
        case=case,
        provenance=Provenance(
            case_id=case.case_id, batch=1, outcome=UNMATCHED, rule_id=None,
            rule_state_at_fire=None, source_resolution_id=None, source_operator=None,
            proposed_cause=None, guardrails_evaluated=(), guardrail_detail=(), note="",
        ),
        rule=None, guardrails=None,
    )
    assert cause_of(with_rule, hypothesis) == ("missing_settlement_row", "rule")
    assert cause_of(without, hypothesis) == ("weight_dispute_hold", "hypothesis")


# --------------------------------------------------------------------------- #
# Recovery
# --------------------------------------------------------------------------- #


def _claim(claim_id: str, order: str, amount: str, status=ClaimStatus.OPEN) -> Claim:
    from pipeline.claims.deadlines import Deadline

    return Claim(
        claim_id=claim_id, platform="amazon", amount_inr=Decimal(amount),
        cause="missing_settlement_row", resolution_class="counterparty_claim",
        order_key=order, evidence=(Evidence("internal_ledger", order),),
        opened_at=date(2025, 1, 19), opened_batch=1,
        deadline=Deadline(DURATION, date(2025, 2, 18), "test"), status=status,
    )


def _credit(entity: str, order: str | None, credit: str, debit: str = "0.00") -> SettlementRow:
    return SettlementRow(
        entity_id=entity, type=TransactionType.ADJUSTMENT, channel=Channel.AMAZON,
        order_id=order, amount=Decimal(credit), fee=ZERO, tax=ZERO, tcs=ZERO, tds=ZERO,
        debit=Decimal(debit), credit=Decimal(credit), settlement_id="S", settlement_utr="U",
        created_at=date(2025, 2, 1), settled_at=date(2025, 2, 3),
    )


def test_a_credit_on_the_claimed_order_within_tolerance_closes_it() -> None:
    matches = match_recoveries(
        [_claim("CLM-0001", "ord_000001", "775.36")],
        [_credit("st_1", "ord_000001", "775.90")],
        Decimal("1.00"),
    )
    assert [m.claim_id for m in matches] == ["CLM-0001"]
    assert matches[0].delta_inr == Decimal("0.54")


def test_a_credit_outside_the_tolerance_does_not_close_anything() -> None:
    assert match_recoveries(
        [_claim("CLM-0001", "ord_000001", "775.36")],
        [_credit("st_1", "ord_000001", "790.00")],
        Decimal("1.00"),
    ) == []


def test_a_credit_on_a_different_order_does_not_close_a_claim() -> None:
    """Amount and platform alone are not a key: two ₹775 Amazon claims are two claims."""
    assert match_recoveries(
        [_claim("CLM-0001", "ord_000001", "775.36")],
        [_credit("st_1", "ord_000002", "775.36")],
        Decimal("1.00"),
    ) == []


def test_money_leaving_never_counts_as_a_recovery() -> None:
    assert match_recoveries(
        [_claim("CLM-0001", "ord_000001", "775.36")],
        [_credit("st_1", "ord_000001", "0.00", debit="775.36")],
        Decimal("1.00"),
    ) == []


def test_one_credit_closes_at_most_one_claim() -> None:
    """A single credit cannot honestly be counted against two different debts."""
    matches = match_recoveries(
        [_claim("CLM-0001", "ord_000001", "775.36"), _claim("CLM-0002", "ord_000001", "775.36")],
        [_credit("st_1", "ord_000001", "775.36")],
        Decimal("1.00"),
    )
    assert len(matches) == 1


def test_a_closed_claim_is_not_recovered_twice() -> None:
    assert match_recoveries(
        [_claim("CLM-0001", "ord_000001", "775.36", status=ClaimStatus.RECOVERED)],
        [_credit("st_1", "ord_000001", "775.36")],
        Decimal("1.00"),
    ) == []


# --------------------------------------------------------------------------- #
# The queue view
# --------------------------------------------------------------------------- #


def test_the_queue_sorts_by_expiry_and_puts_unclocked_claims_last() -> None:
    from pipeline.claims.deadlines import Deadline

    def at(claim_id: str, on: date | None) -> Claim:
        claim = _claim(claim_id, f"ord_{claim_id}", "100.00")
        return type(claim)(
            **{**claim.__dict__, "deadline": Deadline(
                DURATION if on else UNCONFIGURED, on, "test")}
        )

    view = build_queue(
        [at("C", date(2025, 3, 1)), at("A", None), at("B", date(2025, 2, 1))],
        date(2025, 1, 20),
    )
    assert [row.claim.claim_id for row in view.rows] == ["B", "C", "A"]
    assert view.unclocked_count == 1
    assert view.soonest_days == 12


def test_the_header_counts_the_money_and_the_nearest_expiry() -> None:
    from pipeline.claims.deadlines import Deadline

    claims = []
    for claim_id, on in (("A", date(2025, 2, 1)), ("B", date(2025, 2, 1)), ("C", date(2025, 3, 1))):
        base = _claim(claim_id, f"ord_{claim_id}", "1000.00")
        claims.append(type(base)(**{**base.__dict__,
                                    "deadline": Deadline(DURATION, on, "test")}))
    view = build_queue(claims, date(2025, 1, 28))
    assert view.header == "₹3,000.00 open across 3 claims · 2 expiring in 4 days"


# --------------------------------------------------------------------------- #
# Drafts, against a real run
# --------------------------------------------------------------------------- #


def test_every_numeral_in_a_draft_traces_to_the_matchers_own_figures(scored) -> None:
    """The model is forbidden a numeral, so every number in a letter came from the matcher.

    This is the other end of that constraint: each numeric token in a finished draft is
    matched against the claim's own fields and the verdict detail of the case it was
    built from. A figure that traces to neither would be a figure nobody computed, in a
    letter addressed to Amazon.
    """
    cases = {
        case.case_id: case
        for batch in scored.run.batches
        for case in batch.cases
    }
    drafted = [c for c in scored.claims.claims if c.draft]
    assert drafted, "no claim was drafted in this run"

    for claim in drafted:
        case = next(cases[c] for c in claim.case_ids if c in cases)
        allowed = _traceable_numbers(claim, case)
        for token in NUMERIC_TOKEN.findall(claim.draft):
            assert token in allowed, (
                f"{claim.claim_id}: {token!r} in the draft traces to no figure the "
                "matcher produced"
            )


def _traceable_numbers(claim, case) -> set[str]:
    """Every numeric token the draft is allowed to contain, from the claim and its rows."""
    from pipeline.claims.drafting import context_for, rupees

    context = context_for(claim, case)
    values: set[str] = set()
    for raw in list(context.detail.values()) + [str(claim.amount_inr)]:
        values.update(NUMERIC_TOKEN.findall(raw))
        try:
            values.update(NUMERIC_TOKEN.findall(rupees(raw)))
        except (ArithmeticError, ValueError):
            pass
    for text in [claim.claim_id, claim.order_key or "", claim.opened_at.isoformat(),
                 "" if claim.deadline.on is None else claim.deadline.on.isoformat(),
                 claim.deadline.basis, *claim.evidence_row_ids]:
        values.update(NUMERIC_TOKEN.findall(text))
    return values


def test_only_counterparty_claims_are_drafted(scored) -> None:
    """A TCS discrepancy is in the register for its cutoff. Nothing writes it a letter."""
    for claim in scored.claims.claims:
        if claim.resolution_class != "counterparty_claim":
            assert claim.draft is None, f"{claim.claim_id} ({claim.cause}) was drafted"


def test_every_planted_recovery_pair_is_accounted_for(scored) -> None:
    """Three of five auto-close. The two misses are reported, not excluded."""
    planted = scored.claims.planted
    assert len(planted) == 5
    assert scored.claims.planted_caught == 3
    for entry in planted:
        if entry.linked_correctly:
            continue
        # A claim the system had no cause to open is still reported as a miss.
        assert entry.claim_opened is False, entry.to_json()


def test_a_recovered_claim_carries_the_row_that_closed_it(scored) -> None:
    recovered = [c for c in scored.claims.claims if c.status is ClaimStatus.RECOVERED]
    assert recovered
    for claim in recovered:
        assert claim.recovery_row_id
        assert claim.recovered_batch and claim.recovered_batch > claim.opened_batch
        assert abs(claim.recovered_amount_inr - claim.amount_inr) <= Decimal("1.00")


def test_a_claim_never_opens_and_closes_in_the_same_batch(scored) -> None:
    for claim in scored.claims.claims:
        for transition in claim.transitions:
            if transition.to_status == ClaimStatus.RECOVERED.value:
                assert transition.batch > claim.opened_batch


def test_one_open_claim_per_order(scored) -> None:
    seen: dict[tuple[str, str], int] = {}
    for claim in scored.claims.claims:
        if claim.order_key is None:
            continue
        key = (claim.platform, claim.order_key)
        seen[key] = seen.get(key, 0) + 1
    assert not [key for key, count in seen.items() if count > 1], (
        "the same order opened more than one claim"
    )
