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
   the graph through an in-process **cache keyed by the content sha of both
   ``graph.json`` and its ADR 0065 volatile-meta sidecar** so a repeated MCP
   call does not re-read and re-parse the (on this repo ~14 MB) ``graph.json``
   every time. Both files key the cache because ``Graph.load()`` overlays the
   sidecar onto the cached object's ``meta`` (``git_sha`` / ``git_branch`` /
   ``updated_at``) -- a write that changes only the sidecar (``wd touch``)
   must still invalidate the entry, or the cache would keep serving a stale
   ``git_sha`` a fresh load would not (bd 7bjw).
2. :func:`freshness_for` derives the small
   ``{stale, commits_behind, branch}`` object stamped onto every read payload
   by :func:`stamp_freshness`.
3. :func:`stale_for_root` answers ``weld_stale`` -- the detailed surface the
   stamp is a proxy for -- by delegating to the one shaper ``wd stale`` uses,
   at a single repo and at a federated root alike, so the two surfaces agree
   field for field (ADR 0083, ADR 0100). It is the one read here that does
   **not** take step 1: the freshness oracle is exempt from
   refresh-before-serve (ADR 0102), because a probe that heals before
   answering reports on a state it just destroyed. It measures; it never
   rewrites the graph.

Scope / safety:

* The cache serves **read** tools only (``query`` / ``context`` / ``path`` /
  ``brief`` / ``callers`` / ``references`` / ``trace`` / ``impact``, plus
  ``stale``, which uses it without the refresh). Mutating tools (``enrich`` /
  ``review``) keep their own uncached load so a persist never writes back from
  a shared in-memory object.
* The freshness object exposes only three scalars (a bool, an int, and the
  live branch name or ``None``). It never carries a path, a SHA, or graph
  content -- so it cannot leak a secret living in a half-written graph or
  reveal the server's filesystem layout.
* This module is a thin *caller* of the shared auto-refresh entry point; it
  does NOT re-implement or alter the ADR 0017 staleness decision tree (that
  stays single-sourced in :mod:`weld._staleness` / :mod:`weld._auto_refresh`).
