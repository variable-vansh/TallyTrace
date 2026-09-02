"""Turning a completed run into the corpus the registry computes over.

One function, and the reason it is its own module is the boundary it keeps: the
registry knows nothing about ``BatchLearning``, ``Decision`` or ``Verdict``, so a
metric cannot reach past its inputs and start re-deriving the matcher's opinion. It
gets sums and counts and works on those.

The review-rate numbers here are computed the same way ``harness/metrics.py``
computes them -- flagged rows minus the rows a learned rule closed, over the batch's
settlement rows. They are recomputed rather than imported because the harness reads
the answer key and this does not, and a metric the dashboard shows must never be one
only a scored run can produce.
"""

from __future__ import annotations

from pipeline.claims.routing import cause_of
from pipeline.learn import BatchLearning, LearningRun
from pipeline.matcher import Bucket
from pipeline.metrics.corpus import BatchQueue, Corpus, facts_for, order_values


def _queue_for(batch: BatchLearning) -> BatchQueue:
    counts = batch.result.counts("settlement_report")
    causes: dict[str, int] = {}
    for decision in batch.decisions:
        cause, _ = cause_of(decision, batch.hypotheses.get(decision.case.case_id))
        label = cause or decision.case.reason
        causes[label] = causes.get(label, 0) + 1

    return BatchQueue(
        batch=batch.batch,
        settlement_rows=sum(counts.values()),
        flagged_rows=counts[Bucket.VARIANCE.value]
        + counts[Bucket.UNMATCHED.value]
        + counts[Bucket.QUARANTINED.value],
        auto_resolved_rows=sum(
            len(decision.case.settlement_row_ids) for decision in batch.auto_resolved
        ),
        cases_by_cause=causes,
    )


def corpus_from(run: LearningRun) -> Corpus:
    """Everything the metric registry is allowed to see, from one completed run."""
    values = order_values(batch.tables.ledger for batch in run.batches)
    return Corpus(
        facts=tuple(
            facts_for(batch.batch, batch.tables.settlements, values)
            for batch in run.batches
        ),
        queues=tuple(_queue_for(batch) for batch in run.batches),
        claims=tuple(run.register.claims),
    )
