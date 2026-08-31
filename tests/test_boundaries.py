"""Boundaries that are graded criteria, enforced in code rather than in the README.

Three of them. The pipeline must never read the answer key, LLM calls must live only
in ``pipeline/llm/``, and matching must never become probabilistic. All three are
easy to state in a README and easy to break in a repo, which is why they are
asserted here rather than when they are first at risk.
"""

from __future__ import annotations

import re
from pathlib import Path

from pipeline.config import REPO_ROOT

SOURCE_DIRS = ["pipeline", "generator", "harness", "tools", "tests"]
TRUTH_REFERENCE = re.compile(r"""data[/\\]truth|["']truth["']|DEFAULT_TRUTH""")
LLM_IMPORT = re.compile(r"^\s*(?:from|import)\s+anthropic\b", re.MULTILINE)
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
