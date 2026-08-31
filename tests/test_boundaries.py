"""Boundaries that are graded criteria, enforced in code rather than in the README.

Two of them. The pipeline must never read the answer key, and LLM calls must live
only in ``pipeline/llm/``. Both are trivially true today and easy to break later,
which is exactly why they are asserted now rather than when they are first at risk.
"""

from __future__ import annotations

import re
from pathlib import Path

from pipeline.config import REPO_ROOT

SOURCE_DIRS = ["pipeline", "generator", "harness", "tests"]
TRUTH_REFERENCE = re.compile(r"""data[/\\]truth|["']truth["']|DEFAULT_TRUTH""")
LLM_IMPORT = re.compile(r"^\s*(?:from|import)\s+anthropic\b", re.MULTILINE)


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


def test_the_truth_directory_is_not_importable_from_the_pipeline_package() -> None:
    assert (REPO_ROOT / "data" / "truth").is_dir()
    assert not (REPO_ROOT / "pipeline" / "truth").exists()


def test_llm_client_is_imported_only_under_pipeline_llm() -> None:
    """LLM calls may only appear in pipeline/llm/. Enforced now, before any exist."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in python_files(*SOURCE_DIRS)
        if LLM_IMPORT.search(path.read_text(encoding="utf-8"))
        and path.parent != REPO_ROOT / "pipeline" / "llm"
        and path.name != "test_boundaries.py"
    ]
    assert offenders == [], f"anthropic imported outside pipeline/llm/: {offenders}"
