"""Bank-level matching. The N:1 join.

One bank credit corresponds to many settlement rows: payments, minus refunds, minus
fees. Group by ``settlement_utr``, sum in the normalised convention, compare to the
credit within ``rounding_tolerance_inr``.

When it does not tie out, saying "off by 4186.03" is not useful to a bookkeeper.
Saying "these 58 rows account for the credit, this one does not" is. So the harder
half of this module is the residual search: given the credit and the candidate pool,
find the subset the credit explains and name the rows left over.

The search is deliberately bounded -- a greedy pass, then a small exhaustive sweep
over short combinations. This is not a general subset-sum solver and must not become
one: an unbounded search that finds *some* subset summing to the credit invents an
explanation, and an invented explanation in a reconciliation is worse than an
unresolved one. When the bounded search finds nothing, the group is reported with
its shortfall and its full candidate list, and a human looks at it.
"""

from __future__ import annotations

from itertools import combinations
from decimal import Decimal

from pipeline.matcher.normalise import NormalisedRow, ZERO, inr, total_net
from pipeline.matcher.settings import MatchConfig
from pipeline.matcher.verdicts import GroupFinding
from pipeline.models import BankRow


def group_by_utr(rows: list[NormalisedRow]) -> dict[str, list[NormalisedRow]]:
    """Settlement rows keyed by the bank reference of their payout.

    Report order is preserved inside each group. It carries information: when two
    rows in a payout are indistinguishable, which one arrived first is the only
    thing that separates them.
    """
    groups: dict[str, list[NormalisedRow]] = {}
    for row in rows:
        groups.setdefault(row.utr, []).append(row)
    return groups


def _greedy_excess(
    pool: list[NormalisedRow], target: Decimal, tolerance: Decimal, max_size: int
) -> list[NormalisedRow] | None:
    """Largest-first accumulation towards ``target``. Cheap, and usually enough.

    Capped at ``max_size`` like the exhaustive sweep. An explanation that needs half
    the payout to state is not an explanation, and a bookkeeper handed one would
    have to redo the work anyway.
    """
    taken: list[NormalisedRow] = []
    running = ZERO
    for row in pool:
        if len(taken) >= max_size:
            return None
        if abs(running + row.net - target) <= tolerance:
            return taken + [row]
        if abs(running + row.net) <= abs(target):
            taken.append(row)
            running = inr(running + row.net)
    return taken if taken and abs(running - target) <= tolerance else None


def _exhaustive_excess(
    pool: list[NormalisedRow], target: Decimal, tolerance: Decimal, max_size: int
) -> list[NormalisedRow] | None:
    """Short combinations only. Bounded by ``max_size`` and by the pool cap."""
    for size in range(1, max_size + 1):
        for combo in combinations(pool, size):
            if abs(sum((row.net for row in combo), ZERO) - target) <= tolerance:
                return list(combo)
    return None


def find_excess_rows(
    rows: list[NormalisedRow], shortfall: Decimal, cfg: MatchConfig
) -> list[NormalisedRow] | None:
    """The rows that account for the difference between the group and the credit.

    ``shortfall`` is ``group_sum - bank_amount``: positive when the settlement report
    claims more than the bank funded, which is what a duplicated row looks like.
    """
    tolerance = cfg.rounding_tolerance_inr
    position = {row.entity_id: index for index, row in enumerate(rows)}
    # Largest first, because one big row explains a shortfall more often than three
    # small ones. Ties break towards the row that arrived *later* in the report:
    # when a payout over-reports and two rows are identical, the first occurrence is
    # the transaction and the second is the re-emission. First write wins.
    pool = sorted(
        (row for row in rows if abs(row.net) <= abs(shortfall) + tolerance),
        key=lambda r: (-abs(r.net), -position[r.entity_id]),
    )[: cfg.subset_max_candidates]
    if not pool:
        return None
    return _greedy_excess(pool, shortfall, tolerance, cfg.subset_max_size) or _exhaustive_excess(
        pool, shortfall, tolerance, cfg.subset_max_size
    )


def reconcile_group(
    utr: str, rows: list[NormalisedRow], credit: BankRow | None, cfg: MatchConfig
) -> GroupFinding:
    """Compare one settlement group to its bank credit and explain the difference."""
    candidates = [row.entity_id for row in rows]
    group_sum = total_net(rows)

    if credit is None:
        # A cycle netting to zero -- a same-day capture and refund -- is never
        # wired, so no credit for it is the correct outcome, not a finding.
        ties_out = group_sum == ZERO
        return GroupFinding(
            utr=utr, settlement_sum=group_sum, bank_amount=None, shortfall=group_sum,
            ties_out=ties_out, explained_row_ids=candidates if ties_out else [],
            residual_row_ids=[] if ties_out else candidates, candidate_row_ids=candidates,
        )

    shortfall = inr(group_sum - credit.amount)
    if abs(shortfall) <= cfg.rounding_tolerance_inr:
        return GroupFinding(
            utr=utr, settlement_sum=group_sum, bank_amount=credit.amount, shortfall=shortfall,
            ties_out=True, explained_row_ids=candidates, residual_row_ids=[],
            candidate_row_ids=candidates,
        )

    excess = find_excess_rows(rows, shortfall, cfg)
    residual = sorted(row.entity_id for row in excess) if excess else []
    return GroupFinding(
        utr=utr, settlement_sum=group_sum, bank_amount=credit.amount, shortfall=shortfall,
        ties_out=False,
        explained_row_ids=sorted(set(candidates) - set(residual)) if residual else [],
        residual_row_ids=residual, candidate_row_ids=candidates,
        search_exhausted=excess is None,
    )


def reconcile_bank(
    rows: list[NormalisedRow], bank: list[BankRow], cfg: MatchConfig
) -> list[GroupFinding]:
    """Every settlement group and every bank credit in the batch, N:1.

    Credits with no group at all are returned as findings with an empty candidate
    list, so the caller can bucket them without a second pass over the bank table.
    """
    groups = group_by_utr(rows)
    credits = {credit.utr: credit for credit in bank}

    findings = [
        reconcile_group(utr, groups[utr], credits.get(utr), cfg) for utr in sorted(groups)
    ]
    findings.extend(
        GroupFinding(
            utr=utr, settlement_sum=ZERO, bank_amount=credits[utr].amount,
            shortfall=inr(-credits[utr].amount), ties_out=False, explained_row_ids=[],
            residual_row_ids=[], candidate_row_ids=[],
        )
        for utr in sorted(credits)
        if utr not in groups
    )
    return findings
