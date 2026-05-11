"""Libclang variant of the ``weld`` adapter for the public benchmark.

This adapter runs the same retrieval surfaces as
:mod:`weld.bench.adapters.weld` (brief / references / query) but
activates the C++ best-in-class methodology by setting
``WELD_CPP_LIBCLANG=1`` before invoking discovery. The methodology
gates itself on three independent preconditions; this adapter surfaces
each one with a distinct status so the report tells the truth:

  - ``unavailable``  -- the ``[cpp-libclang]`` extra is not installable
                        in this runtime (the ``clang.cindex`` import
                        fails). The row is excluded from per-family
                        aggregates.
  - ``skipped``      -- the extra is present but ``compile_commands.json``
                        is missing (cmake not on PATH at corpus setup
                        time, or the setup command failed). The row
                        renders ``SKIPPED: <reason>`` in the report and
                        is excluded from aggregates.
  - ``ok``           -- both preconditions met; discovery ran with
                        libclang active and the query returned a file
                        list. Metric format matches the tree-sitter
                        ``weld`` adapter so columns line up.

The env var flip is scoped strictly to the discovery call: we capture
the prior value, set ``WELD_CPP_LIBCLANG=1``, and restore the original
on the way out (including on exception) so no leak ever lands in the
parent process.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from weld.bench._public_runner import AdapterResult, PublicTask
from weld.bench.adapters.weld import _GRAPH_REL, _collect_files
from weld.bench.primitives import count_tokens
from weld.strategies._cpp_libclang_db import (
    ENABLE_ENV_VAR,
    find_compile_db,
    is_libclang_available,
)


def _is_libclang_available() -> bool:
    """Indirection so tests can mock the import probe."""
    return is_libclang_available()


def _compile_db_path(repo_root: Path) -> Path | None:
    """Indirection so tests can mock the database lookup."""
    return find_compile_db(repo_root)


def _serialize_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


_BACKUP_GRAPH_REL = ".weld/graph.json.weld_libclang_backup"


def _ensure_graph(repo_root: Path) -> bool:
    """Force a fresh libclang-active discovery for ``repo_root``.

    Unlike the tree-sitter ``weld`` adapter, we cannot reuse an existing
    ``.weld/graph.json`` -- the public-bench runner dispatches the
    ``weld`` adapter first, which writes a graph built WITHOUT
    libclang. If this helper short-circuited on existence, the libclang
    strategy would never run and the libclang column in the report
    would be indistinguishable from the tree-sitter column.

    We therefore back up any pre-existing graph, run discovery again
    with ``WELD_CPP_LIBCLANG=1`` already set by the caller, and rely on
    the matching :func:`_restore_graph` (invoked from ``run``'s finally
    clause) to put the backup back in place once the libclang query is
    done. That isolation matters because a subsequent task in the same
    repo would otherwise see a libclang-flavoured graph when the
    ``weld`` adapter runs.
    """
    graph_path = repo_root / _GRAPH_REL
    backup_path = repo_root / _BACKUP_GRAPH_REL
    try:
        if graph_path.exists():
            # Replace any stale backup from a prior aborted run.
            if backup_path.exists():
                backup_path.unlink()
            graph_path.rename(backup_path)
    except OSError:
        # Worst case: backup didn't land, downstream adapters see the
        # libclang graph for the rest of this run. Still safer than
        # crashing the adapter.
        pass
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


def _restore_graph(repo_root: Path) -> None:
    """Restore the pre-libclang graph (if any) after a libclang call.

    Pairs with :func:`_ensure_graph`. Always called from ``run``'s
    finally clause so adjacent tasks/adapters see the pre-libclang
    state again. Best-effort: a failure here would leak the
    libclang-flavoured graph into the next adapter, but never crashes.
    """
    graph_path = repo_root / _GRAPH_REL
    backup_path = repo_root / _BACKUP_GRAPH_REL
    try:
        if not backup_path.exists():
            # No backup means there was no pre-existing graph. Remove
            # the one we just wrote so the next adapter starts fresh.
            if graph_path.exists():
                graph_path.unlink()
            return
        if graph_path.exists():
            graph_path.unlink()
        backup_path.rename(graph_path)
    except OSError:
        pass


def _run_query_with_env(task: PublicTask, repo_root: Path) -> object:
    """Run discovery + query for ``task`` against ``repo_root``.

    Called from inside the env-var scope so the libclang strategy
    activates. The caller (``run``) is responsible for verifying the
    graph is in place beforehand and for restoring the env var on
    return; here we only do the retrieval.

    Returns the same shape as the tree-sitter weld adapter's
    query result (a JSON-serializable dict / list).
    """
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
    """Run the libclang variant of the weld retrieval stack."""
    start = time.perf_counter()

    # Gate 1: libclang extra installed.
    if not _is_libclang_available():
        return AdapterResult(
            status="unavailable",
            files=[],
            tokens=0,
            duration_ms=(time.perf_counter() - start) * 1000.0,
            error="libclang extra not installed (clang.cindex import failed)",
        )

    # Gate 2: compile_commands.json present (produced by the corpus
    # setup step). Without the DB libclang cannot resolve anything and
    # any output would be indistinguishable from the tree-sitter
    # variant -- emit SKIPPED so the report tells the truth.
    if _compile_db_path(repo_root) is None:
        return AdapterResult(
            status="skipped",
            files=[],
            tokens=0,
            duration_ms=(time.perf_counter() - start) * 1000.0,
            error=(
                "compile_commands.json not produced "
                "(cmake unavailable or setup failed)"
            ),
        )

    # Gate 3: flip env var, ensure graph (which runs discovery with
    # libclang active), run query, restore env and graph on exit.
    prior = os.environ.get(ENABLE_ENV_VAR)
    os.environ[ENABLE_ENV_VAR] = "1"
    try:
        if not _ensure_graph(repo_root):
            elapsed = (time.perf_counter() - start) * 1000.0
            return AdapterResult(
                status="degraded",
                files=[],
                tokens=0,
                duration_ms=elapsed,
                error="weld graph missing and discovery failed",
            )
        result = _run_query_with_env(task, repo_root)
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000.0
        return AdapterResult(
            status="degraded",
            files=[],
            tokens=0,
            duration_ms=elapsed,
            error=str(exc),
        )
    finally:
        if prior is None:
            os.environ.pop(ENABLE_ENV_VAR, None)
        else:
            os.environ[ENABLE_ENV_VAR] = prior
        _restore_graph(repo_root)

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
