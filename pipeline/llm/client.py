"""The only module in the repo that imports ``anthropic``.

``tests/test_boundaries.py`` greps the whole tree and fails if that stops being
true. The boundary is a graded criterion and it is worth exactly as much as its
enforcement, so it is a test rather than a paragraph.

Three properties this client guarantees to everything above it:

**Cache first, always.** A question whose hash is already on disk is answered from
disk and never leaves the machine. A second ``make demo`` costs nothing and produces
identical bytes.

**Schema in the request, not in the prose.** The output type is handed to the API as
a forced tool call whose ``input_schema`` carries the frozen enum. The model's reply
is then validated against the same pydantic model. A cause outside the enum fails
validation and raises; there is no fallback branch, because a fallback is how an
invented cause reaches a bookkeeper wearing a confidence score.

**No key, no silent degradation.** With nothing in the cache and no API key, the
call raises :class:`~pipeline.llm.cache.CacheMiss`. It does not return an empty
hypothesis and it does not guess.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from pipeline.llm.cache import (
    SOURCE_API,
    CacheEntry,
    CacheMiss,
    ResponseCache,
    estimate_tokens,
    key_for,
)
from pipeline.llm.schemas import json_schema
from pipeline.llm.usage import UsageLedger

Output = TypeVar("Output", bound=BaseModel)

DEFAULT_MAX_TOKENS = 700
API_KEY_ENV = "ANTHROPIC_API_KEY"


class SchemaViolation(ValueError):
    """The model returned something the schema does not permit. Never softened."""


@dataclass(frozen=True)
class Ask:
    """One question: the prompts, the output type, and what to call the tool."""

    task: str                    # hypothesis | induction | intent
    system: str
    user: str
    output: type[BaseModel]
    tool_name: str


class LlmClient:
    """Cache-first, schema-constrained, temperature-zero."""

    def __init__(
        self,
        *,
        model: str,
        cache: ResponseCache | None = None,
        ledger: UsageLedger | None = None,
        chars_per_token: Decimal = Decimal("3.6"),
        allow_network: bool = True,
    ) -> None:
        self.model = model
        self.cache = cache or ResponseCache()
        self.ledger = ledger or UsageLedger()
        self.chars_per_token = chars_per_token
        self.allow_network = allow_network
        #: True once any answer billed to the ledger carried estimated rather than
        #: metered token counts. The report says so where the number is printed.
        self.tokens_estimated = False
        self._client: Any = None

    # -- transport --------------------------------------------------------- #

    def _api(self) -> Any:
        """Build the SDK client on first use, so an offline run never imports a key."""
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=os.environ[API_KEY_ENV])
        return self._client

    def _can_call(self) -> bool:
        return self.allow_network and bool(os.environ.get(API_KEY_ENV))

    def _call(self, ask: Ask, schema: dict[str, Any], key: str) -> CacheEntry:
        """Ask the model once, at temperature zero, with the schema forced."""
        response = self._api().messages.create(
            model=self.model,
            max_tokens=DEFAULT_MAX_TOKENS,
            temperature=0,
            system=ask.system,
            messages=[{"role": "user", "content": ask.user}],
            tools=[{
                "name": ask.tool_name,
                "description": f"Return the {ask.task} as structured data.",
                "input_schema": schema,
            }],
            tool_choice={"type": "tool", "name": ask.tool_name},
        )
        payload = next(
            (block.input for block in response.content if getattr(block, "type", "") == "tool_use"),
            None,
        )
        if payload is None:
            raise SchemaViolation(f"{ask.task}: model returned no tool call")

        return CacheEntry(
            key=key,
            task=ask.task,
            model=self.model,
            source=SOURCE_API,
            request={"system": ask.system, "user": ask.user, "tool": ask.tool_name},
            response=dict(payload),
            input_tokens=int(response.usage.input_tokens),
            output_tokens=int(response.usage.output_tokens),
        )

    # -- public surface ---------------------------------------------------- #

    def ask(self, ask: Ask, batch: int, output_type: type[Output]) -> Output:
        """Answer one question, from the cache if possible, and bill the batch."""
        schema = json_schema(ask.output)
        key = key_for(model=self.model, system=ask.system, user=ask.user, schema=schema)

        entry = self.cache.get(key)
        cached = entry is not None
        if entry is None:
            if not self._can_call():
                raise CacheMiss(
                    f"{ask.task}: no cached answer for {key} and no {API_KEY_ENV} to ask with. "
                    f"Run `make llm-fixtures` with a key set, or restore data/llm_cache/."
                )
            entry = self._call(ask, schema, key)
            self.cache.put(entry)

        self._bill(batch, entry, cached=cached)
        return _validate(entry, output_type)

    def _bill(self, batch: int, entry: CacheEntry, *, cached: bool) -> None:
        """Record what this answer cost.

        A cache hit is billed as a cache *read*, at the cache-read rate, rather than
        as free. Zero would be the flattering number and it would also be wrong: the
        first run paid for the answer, and a per-transaction cost that only counts
        the runs where the disk happened to be empty is not a cost.
        """
        self.tokens_estimated = self.tokens_estimated or entry.tokens_are_estimated
        tokens_in = entry.input_tokens or estimate_tokens(
            entry.request.get("system", "") + entry.request.get("user", ""), self.chars_per_token
        )
        tokens_out = entry.output_tokens or estimate_tokens(
            json.dumps(entry.response, ensure_ascii=False), self.chars_per_token
        )
        if cached:
            self.ledger.record(batch, cache_read_input_tokens=tokens_in, output_tokens=tokens_out)
        else:
            self.ledger.record(batch, input_tokens=tokens_in, output_tokens=tokens_out)


def _validate(entry: CacheEntry, output_type: type[Output]) -> Output:
    """Parse the model's reply against the schema. A violation is an error, not a default."""
    try:
        return output_type.model_validate(entry.response)
    except ValidationError as error:
        raise SchemaViolation(
            f"{entry.task} {entry.key}: model output violates {output_type.__name__}: {error}"
        ) from error


def client_from(
    pricing_model: str,
    *,
    cache_dir: Path | None = None,
    ledger: UsageLedger | None = None,
    chars_per_token: Decimal = Decimal("3.6"),
) -> LlmClient:
    return LlmClient(
        model=pricing_model,
        cache=ResponseCache(cache_dir),
        ledger=ledger,
        chars_per_token=chars_per_token,
    )
