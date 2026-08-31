"""LLM token accounting.

There is no LLM in this build yet. This ledger exists anyway, because the harness
reports rupee cost per reconciled transaction and that number has to have somewhere
to come from the moment checkpoint 3 makes its first call. Adding the plumbing
afterwards means changing the harness, the report, the JSON artifact and the chart
at the same time as adding the model -- and then having no idea which of the two
changes moved the number.

Today every batch reports zero. Checkpoint 3's client calls :meth:`record` after
each response with ``response.usage``; nothing else about the harness changes.

Lives under ``pipeline/llm/`` because that is where anything LLM-shaped belongs,
even a dataclass. It imports no client -- the boundary test greps for that.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class LlmUsage:
    """Tokens spent on one batch. Field names mirror the Messages API response."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: "LlmUsage") -> "LlmUsage":
        return LlmUsage(
            calls=self.calls + other.calls,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
        )

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "total_tokens": self.total_tokens,
        }


ZERO_USAGE = LlmUsage()


class UsageLedger:
    """Per-batch token accounting, accumulated as the pipeline runs."""

    def __init__(self) -> None:
        self._by_batch: dict[int, LlmUsage] = {}

    def record(
        self,
        batch: int,
        *,
        calls: int = 1,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_input_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
    ) -> None:
        """Add one response's usage to a batch's running total."""
        self._by_batch[batch] = self._by_batch.get(batch, ZERO_USAGE) + LlmUsage(
            calls=calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
        )

    def usage_for(self, batch: int) -> LlmUsage:
        return self._by_batch.get(batch, ZERO_USAGE)

    def total(self) -> LlmUsage:
        total = ZERO_USAGE
        for usage in self._by_batch.values():
            total = total + usage
        return total

    def is_empty(self) -> bool:
        return self.total() == ZERO_USAGE

    def scaled(self, factor: int) -> "UsageLedger":
        """A copy with every count multiplied. Used only by the harness's own tests."""
        clone = UsageLedger()
        for batch, usage in self._by_batch.items():
            clone._by_batch[batch] = replace(
                usage,
                calls=usage.calls * factor,
                input_tokens=usage.input_tokens * factor,
                output_tokens=usage.output_tokens * factor,
            )
        return clone
