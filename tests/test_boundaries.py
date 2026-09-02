"""Boundaries that are graded criteria, enforced in code rather than in the README.

Five of them. The pipeline must never read the answer key, LLM calls must live only
in ``pipeline/llm/``, matching must never become probabilistic, the metric registry
must never construct a model, and the claims register must never call one either.
Every one is easy to state in a README and easy to break in a repo, which is why they
are asserted here rather than when they are first at risk.
"""

from __future__ import annotations

import re
from pathlib import Path

from pipeline.config import REPO_ROOT

SOURCE_DIRS = ["pipeline", "generator", "harness", "tools", "tests"]
TRUTH_REFERENCE = re.compile(r"""data[/\\]truth|["']truth["']|DEFAULT_TRUTH""")
LLM_IMPORT = re.compile(r"^\s*(?:from|import)\s+anthropic\b", re.MULTILINE)
#: Constructing or calling the model, as opposed to naming one of its output schemas.
#: ``pipeline.llm.schemas`` is a pydantic module with no transport in it, so importing
#: a type from it is not a model call and is deliberately not matched here.
CLIENT_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+pipeline\.llm\.(client|hypotheses|induction|drafts|intent)\b",
    re.MULTILINE,
)
FUZZY_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(recordlinkage|fuzzywuzzy|rapidfuzz|thefuzz|difflib|jellyfish)\b",
    re.MULTILINE,
)


def python_files(*directories: str) -> list[Path]:
    files: list[Path] = []
    for directory in directories:
        root = REPO_ROOT / directory
        if root.exists():
            files.extend(sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts))
    return files


def test_pipeline_never_references_the_ground_truth_path() -> None:
    """/data/truth is written by the generator and read only by /harness."""
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{index}"
        for path in python_files("pipeline")
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if TRUTH_REFERENCE.search(line)
    ]
    assert offenders == [], f"pipeline reads the answer key at: {offenders}"


def test_only_the_generators_entry_point_names_the_truth_path() -> None:
    """The generator writes the answer key, so exactly one module there may name it.

    ``generator/main.py`` takes the directory as a CLI argument and hands it to the
    writer; nothing deeper down constructs a truth path of its own. One writer and one
    reader means "who could have touched the answers?" has a two-line answer.
    """
    writers = sorted(
        path.name
        for path in python_files("generator")
        if TRUTH_REFERENCE.search(path.read_text(encoding="utf-8"))
    )
    assert writers == ["main.py"], f"the answer key path is named in: {writers}"


def test_the_truth_directory_is_not_importable_from_the_pipeline_package() -> None:
    assert (REPO_ROOT / "data" / "truth").is_dir()
    assert not (REPO_ROOT / "pipeline" / "truth").exists()


def test_llm_client_is_imported_only_under_pipeline_llm() -> None:
    """LLM calls may only appear in pipeline/llm/.

    ``tools/`` is in scope as well as the packages: the fixture writer builds the same
    prompts the client sends, and it would be the obvious place for a shortcut that
    calls a model directly and never goes through the cache.
    """
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in python_files(*SOURCE_DIRS)
        if LLM_IMPORT.search(path.read_text(encoding="utf-8"))
        and path.parent != REPO_ROOT / "pipeline" / "llm"
        and path.name != "test_boundaries.py"
    ]
    assert offenders == [], f"anthropic imported outside pipeline/llm/: {offenders}"


def test_matching_never_becomes_probabilistic() -> None:
    """Exact keys and explicit tolerance bands. No fuzzy or probabilistic linkage.

    In finance a 0.87-confidence match is not a match, it is a liability: it books
    money against an order nobody chose to book it against, and the audit trail
    reads "the algorithm was fairly sure". This is a deliberate design decision, it
    is in the README, and it is asserted here so it survives contact with an agent
    that would rather make the match rate look better.
    """
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in python_files(*SOURCE_DIRS)
        if FUZZY_IMPORT.search(path.read_text(encoding="utf-8"))
        and path.name != "test_boundaries.py"
    ]
    assert offenders == [], f"fuzzy matching imported at: {offenders}"


def test_the_answer_key_is_read_in_exactly_one_module() -> None:
    """The boundary is only real if something is actually on the other side of it.

    ``harness/truth.py`` reads ``data/truth``; nothing else in the harness does. One
    reader means one place to look when asking "could this number have seen the
    answers?", and it keeps the pipeline-side assertion above from being vacuously
    true because nobody reads the key at all.
    """
    readers = sorted(
        path.name
        for path in python_files("harness")
        if TRUTH_REFERENCE.search(path.read_text(encoding="utf-8"))
    )
    assert readers == ["truth.py"], f"the answer key is read in: {readers}"


def test_the_metric_registry_never_constructs_a_model() -> None:
    """The registry computes; the model only selects. Asserted, because it is the claim.

    Every metric is a pure function over ``pipeline.metrics.corpus.Corpus``, which is
    what makes a pinned metric recompute every batch with nothing in the loop. A module
    under ``pipeline/metrics/`` that could build a client would make that a promise
    rather than a property. ``ask.py`` names ``MetricIntent`` -- a pydantic type with no
    transport behind it -- and that is not a model call.
    """
    offenders = [
        f"{path.relative_to(REPO_ROOT)}"
        for path in python_files("pipeline/metrics")
        if CLIENT_IMPORT.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"the metric registry can reach the model at: {offenders}"


def test_the_claims_register_never_calls_a_model_itself() -> None:
    """Drafting is injected as a callable, so the register does no I/O and asks nothing.

    The deadline clock, the routing and the recovery match are arithmetic over config,
    and they have to keep working when the model is unavailable. Passing the drafter in
    is what makes that structural instead of incidental.
    """
    offenders = [
        f"{path.relative_to(REPO_ROOT)}"
        for path in python_files("pipeline/claims")
        if CLIENT_IMPORT.search(path.read_text(encoding="utf-8"))
        and path.name != "cli.py"
    ]
    assert offenders == [], f"the claims register can reach the model at: {offenders}"


def test_no_sql_is_generated_anywhere() -> None:
    """The registry exists so that nothing has to write a query. Nothing does.

    Enterprise text-to-SQL execution accuracy runs roughly 21-39% on realistic schemas
    and its failures are silent, so "we do not generate SQL" is a design claim worth
    more than a paragraph. There is no database in this repo and no query builder;
    this fails if either arrives.
    """
    sql = re.compile(
        r"^\s*(?:from|import)\s+(sqlite3|sqlalchemy|psycopg2?|pymysql|duckdb)\b",
        re.MULTILINE,
    )
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in python_files(*SOURCE_DIRS)
        if sql.search(path.read_text(encoding="utf-8"))
        and path.name != "test_boundaries.py"
    ]
    assert offenders == [], f"a SQL engine is imported at: {offenders}"

