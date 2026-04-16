"""Agent task dataclass and fixture loader.

An :class:`AgentTask` is a single realistic agent scenario with a known
answer key. Fixtures are YAML documents with a top-level ``tasks:`` list.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from weld._yaml import parse_yaml


@dataclass(frozen=True)
class AgentTask:
    """One comparative benchmark task.

    Attributes:
        id: Short stable identifier (``t01`` etc.).
        prompt: Natural-language question an agent might ask.
        category: One of ``navigation``, ``dependency``, ``callgraph`` --
            controls which weld retrieval surface the comparator uses
            (see :mod:`weld.bench_tasks.compare`).
        term: Search token used by both the grep baseline and the weld
            query/brief calls.
        answer_files: Repo-relative paths an unaided operator would
            consider correct hits for this task. Used to score precision
            and recall for both retrieval modes.
        symbol: Optional bare symbol name for callgraph queries.
    """

    id: str
    prompt: str
    category: str
    term: str
    answer_files: tuple[str, ...]
    symbol: str | None = None


def load_tasks(path: Path) -> list[AgentTask]:
    """Load agent tasks from a YAML fixture.

    The fixture format is::

        tasks:
          - id: t01
            prompt: "Where is Store defined?"
            category: navigation
            term: Store
            answer_files:
              - src/store.py
          - id: t02
            prompt: "Who calls Store?"
            category: callgraph
            term: Store
            symbol: Store
            answer_files:
              - src/use_store.py

    Non-dict list entries are silently skipped so the fixture remains
    forgiving of commented-out placeholders.
    """
    data = parse_yaml(path.read_text(encoding="utf-8"))
    items = data.get("tasks", []) if isinstance(data, dict) else data
    out: list[AgentTask] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        answer_files = tuple(
            str(p) for p in (item.get("answer_files") or [])
        )
        out.append(
            AgentTask(
                id=str(item.get("id", "")),
                prompt=str(item.get("prompt", "")),
                category=str(item.get("category", "")),
                term=str(item.get("term", "")),
                answer_files=answer_files,
                symbol=(
                    str(item["symbol"]) if item.get("symbol") else None
                ),
            )
        )
    return out
