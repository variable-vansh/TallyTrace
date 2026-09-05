"""The closed vocabulary a question may be answered in, and the refusal when it is not.

The registry has always been a closed set -- the model picks a metric id from a
``Literal`` and can return nothing else. What it could not do until now was *say which
word it could not honour*. The refusal was a sentence the model wrote, and a refusal
written by the thing being refused is not a check, it is a courtesy.

So this module holds the vocabulary as data and refuses deterministically. Every slot
a question can fill -- the metric, the grouping, the channel, the batch range -- is
checked against the registry by lookup, and an unsupported value produces a
:class:`Refusal` that **names the term** and lists what was available instead. No
nearest match, no partial fulfilment, no plausible adjacent chart. A wrong number in a
reconciliation dashboard is worse than a blank, because a blank gets questioned.

The model's own refusal sentence is still shown where it wrote one -- it is better
prose than a lookup can produce -- but it is commentary on a decision made here, not
the decision itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from pipeline.models import Channel
from pipeline.metrics.registry import REGISTRY, Metric

#: The slots a question can fill. Named so a refusal can say which one failed.
METRIC = "metric"
GROUPING = "grouping"
CHANNEL = "channel"
BATCH_RANGE = "batch_range"


@dataclass(frozen=True)
class Refusal:
    """One unsupported term, named, with the supported set beside it."""

    slot: str
    term: str
    supported: tuple[str, ...]

    @property
    def message(self) -> str:
        return (
            f"{self.term!r} is not a supported {self.slot.replace('_', ' ')}. "
            f"This dashboard can answer for: {', '.join(self.supported)}."
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "term": self.term,
            "supported": list(self.supported),
            "message": self.message,
        }


def known_metrics() -> tuple[str, ...]:
    return tuple(sorted(REGISTRY))


def known_channels() -> tuple[str, ...]:
    """The frozen channel enum, not the channels config file.

    ``config/channels.yaml`` describes the synthetic world's economics and its top
    level is sections, not channel names. Reading it here would have offered the
    operator a choice of "taxes" and "channel_mix".
    """
    return tuple(channel.value for channel in Channel)


def known_groupings(metric_id: str | None = None) -> tuple[str, ...]:
    """Groupings in the vocabulary, or the ones a particular metric declares."""
    if metric_id is not None and metric_id in REGISTRY:
        return tuple(REGISTRY[metric_id].groupings)
    return tuple(sorted({g for metric in REGISTRY.values() for g in metric.groupings}))


def vocabulary() -> dict[str, tuple[str, ...]]:
    """The whole closed set, as data. What a refusal shows and what the prompt lists."""
    return {
        METRIC: known_metrics(),
        GROUPING: known_groupings(),
        CHANNEL: known_channels(),
    }


def check(
    metric_id: str | None,
    group_by: str | None,
    channel: str | None,
    batches: Iterable[int | None] = (),
    known_batches: tuple[int, ...] = (),
) -> Refusal | None:
    """The first unsupported term in this request, or None if every slot is in vocabulary.

    Checked in the order a reader would ask about them: the metric first, because a
    grouping is only meaningful relative to one. Returns the *first* problem rather
    than all of them -- a refusal that lists four faults invites negotiating them away
    one at a time, and the answer is the same after the first.
    """
    if metric_id is not None and metric_id not in REGISTRY:
        return Refusal(METRIC, metric_id, known_metrics())

    if channel is not None and channel not in known_channels():
        return Refusal(CHANNEL, channel, known_channels())

    if group_by is not None:
        allowed = known_groupings(metric_id)
        if group_by not in allowed:
            return Refusal(GROUPING, group_by, allowed)

    if known_batches:
        span = tuple(str(b) for b in known_batches)
        for batch in batches:
            if batch is not None and batch not in known_batches:
                return Refusal(BATCH_RANGE, str(batch), span)

    return None


def metric_or_refusal(metric_id: str) -> tuple[Metric | None, Refusal | None]:
    """Look a metric up, or say why it cannot be looked up. Never guesses a near match."""
    refusal = check(metric_id, None, None)
    if refusal is not None:
        return None, refusal
    return REGISTRY[metric_id], None
