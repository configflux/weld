"""``wd prime``'s two lines of advice about ``.weld/discover.yaml``.

Carved out of :mod:`weld.prime` to keep that module under the 400-line CLAUDE.md
cap, alongside the ``_prime_coverage`` and ``_unclaimed_sources`` splits it
already has -- callers of ``weld.prime.prime()`` see identical output.

Both lines here are advice *about the discovery config*, which is why they share
a file and a guard. The first reports the config or its absence; the second is
the ``Graph has only N nodes -- consider adding more sources to discover.yaml``
advisory, which is a sentence about ``discover.yaml`` that happens to be
triggered by a node count.

The guard is the part with a decision behind it (ADR 0141 D3). A federated
``wd discover`` reads ``.weld/workspaces.yaml`` and the children's graphs and
resolves no source glob at the root, so a root whose only discovery input is the
registry has nothing of its own to discover. Prime told such a root to run
``wd init`` and then nagged that its graph was small -- and neither line can be
acted on: ``wd init`` there re-scaffolds a stub config the federated discover
never resolves, and a root meta-graph holding one node per child is the shape
federation is meant to produce. The only next step prime offered was one that
makes the setup worse (field-eval v0.25.0 finding M3). ``wd doctor`` had the
same blind spot; bd 5038-lcq0c.5 fixed doctor's half and left the two commands
contradicting each other about the same root, which is what this file closes.

Federation is decided by :func:`weld.workspace_state.find_workspaces_yaml` --
the same question every graph-backed read asks (``wd brief``, the MCP readiness
guard, the ``wd find`` precondition, and doctor since lcq0c.5). Prime
disagreeing with the read path about what a workspace root is *is* the finding,
so a second detector spelled locally here would put it straight back, one
command over.

The guard is deliberately narrower than "any root holding a registry": it asks
whether the registry is this root's *whole* discovery input. A root that
federates **and** discovers keeps both lines, because there the config exists
and the root really does resolve sources of its own, so the advisory names a
real file and real work. A plain repository is untouched -- a carve-out reaching
every project would tell each un-``wd init``-ed checkout it was fine, which is a
worse bug than the one M3 reports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from weld.workspace_state import find_workspaces_yaml

StatusFn = Callable[[str, str], str]
ActionFn = Callable[[str, str], tuple[str, str]]

# Below this many nodes, a graph is thin enough that the discovery config is
# the likely cause. Unchanged from when this lived in ``weld.prime``.
_THIN_GRAPH_NODES = 5


def _only_federates(weld_dir: Path, root: Path) -> bool:
    """Whether the workspace registry is *root*'s whole discovery input."""
    if (weld_dir / "discover.yaml").is_file():
        return False
    return find_workspaces_yaml(root) is not None


def check_discover_yaml(
    weld_dir: Path, root: Path, status: StatusFn, action: ActionFn,
) -> tuple[list[str], list[str]]:
    """Report the discovery config at *weld_dir*, or its considered absence."""
    path = weld_dir / "discover.yaml"
    if path.is_file():
        count = _count_active_sources(path)
        plural = "s" if count != 1 else ""
        return [status("OK", f"discover.yaml exists ({count} active source{plural})")], []
    if _only_federates(weld_dir, root):
        return [status(
            "INFO",
            "discover.yaml not found -- this root federates; it has no "
            "sources of its own to discover",
        )], []
    line, cmd = action("discover.yaml not found", "wd init")
    return [line], [cmd]


def node_count_lines(
    total: int, weld_dir: Path, root: Path, status: StatusFn,
) -> list[str]:
    """Advise on a thin graph, unless the root's graph is a federation meta-graph."""
    if total >= _THIN_GRAPH_NODES or _only_federates(weld_dir, root):
        return []
    return [status(
        "INFO",
        f"Graph has only {total} node{'s' if total != 1 else ''} — "
        f"consider adding more sources to discover.yaml",
    )]


def _count_active_sources(path: Path) -> int:
    try:
        from weld._yaml import parse_yaml

        data = parse_yaml(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            sources = data.get("sources", [])
            if isinstance(sources, list):
                return len(sources)
    except Exception:
        pass
    return 0


__all__ = ["check_discover_yaml", "node_count_lines"]
