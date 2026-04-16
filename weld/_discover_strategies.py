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
import sys
from pathlib import Path

from weld.strategies._helpers import StrategyResult

# ---------------------------------------------------------------------------
# Strategy loader
# ---------------------------------------------------------------------------

def load_strategy(name: str, root: Path):
    """Load a strategy's ``extract`` function by name."""
    project_local = root / ".weld" / "strategies" / f"{name}.py"
    bundled = Path(__file__).resolve().parent / "strategies" / f"{name}.py"

    resolved_path: Path | None = None
    is_shadow = False

    if project_local.is_file():
        resolved_path = project_local
        if bundled.is_file():
            is_shadow = True
    elif bundled.is_file():
        resolved_path = bundled

    if resolved_path is None:
        print(f"[weld] warning: strategy '{name}' not found", file=sys.stderr)
        return None

    if is_shadow:
        print(
            f"[weld] notice: project-local strategy '{name}' shadows bundled one",
            file=sys.stderr,
        )

    spec = importlib.util.spec_from_file_location(
        f"weld_strategy_{name}",
        resolved_path,
    )
    if spec is None or spec.loader is None:
        print(
            f"[weld] warning: could not load strategy '{name}' from {resolved_path}",
            file=sys.stderr,
        )
        return None

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    fn = getattr(mod, "extract", None)
    if fn is None:
        print(
            f"[weld] warning: strategy '{name}' has no extract() function",
            file=sys.stderr,
        )
        return None

    return fn


# ---------------------------------------------------------------------------
# External JSON adapter
# ---------------------------------------------------------------------------

_EXTERNAL_JSON_TIMEOUT: int = 30


def run_external_json(root: Path, source: dict) -> StrategyResult:
    """Run an external command, validate its JSON stdout as a graph fragment."""
    from weld.contract import validate_fragment

    empty = StrategyResult(nodes={}, edges=[], discovered_from=[])
    cmd_str = source.get("command", "")
    if not cmd_str:
        print("[weld] warning: external_json source missing 'command' key", file=sys.stderr)
        return empty

    timeout = int(source.get("timeout", _EXTERNAL_JSON_TIMEOUT))
    try:
        argv = shlex.split(cmd_str)
    except ValueError as exc:
        print(f"[weld] warning: external_json bad command string: {exc}", file=sys.stderr)
        return empty

    env = {**os.environ, "LC_ALL": "C"}
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, cwd=str(root),
            timeout=timeout, env=env,
        )
    except FileNotFoundError:
        print(f"[weld] warning: external_json command not found: {argv[0]}", file=sys.stderr)
        return empty
    except subprocess.TimeoutExpired:
        print(
            f"[weld] warning: external_json command timed out after {timeout}s",
            file=sys.stderr,
        )
        return empty

    if proc.returncode != 0:
        snippet = (proc.stderr or "").strip()[:200]
        print(
            f"[weld] warning: external_json command exited {proc.returncode}"
            + (f": {snippet}" if snippet else ""),
            file=sys.stderr,
        )
        return empty

    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        print(
            f"[weld] warning: external_json command emitted invalid JSON: {exc}",
            file=sys.stderr,
        )
        return empty

    if not isinstance(data, dict):
        print("[weld] warning: external_json output must be a JSON object", file=sys.stderr)
        return empty

    label = f"external_json:{cmd_str.split()[0] if cmd_str else '?'}"
    errs = validate_fragment(data, source_label=label, allow_dangling_edges=True)
    if errs:
        for e in errs:
            print(f"[weld] validation: {e}", file=sys.stderr)
        return empty

    return StrategyResult(
        nodes=data.get("nodes", {}),
        edges=data.get("edges", []),
        discovered_from=data.get("discovered_from", []),
    )


# ---------------------------------------------------------------------------
# Source runner
# ---------------------------------------------------------------------------

def run_source(root: Path, source: dict, context: dict) -> StrategyResult:
    """Run a single source entry through its strategy."""
    name = source.get("strategy", "")
    if name == "external_json":
        return run_external_json(root, source)
    extract_fn = load_strategy(name, root)
    if not extract_fn:
        return StrategyResult(nodes={}, edges=[], discovered_from=[])
    return extract_fn(root, source, context)