"""

from __future__ import annotations

from pathlib import Path

from weld._auto_refresh import auto_refresh_if_stale
from weld._git_worktree import get_git_branch
from weld._graph_meta_sidecar import sidecar_path_for as _sidecar_path_for
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
# Keyed by the absolute graph-file path; the value carries the (graph_sha,
# sidecar_sha) pair the Graph was loaded from. A cache hit requires BOTH
# on-disk shas to still match, so an external rewrite of either file -- a
# ``wd discover`` in another process, the auto-refresh that just ran above,
# or a sidecar-only ``wd touch`` -- is picked up on the next call (bd 7bjw:
# graph.json alone under-witnessed the cached object, because Graph.load()
# overlays the sidecar into ``meta`` at load time). Federated roots are never
# cached: their answers fold in child graphs that mutate independently of the
# root file, so a root-file sha cannot witness child freshness.
_GRAPH_CACHE: dict[str, tuple[tuple[str, str | None], _Graph]] = {}


def clear_graph_cache() -> None:
    """Drop all cached graphs (test seam; also safe to call in long sessions)."""
    _GRAPH_CACHE.clear()


def _graph_path(root: Path) -> Path:
    return root / ".weld" / "graph.json"


def _file_sha(path: Path) -> str | None:
    """Return a content sha for *path*, or ``None`` when it cannot be read.

    Delegates to the process-local :func:`weld._graph_digest.file_sha256` memo
    (bd aqqa) so the (~16 MB) ``graph.json`` is hashed once per cold read rather
    than again here after :mod:`weld._query_sidecar` already hashed it for its
    envelope check. The digest is an opaque cache key only; it is never
    surfaced to a client, so the memo's (path, mtime, size) key -- which busts
    on any write -- is a performance/collision tradeoff, not a security
    boundary.
    """
    from weld._graph_digest import file_sha256

    return file_sha256(path)


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

    A cache hit requires BOTH the on-disk ``graph.json`` bytes AND its ADR
    0065 volatile-meta sidecar (``graph-meta.json``) to be unchanged since the
    last load; otherwise the graph is reloaded and the entry refreshed. The
    sidecar must key the cache too: ``Graph.load()`` overlays its volatile
    keys (``git_sha`` / ``git_branch`` / ``updated_at``) onto the loaded
    object's ``meta``, and a write that touches only the sidecar -- ``wd
    touch`` (``Graph.save(touch_git_sha=True)``) leaves ``graph.json``
    byte-identical whenever the non-volatile body is unchanged -- would
    otherwise leave a stale cached object keyed on an unchanged graph.json sha
    (bd 7bjw). The sidecar is a few hundred bytes, so hashing it costs one
    extra ``stat`` (memoized: :func:`weld._graph_digest.file_sha256` only
    re-hashes when the sidecar's own mtime/size change) per dispatch -- no
    measurable overhead next to the ``graph.json`` read this function already
    does. A missing sidecar keys as ``None``, distinct from any real digest
    (including the sha256 of an empty file), so "no sidecar" can never
    collide with "sidecar present but empty".

    A corrupt / unsupported graph raises out of ``Graph.load`` so the
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
    # A readable sha implies graph.json exists; resolve() normalises a
    # relative root (the MCP default cwd ``.``) so one process keys the cache
    # stably. The sidecar sha is read whether or not the sidecar exists --
    # ``_file_sha`` degrades to ``None`` on a missing file -- so absence is
    # itself part of the key and a sidecar that appears (or disappears)
    # between calls busts the cache exactly like a content change would.
    key = (sha, _file_sha(_sidecar_path_for(path)))
    cache_key = str(path.resolve())
    cached = _GRAPH_CACHE.get(cache_key)
    if cached is not None and cached[0] == key:
        return cached[1]
    g = _Graph(root)
    g.load()
    _GRAPH_CACHE[cache_key] = (key, g)
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


# ---------------------------------------------------------------------------
# Freshness signal
# ---------------------------------------------------------------------------

def freshness_for(root: Path | str) -> dict:
    """Return ``{"stale": bool, "commits_behind": int, "branch": str|None}``.

    Staleness is derived from the same ADR 0017 oracle the CLI uses --
    :func:`weld._staleness.compute_stale_info` over the root's
    ``.weld/graph.json`` meta -- so ``stale`` is the source-drift signal and
    ``commits_behind`` is the recorded-SHA-vs-HEAD distance (``-1`` when no SHA
    was recorded yet). At a federated root this reflects the *root meta-graph*,
    which the preceding auto-refresh rebuilds after recursing stale children;
    it is a cheap inline proxy, while ``weld_stale`` remains the detailed
    per-child surface. Using the root graph file (rather than constructing a
    second :class:`~weld.federation.FederatedGraph`) keeps the stamp off the
    expensive child fan-out so it never doubles a federated read's cost.

    ``branch`` (ADR 0096 §3) is the branch **live** at *root* at stamp time --
    deliberately not the sidecar's recorded ``git_branch``, because the
    question this field answers is "which checkout am I being served from",
    and the live value is the one that exposes a wrong-root answer. ``None``
    outside git and on a detached ``HEAD``. ``wd stale`` is the surface that
    reports live-vs-recorded side by side
    (:func:`weld._stale_payload.branch_identity`).

    The result deliberately carries **only** the three whitelisted scalars so
    the payload can never leak the graph path, the recorded SHA, or graph
    content -- a branch name is checkout identity the caller already holds,
    not a secret the graph could contain. The key set is invariant: the
    degraded path below reports the same three keys, so a consumer never has
    to probe for presence. Best-effort throughout: any failure degrades to
    ``stale=False, commits_behind=0`` so a freshness probe never breaks a read.
    """
    root_path = Path(root)
    branch = get_git_branch(root_path)
    default = {"stale": False, "commits_behind": 0, "branch": branch}
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
        "branch": branch,
    }


def _coerce_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def stale_for_root(root: Path | str) -> dict:
    """Return the detailed ``wd stale`` payload for *root* -- the weld_stale answer.

    The counterpart to :func:`freshness_for`: that one is the cheap two-scalar
    proxy stamped on every read, this one is the full surface an agent asks for
    on purpose. Neither may re-derive what the product already computes
    (ADR 0083), so **every** root -- single repo and federated alike -- is
    shaped by :func:`weld._stale_payload.stale_payload`, byte-for-byte
    what ``wd stale --json`` prints. That shaper detects federation itself and
    folds per-child staleness through the ADR 0066 oracle, so there is nothing
    left for a federated branch here to add (ADR 0100). Shaping in the product
    rather than in the handler is also what carries the ``{branch,
    graph_branch}`` pair (ADR 0096 §3): on a surface that answers for a
    caller-named root, *which checkout answered* is the one field the caller
    cannot re-derive for itself.

    The graph loaded is the **root's own** ``graph.json`` even at a federated
    root -- the same file ``wd stale`` loads, since ``stale`` is not a
    :data:`weld._graph_cli.FEDERATED_CLI_COMMANDS` member. Loading a
    :class:`~weld.federation.FederatedGraph` instead would pull every child
    graph into memory to answer a question the ledger-driven oracle answers
    from git and file-stat alone.

    It is loaded through :func:`_load_single_repo_cached` -- the cache without
    the :func:`_refresh_before_serve` step -- because the freshness oracle is
    exempt from ADR 0051's refresh-before-serve (ADR 0102, amending ADR 0100
    point 3; the refreshing single-repo loader that used to stand here had no
    other caller and went with it).
    Self-heal exists so a caller never *consumes* stale content; a staleness
    payload carries no graph content, so there is nothing here to heal -- and
    healing first would change the answer, which ADR 0083 forbids transport
    from doing. Concretely, a handler that refreshed would rediscover a stale
    single repo and then report ``stale=false`` where ``wd stale`` at the same
    root in the same second reports ``stale=true``; at a federated root it
    would recurse the stale children first and report each as ``fresh``,
    defeating the ADR 0066 §2 child-drift gate ADR 0100 exists to deliver. The
    cache is still legitimate transport (ADR 0083): a hit requires both the
    ``graph.json`` bytes AND the ADR 0065 sidecar bytes to be unchanged, so it
    equals a cold load (bd 7bjw closed the gap where the sidecar alone could
    drift under an unchanged cache key).

    *root* is trusted here: bounding a request-supplied root to the served
    repository is :func:`weld._mcp_guard.resolve_dispatch_root`'s job and
    happens before any handler runs, so this function must not be called with
    a raw request argument.

    The shaper is imported per call, matching :func:`freshness_for`: it keeps
    the module-level import graph flat, and it keeps both functions patchable
    at their defining module -- which is how the test that pins
    "``freshness_for`` never triggers the child fan-out" stays honest.
    """
    from weld._stale_payload import stale_payload

    root_path = Path(root)
    return stale_payload(root_path, _load_single_repo_cached(root_path).stale())


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


def attach_children_status(
    graph: _Graph | _FederatedGraph, result: dict,
) -> dict:
    """Attach a budget-bounded ``children_status`` when *graph* is federated.

    The third additive stamp on a read payload, beside
    :func:`stamp_freshness` and :func:`weld._mcp_guard.stamp_node_not_found`,
    and it lives here for the same reason they live where they do: the
    handlers stay pure adapters. Single-repo callers see no change.
    Federated callers receive a mapping of child name -> status payload
    (``present`` / ``missing`` / ``uninitialized`` / ``corrupt``) so agents
    can tell which child repos are indexed vs degraded without probing each
    one.

    Unlike ``freshness``, this stamp is not a fixed handful of bytes: it is
    one entry per registered child, so an unbounded workspace can make it the
    dominant cost of the dispatched payload (ADR 0082 amendment, bd hwwo).
    It is bounded to :data:`weld._read_budget.CHILDREN_STATUS_RESERVE_BYTES`
    via :func:`weld._read_budget.bound_dict_to_budget` -- same fit-and-report
    contract as every other bounded bucket, never a silent truncation: a
    workspace whose children do not fit reports the survivor count via
    ``children_status_omitted`` (present and ``0`` whenever ``children_status``
    is, so a consumer never has to probe). Attached even on an error payload
    (e.g. ``weld_context`` on an unknown node), matching the existing
    contract that a caller can tell *which* child might have held it.

    ``graph.children_status()`` already returns its entries in priority
    order -- non-present states first, alphabetical within each class (ADR
    0082 amendment, bd sk3c) -- so ``bound_dict_to_budget``'s tail-drop sheds
    ordinary ``present`` entries before an actionable ``missing`` /
    ``uninitialized`` / ``corrupt`` one; this function does not re-sort.
    """
    if isinstance(graph, _FederatedGraph):
        from weld._read_budget import CHILDREN_STATUS_RESERVE_BYTES, bound_dict_to_budget

        kept, omitted = bound_dict_to_budget(
            graph.children_status(), CHILDREN_STATUS_RESERVE_BYTES,
            key="children_status",
        )
        result["children_status"] = kept
        result["children_status_omitted"] = omitted
    return result
