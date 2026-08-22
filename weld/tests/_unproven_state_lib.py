"""Shared fixture for the "is this inventory a valid delta basis?" suites.

One repository shape serves both halves of the ADR 0101 rule, because both
turn on the same observable: a single source file, rewritten **in place**, and
the exports the ``python_module`` strategy attributes to it. An in-place edit
is the only shape that reproduces either failure -- a file that was added or
deleted moves the file *set*, which the hash diff and the ADR 0008 per-file
repair both already catch, while a file whose content merely drifted still
carries nodes, just the wrong ones.

The two suites differ only in how the divergence is produced:

* ``discover_unproven_state_forces_full_test`` (bd nwyq) advances the
  inventory past the graph, via a run that resolves files without publishing.
* ``discover_replaced_body_forces_full_test`` (bd wq9i) leaves a healthy,
  publishing run alone and replaces the *body* underneath it.

Rides in ``srcs`` rather than becoming a ``py_library`` -- the
``_impact_test_helpers`` pattern -- because it is test data, not a surface
anything imports outside these targets.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from weld._discover_state_check import state_vouches_for_graph
from weld.discover import _discover_single_repo

#: The file the fixture rewrites in place, and the node the python_module
#: strategy extracts it to. ``props.exports`` is the observable, because it
#: changes with the file's *content* while the file set stays fixed.
MOD = "src/mod.py"
MOD_NODE = "file:src/mod"

OLD = "def helper():\n    return 1\n"
NEW = "def replacement():\n    return 2\n"

CONFIG = (
    "topology:\n"
    "  nodes:\n"
    "    - id: pkg:src\n"
    "      type: package\n"
    "      label: src\n"
    "sources:\n"
    "  - strategy: python_module\n"
    "    glob: src/**/*.py\n"
    "    type: file\n"
    "    package: pkg:src\n"
)


def build_fixture(root: Path) -> None:
    """One source file the python_module strategy extracts exports from."""
    src = root / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "mod.py").write_text(OLD, encoding="utf-8")
    (root / ".weld").mkdir()
    (root / ".weld" / "discover.yaml").write_text(CONFIG, encoding="utf-8")


def exports(graph: dict) -> list[str]:
    """Symbols *graph* attributes to the rewritten file."""
    node = (graph.get("nodes") or {}).get(MOD_NODE) or {}
    return list((node.get("props") or {}).get("exports") or [])


def on_disk(root: Path) -> dict:
    return json.loads(
        (root / ".weld" / "graph.json").read_text(encoding="utf-8")
    )


def vouches(state, root: Path) -> bool:
    """Whether *state* claims the ``graph.json`` currently at *root*."""
    return state_vouches_for_graph(state, root / ".weld" / "graph.json")


def desync(root: Path) -> None:
    """Leave *root* with an inventory ahead of the graph it cannot vouch for.

    Built by the production producer rather than by hand. A full run publishes
    graph and state together; the file is then rewritten; a second run in the
    library shape (``write_graph=False``) advances the inventory to the new
    content while the graph on disk stays at the old. That second shape is the
    ``discover()`` caller ADR 0101 names, and it is how the checkout in bd
    nwyq reached this state.
    """
    _discover_single_repo(root, incremental=False, write_graph=True)
    (root / MOD).write_text(NEW, encoding="utf-8")
    _discover_single_repo(root, incremental=None, write_graph=False)


def stamp_a_later_mtime(path: Path) -> None:
    """Move *path*'s timestamp somewhere a recorded stat pin is not.

    Set rather than waited for. What a caller needs here is "the cheap
    shortcut no longer matches"; sleeping until the filesystem's clock happens
    to tick is a flaky way to ask for it, and the granularity is the
    platform's business rather than the assertion's.
    """
    stamp = path.stat().st_mtime_ns + 1_000_000_000
    os.utime(path, ns=(stamp, stamp))
