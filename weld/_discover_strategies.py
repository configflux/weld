"""Strategy loader and external-JSON adapter for discovery.

Loads strategy plugins from ``weld/strategies/`` (bundled) or
``.weld/strategies/`` (project-local override), and runs the
``external_json`` pseudo-strategy via subprocess.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies._incremental_hint import (  # noqa: F401 -- re-export
    INCREMENTAL_HINT_KEY,
    IncrementalHint,
)
from weld._notice import emit

# ---------------------------------------------------------------------------
# Strategy loader
# ---------------------------------------------------------------------------

def load_strategy(name: str, root: Path, *, safe: bool = False):
    """Load a strategy's ``extract`` function by name.

    When *safe* is True, project-local strategies under
    ``<root>/.weld/strategies/<name>.py`` are refused (ADR 0024). The
    bundled strategy is used if present; otherwise the strategy is
    treated as missing.
    """
    project_local = root / ".weld" / "strategies" / f"{name}.py"
    bundled = Path(__file__).resolve().parent / "strategies" / f"{name}.py"

    resolved_path: Path | None = None
    is_shadow = False

    if project_local.is_file():
        if safe:
            # Safe mode: refuse to execute project-local code. Fall back to
            # the bundled implementation if one exists; otherwise treat the
            # strategy as missing.
            emit(
                f"[weld] safe mode: skipped project-local strategy '{name}'"
            )
            if bundled.is_file():
                resolved_path = bundled
        else:
            # Unsafe mode: project-local Python is about to be imported and
            # executed. Surface a stable, grep-friendly warning so operators
            # can see what local code ran. ADR 0024.
            emit(
                f"[weld] warning: project-local strategy '{name}' "
                f"will execute local code; pass --safe to refuse"
            )
            resolved_path = project_local
            if bundled.is_file():
                is_shadow = True
    elif bundled.is_file():
        resolved_path = bundled

    if resolved_path is None:
        emit(f"[weld] warning: strategy '{name}' not found")
        return None

    if is_shadow:
        emit(
            f"[weld] notice: project-local strategy '{name}' shadows bundled one"
        )

    spec = importlib.util.spec_from_file_location(
        f"weld_strategy_{name}",
        resolved_path,
    )
    if spec is None or spec.loader is None:
        emit(
            f"[weld] warning: could not load strategy '{name}' from {resolved_path}"
        )
        return None

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    fn = getattr(mod, "extract", None)
    if fn is None:
        emit(
            f"[weld] warning: strategy '{name}' has no extract() function"
        )
        return None

    return fn


# ---------------------------------------------------------------------------
# External JSON adapter
# ---------------------------------------------------------------------------

_EXTERNAL_JSON_TIMEOUT: int = 30


def run_external_json(root: Path, source: dict, *, safe: bool = False) -> StrategyResult:
    """Run an external command, validate its JSON stdout as a graph fragment.

    When *safe* is True, the subprocess is never spawned (ADR 0024). An
    empty :class:`StrategyResult` is returned and a single notice is
    written to stderr.
    """
    from weld.contract import validate_fragment

    empty = StrategyResult(nodes={}, edges=[], discovered_from=[])
    cmd_str = source.get("command", "")
    if not cmd_str:
        emit("[weld] warning: external_json source missing 'command' key")
        return empty

    if safe:
        emit(
            f"[weld] safe mode: skipped external_json '{cmd_str}'"
        )
        return empty

    # Unsafe mode: a configured subprocess is about to run. Surface a
    # stable, grep-friendly warning so operators can see what local code
    # ran. ADR 0024.
    emit(
        f"[weld] warning: external_json '{cmd_str}' "
        f"will execute local code; pass --safe to refuse"
    )

    timeout = int(source.get("timeout", _EXTERNAL_JSON_TIMEOUT))
    try:
        argv = shlex.split(cmd_str)
    except ValueError as exc:
        emit(f"[weld] warning: external_json bad command string: {exc}")
        return empty

    env = {**os.environ, "LC_ALL": "C"}
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, cwd=str(root),
            timeout=timeout, env=env,
        )
    except FileNotFoundError:
        emit(f"[weld] warning: external_json command not found: {argv[0]}")
        return empty
    except subprocess.TimeoutExpired:
        emit(
            f"[weld] warning: external_json command timed out after {timeout}s"
        )
        return empty

    if proc.returncode != 0:
        snippet = (proc.stderr or "").strip()[:200]
        emit(
            f"[weld] warning: external_json command exited {proc.returncode}"
            + (f": {snippet}" if snippet else "")
        )
        return empty

    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        emit(
            f"[weld] warning: external_json command emitted invalid JSON: {exc}"
        )
        return empty

    if not isinstance(data, dict):
        emit("[weld] warning: external_json output must be a JSON object")
        return empty

    label = f"external_json:{cmd_str.split()[0] if cmd_str else '?'}"
    errs = validate_fragment(data, source_label=label, allow_dangling_edges=True)
    if errs:
        for e in errs:
            emit(f"[weld] validation: {e}")
        return empty

    return StrategyResult(
        nodes=data.get("nodes", {}),
        edges=data.get("edges", []),
        discovered_from=data.get("discovered_from", []),
    )


# ---------------------------------------------------------------------------
# Source runner
# ---------------------------------------------------------------------------

def run_source(
    root: Path,
    source: dict,
    context: dict,
    *,
    safe: bool = False,
    incremental_hint: IncrementalHint | None = None,
) -> StrategyResult:
    """Run a single source entry through its strategy.

    When *safe* is True, project-local strategy overrides and the
    ``external_json`` subprocess adapter are refused (ADR 0024).

    *incremental_hint* (ADR 0074), when present, carries the dirty-file
    scope and the post-purge prior node set for incremental-aware
    strategies. It is deposited under ``context[INCREMENTAL_HINT_KEY]`` for
    the duration of this strategy call (and removed afterwards) so the
    ``extract(root, source, context)`` contract is preserved and the hint
    never leaks onto the declarative ``source`` dict. Strategies that do
    not consult the key (every strategy except ``python_callgraph``) are
    unaffected.
    """
    name = source.get("strategy", "")
    if name == "external_json":
        return run_external_json(root, source, safe=safe)
    extract_fn = load_strategy(name, root, safe=safe)
    if not extract_fn:
        return StrategyResult(nodes={}, edges=[], discovered_from=[])
    if incremental_hint is None or not isinstance(context, dict):
        return extract_fn(root, source, context)
    prior = context.get(INCREMENTAL_HINT_KEY)
    context[INCREMENTAL_HINT_KEY] = incremental_hint
    try:
        return extract_fn(root, source, context)
    finally:
        if prior is None:
            context.pop(INCREMENTAL_HINT_KEY, None)
        else:
            context[INCREMENTAL_HINT_KEY] = prior
