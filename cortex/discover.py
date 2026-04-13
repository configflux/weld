#!/usr/bin/env python3
"""Config-driven codebase discovery for the knowledge graph.

Reads ``.cortex/discover.yaml`` to determine what to scan, then loads strategy
plugins from ``cortex/strategies/`` (bundled) or ``.cortex/strategies/`` (project-local)
and dispatches to their ``extract()`` functions.

Strategy resolution order for ``strategy: foo``:
  1. ``.cortex/strategies/foo.py`` (project-local override)
  2. ``cortex/strategies/foo.py``  (bundled with tool)

When a project-local strategy shadows a bundled one, a one-line notice is
emitted to stderr.

Outputs a graph JSON document to stdout suitable for ``cortex import``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from cortex._git import get_git_sha
from cortex._yaml import parse_yaml
from cortex.contract import SCHEMA_VERSION
from cortex.strategies._helpers import StrategyResult

# ---------------------------------------------------------------------------
# Strategy loader
# ---------------------------------------------------------------------------

def _load_strategy(name: str, root: Path):
    """Load a strategy's ``extract`` function by name.

    Resolution order:
      1. ``<root>/.cortex/strategies/<name>.py``  (project-local)
      2. ``<kg_package>/strategies/<name>.py`` (bundled)

    Returns the ``extract`` callable, or None if not found.
    """
    project_local = root / ".cortex" / "strategies" / f"{name}.py"
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
        print(f"[cortex] warning: strategy '{name}' not found", file=sys.stderr)
        return None

    if is_shadow:
        print(
            f"[cortex] notice: project-local strategy '{name}' shadows bundled one",
            file=sys.stderr,
        )

    spec = importlib.util.spec_from_file_location(f"kg_strategy_{name}", resolved_path)
    if spec is None or spec.loader is None:
        print(
            f"[cortex] warning: could not load strategy '{name}' from {resolved_path}",
            file=sys.stderr,
        )
        return None

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    fn = getattr(mod, "extract", None)
    if fn is None:
        print(
            f"[cortex] warning: strategy '{name}' has no extract() function",
            file=sys.stderr,
        )
        return None

    return fn

# ---------------------------------------------------------------------------
# External JSON adapter
# ---------------------------------------------------------------------------

#: Default timeout in seconds for external adapter commands.
_EXTERNAL_JSON_TIMEOUT: int = 30

def _run_external_json(root: Path, source: dict) -> StrategyResult:
    """Run an external command, validate its JSON stdout as a graph fragment.

    Source keys: ``command`` (str, required — parsed via shlex),
    ``timeout`` (int, optional — default ``_EXTERNAL_JSON_TIMEOUT``).
    Returns an empty StrategyResult on any failure.
    """
    from cortex.contract import validate_fragment

    empty = StrategyResult(nodes={}, edges=[], discovered_from=[])

    cmd_str = source.get("command", "")
    if not cmd_str:
        print(
            "[cortex] warning: external_json source missing 'command' key",
            file=sys.stderr,
        )
        return empty

    timeout = int(source.get("timeout", _EXTERNAL_JSON_TIMEOUT))

    try:
        argv = shlex.split(cmd_str)
    except ValueError as exc:
        print(
            f"[cortex] warning: external_json bad command string: {exc}",
            file=sys.stderr,
        )
        return empty

    # Execute
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=timeout,
        )
    except FileNotFoundError:
        print(
            f"[cortex] warning: external_json command not found: {argv[0]}",
            file=sys.stderr,
        )
        return empty
    except subprocess.TimeoutExpired:
        print(
            f"[cortex] warning: external_json command timed out after {timeout}s",
            file=sys.stderr,
        )
        return empty

    if proc.returncode != 0:
        stderr_snippet = (proc.stderr or "").strip()[:200]
        print(
            f"[cortex] warning: external_json command exited {proc.returncode}"
            + (f": {stderr_snippet}" if stderr_snippet else ""),
            file=sys.stderr,
        )
        return empty

    # Parse JSON
    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        print(
            f"[cortex] warning: external_json command emitted invalid JSON: {exc}",
            file=sys.stderr,
        )
        return empty

    if not isinstance(data, dict):
        print(
            "[cortex] warning: external_json output must be a JSON object",
            file=sys.stderr,
        )
        return empty

    # Validate through the shared fragment contract
    label = f"external_json:{cmd_str.split()[0] if cmd_str else '?'}"
    errs = validate_fragment(data, source_label=label, allow_dangling_edges=True)
    if errs:
        for e in errs:
            print(f"[cortex] validation: {e}", file=sys.stderr)
        return empty

    return StrategyResult(
        nodes=data.get("nodes", {}),
        edges=data.get("edges", []),
        discovered_from=data.get("discovered_from", []),
    )

# ---------------------------------------------------------------------------
# Discovery orchestrator
# ---------------------------------------------------------------------------

def discover(root: Path) -> dict:
    """Walk the codebase and build a knowledge graph from config.

    Loads strategies by name from discover.yaml, merges their results,
    and handles all post-processing.

    Known context keys (producers -> consumers):

    - ``table_to_entity`` (dict[str, str]):
        Producer: sqlalchemy strategy.
        Consumer: orchestrator post-processing (FK edge resolution).
        Maps SQL table names to entity node IDs.

    - ``pending_fk_edges`` (list[dict]):
        Producer: sqlalchemy strategy.
        Consumer: orchestrator post-processing (FK edge resolution).
        Edges with ``__table__:`` placeholder targets awaiting resolution.

    - ``command_texts`` (dict[str, str]):
        Producer: firstline_md strategy.
        Consumer: orchestrator post-processing (invokes edge detection).
        Maps command node IDs to their full markdown text for agent
        name matching.
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []
    context: dict = {}

    # Load config
    config_path = root / ".cortex" / "discover.yaml"
    if config_path.exists():
        config = parse_yaml(config_path.read_text(encoding="utf-8"))
    else:
        config = {"sources": [], "topology": {}}

    # Run each source through its strategy
    for source in config.get("sources", []):
        strategy_name = source.get("strategy", "")
        if strategy_name == "external_json":
            result = _run_external_json(root, source)
        else:
            extract_fn = _load_strategy(strategy_name, root)
            if not extract_fn:
                continue
            result = extract_fn(root, source, context)
        nodes.update(result.nodes)
        edges.extend(result.edges)
        discovered_from.extend(result.discovered_from)

    # --- Post-processing: resolve FK edges from sqlalchemy strategy ---
    table_to_entity = context.get("table_to_entity", {})
    pending_fk_edges = context.get("pending_fk_edges", [])
    for e in pending_fk_edges:
        to_id = e["to"]
        if to_id.startswith("__table__:"):
            tname = to_id.split(":", 1)[1]
            real = table_to_entity.get(tname)
            if real:
                edges.append({**e, "to": real})
        else:
            edges.append(e)

    # --- Post-processing: detect agent invocations in commands ---
    agent_names = [nid.split(":", 1)[1] for nid in nodes if nid.startswith("agent:")]
    command_texts = context.get("command_texts", {})
    for cmd_nid, text in command_texts.items():
        text_lower = text.lower()
        for agent_name in agent_names:
            if agent_name.lower() in text_lower:
                edges.append(
                    {
                        "from": cmd_nid,
                        "to": f"agent:{agent_name}",
                        "type": "invokes",
                        "props": {
                            "source_strategy": "post_processing",
                            "confidence": "inferred",
                        },
                    }
                )

    # Apply static topology
    topology = config.get("topology", {})

    # Static nodes
    for static_node in topology.get("nodes", []):
        nid = static_node["id"]
        if nid not in nodes:
            raw_props = static_node.get("props", {})
            props = dict(raw_props) if isinstance(raw_props, dict) else {}
            if "path" in props:
                if not (root / props["path"]).is_dir():
                    continue
            props.setdefault("source_strategy", "topology")
            props.setdefault("authority", "manual")
            props.setdefault("confidence", "definite")
            nodes[nid] = {
                "type": static_node["type"],
                "label": static_node.get("label", nid),
                "props": props,
            }

    # Static edges
    for static_edge in topology.get("edges", []):
        raw_edge_props = static_edge.get("props", {})
        edge_props = dict(raw_edge_props) if isinstance(raw_edge_props, dict) else {}
        edge_props.setdefault("source_strategy", "topology")
        edge_props.setdefault("confidence", "definite")
        edges.append(
            {
                "from": static_edge["from"],
                "to": static_edge["to"],
                "type": static_edge["type"],
                "props": edge_props,
            }
        )

    # Entity package containment edges
    entity_packages = topology.get("entity_packages", [])
    if isinstance(entity_packages, list):
        for mapping in entity_packages:
            pkg_id = mapping.get("package", "")
            modules = mapping.get("modules", [])
            if isinstance(modules, list):
                for nid, n in list(nodes.items()):
                    if n["type"] == "entity":
                        mod = n["props"].get("module")
                        if mod in modules:
                            edges.append(
                                {
                                    "from": pkg_id,
                                    "to": nid,
                                    "type": "contains",
                                    "props": {
                                        "source_strategy": "topology",
                                        "confidence": "definite",
                                    },
                                }
                            )

    # Clean edges: remove edges pointing to non-existent nodes
    edges = [e for e in edges if e["from"] in nodes and e["to"] in nodes]

    # Deduplicate edges
    seen: set[str] = set()
    deduped: list[dict] = []
    for e in edges:
        key = f"{e['from']}|{e['to']}|{e['type']}"
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    # Deduplicate discovered_from
    seen_paths: set[str] = set()
    unique_from: list[str] = []
    for p in discovered_from:
        if p not in seen_paths:
            seen_paths.add(p)
            unique_from.append(p)

    meta: dict = {
        "version": SCHEMA_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "discovered_from": unique_from,
    }
    git_sha = get_git_sha(root)
    if git_sha is not None:
        meta["git_sha"] = git_sha

    return {
        "meta": meta,
        "nodes": nodes,
        "edges": deduped,
    }

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="cortex discover",
        description="Run config-driven Cortex discovery and emit graph JSON to stdout",
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Project root directory (default: current directory)",
    )
    args = parser.parse_args(argv)
    root = Path(args.root)
    result = discover(root)
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")

if __name__ == "__main__":
    main()
