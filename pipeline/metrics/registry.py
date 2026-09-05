"""The metric registry. A fixed set of computable answers, and nothing else.

**No SQL is generated anywhere in this system.** Enterprise text-to-SQL execution
accuracy runs roughly 21-39% on realistic schemas -- and that is *execution* accuracy,
so the other two thirds of the time a plausible query returns a plausible number that
is wrong, with nothing on screen to say so. A wrong number in a reconciliation
dashboard is worse than a refusal, because a refusal gets checked.

So the model does not write queries and does not compute. It maps a sentence onto one
of the ids below, and everything after that is a pure function over
:class:`~pipeline.metrics.corpus.Corpus`. The registry is small on purpose: the limit
is what makes the answers trustworthy, and every question it cannot answer is
refused rather than approximated.

Each metric declares the groupings it supports, so a mapping that asks for a grouping
a metric does not have is rejected here rather than producing an empty chart.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable

from pipeline.claims.models import ClaimStatus
from pipeline.metrics.corpus import BatchFacts, Corpus

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")
PCT = Decimal("0.01")

INR = "inr"
PERCENT = "pct"
COUNT = "count"

BY_CHANNEL = "channel"
BY_BATCH = "batch"
BY_CAUSE = "cause"
BY_PLATFORM = "platform"


def _pct(part: Decimal, whole: Decimal) -> Decimal:
    """A percentage to two places. Zero over zero is zero, not a division error."""
    if whole == ZERO:
        return ZERO
    return (part / whole * HUNDRED).quantize(PCT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class MetricParams:
    """What a caller may vary. Nothing here changes *what* is computed, only over what."""

    from_batch: int | None = None
    to_batch: int | None = None
    channel: str | None = None
    group_by: str = BY_CHANNEL

    def to_json(self) -> dict[str, Any]:
        return {
            "from_batch": self.from_batch, "to_batch": self.to_batch,
            "channel": self.channel, "group_by": self.group_by,
        }


@dataclass(frozen=True)
class MetricPoint:
    label: str
    value: Decimal

    def to_json(self) -> dict[str, str]:
        return {"label": self.label, "value": str(self.value)}


@dataclass(frozen=True)
class MetricResult:
    """One computed answer, and enough context to render it without asking anything else."""

    metric_id: str
    version: int
    title: str
    unit: str
    group_by: str
    points: tuple[MetricPoint, ...]
    params: MetricParams
    #: Settlement rows the figure was derived from. A preview shows this next to the
    #: number, because a total over nine rows and the same total over nine hundred are
    #: not the same claim.
    row_count: int = 0

    @property
    def total(self) -> Decimal:
        """Meaningful for rupees and counts; deliberately not shown for percentages."""
        return sum((point.value for point in self.points), ZERO)

    def to_json(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "version": self.version,
            "title": self.title,
            "unit": self.unit,
            "group_by": self.group_by,
            "params": self.params.to_json(),
            "row_count": self.row_count,
            "points": [point.to_json() for point in self.points],
            "total": str(self.total) if self.unit != PERCENT else None,
        }


class UnsupportedGrouping(ValueError):
    """Asked for a grouping the metric does not define. Refused, never silently ignored."""


@dataclass(frozen=True)
class Metric:
    """One registered answer: an id, words, a unit, and a pure function."""

    metric_id: str
    title: str
    description: str
    unit: str
    groupings: tuple[str, ...]
    compute: Callable[[Corpus, MetricParams], tuple[MetricPoint, ...]]
    #: Bumped whenever the *definition* changes -- a different denominator, a changed
    #: filter. The registered function is this system's compiled artifact (there is no
    #: SQL and no database), so the version is what lets a pinned figure say which
    #: definition produced it. A pin that recomputes under a new version is answering
    #: a subtly different question, and that has to be legible rather than silent.
    version: int = 1

    def run(self, corpus: Corpus, params: MetricParams) -> MetricResult:
        if params.group_by not in self.groupings:
            raise UnsupportedGrouping(
                f"{self.metric_id} cannot group by {params.group_by!r}; "
                f"it supports {', '.join(self.groupings)}"
            )
        window = corpus.window(params.from_batch, params.to_batch)
        return MetricResult(
            metric_id=self.metric_id,
            version=self.version,
            title=self.title,
            unit=self.unit,
            group_by=params.group_by,
            points=self.compute(window, params),
            params=params,
            row_count=window.row_count,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "unit": self.unit,
            "groupings": list(self.groupings),
        }


# --------------------------------------------------------------------------- #
# Helpers shared by the money metrics
# --------------------------------------------------------------------------- #


def _channels(corpus: Corpus, params: MetricParams) -> tuple[str, ...]:
    return (params.channel,) if params.channel else corpus.channels


def _sum_over(
    facts: tuple[BatchFacts, ...], field: str, channel: str | None
) -> Decimal:
    return sum((entry.total(getattr(entry, field), channel) for entry in facts), ZERO)


def _money_points(corpus: Corpus, params: MetricParams, field: str) -> tuple[MetricPoint, ...]:
    if params.group_by == BY_BATCH:
        return tuple(
            MetricPoint(f"batch {entry.batch}", _sum_over((entry,), field, params.channel))
            for entry in corpus.facts
        )
    return tuple(
        MetricPoint(channel, _sum_over(corpus.facts, field, channel))
        for channel in _channels(corpus, params)
    )


def _deductions(facts: tuple[BatchFacts, ...], channel: str | None) -> Decimal:
    return _sum_over(facts, "fees_charged", channel) + _sum_over(facts, "taxes_withheld", channel)


def _take_rate_points(corpus: Corpus, params: MetricParams) -> tuple[MetricPoint, ...]:
    """Deductions as a share of gross order value -- the effective take rate.

    A percentage, never rupees. Batch sizes in this corpus grow from 59 settlement
    rows to 181, so an absolute fee line rises whatever the platforms do and says
    nothing. As a share of the sale it says exactly one thing, and it is the thing
    that matters: a rising line is the effective take rate climbing, which is what a
    silent commission change looks like from the outside.
    """
    if params.group_by == BY_BATCH:
        return tuple(
            MetricPoint(
                f"batch {entry.batch}",
                _pct(_deductions((entry,), params.channel),
                     _sum_over((entry,), "gross_order_value", params.channel)),
            )
            for entry in corpus.facts
        )
    return tuple(
        MetricPoint(
            channel,
            _pct(_deductions(corpus.facts, channel),
                 _sum_over(corpus.facts, "gross_order_value", channel)),
        )
        for channel in _channels(corpus, params)
    )


def _fee_share_points(corpus: Corpus, params: MetricParams) -> tuple[MetricPoint, ...]:
    """Commission alone as a share of gross, with tax withholding excluded."""
    if params.group_by == BY_BATCH:
        return tuple(
            MetricPoint(
                f"batch {entry.batch}",
                _pct(_sum_over((entry,), "fees_charged", params.channel),
                     _sum_over((entry,), "gross_order_value", params.channel)),
            )
            for entry in corpus.facts
        )
    return tuple(
        MetricPoint(
            channel,
            _pct(_sum_over(corpus.facts, "fees_charged", channel),
                 _sum_over(corpus.facts, "gross_order_value", channel)),
        )
        for channel in _channels(corpus, params)
    )


# --------------------------------------------------------------------------- #
# Queue and claim metrics
# --------------------------------------------------------------------------- #


def _review_rate_points(corpus: Corpus, params: MetricParams) -> tuple[MetricPoint, ...]:
    return tuple(
        MetricPoint(
            f"batch {queue.batch}",
            _pct(
                Decimal(max(queue.flagged_rows - queue.auto_resolved_rows, 0)),
                Decimal(queue.settlement_rows),
            ),
        )
        for queue in corpus.queues
    )


def _exceptions_by_cause(corpus: Corpus, params: MetricParams) -> tuple[MetricPoint, ...]:
    totals: dict[str, int] = {}
    for queue in corpus.queues:
        for cause, count in queue.cases_by_cause.items():
            totals[cause] = totals.get(cause, 0) + count
    ordered = sorted(totals.items(), key=lambda pair: (-pair[1], pair[0]))
    return tuple(MetricPoint(cause, Decimal(count)) for cause, count in ordered)


def _auto_resolved_points(corpus: Corpus, params: MetricParams) -> tuple[MetricPoint, ...]:
    return tuple(
        MetricPoint(f"batch {queue.batch}", Decimal(queue.auto_resolved_rows))
        for queue in corpus.queues
    )


def _claim_recovery_points(corpus: Corpus, params: MetricParams) -> tuple[MetricPoint, ...]:
    """Recovered as a share of *settled* claims. Open claims are not yet a result.

    A platform whose claims are all still open is **omitted**, not plotted at zero. Six
    website chargebacks are open and none has settled either way, and rendering that as
    "0% recovery on website" would put a failure on screen where there is only an
    unfinished window. A metric with no denominator has no value, and the honest way to
    say so on a bar chart is to have no bar.
    """
    points = []
    for platform in sorted({claim.platform for claim in corpus.claims}):
        claims = [c for c in corpus.claims if c.platform == platform]
        recovered = sum(1 for c in claims if c.status is ClaimStatus.RECOVERED)
        settled = recovered + sum(1 for c in claims if c.status is ClaimStatus.EXPIRED)
        if settled == 0:
            continue
        points.append(MetricPoint(platform, _pct(Decimal(recovered), Decimal(settled))))
    return tuple(points)


def _open_claims_points(corpus: Corpus, params: MetricParams) -> tuple[MetricPoint, ...]:
    totals: dict[str, Decimal] = {}
    for claim in corpus.claims:
        if claim.is_open:
            totals[claim.platform] = totals.get(claim.platform, ZERO) + claim.amount_inr
    return tuple(MetricPoint(platform, totals[platform]) for platform in sorted(totals))


def _rupees_at_risk_points(corpus: Corpus, params: MetricParams) -> tuple[MetricPoint, ...]:
    """Money that lapsed unrecovered, per batch. The number the deadline clock exists for."""
    by_batch: dict[int, Decimal] = {batch: ZERO for batch in corpus.batches}
    for claim in corpus.claims:
        for transition in claim.transitions:
            # A claim opened inside the window can expire outside it. The series is
            # about the weeks in the window, so that rupee belongs to no bar here
            # rather than to a bar the caller did not ask for.
            if transition.to_status == ClaimStatus.EXPIRED.value and transition.batch in by_batch:
                by_batch[transition.batch] += claim.amount_inr
    return tuple(MetricPoint(f"batch {batch}", by_batch[batch]) for batch in sorted(by_batch))


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #

METRICS: tuple[Metric, ...] = (
    Metric(
        metric_id="net_revenue_by_channel",
        title="Net revenue settled",
        description="Money that actually reached the bank, after every platform deduction.",
        unit=INR,
        groupings=(BY_CHANNEL, BY_BATCH),
        compute=lambda corpus, params: _money_points(corpus, params, "net_settled"),
    ),
    Metric(
        metric_id="gross_order_value",
        title="Gross order value booked",
        description="What customers paid, from the seller's own ledger, before any deduction.",
        unit=INR,
        groupings=(BY_CHANNEL, BY_BATCH),
        compute=lambda corpus, params: _money_points(corpus, params, "gross_order_value"),
    ),
    Metric(
        metric_id="effective_take_rate",
        title="Effective take rate",
        description=(
            "Every deduction -- commission, GST on commission, TCS and TDS -- as a "
            "percentage of gross order value. A rising line is the take rate climbing."
        ),
        unit=PERCENT,
        groupings=(BY_CHANNEL, BY_BATCH),
        compute=_take_rate_points,
    ),
    Metric(
        metric_id="commission_share_of_gross",
        title="Commission as a share of gross",
        description="Platform and fulfilment fee alone, excluding tax withholding.",
        unit=PERCENT,
        groupings=(BY_CHANNEL, BY_BATCH),
        compute=_fee_share_points,
    ),
    Metric(
        metric_id="exception_count_by_cause",
        title="Exceptions by cause",
        description="How many exceptions each cause produced across the window.",
        unit=COUNT,
        groupings=(BY_CAUSE,),
        compute=_exceptions_by_cause,
    ),
    Metric(
        metric_id="review_rate_trend",
        title="Manual review rate",
        description=(
            "Settlement rows still needing a human after learned rules fire, as a "
            "percentage of the batch."
        ),
        unit=PERCENT,
        groupings=(BY_BATCH,),
        compute=_review_rate_points,
    ),
    Metric(
        metric_id="auto_resolved_rows",
        title="Rows auto-resolved",
        description="Settlement rows a learned rule closed without a human, per batch.",
        unit=COUNT,
        groupings=(BY_BATCH,),
        compute=_auto_resolved_points,
    ),
    Metric(
        metric_id="claim_recovery_rate",
        title="Claim recovery rate",
        description=(
            "Share of settled claims that recovered, per platform. Claims still inside "
            "their filing window are not counted either way."
        ),
        unit=PERCENT,
        groupings=(BY_PLATFORM,),
        compute=_claim_recovery_points,
    ),
    Metric(
        metric_id="open_claim_value",
        title="Open claim value",
        description="Rupees still being chased, per platform.",
        unit=INR,
        groupings=(BY_PLATFORM,),
        compute=_open_claims_points,
    ),
    Metric(
        metric_id="rupees_expired_unrecovered",
        title="Rupees lapsed unrecovered",
        description="Money whose filing window closed with no recovery, per batch.",
        unit=INR,
        groupings=(BY_BATCH,),
        compute=_rupees_at_risk_points,
    ),
)

REGISTRY: dict[str, Metric] = {metric.metric_id: metric for metric in METRICS}


class UnknownMetric(KeyError):
    """Asked for a metric id that is not registered. There is no nearest match."""


def get(metric_id: str) -> Metric:
    if metric_id not in REGISTRY:
        raise UnknownMetric(
            f"{metric_id!r} is not a registered metric. Registered: "
            f"{', '.join(sorted(REGISTRY))}"
        )
    return REGISTRY[metric_id]


def compute(metric_id: str, corpus: Corpus, params: MetricParams) -> MetricResult:
    """The only way a metric is computed. No model is involved past this line."""
    return get(metric_id).run(corpus, params)


def catalogue() -> list[dict[str, Any]]:
    """The registry as data -- what the intent prompt is built from and what the UI lists."""
    return [metric.to_json() for metric in METRICS]
