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
from typing import Sequence

from weld.strategies._helpers import StrategyResult
from weld.strategies._incremental_hint import (  # noqa: F401 -- re-export
    INCREMENTAL_HINT_KEY,
    IncrementalHint,
)
from weld.strategies._strategy_failure import (
    KIND_BAD_COMMAND_STRING,
    KIND_COMMAND_NOT_FOUND,
    KIND_INVALID_JSON,
    KIND_INVALID_OUTPUT_SHAPE,
    KIND_MISSING_COMMAND,
    KIND_NONZERO_EXIT,
    KIND_SAFE_MODE_SKIPPED,
    KIND_STRATEGY_UNAVAILABLE,
    KIND_TIMEOUT,
    KIND_VALIDATION_FAILED,
    note_source_failure,
    note_strategy_failure,
)
from weld._discover_basis import entry_fingerprint
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


def run_external_json(
    root: Path,
    source: dict,
    *,
    safe: bool = False,
    context: dict | None = None,
    source_files: Sequence[str] = (),
) -> StrategyResult:
    """Run an external command, validate its JSON stdout as a graph fragment.

    When *safe* is True, the subprocess is never spawned (ADR 0024). An
    empty :class:`StrategyResult` is returned and a single notice is
    written to stderr.

    Every early return below is a refusal or an error -- never this source
    deciding it contributes nothing -- so *source_files* are noted on
    *context* as files no strategy spoke for (bd hch4). A command-only entry
    (no ``glob``/``path``/``files`` key) resolves *source_files* to nothing,
    which makes that channel a no-op -- so every early return also notes the
    entry itself, by fingerprint, on the sibling channel (bd um00), and that
    one is never a no-op for this shape of entry. Only the tail return is a
    real answer, including when the command legitimately emits an empty
    fragment. Both parameters are optional: a caller with no shared context
    (the direct-call tests) gets exactly the previous behaviour.
    """
    from weld.contract import validate_fragment

    empty = StrategyResult(nodes={}, edges=[], discovered_from=[])

    def unspoken(kind: str, reason: str = "") -> StrategyResult:
        note_strategy_failure(context, source_files)
        if not source_files:
            note_source_failure(
                context, entry_fingerprint(source), kind=kind, reason=reason,
            )
        return empty

    cmd_str = source.get("command", "")
    if not cmd_str:
        emit("[weld] warning: external_json source missing 'command' key")
        return unspoken(KIND_MISSING_COMMAND, "missing 'command' key")

    if safe:
        emit(
            f"[weld] safe mode: skipped external_json '{cmd_str}'"
        )
        return unspoken(KIND_SAFE_MODE_SKIPPED, f"safe mode: skipped '{cmd_str}'")

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
        return unspoken(KIND_BAD_COMMAND_STRING, f"bad command string: {exc}")

    env = {**os.environ, "LC_ALL": "C"}
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, cwd=str(root),
            timeout=timeout, env=env,
        )
    except FileNotFoundError:
        emit(f"[weld] warning: external_json command not found: {argv[0]}")
        return unspoken(KIND_COMMAND_NOT_FOUND, f"command not found: {argv[0]}")
    except subprocess.TimeoutExpired:
        emit(
            f"[weld] warning: external_json command timed out after {timeout}s"
        )
        return unspoken(KIND_TIMEOUT, f"timed out after {timeout}s")

    if proc.returncode != 0:
        snippet = (proc.stderr or "").strip()[:200]
        emit(
            f"[weld] warning: external_json command exited {proc.returncode}"
            + (f": {snippet}" if snippet else "")
        )
        reason = f"exited {proc.returncode}" + (f": {snippet}" if snippet else "")
        return unspoken(KIND_NONZERO_EXIT, reason)

    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        emit(
            f"[weld] warning: external_json command emitted invalid JSON: {exc}"
        )
        return unspoken(KIND_INVALID_JSON, f"invalid JSON: {exc}")

    if not isinstance(data, dict):
        emit("[weld] warning: external_json output must be a JSON object")
        return unspoken(KIND_INVALID_OUTPUT_SHAPE, "output must be a JSON object")

    label = f"external_json:{cmd_str.split()[0] if cmd_str else '?'}"
    errs = validate_fragment(data, source_label=label, allow_dangling_edges=True)
    if errs:
        for e in errs:
            emit(f"[weld] validation: {e}")
        return unspoken(KIND_VALIDATION_FAILED, "; ".join(str(e) for e in errs))

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
    source_files: Sequence[str] = (),
) -> StrategyResult:
    """Run a single source entry through its strategy.

    When *safe* is True, project-local strategy overrides and the
    ``external_json`` subprocess adapter are refused (ADR 0024).

    *source_files* are the repo-relative paths this entry resolved. They are
    used only when no strategy runs -- refused, missing, or without an
    ``extract`` -- to note on *context* that nothing spoke for them, so the
    state records a failure rather than a decision (bd hch4). The orchestrator
    passes them; callers that only want the fragment may omit them. When
    *source_files* is empty (no ``glob``/``path``/``files`` key at all), the
    entry itself is additionally noted by fingerprint (bd um00), since that
    channel has no file to record.

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
        return run_external_json(
            root, source, safe=safe, context=context, source_files=source_files,
        )
    extract_fn = load_strategy(name, root, safe=safe)
    if not extract_fn:
        note_strategy_failure(context, source_files)
        if not source_files:
            note_source_failure(
                context, entry_fingerprint(source),
                kind=KIND_STRATEGY_UNAVAILABLE,
                reason=f"strategy '{name}' unavailable",
            )
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
