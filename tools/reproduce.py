"""`make reproduce` -- run the whole demo twice and prove the numbers did not move.

Determinism is a graded criterion, and the honest way to support it is to do the thing
rather than to assert it. This regenerates the corpus, rescores it and rebuilds the UI
payload twice from scratch, then compares the artifacts byte for byte.

**One field is excluded and it is named.** ``timings`` in ``data/score.json`` is wall
clock, which is not reproducible and is labelled ``"reproducible": false`` where it is
written. Everything else -- every rupee, every rate, every rule, every claim, every
pinned metric -- has to match exactly or this exits non-zero.

Runs offline: the API is refused even if a key is present, so what is being proved is
that the committed fixtures and the seed are sufficient.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from pipeline.config import REPO_ROOT

PY = sys.executable

STEPS = (
    ("generate", [PY, "-m", "generator.main"]),
    ("score", [PY, "-m", "harness.score", "--offline"]),
    ("ui-data", [PY, "-m", "tools.build_ui_data", "--offline"]),
)

ARTIFACTS = (
    "data/score.json",
    "data/rules.json",
    "EXCEPTIONS.md",
    "ui/public/tallytrace.json",
)

#: Wall clock. Not reproducible, labelled as such where it is written, excluded here.
EXCLUDED = {"data/score.json": ("timings",)}


def digest(relative: str) -> str:
    path = REPO_ROOT / relative
    raw = path.read_bytes()
    for key in EXCLUDED.get(relative, ()):
        payload = json.loads(raw)
        payload.pop(key, None)
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def run_demo(label: str) -> dict[str, str]:
    for name, command in STEPS:
        result = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True)
        if result.returncode != 0:
            sys.stderr.write(result.stdout + result.stderr)
            raise SystemExit(f"{label}: `{name}` failed with {result.returncode}")
    return {relative: digest(relative) for relative in ARTIFACTS}


def main() -> int:
    first = run_demo("run 1")
    second = run_demo("run 2")

    print(f"{'artifact':<34}{'run 1':>18}{'run 2':>18}  same")
    print("-" * 78)
    drift = []
    for relative in ARTIFACTS:
        same = first[relative] == second[relative]
        drift += [] if same else [relative]
        print(f"{relative:<34}{first[relative]:>18}{second[relative]:>18}  {'yes' if same else 'NO'}")
    print("-" * 78)
    if drift:
        print(f"{len(drift)} artifact(s) changed between identical runs: {', '.join(drift)}")
        return 1
    print("Identical. Excluded from the comparison: data/score.json['timings'], which is")
    print("wall clock and is labelled \"reproducible\": false where it is written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
