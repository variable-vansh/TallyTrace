"""Model responses, on disk, keyed by a hash of exactly what was asked.

Determinism is a graded criterion and ``temperature=0`` does not buy it on its own:
the same prompt to the same model on two different days is not guaranteed to be the
same tokens. A cache is what makes ``make demo`` produce identical numbers twice, and
it is what makes a second run cost nothing.

The key is a hash of the model, the system prompt, the user prompt and the output
schema. Change any of them and it is a different question, so it misses and is asked
again -- which is the behaviour you want: a prompt edit that silently reused an old
answer would be the worst kind of stale.

Every entry records its ``source``. That field exists because the fixtures in this
repo did not all come from the HTTP API, and a cost report built on tokens that were
estimated rather than metered has to say so where the number is produced rather than
in a footnote someone can skip.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from pipeline.config import REPO_ROOT

CACHE_DIR = REPO_ROOT / "data" / "llm_cache"

#: An entry the HTTP API produced. Token counts are metered by the API.
SOURCE_API = "api"
#: An entry produced by the same model through a different transport, with the
#: request and response recorded verbatim. Token counts are *estimated* -- see
#: ``estimate_tokens`` -- and every report that shows them says so.
SOURCE_TRANSCRIPT = "transcript"


class CacheMiss(LookupError):
    """Asked something no fixture answers, with no way to ask the model.

    Raised rather than defaulted. A pipeline that silently produces an empty
    hypothesis when the cache misses reports a clean run over questions nobody
    answered, which is the failure mode this whole layer exists to avoid.
    """


def key_for(*, model: str, system: str, user: str, schema: dict[str, Any]) -> str:
    """A stable hash of the entire question."""
    payload = json.dumps(
        {"model": model, "system": system, "user": user, "schema": schema},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class CacheEntry:
    """One question and the answer it got."""

    key: str
    task: str
    model: str
    source: str
    request: dict[str, Any]
    response: dict[str, Any]
    input_tokens: int
    output_tokens: int

    @property
    def tokens_are_estimated(self) -> bool:
        return self.source != SOURCE_API

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "task": self.task,
            "model": self.model,
            "source": self.source,
            "usage": {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens},
            "request": self.request,
            "response": self.response,
        }


def _from_json(payload: dict[str, Any]) -> CacheEntry:
    usage = payload.get("usage", {})
    return CacheEntry(
        key=str(payload["key"]),
        task=str(payload["task"]),
        model=str(payload["model"]),
        source=str(payload["source"]),
        request=dict(payload["request"]),
        response=dict(payload["response"]),
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
    )


class ResponseCache:
    """A directory of JSON files, one per question."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or CACHE_DIR

    def path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> CacheEntry | None:
        path = self.path_for(key)
        if not path.exists():
            return None
        return _from_json(json.loads(path.read_text(encoding="utf-8")))

    def put(self, entry: CacheEntry) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path_for(entry.key).write_text(
            json.dumps(entry.to_json(), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def entries(self) -> list[CacheEntry]:
        if not self.directory.exists():
            return []
        return [
            _from_json(json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(self.directory.glob("*.json"))
        ]


def estimate_tokens(text: str, chars_per_token: Decimal) -> int:
    """Token count for a transcript-sourced entry, from character length.

    An approximation, and labelled as one everywhere it surfaces. The alternative --
    recording zero -- would report a model-backed pipeline as free, which is a more
    misleading number than an approximate one.
    """
    if chars_per_token <= 0:
        raise ValueError("chars_per_token must be positive")
    return int((Decimal(len(text)) / chars_per_token).to_integral_value())
