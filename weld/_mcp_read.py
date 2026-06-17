"""Shared read-path core for the weld MCP server (bd 85tb.3).

The CLI self-heals on every read: it calls
:func:`weld._auto_refresh.auto_refresh_if_stale` *before* loading the graph
(ADR 0051) and reports freshness via ``wd stale`` (ADR 0017). The MCP surface
historically did neither -- it loaded a possibly-stale graph and emitted no
freshness signal, so an agent driving the MCP server could silently consume
stale answers. This module routes MCP graph-backed *reads* through the same
core path so the two surfaces share one freshness contract:

1. :func:`load_graph_for_read` runs ``auto_refresh_if_stale`` (which already
   honours the ``WELD_AUTO_REFRESH=0`` / ``--no-refresh`` opt-outs and, at a
   federated root, delegates to the ADR 0066 auto-recurse path), then loads
   the graph through an in-process **cache keyed by the graph file's content
   sha** so a repeated MCP call does not re-read and re-parse the (on this
   repo ~14 MB) ``graph.json`` every time.
2. :func:`freshness_for` derives the small ``{stale, commits_behind}`` object
   stamped onto every read payload by :func:`stamp_freshness`.

Scope / safety:

* The cache serves **read** tools only (``query`` / ``context`` / ``path`` /
  ``brief`` / ``callers`` / ``references`` / ``trace`` / ``impact``). Mutating
  tools (``enrich`` / ``review``) keep their own uncached load so a persist
  never writes back from a shared in-memory object.
* The freshness object exposes only two scalars (a bool and an int). It never
  carries a path, a SHA, or graph content -- so it cannot leak a secret living
  in a half-written graph or reveal the server's filesystem layout.
* This module is a thin *caller* of the shared auto-refresh entry point; it
  does NOT re-implement or alter the ADR 0017 staleness decision tree (that
  stays single-sourced in :mod:`weld._staleness` / :mod:`weld._auto_refresh`).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from weld._auto_refresh import auto_refresh_if_stale
from weld.federation import FederatedGraph as _FederatedGraph
from weld.graph import Graph as _Graph
from weld.workspace_state import find_workspaces_yaml as _find_workspaces_yaml

#: MCP read tools whose successful payload is stamped with the ``freshness``
#: object at the dispatch boundary. Graph-backed reads only -- ``weld_find``
#: reads the file index (not the graph), ``weld_stale`` *is* the detailed
#: freshness surface, and the diagnostic (``weld_diff`` / ``weld_export``) and
#: mutating (``weld_enrich`` / ``weld_review``) tools are out of scope.
FRESHNESS_TOOLS: frozenset[str] = frozenset(
    {
        "weld_query",
        "weld_context",
        "weld_path",
        "weld_brief",
        "weld_callers",
        "weld_references",
        "weld_trace",
        "weld_impact",
    }
)

# ---------------------------------------------------------------------------
# In-process graph cache (single-repo only)
# ---------------------------------------------------------------------------
# Keyed by the absolute graph-file path; the value carries the content sha the
# Graph was loaded from. A cache hit requires the on-disk sha to still match,
# so an external rewrite (a ``wd discover`` in another process, or the
# auto-refresh that just ran above) is picked up on the next call. Federated
# roots are never cached: their answers fold in child graphs that mutate
# independently of the root file, so a root-file sha cannot witness child
# freshness.
_GRAPH_CACHE: dict[str, tuple[str, _Graph]] = {}


def clear_graph_cache() -> None:
    """Drop all cached graphs (test seam; also safe to call in long sessions)."""
    _GRAPH_CACHE.clear()


def _graph_path(root: Path) -> Path:
    return root / ".weld" / "graph.json"


def _file_sha(path: Path) -> str | None:
    """Return a content sha for *path*, or ``None`` when it cannot be read.

    Uses a streamed sha-256 over the raw bytes -- the digest is an opaque
    cache key only; it is never surfaced to a client, so the choice of hash is
    a performance/collision tradeoff, not a security boundary.
    """
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _is_federated(root: Path) -> bool:
    return _find_workspaces_yaml(root) is not None


def _refresh_before_serve(root: Path) -> None:
    """Run ``auto_refresh_if_stale`` ahead of a read (ADR 0051).

    ``json_output=True`` suppresses the human banner on the JSON MCP surface;
    the ``WELD_AUTO_REFRESH=0`` / ``--no-refresh`` opt-outs and the federated
    auto-recurse delegation are all resolved inside the shared call, which is
    itself failure-isolated and never raises on the read path.
    """
    auto_refresh_if_stale(root, json_output=True)


def _load_single_repo_cached(root: Path) -> _Graph:
    """Load a single-repo ``Graph`` for *root* via the sha-keyed cache.

    A cache hit requires the on-disk ``graph.json`` bytes to be unchanged since
    the last load; otherwise the graph is reloaded and the entry refreshed. A
    corrupt / unsupported graph raises out of ``Graph.load`` so the
    :mod:`weld._mcp_guard` boundary can convert it -- exactly as before. A
    genuinely missing file is loaded uncached (``Graph`` synthesizes an empty
    in-memory graph), preserving the prior empty-payload behaviour for callers
    that reach here without the missing-graph guard.
    """
    path = _graph_path(root)
    sha = _file_sha(path)
    if sha is None:
        g = _Graph(root)
        g.load()
        return g
    # A readable sha implies the file exists; resolve() normalises a relative
    # root (the MCP default cwd ``.``) so one process keys the cache stably.
    cache_key = str(path.resolve())
    cached = _GRAPH_CACHE.get(cache_key)
    if cached is not None and cached[0] == sha:
        return cached[1]
    g = _Graph(root)
    g.load()
    _GRAPH_CACHE[cache_key] = (sha, g)
    return g


def load_graph_for_read(root: Path | str) -> _Graph | _FederatedGraph:
    """Auto-refresh if stale, then return a loaded graph for *root*.

    The single entry the federation-aware MCP read tools (``weld_query`` /
    ``weld_context`` / ``weld_path`` / ``weld_brief`` / ``weld_callers`` /
    ``weld_references``) use instead of constructing a graph directly:
    ``auto_refresh_if_stale`` runs first, then a single-repo graph is served
    from the sha-keyed in-process cache while a federated root is rebuilt each
    call (its child graphs are not witnessed by the root file's sha).
    """
    root_path = Path(root)
    _refresh_before_serve(root_path)
    if _is_federated(root_path):
        return _FederatedGraph(root_path)
    return _load_single_repo_cached(root_path)


def load_single_repo_for_read(root: Path | str) -> _Graph:
    """Auto-refresh, then return a *single-repo* cached ``Graph`` for *root*.

    Used by the read helpers that operate on a single-repo graph even at a
    federated root (``weld_trace`` / ``weld_impact`` -- they index the root's
    own graph, and the alias / trace / impact helpers expect a ``Graph``). The
    refresh is still federation-aware (it recurses stale children when *root* is
    a workspace), so those reads self-heal identically; only the *loaded object*
    is pinned to the single-repo ``Graph`` to preserve prior behaviour.
    """
    root_path = Path(root)
    _refresh_before_serve(root_path)
    return _load_single_repo_cached(root_path)


# ---------------------------------------------------------------------------
# Freshness signal
# ---------------------------------------------------------------------------

def freshness_for(root: Path | str) -> dict:
    """Return ``{"stale": bool, "commits_behind": int}`` for *root*.

    Derived from the same ADR 0017 oracle the CLI uses --
    :func:`weld._staleness.compute_stale_info` over the root's
    ``.weld/graph.json`` meta -- so ``stale`` is the source-drift signal and
    ``commits_behind`` is the recorded-SHA-vs-HEAD distance (``-1`` when no SHA
    was recorded yet). At a federated root this reflects the *root meta-graph*,
    which the preceding auto-refresh rebuilds after recursing stale children;
    it is a cheap inline proxy, while ``weld_stale`` remains the detailed
    per-child surface. Using the root graph file (rather than constructing a
    second :class:`~weld.federation.FederatedGraph`) keeps the stamp off the
    expensive child fan-out so it never doubles a federated read's cost.

    The result deliberately carries **only** the two whitelisted scalars so the
    payload can never leak the graph path, the recorded SHA, or graph content.
    Best-effort: any failure degrades to ``{"stale": False, "commits_behind":
    0}`` so a freshness probe never breaks a read.
    """
    default = {"stale": False, "commits_behind": 0}
    root_path = Path(root)
    path = _graph_path(root_path)
    if not path.is_file():
        return default
    try:
        from weld._auto_refresh import _read_graph_meta
        from weld._staleness import compute_stale_info

        meta = _read_graph_meta(path)
        if meta is None:
            return default
        info = compute_stale_info(path, meta)
    except Exception:  # noqa: BLE001 - freshness is advisory; never break a read.
        return default
    return {
        "stale": bool(info.get("stale", False)),
        "commits_behind": _coerce_int(info.get("commits_behind", 0)),
    }


def _coerce_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def stamp_freshness(result: dict, root: Path | str) -> dict:
    """Add the ``freshness`` object to a successful read payload, in place.

    Mirrors the additive contract of
    :func:`weld._mcp_guard.stamp_node_not_found`: the field is purely additive
    and is *skipped* for any payload that is not a plain ``dict`` or that
    already carries an ``error_code`` (a structured graph-load / node-not-found
    error must stay clean -- a freshness probe on a broken graph is both
    meaningless and a needless second filesystem hit). An explicit ``error``
    string with no code (e.g. ``weld_impact`` bad-target) is also left
    unstamped for the same reason.
    """
    if not isinstance(result, dict):
        return result
    if "error_code" in result or "error" in result:
        return result
    result["freshness"] = freshness_for(root)
    return result
