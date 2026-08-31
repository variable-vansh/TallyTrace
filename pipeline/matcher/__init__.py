"""Deterministic matching. No LLM, by design -- see the README.

Public surface: build a :class:`ReconInput`, build a :class:`MatchConfig`, call
:func:`reconcile`. Everything inside is a pure function over those two.
"""

from pipeline.matcher.engine import ReconInput, reconcile, total_impact
from pipeline.matcher.orders import OpenOrder, OrderFinding
from pipeline.matcher.reasons import Bucket, Reason
from pipeline.matcher.quarantine import QuarantineRecord
from pipeline.matcher.settings import MatchConfig, match_config_from
from pipeline.matcher.verdicts import BatchResult, GroupFinding, Verdict

__all__ = [
    "BatchResult", "Bucket", "GroupFinding", "MatchConfig", "OpenOrder", "OrderFinding",
    "QuarantineRecord", "ReconInput", "Reason", "Verdict", "match_config_from", "reconcile", "total_impact",
]
