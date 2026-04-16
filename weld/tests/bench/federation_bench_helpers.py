"""Shared helpers for the federation performance benchmark suite.

Deterministic fixture generation, timing harness, and baseline
persistence for parameterized N-child workspace benchmarks.
"""

from __future__ import annotations

import hashlib
import json
import os
import resource
import subprocess
import time
from pathlib import Path

from weld.contract import SCHEMA_VERSION
from weld.discover import discover
from weld.federation import FederatedGraph
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEED = 42
_TS = "2026-04-15T12:00:00+00:00"
NODES_PER_CHILD = 8
EDGES_PER_CHILD = 4
REGRESSION_THRESHOLD_PCT = 50.0  # generous for CI jitter

_NODE_TYPES = ("service", "route", "file", "symbol", "rpc", "event", "module", "config")

_BASELINE_DIR = Path(__file__).resolve().parent
BASELINE_FILE = _BASELINE_DIR / "federation_benchmark_baseline.json"

# N-values covered by the parameterized suite.
BENCHMARK_N_VALUES = (1, 5, 20)


# ---------------------------------------------------------------------------
# Deterministic fixture generator
# ---------------------------------------------------------------------------


def deterministic_hash(seed: int, child_idx: int, item_idx: int) -> str:
    """Return a short hex digest derived from fixed seed + indices."""
    raw = f"{seed}:{child_idx}:{item_idx}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def generate_child_graph(child_idx: int) -> dict:
    """Build a deterministic graph payload for child *child_idx*."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    for i in range(NODES_PER_CHILD):
        ntype = _NODE_TYPES[i % len(_NODE_TYPES)]
        tag = deterministic_hash(SEED, child_idx, i)
        nid = f"{ntype}:{tag}"
        nodes[nid] = {
            "type": ntype,
            "label": f"child{child_idx}_{ntype}_{i}",
            "props": {
                "file": f"src/{ntype}_{i}.py",
                "description": f"Synthetic {ntype} node {i} for child {child_idx}.",
            },
        }

    node_ids = list(nodes.keys())
    for i in range(EDGES_PER_CHILD):
        src_idx = i % len(node_ids)
        dst_idx = (i + 1) % len(node_ids)
        edges.append({
            "from": node_ids[src_idx],
            "to": node_ids[dst_idx],
            "type": "calls",
            "props": {},
        })

    return {
        "meta": {
            "version": SCHEMA_VERSION,
            "updated_at": _TS,
            "schema_version": 1,
        },
        "nodes": nodes,
        "edges": edges,
    }


# ---------------------------------------------------------------------------
# Git + workspace helpers
# ---------------------------------------------------------------------------


def git(repo_root: Path, *args: str) -> str:
    """Run a git command in *repo_root* with stable locale."""
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        check=True,
    )
    return proc.stdout.strip()


def init_repo(repo_root: Path) -> Path:
    """Initialise a minimal git repo with one commit."""
    repo_root.mkdir(parents=True, exist_ok=True)
    git(repo_root, "init", "-q")
    git(repo_root, "config", "user.email", "bench@example.com")
    git(repo_root, "config", "user.name", "Bench")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    git(repo_root, "add", "README.md")
    git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def write_graph(repo_root: Path, payload: dict) -> None:
    """Write a graph.json into the child's ``.weld/`` directory."""
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_workspaces(root: Path, children: list[ChildEntry]) -> None:
    """Write workspaces.yaml to the root's ``.weld/`` directory."""
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    dump_workspaces_yaml(config, weld_dir / "workspaces.yaml")


def setup_synthetic_workspace(
    base: Path, n_children: int = 5,
) -> tuple[Path, list[str]]:
    """Create a workspace root with *n_children* deterministic child repos."""
    child_names: list[str] = []
    child_entries: list[ChildEntry] = []

    for idx in range(n_children):
        name = f"child-{idx:02d}"
        child_dir = base / name
        init_repo(child_dir)
        graph = generate_child_graph(idx)
        write_graph(child_dir, graph)
        child_names.append(name)
        child_entries.append(ChildEntry(name=name, path=name))

    write_workspaces(base, child_entries)
    return base, child_names


# ---------------------------------------------------------------------------
# Timing + memory harness
# ---------------------------------------------------------------------------


def time_discover(root: Path) -> tuple[dict, float]:
    """Run discover and return (graph, elapsed_seconds)."""
    start = time.monotonic()
    graph = discover(root, incremental=False, write_root_graph=True)
    elapsed = time.monotonic() - start
    return graph, elapsed


def time_query(root: Path, term: str) -> tuple[dict, float]:
    """Run a federated query and return (result, elapsed_seconds)."""
    fg = FederatedGraph(root)
    start = time.monotonic()
    result = fg.query(term, limit=50)
    elapsed = time.monotonic() - start
    return result, elapsed


def measure_memory_delta_kb(root: Path) -> tuple[dict, float, float]:
    """Run discover and return (graph, elapsed_s, memory_delta_kb).

    Uses ``resource.getrusage`` to measure peak RSS delta around the
    discover call.  On Linux the unit is KB; on macOS it is bytes, but
    CI runs on Linux so KB is the expected unit.
    """
    before_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    graph, elapsed = time_discover(root)
    after_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    delta_kb = max(0.0, float(after_kb - before_kb))
    return graph, elapsed, delta_kb


# ---------------------------------------------------------------------------
# Baseline persistence
# ---------------------------------------------------------------------------


def load_baseline(path: Path | None = None) -> dict | None:
    """Load persisted baseline, or None if absent."""
    target = path or BASELINE_FILE
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_baseline(data: dict, path: Path | None = None) -> None:
    """Persist baseline numbers."""
    target = path or BASELINE_FILE
    target.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
