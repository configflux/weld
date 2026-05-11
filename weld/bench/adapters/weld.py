"""``weld`` retrieval adapter for the public benchmark (ADR 0059).

Mirrors the surface choice used by :mod:`weld.bench_tasks.compare`:

  - ``navigation``                       -> ``wd brief``
  - ``callgraph`` (with symbol)          -> ``wd references`` + file index
  - everything else                      -> ``wd query``

The adapter does not require a pre-built graph -- if no graph is on
disk we report ``status="degraded"`` and an empty file list so the
overall run completes and the loss is recorded in the Caveats section
of the report (per ADR 0059 'honest losing'). For the smoke fixture,
we attempt an in-process discovery so weld has something to retrieve;
the discovery itself is best-effort and never raises through the
adapter boundary.

Dependency gate: when the ``tree_sitter`` Python bindings are
not installed and the task targets a language that requires tree-sitter
(C++, C#, Go, Java, Rust, TypeScript -- everything except Python, which
uses the ``python_module`` strategy), the adapter reports
``status='unavailable'`` so per-family aggregates exclude the row
rather than dishonestly scoring it as F1=0.00. This mirrors the
three-gate cascade in the sibling
:mod:`weld.bench.adapters.weld_libclang` adapter. When tree-sitter IS
present but extraction returned 0 nodes, the adapter still reports
``status='ok'`` -- that is the honest "extraction ran but missed"
signal.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from weld.bench._public_runner import AdapterResult, PublicTask
from weld.bench.primitives import count_tokens


_GRAPH_REL = ".weld/graph.json"

# Map of file extensions (lowercase, with leading dot) to the
# tree-sitter language identifier the ``tree_sitter`` strategy expects.
# The set matches ``weld/languages/*.yaml`` entries minus ``python``:
# Python is handled by the ``python_module`` strategy, NOT tree-sitter,
# so a Python-only task must remain runnable even when the tree-sitter
# bindings are absent.
_TREE_SITTER_LANG_BY_EXT: dict[str, str] = {
    # C/C++ (the original bug class -- nlohmann/json scored F1=0.00).
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".cc": "cpp",
    ".c": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".hh": "cpp",
    ".h": "cpp",
    # C#.
    ".cs": "csharp",
    # Go.
    ".go": "go",
    # Java.
    ".java": "java",
    # Rust.
    ".rs": "rust",
    # TypeScript.
    ".ts": "typescript",
    ".tsx": "typescript",
}


def _is_tree_sitter_available() -> bool:
    """Indirection so tests can mock the import probe.

    Returns True iff the umbrella ``tree_sitter`` Python package is
    importable. The per-language bindings (``tree_sitter_cpp``,
    ``tree_sitter_python``, ...) are loaded lazily by
    :mod:`weld.strategies._ts_parse`; gating on the umbrella is enough
    to distinguish "extras not installed" (the bug) from "extras
    installed but extraction missed" (the honest zero we want to
    keep).
    """
    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return False
    return True


def _tree_sitter_languages_for_task(task: PublicTask) -> set[str]:
    """Infer which tree-sitter languages the task's answers require.

    Returns the set of language identifiers (matching
    ``weld/languages/<lang>.yaml``) that appear in the task's answer
    files. Python is intentionally excluded -- its strategy does not
    need tree-sitter, so a Python-only task is NOT gated on the
    tree-sitter bindings.

    An empty set means "this task does not require tree-sitter" and the
    adapter continues to the existing ``_ensure_graph`` path. This
    keeps Python tasks (and tasks with answer files that don't map to
    a known tree-sitter language) runnable in environments where the
    tree-sitter extras are not installed.
    """
    langs: set[str] = set()
    for path in task.answer_files:
        # ``str.rpartition`` is cheaper than constructing a ``Path``
        # and the answer-file paths are already POSIX strings from
        # the YAML manifest.
        _, dot, ext = path.rpartition(".")
        if not dot:
            continue
        lang = _TREE_SITTER_LANG_BY_EXT.get("." + ext.lower())
        if lang is not None:
            langs.add(lang)
    return langs


def _serialize_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _collect_files(obj: object) -> list[str]:
    """Walk ``obj`` and harvest any file paths mentioned.

    Mirrors :func:`weld.bench_tasks.compare._collect_files` so the public
    bench scores the same surface the comparative bench does.
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


