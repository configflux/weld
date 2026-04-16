"""Comparative retrieval runner: grep baseline vs. weld for each task.

For each :class:`~weld.bench_tasks.tasks.AgentTask` this module runs two
retrieval modes over the same query and collects three metrics:

  - token cost    (how many tokens the mode lands in the agent window)
  - accuracy      (precision / recall / F1 of files surfaced vs. the
                   task's ``answer_files`` key)
  - latency       (wall-clock ms per retrieval call)

The design deliberately reuses :mod:`weld.bench.runner` for token
counting and for the grep-mode text baseline, so the comparative mode
and the existing token-cost bench always agree on tokenization.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence, TypeVar

from weld.bench.primitives import Prompt, count_tokens, grep_baseline
from weld.bench_tasks.tasks import AgentTask


# --- Timing -----------------------------------------------------------------

T = TypeVar("T")


def latency_ms(fn: Callable[[], T]) -> tuple[float, T]:
    """Run ``fn`` and return ``(elapsed_ms, return_value)``.

    Uses :func:`time.perf_counter` for monotonic sub-millisecond precision.
    Exceptions from ``fn`` propagate unchanged so callers can surface real
    retrieval failures instead of hiding them behind a zero-latency sample.
    """
    start = time.perf_counter()
    value = fn()
    elapsed = (time.perf_counter() - start) * 1000.0
    return elapsed, value


# --- Accuracy ---------------------------------------------------------------


@dataclass(frozen=True)
class CompareMetrics:
    """Precision/recall/F1 of a retrieval result against an answer key."""

    precision: float
    recall: float
    f1: float
    found_count: int
    expected_count: int
    hit_count: int


def accuracy_metrics(
    found: Sequence[str],
    expected: Sequence[str],
) -> CompareMetrics:
    """Score ``found`` against ``expected`` as precision / recall / F1.

    Both inputs are deduplicated before scoring so repeated hits cannot
    inflate precision. When either side is empty all three scores are 0.0
    -- this lets callers average the metric across a task batch without a
    special case for tasks that returned nothing.
    """
    found_set = {f for f in found if f}
    expected_set = {e for e in expected if e}
    hit = found_set & expected_set
    tp = len(hit)
    precision = tp / len(found_set) if found_set else 0.0
    recall = tp / len(expected_set) if expected_set else 0.0
    f1 = (
        (2 * precision * recall) / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    return CompareMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        found_count=len(found_set),
        expected_count=len(expected_set),
        hit_count=tp,
    )


# --- Retrieval adapters -----------------------------------------------------

# Files in a grep match chunk are prefixed with `# file: <rel>` -- the grep
# baseline in weld.bench.runner emits exactly that marker. This regex lets
# us reconstruct the file list without re-running the grep walk.
_GREP_FILE_MARKER = re.compile(r"^# file: (\S+)$", re.MULTILINE)


def grep_files_and_text(task: AgentTask, root: Path) -> tuple[list[str], str]:
    """Run the grep baseline and return ``(files, text)``.

    ``files`` are repo-relative paths in the order grep landed them. ``text``
    is the same string :func:`weld.bench.primitives.grep_baseline` returns,
    fed into :func:`count_tokens` for the token-cost metric.
    """
    prompt = Prompt(
        id=task.id,
        prompt=task.prompt,
        category=task.category,
        term=task.term,
        symbol=task.symbol,
    )
    text = grep_baseline(prompt, root)
    files = _GREP_FILE_MARKER.findall(text)
    return files, text


def weld_files_and_text(
    task: AgentTask, root: Path,
) -> tuple[list[str], str]:
    """Run the weld retrieval path and return ``(files, text)``.

    Surface choice mirrors :func:`weld.bench.runner.weld_cli_baseline`:

      - navigation -> ``brief``
      - callgraph  -> ``references`` (when a symbol is supplied)
      - otherwise  -> ``query``

    The files list is collected by walking the returned JSON structures
    and harvesting any ``file`` properties plus any explicit ``files``
    entries (the references surface emits both).
    """
    from weld.brief import brief as _brief
    from weld.file_index import find_files as _find_files
    from weld.file_index import load_file_index as _load_file_index
    from weld.graph import Graph as _Graph

    g = _Graph(root)
    g.load()
    if task.category == "navigation":
        result = _brief(g, task.term, limit=20)
    elif task.category == "callgraph" and task.symbol:
        refs = g.references(task.symbol)
        try:
            index = _load_file_index(root)
            refs["files"] = _find_files(index, task.symbol).get("files", [])
        except FileNotFoundError:
            refs.setdefault("files", [])
        result = refs
    else:
        result = g.query(task.term, limit=20)

    files = _collect_files(result)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    return files, text


def _collect_files(obj: object) -> list[str]:
    """Walk ``obj`` and harvest any file paths it mentions.

    Weld retrieval payloads carry file information in two places:

      - Node dicts expose the source path as ``props.file`` or as a
        top-level ``file`` key (``brief`` flattens this).
      - The ``references`` surface includes a top-level ``files`` list
        populated from the file index.

    This helper handles both without needing per-surface knowledge.
    Order of first appearance is preserved so downstream rendering is
    deterministic; duplicates are suppressed.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(value: object) -> None:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            out.append(value)

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                if key == "file":
                    _add(val)
                elif key == "files" and isinstance(val, list):
                    for item in val:
                        if isinstance(item, str):
                            _add(item)
                        elif isinstance(item, dict):
                            _add(item.get("file"))
                            _add(item.get("path"))
                else:
                    _walk(val)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(obj)
    return out


