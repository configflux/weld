"""The five-file cast bd 5038-rwi34 was minimized to, as real files.

Shared by the two halves of that pin -- the unit one over the anchor predicate
(``discovery_state_closure_anchor_test``) and the end-to-end one over both
discovery paths (``incremental_closure_anchored_stub_equivalence_test``) --
because both need the *same* tree and a cast written twice is a cast that can
drift into proving two different things. A fixture module is where the
hand-built-payload rule (ADR 0139 mechanism 1) says a payload belongs: nothing
here writes a node or an edge, only source files and a ``discover.yaml`` that
the real producers are then run over.

The shape, narrowed by hand from the generative sweep's seed 369 (bd scjs2)::

    alpha/__init__.py   from .core import fn_alpha     <- facade, unanchored
    alpha/core.py       def fn_alpha(): ...            <- the definition
    beta/__init__.py    (empty)
    beta/use.py         from alpha import fn_alpha     <- SOLE stub minter
    beta/sub/use.py     from alpha import core         <- clean consumer

Two properties make it the minimum. ``python_module`` anchors no
``__init__.py``, so ``alpha`` has no ``file:`` node and
``graph_closure._module_index`` binds the module name to the never-walked stub
``symbol:py:alpha:fn_alpha`` that ``beta/use.py`` minted -- which is what puts
a ``graph_closure``-authored ``depends_on`` from the clean ``beta/sub/use.py``
onto that stub. And the clean consumer imports the *submodule*, so deleting
``beta/use.py`` leaves the stub with no strategy-authored edge at all while
that closure edge still names it.

The third glob the generated case drew is not required and is not here; the
whole round reproduces with the minter and the clean consumer in one glob, so
this is not a cross-glob shape (unlike bd yhz70).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

#: The never-walked resolved-target stub (bd n4nvt's shape) the round strands.
STUB_ID = "symbol:py:alpha:fn_alpha"

#: Where a full discover of the post-delete tree puts the surviving consumer's
#: import instead -- ``graph_closure``'s external fallback, which lands on the
#: package node ``python_package`` already emitted under the same name.
PACKAGE_ID = "package:python:alpha"

#: The clean consumer whose closure-authored ``depends_on`` did the anchoring.
CLEAN_CONSUMER = "file:beta/sub/use"

#: The symbol whose ``calls`` edge is the stub's only strategy-authored anchor.
SOLE_IMPORTER_SYMBOL = "symbol:py:beta.use:use_beta"

#: The file the mutation round deletes.
SOLE_IMPORTER = "beta/use.py"

#: A second minter, written only when a caller asks for it.
SECOND_IMPORTER = "beta/second.py"

#: The canonical Python trio over two globs, exactly as the generative sweep
#: draws it -- the configuration seed 369 found this shape under.
DISCOVER_YAML = "sources:\n" + "".join(
    f"  - strategy: {strategy}\n    glob: {root}/**/*.py\n    type: {node_type}\n"
    for root in ("alpha", "beta")
    for strategy, node_type in (
        ("python_module", "file"),
        ("python_callgraph", "symbol"),
        ("python_package", "package"),
    )
)

_FILES = {
    "alpha/__init__.py": 'from .core import fn_alpha\n\n__all__ = ["fn_alpha"]\n',
    "alpha/core.py": "def fn_alpha():\n    return 1\n",
    "beta/__init__.py": "",
    SOLE_IMPORTER: (
        "from alpha import fn_alpha\n\n\ndef use_beta():\n    return fn_alpha()\n"
    ),
    "beta/sub/__init__.py": "",
    "beta/sub/use.py": (
        "from alpha import core\n\n\ndef use_sub():\n    return core.fn_alpha()\n"
    ),
    SECOND_IMPORTER: (
        "from alpha import fn_alpha\n\n\ndef use_second():\n    return fn_alpha()\n"
    ),
}


def git_init(root: Path) -> None:
    """Make *root* a committable git repo -- discovery reads the index."""
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "T"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, cwd=str(root), check=True)


def commit(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=str(root), check=True)


def write(root: Path, *, second_importer: bool = False) -> None:
    """Materialise the cast under *root*, with or without the second minter."""
    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    (weld_dir / "discover.yaml").write_text(DISCOVER_YAML, encoding="utf-8")
    for rel, text in sorted(_FILES.items()):
        if rel == SECOND_IMPORTER and not second_importer:
            continue
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def node_ids(graph: dict) -> set[str]:
    return set(graph.get("nodes", {}))


def edge_set(graph: dict) -> set[tuple[str, str, str]]:
    return {(e["from"], e["type"], e["to"]) for e in graph.get("edges", [])}


def strip_meta(graph: dict) -> dict:
    """Drop volatile keys, plus ``discovered_from`` -- its ORDER (not set)
    legitimately differs between the two construction paths (bd 8084).
    """
    out = {k: v for k, v in graph.items() if k != "meta"}
    out["meta"] = {
        k: v for k, v in graph.get("meta", {}).items()
        if k not in ("updated_at", "git_sha", "discovered_from")
    }
    return out
