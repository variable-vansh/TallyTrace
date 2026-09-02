"""LLM job 3 -- a plain-language question, mapped onto a registered metric.

The model's whole contribution to the reporting surface is choosing an id from a list
of ten. It does not write SQL, it does not compute, and it does not see a single row
of data. What it sees is the registry's own catalogue -- each metric's id, its
description, its unit and the groupings it supports -- rendered into the prompt from
:func:`pipeline.metrics.registry.catalogue`, so the prompt cannot drift from the code
it is describing.

**Why the limit is the feature.** Free-form question answering over a warehouse means
generated SQL, and enterprise text-to-SQL execution accuracy runs roughly 21-39% on
realistic schemas. The failures are silent: a syntactically fine query returns a
number that looks like an answer. A closed registry can be wrong in exactly one way --
picking the wrong id out of ten - and the restatement puts that choice in front of a
human before anything runs.

**Three outcomes, two of which are not answers.** Ambiguity gets one clarifying
question rather than a guess. A question the registry cannot answer gets a refusal
rather than a plausible adjacent chart. Both are constrained in the schema.
"""

from __future__ import annotations

from typing import Any

from pipeline.llm.client import Ask, LlmClient
from pipeline.llm.schemas import MetricIntent
from pipeline.metrics.registry import catalogue

SYSTEM = """You map a question about a reconciliation dashboard onto exactly one metric \
from a fixed registry, or you decline.

You never compute anything, you never write a query, and you never see the data. You \
choose an id and its parameters, and a deterministic function does the rest.

Rules you must follow:
- Choose only from the metric ids in the schema. There is no nearest match and no \
"closest available" answer.
- Choose a grouping the metric supports. The catalogue below lists them per metric.
- restatement is required on every outcome. Write what is about to be computed in one \
plain sentence, as it will be shown to the operator for confirmation before it runs. \
Name the metric in words, the grouping, and any channel or batch range you set.
- Use outcome "clarify" when two metrics could genuinely be meant, or when the question \
turns on a distinction the registry draws and the asker did not. Ask exactly one \
question. Do not also pick a metric.
- Use outcome "refuse" when nothing in the registry answers the question. Say plainly \
what is not available. Do not offer a different chart as a consolation, and do not \
suggest the answer might be approximated by something adjacent -- an approximate \
answer to a money question is worse than no answer, because nobody checks it.
- Set from_batch and to_batch only if the question names a period. A batch is one week; \
batch one is the earliest."""

TOOL = "map_to_metric"


def render_catalogue() -> str:
    """The registry, as the model sees it. Generated, so it cannot go stale."""
    lines = []
    for entry in catalogue():
        lines.append(
            f"- {entry['metric_id']} [{entry['unit']}] "
            f"(group by: {', '.join(entry['groupings'])})\n"
            f"    {entry['description']}"
        )
    return "\n".join(lines)


def render(question: str) -> str:
    return "\n".join(
        [
            "The registry of metrics that can be computed:",
            "",
            render_catalogue(),
            "",
            "Channels in this business: amazon, flipkart, myntra, website, offline.",
            "The corpus is ten weekly batches, numbered one to ten.",
            "",
            "The operator asked:",
            "",
            f'    "{question.strip()}"',
            "",
            "Map it, ask one clarifying question, or refuse.",
        ]
    )


def ask_for(question: str) -> Ask:
    return Ask(
        task="intent",
        system=SYSTEM,
        user=render(question),
        output=MetricIntent,
        tool_name=TOOL,
    )


def map_question(client: LlmClient, question: str, batch: int) -> MetricIntent:
    """One question, mapped. Cached by the question text and the rendered catalogue."""
    return client.ask(ask_for(question), batch, MetricIntent)


def to_json(intent: MetricIntent) -> dict[str, Any]:
    return intent.model_dump(mode="json")
