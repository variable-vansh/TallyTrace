"""The LLM boundary: where it may live, what it may say, and what it costs.

Three graded claims are enforced here rather than described:

- calls happen in ``pipeline/llm/`` and nowhere else, and the deterministic half of
  the learning loop imports nothing from it that could make a call;
- output is constrained by the schema, and a cause outside the frozen enum is a hard
  error with no fallback branch;
- the same question asked twice is answered from disk, identically, for free.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from pipeline.config import CONFIG_DIR, REPO_ROOT, causes, load_yaml
from pipeline.llm.cache import (
    SOURCE_API, SOURCE_TRANSCRIPT, CacheEntry, CacheMiss, ResponseCache, estimate_tokens, key_for,
)
from pipeline.llm.client import Ask, LlmClient, SchemaViolation
from pipeline.llm.hypotheses import Question, ask_for, question_for
from pipeline.llm.schemas import Hypothesis, InducedRule, json_schema
from pipeline.llm.usage import UsageLedger
from pipeline.models import Cause

D = Decimal
LLM_IMPORT = re.compile(r"^\s*(?:from|import)\s+anthropic\b", re.MULTILINE)


def entry(key: str, response: dict, source: str = SOURCE_TRANSCRIPT) -> CacheEntry:
    return CacheEntry(
        key=key, task="hypothesis", model="claude-opus-5", source=source,
        request={"system": "s", "user": "u", "tool": "record_hypothesis"},
        response=response, input_tokens=100, output_tokens=40,
    )


def ask() -> Ask:
    return Ask(task="hypothesis", system="s", user="u", output=Hypothesis,
               tool_name="record_hypothesis")


# --------------------------------------------------------------------------- #
# Where the model may live
# --------------------------------------------------------------------------- #


def test_the_deterministic_half_of_the_loop_cannot_reach_a_client() -> None:
    """``pipeline/rules/`` is predicate evaluation. No model import, ever.

    ``tests/test_boundaries.py`` already asserts the repo-wide rule. This one is
    narrower and about the design claim: application is arithmetic, and the package
    that does it must not be one edit away from asking a model instead.
    """
    for path in sorted((REPO_ROOT / "pipeline" / "rules").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert not LLM_IMPORT.search(source), path
        assert "pipeline.llm.client" not in source, path


def test_only_the_client_module_imports_anthropic() -> None:
    offenders = [
        path.name
        for path in sorted((REPO_ROOT / "pipeline" / "llm").glob("*.py"))
        if LLM_IMPORT.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == ["client.py"]


# --------------------------------------------------------------------------- #
# The schema is the constraint
# --------------------------------------------------------------------------- #


def test_the_frozen_enum_is_inlined_in_the_schema_not_just_asked_for_in_the_prompt() -> None:
    schema = json_schema(Hypothesis)
    assert schema["properties"]["cause"]["enum"] == [c.value for c in Cause]
    assert "$ref" not in json.dumps(schema)


def test_the_schema_enum_and_the_config_file_agree() -> None:
    configured = [entry["cause"] for entry in causes()["causes"]]
    assert json_schema(InducedRule)["properties"]["cause"]["enum"] == configured


def test_a_cause_outside_the_enum_is_a_hard_error_with_no_fallback() -> None:
    """Not an 'unknown' bucket, not the nearest match. A reconciliation system that
    invents a cause is worse than one that abstains."""
    cache = ResponseCache(Path("/nonexistent"))
    client = LlmClient(model="claude-opus-5", cache=cache, allow_network=False)
    key = key_for(model="claude-opus-5", system="s", user="u", schema=json_schema(Hypothesis))
    cache.get = lambda _: entry(  # type: ignore[method-assign]
        key, {"cause": "vendor_being_difficult", "hypothesis": "they are being difficult",
              "confidence": "0.9"}
    )
    with pytest.raises(SchemaViolation, match="violates Hypothesis"):
        client.ask(ask(), batch=1, output_type=Hypothesis)


def test_confidence_is_stored_as_a_decimal_so_a_threshold_comparison_is_exact() -> None:
    assert isinstance(Hypothesis.model_validate(
        {"cause": "rounding_variance", "hypothesis": "paise-level drift", "confidence": 0.85}
    ).confidence, Decimal)


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #


def test_a_missing_answer_with_no_key_raises_rather_than_guessing(tmp_path: Path) -> None:
    """No silent degradation. An empty hypothesis on a cache miss would report a clean
    run over questions nobody answered."""
    client = LlmClient(model="claude-opus-5", cache=ResponseCache(tmp_path), allow_network=False)
    with pytest.raises(CacheMiss, match="no cached answer"):
        client.ask(ask(), batch=1, output_type=Hypothesis)


def test_the_same_question_is_answered_from_disk_the_second_time(tmp_path: Path) -> None:
    ledger = UsageLedger()
    cache = ResponseCache(tmp_path)
    client = LlmClient(model="claude-opus-5", cache=cache, ledger=ledger, allow_network=False)
    key = key_for(model="claude-opus-5", system="s", user="u", schema=json_schema(Hypothesis))
    cache.put(entry(key, {"cause": "rounding_variance", "hypothesis": "paise drift, ignore it",
                          "confidence": "0.7"}))

    first = client.ask(ask(), batch=1, output_type=Hypothesis)
    second = client.ask(ask(), batch=1, output_type=Hypothesis)
    assert first == second
    # Billed as a cache read, not as free: the first run paid for the answer.
    assert ledger.usage_for(1).cache_read_input_tokens == 200
    assert ledger.usage_for(1).input_tokens == 0


def test_changing_the_prompt_changes_the_key() -> None:
    """A prompt edit that silently reused an old answer is the worst kind of stale."""
    schema = json_schema(Hypothesis)
    base = key_for(model="claude-opus-5", system="s", user="u", schema=schema)
    assert key_for(model="claude-opus-5", system="s", user="u2", schema=schema) != base
    assert key_for(model="claude-opus-5", system="s2", user="u", schema=schema) != base
    assert key_for(model="claude-sonnet-5", system="s", user="u", schema=schema) != base
    assert key_for(model="claude-opus-5", system="s", user="u", schema={}) != base


def test_the_key_does_not_depend_on_dict_ordering() -> None:
    a = key_for(model="m", system="s", user="u", schema={"x": 1, "y": 2})
    b = key_for(model="m", system="s", user="u", schema={"y": 2, "x": 1})
    assert a == b


def test_a_transcript_entry_declares_that_its_tokens_are_estimated() -> None:
    """Cost built on estimated tokens has to say so where the number is produced."""
    assert entry("k", {}, SOURCE_TRANSCRIPT).tokens_are_estimated
    assert not entry("k", {}, SOURCE_API).tokens_are_estimated


def test_token_estimation_is_proportional_and_refuses_a_nonsense_divisor() -> None:
    chars_per_token = Decimal(str(load_yaml(CONFIG_DIR / "pricing.yaml")["estimated_chars_per_token"]))
    assert estimate_tokens("x" * 360, chars_per_token) == 100
    with pytest.raises(ValueError):
        estimate_tokens("x", D("0"))


# --------------------------------------------------------------------------- #
# Deduplication by question
# --------------------------------------------------------------------------- #


def test_two_numerically_identical_cases_ask_one_question() -> None:
    """Asking the same question 89 times produces 89 identical answers and a cost
    report that overstates the model by two orders of magnitude."""
    from pipeline.cases import CaseFeatures

    def features(variance: str) -> CaseFeatures:
        return CaseFeatures(
            channel="myntra", reason="fee_variance_outside_tolerance", bucket="variance",
            transaction_type=None, direction="short", variance_inr=D(variance),
            fee_variance_pct=D("8.80"), net_variance_pct=D("-3.74"),
            days_after_settlement=None, days_since_order=None, days_late=None,
        )

    assert question_for(features("32.73")) == question_for(features("103.96"))
    assert ask_for(question_for(features("32.73"))).user == ask_for(
        question_for(features("103.96"))
    ).user


def test_a_different_variance_band_is_a_different_question() -> None:
    from pipeline.cases import CaseFeatures

    def features(pct: str) -> CaseFeatures:
        return CaseFeatures(
            channel="myntra", reason="fee_variance_outside_tolerance", bucket="variance",
            transaction_type=None, direction="short", variance_inr=D("32.73"),
            fee_variance_pct=D(pct), net_variance_pct=D("-3.74"),
            days_after_settlement=None, days_since_order=None, days_late=None,
        )

    assert question_for(features("8.80")) != question_for(features("40.00"))


def test_day_counts_become_bands_so_a_rule_is_about_a_lag_not_a_tuesday() -> None:
    from pipeline.llm.hypotheses import _bucket

    assert _bucket(7) == "1-7"
    assert _bucket(8) == _bucket(14) == "8-14"
    assert _bucket(21) == "15-21"
    assert _bucket(28) == "22+"
    assert _bucket(None) is None


# --------------------------------------------------------------------------- #
# The shipped cache
# --------------------------------------------------------------------------- #


def test_every_cached_answer_still_validates_against_its_schema() -> None:
    """A schema change that invalidates the fixtures must fail here rather than in
    the middle of a demo."""
    entries = ResponseCache().entries()
    if not entries:
        pytest.skip("run `make llm-fixtures` first")
    models = {"hypothesis": Hypothesis, "induction": InducedRule}
    for cached in entries:
        models[cached.task].model_validate(cached.response)


def test_no_cached_rule_names_a_transaction() -> None:
    from pipeline.rules.models import contains_identifier

    entries = [e for e in ResponseCache().entries() if e.task == "induction"]
    if not entries:
        pytest.skip("run `make llm-fixtures` first")
    for cached in entries:
        assert contains_identifier(json.dumps(cached.response)) is None, cached.key
