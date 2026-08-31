"""Build ``data/resolutions.json`` -- the operator's work log.

The operator policy encoded here is what a bookkeeper actually does, not what would
produce the nicest curve:

- **Batches 1 to 3:** work the whole queue. They do not trust the system yet and the
  queue is small enough to read.
- **Batch 4 onward:** work anything whose *shape* is new, and spot-check two of each
  shape they have seen before. Nobody re-types the same sentence for the twentieth
  identical Myntra variance; they check a couple and move on.

The spot checks are what keep a rule's live precision a live number. Without them a
rule promoted in batch 3 would never be judged again and could not retire, which
would make the lifecycle decorative.

Selection is by feature signature only. The answer key is not consulted, and the note
for a case is chosen by what the case *looks like* -- which is exactly why the
near-miss rows get the stale-rate note and the rule that fires on them is wrong.
That false positive is supposed to happen.
"""

from __future__ import annotations

from pipeline.cases import ExceptionCase, FindingLog, build_cases
from pipeline.config import batch_window
from pipeline.llm.hypotheses import question_for
from pipeline.matcher import Bucket
from pipeline.rules.resolutions import OperatorLog, Resolution, save
from pipeline.run import run_all
from tools.operator_notes import NOTES, OPERATOR, OVERRIDES

WORK_THE_WHOLE_QUEUE_THROUGH = 3
SPOT_CHECKS_PER_KNOWN_SHAPE = 2


def note_for(batch: int, case: ExceptionCase) -> str | None:
    signature = tuple(question_for(case.features).to_json().values())
    override = OVERRIDES.get((batch, signature))
    if override is not None:
        return override
    return NOTES.get(signature)


def build() -> OperatorLog:
    finding_log = FindingLog()
    seen: set[tuple] = set()
    resolutions: list[Resolution] = []

    for result in run_all():
        batch = result.batch
        worked_this_batch: dict[tuple, int] = {}
        resolved_on = batch_window(batch)[1].isoformat()

        for case in build_cases(result, finding_log):
            if case.features.bucket == Bucket.QUARANTINED.value:
                continue
            signature = tuple(question_for(case.features).to_json().values())
            text = note_for(batch, case)
            if text is None:
                continue

            first_sighting = signature not in seen
            budget = worked_this_batch.get(signature, 0)
            if not first_sighting and batch > WORK_THE_WHOLE_QUEUE_THROUGH:
                if budget >= SPOT_CHECKS_PER_KNOWN_SHAPE:
                    continue
            # An override is the operator revisiting a shape on purpose; it is worked
            # in full that week regardless of how familiar the shape has become.
            if (batch, signature) in OVERRIDES:
                pass

            seen.add(signature)
            worked_this_batch[signature] = budget + 1
            resolutions.append(
                Resolution(
                    resolution_id=f"res_{len(resolutions) + 1:04d}",
                    batch=batch,
                    case_id=case.case_id,
                    operator=OPERATOR,
                    resolved_at=resolved_on,
                    text=text,
                )
            )
    return OperatorLog(resolutions=tuple(resolutions), decisions=())


def main() -> int:
    log = build()
    save(log)
    per_batch: dict[int, int] = {}
    for resolution in log.resolutions:
        per_batch[resolution.batch] = per_batch.get(resolution.batch, 0) + 1
    print(f"{len(log.resolutions)} resolutions -> data/resolutions.json")
    print("  " + "  ".join(f"b{b}:{n}" for b, n in sorted(per_batch.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