def _ensure_graph(repo_root: Path) -> bool:
    """Best-effort: build a graph for ``repo_root`` if none exists.

    Returns True when a graph file exists on disk after the call (built
    or pre-existing). Returns False on any failure -- the adapter then
    reports ``status="degraded"``.

    Discovery returns the in-memory graph; for non-federated single-repo
    roots the public-facing CLI (``weld.discover.main``) writes the JSON
    to disk. Here we replicate that write so the in-process adapter
    doesn't need to shell out to a subprocess.
    """
    graph_path = repo_root / _GRAPH_REL
    if graph_path.exists():
        return True
    try:
        from weld.discover import _dumps_graph, discover as _discover
        from weld.workspace_state import atomic_write_text

        graph_path.parent.mkdir(parents=True, exist_ok=True)
        graph = _discover(
            repo_root,
            safe=True,
            allow_empty=True,
            with_sqlite=False,
        )
        atomic_write_text(graph_path, _dumps_graph(graph))
        return graph_path.exists()
    except Exception:
        return False


def _run_query(task: PublicTask, repo_root: Path) -> object:
    """Dispatch to brief / references / query depending on the family."""
    from weld.brief import brief as _brief
    from weld.file_index import find_files as _find_files
    from weld.file_index import load_file_index as _load_file_index
    from weld.graph import Graph as _Graph

    g = _Graph(repo_root)
    g.load()
    if task.family == "navigation":
        return _brief(g, task.term, limit=20)
    if task.family == "callgraph" and task.symbol:
        refs = g.references(task.symbol)
        try:
            index = _load_file_index(repo_root)
            refs["files"] = _find_files(
                index, task.symbol,
            ).get("files", [])
        except FileNotFoundError:
            refs.setdefault("files", [])
        return refs
    return g.query(task.term, limit=20)


def run(task: PublicTask, repo_root: Path) -> AdapterResult:
    """Run the weld retrieval stack for ``task`` against ``repo_root``."""
    start = time.perf_counter()

    # Dependency gate: if the task targets a tree-sitter-only language
    # and the tree-sitter umbrella package is not importable, the
    # underlying strategy would silently return 0 nodes and the bench
    # would score F1=0.00 -- masking "feature didn't load" as
    # "feature ran and missed". Mirror the libclang adapter's
    # precondition pattern and emit ``unavailable`` so per-family
    # aggregates exclude the row (ADR 0059).
    required_langs = _tree_sitter_languages_for_task(task)
    if required_langs and not _is_tree_sitter_available():
        elapsed = (time.perf_counter() - start) * 1000.0
        return AdapterResult(
            status="unavailable",
            files=[],
            tokens=0,
            duration_ms=elapsed,
            error=(
                "tree-sitter extras not installed "
                "(import tree_sitter failed); task requires "
                f"{sorted(required_langs)}"
            ),
        )

    if not _ensure_graph(repo_root):
        elapsed = (time.perf_counter() - start) * 1000.0
        return AdapterResult(
            status="degraded",
            files=[],
            tokens=0,
            duration_ms=elapsed,
            error="weld graph missing and discovery failed",
        )
    try:
        result = _run_query(task, repo_root)
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000.0
        return AdapterResult(
            status="degraded",
            files=[],
            tokens=0,
            duration_ms=elapsed,
            error=str(exc),
        )
    files = _collect_files(result)
    text = _serialize_json(result)
    elapsed = (time.perf_counter() - start) * 1000.0
    return AdapterResult(
        status="ok",
        files=files,
        tokens=count_tokens(text),
        duration_ms=elapsed,
        ttft_ms=elapsed,
    )


__all__ = ["run"]