# --- Result container -------------------------------------------------------


@dataclass
class CompareResult:
    """One task scored across both retrieval modes."""

    task: AgentTask
    grep_tokens: int
    weld_tokens: int
    grep_latency_ms: float
    weld_latency_ms: float
    grep_accuracy: CompareMetrics
    weld_accuracy: CompareMetrics
    grep_files: list[str] = field(default_factory=list)
    weld_files: list[str] = field(default_factory=list)

    @property
    def token_reduction(self) -> float | None:
        """Fractional reduction of weld vs. grep token cost."""
        if self.grep_tokens <= 0:
            return None
        return 1.0 - (self.weld_tokens / self.grep_tokens)

    @property
    def latency_reduction(self) -> float | None:
        """Fractional reduction of weld vs. grep latency."""
        if self.grep_latency_ms <= 0:
            return None
        return 1.0 - (self.weld_latency_ms / self.grep_latency_ms)

    def to_dict(self) -> dict:
        """Return a JSON-serializable view for the ``--report`` artifact."""
        return {
            "task": {
                "id": self.task.id,
                "prompt": self.task.prompt,
                "category": self.task.category,
                "term": self.task.term,
                "symbol": self.task.symbol,
                "answer_files": list(self.task.answer_files),
            },
            "grep": {
                "tokens": self.grep_tokens,
                "latency_ms": self.grep_latency_ms,
                "files": self.grep_files,
                "accuracy": _metrics_to_dict(self.grep_accuracy),
            },
            "weld": {
                "tokens": self.weld_tokens,
                "latency_ms": self.weld_latency_ms,
                "files": self.weld_files,
                "accuracy": _metrics_to_dict(self.weld_accuracy),
            },
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "CompareResult":
        """Rebuild a :class:`CompareResult` from the artifact JSON."""
        task_raw = payload.get("task", {})
        task = AgentTask(
            id=str(task_raw.get("id", "")),
            prompt=str(task_raw.get("prompt", "")),
            category=str(task_raw.get("category", "")),
            term=str(task_raw.get("term", "")),
            answer_files=tuple(task_raw.get("answer_files", []) or []),
            symbol=(
                str(task_raw["symbol"])
                if task_raw.get("symbol")
                else None
            ),
        )
        grep_raw = payload.get("grep", {})
        weld_raw = payload.get("weld", {})
        return cls(
            task=task,
            grep_tokens=int(grep_raw.get("tokens", 0)),
            weld_tokens=int(weld_raw.get("tokens", 0)),
            grep_latency_ms=float(grep_raw.get("latency_ms", 0.0)),
            weld_latency_ms=float(weld_raw.get("latency_ms", 0.0)),
            grep_accuracy=_metrics_from_dict(grep_raw.get("accuracy", {})),
            weld_accuracy=_metrics_from_dict(
                weld_raw.get("accuracy", {})
            ),
            grep_files=list(grep_raw.get("files", []) or []),
            weld_files=list(weld_raw.get("files", []) or []),
        )


def _metrics_to_dict(m: CompareMetrics) -> dict:
    return {
        "precision": m.precision,
        "recall": m.recall,
        "f1": m.f1,
        "found_count": m.found_count,
        "expected_count": m.expected_count,
        "hit_count": m.hit_count,
    }


def _metrics_from_dict(raw: dict) -> CompareMetrics:
    return CompareMetrics(
        precision=float(raw.get("precision", 0.0)),
        recall=float(raw.get("recall", 0.0)),
        f1=float(raw.get("f1", 0.0)),
        found_count=int(raw.get("found_count", 0)),
        expected_count=int(raw.get("expected_count", 0)),
        hit_count=int(raw.get("hit_count", 0)),
    )


# --- Orchestration ----------------------------------------------------------


def run_compare(
    tasks: Iterable[AgentTask],
    root: Path,
) -> list[CompareResult]:
    """Run both retrieval modes for each task and return raw results."""
    results: list[CompareResult] = []
    for task in tasks:
        grep_ms, (grep_files, grep_text) = latency_ms(
            lambda t=task: grep_files_and_text(t, root)
        )
        weld_ms, (weld_files, weld_text) = latency_ms(
            lambda t=task: weld_files_and_text(t, root)
        )
        results.append(
            CompareResult(
                task=task,
                grep_tokens=count_tokens(grep_text),
                weld_tokens=count_tokens(weld_text),
                grep_latency_ms=grep_ms,
                weld_latency_ms=weld_ms,
                grep_accuracy=accuracy_metrics(
                    grep_files, task.answer_files,
                ),
                weld_accuracy=accuracy_metrics(
                    weld_files, task.answer_files,
                ),
                grep_files=list(grep_files),
                weld_files=list(weld_files),
            )
        )
    return results


# Markdown rendering for comparative results lives in
# :mod:`weld.bench_tasks.report` to keep this module focused on
# retrieval and scoring.
