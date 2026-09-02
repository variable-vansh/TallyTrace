"""The metric registry: a fixed set of computable answers.

Public surface: build a :class:`Corpus` with :func:`corpus_from`, then
:func:`compute` a registered metric over it. The LLM selects an id; it never computes
one. See ``pipeline/llm/intent.py`` for the selection half.
"""

from pipeline.metrics.build import corpus_from
from pipeline.metrics.corpus import BatchFacts, BatchQueue, Corpus
from pipeline.metrics.pins import Pin, recompute
from pipeline.metrics.registry import (
    METRICS,
    REGISTRY,
    Metric,
    MetricParams,
    MetricPoint,
    MetricResult,
    UnknownMetric,
    UnsupportedGrouping,
    catalogue,
    compute,
    get,
)

__all__ = [
    "BatchFacts", "BatchQueue", "Corpus", "METRICS", "Metric", "MetricParams",
    "MetricPoint", "MetricResult", "Pin", "REGISTRY", "UnknownMetric",
    "UnsupportedGrouping", "catalogue", "compute", "corpus_from", "get", "recompute",
]
