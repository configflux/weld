"""Stdio MCP server exposing wd query helpers as structured tools.

Thin adapter over :mod:`weld.graph`, :mod:`weld.brief`, and
:mod:`weld.file_index` (ADR 0015). Each tool handler loads a fresh
:class:`weld.graph.Graph` and delegates to the same helper the CLI uses.
The ``mcp`` SDK is optional -- only :func:`run_stdio` requires it.

The registered tool set lives in :func:`weld._mcp_tools.build_tools`, and the
dispatch path a tool name travels in :mod:`weld._mcp_dispatch`; the stdio
transport lives in :mod:`weld._mcp_stdio`. This module is the composition
root for both (ADR 0130 disposition #7): it defines :func:`build_tools` and
wraps each sibling's entry points, injecting ``build_tools`` at the call site
instead of either sibling importing it back -- which used to make the three
files a cycle. ``weld.mcp_server.dispatch`` / ``dispatch_to_text_payload`` /
``run_stdio`` / ``main`` keep their original signatures, so this stays the one
import path callers use for any of them.
"""

# Must stay first, and is imported for its effect: this module is a
# ``python -m`` target, so the launch directory sits ahead of the standard
# library on ``sys.path`` until this import removes it. Every import below
# -- weld's own tree and the stdlib it pulls in -- would otherwise be
# answerable by a file in the repository being served -- and nothing may be
# added above it, which is why this module carries no ``from __future__
# import annotations``: a future statement must be a module's first
# statement and is a real import at runtime, so it would run the served
# repository's own ``__future__.py``. See :mod:`weld._launch_path`.
from weld import _launch_path  # noqa: F401  (launch-path guard; keep first)

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from weld._mcp_enrich import weld_enrich as _weld_enrich
from weld._mcp_guard import (
    graph_present as _graph_present,
    missing_graph_payload as _missing_graph_payload,
)
from weld._mcp_read import (
    attach_children_status as _attach_children_status,
    load_graph_for_read as _load_graph,
    stale_for_root as _stale_for_root,
)
from weld.brief import brief as _brief
from weld.read import read_query as _read_query
from weld.read import shape_brief as _shape_brief
from weld.read import shape_read_envelope as _shape_read_envelope
from weld.read_traversal import shape_callers as _shape_callers
from weld.read_traversal import shape_references as _shape_references
from weld.diff import load_and_diff as _load_and_diff
from weld.federation import FederatedGraph as _FederatedGraph
from weld.federation_tools import (
    federated_callers as _federated_callers,
    federated_references as _federated_references,
)
from weld.file_index import find_files as _find_files
from weld.file_index import load_file_index as _load_file_index
from weld.mcp_helpers import weld_impact as _weld_impact
from weld.mcp_helpers import weld_review_guarded as _weld_review_guarded
from weld.mcp_helpers import weld_trace as _weld_trace
from weld.workspace_state import find_workspaces_yaml as _find_workspaces_yaml

# ---------------------------------------------------------------------------
# Tool descriptors
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Tool:
    """A lightweight, SDK-agnostic description of an MCP tool."""

    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Any] = field(repr=False)

# ---------------------------------------------------------------------------
# Tool implementations (pure adapters)
# ---------------------------------------------------------------------------

def weld_query(
    term: str, limit: int = 20, *,
    full_neighborhood: bool = False, full_size: bool = False,
    include_speculative: bool = False, root: Path | str = ".",
) -> dict:
    """Tokenized ranked search. Delegates to ``Graph.query``; see
    :func:`_attach_children_status` for the federated-only extra field.

    Shaped by the shared :func:`weld.read.read_query`, so the answer is
    identical to ``wd query`` (ADR 0083): the speculative-match filter drops
    ``origin=unresolved`` sentinels from ``matches`` unless
    ``include_speculative=True``, then the ADR 0078 diet + ADR 0082 byte budget
    apply (all reported in ``omitted_neighbors``). ``full_neighborhood=True``
    restores the raw neighborhood; ``full_size=True`` keeps the diet but skips
    the byte budget. Missing-graph guard applies (single-repo root only)."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_query", root=root)
    g = _load_graph(Path(root))
    envelope = _read_query(
        g.query(term, limit=limit), include_speculative=include_speculative,
        full=full_neighborhood, full_size=full_size)
    return _attach_children_status(g, envelope)

def weld_find(term: str, limit: int | None = None, *, root: Path | str = ".") -> dict:
    """File-index substring search. Delegates to ``weld.file_index.find_files``;
    at a federated root fans out across every child index (ADR 0089), matching
    ``wd find``. Negative ``limit`` is ignored (pre-change MCP tolerance)."""
    effective_limit = limit if limit is None or limit >= 0 else None
    root_path = Path(root)
    if _find_workspaces_yaml(root_path) is not None:
        from weld._federation_find import federated_find
        return federated_find(root_path, term, limit=effective_limit)
    # Same self-heal the CLI runs before answering (bd yw4b). MCP is a thin
    # wrapper of the product: a repair that only the CLI performed would make
    # the two surfaces answer differently about the same tree.
    from weld._file_index_coverage import ensure_index_covers_surface
    ensure_index_covers_surface(root_path)
    return _find_files(_load_file_index(root_path), term, limit=effective_limit)

def weld_context(
    node_id: str, *, full_neighborhood: bool = False, full_size: bool = False,
    root: Path | str = ".",
) -> dict:
    """Node + 1-hop neighborhood. Delegates to ``Graph.context``; see
    :func:`_attach_children_status` for the federated-only extra field.

    Bounded read shaping (ADR 0082) applies by default via the shared
    :func:`weld.read.shape_read_envelope`; ``full_neighborhood=True`` restores
    the raw neighborhood and ``full_size=True`` skips only the byte budget. A
    node-not-found miss is returned unchanged. Missing-graph guard applies
    (single-repo root only)."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_context", root=root)
    g = _load_graph(Path(root))
    envelope = _shape_read_envelope(
        g.context(node_id), full=full_neighborhood, full_size=full_size)
    return _attach_children_status(g, envelope)

def weld_path(from_id: str, to_id: str, *, root: Path | str = ".") -> dict:
    """Shortest path between two nodes. Delegates to ``Graph.path``; see
    :func:`_attach_children_status` for the federated-only extra field.
    Missing-graph guard applies (single-repo root only)."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_path", root=root)
    g = _load_graph(Path(root))
    return _attach_children_status(g, g.path(from_id, to_id))

def weld_brief(
    area: str, limit: int = 20, *, full_size: bool = False,
    root: Path | str = ".",
) -> dict:
    """Stable brief JSON for *area*. Delegates to ``weld.brief.brief``, then
    bounds it via the shared :func:`weld.read.shape_brief` (ADR 0082):
    edges are de-dangled to emitted bucket nodes and the ``weld_query`` byte
    budget applies; ``full_size=True`` returns the unbounded brief. In a
    federated workspace the graph is a
    :class:`~weld.federation.FederatedGraph`, so the brief spans child repos.
    Missing-graph guard applies (single-repo root only)."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_brief", root=root)
    g = _load_graph(Path(root))
    return _shape_brief(_brief(g, area, limit=limit), full_size=full_size)

def weld_stale(*, root: Path | str = ".") -> dict:
    """Graph freshness vs git HEAD. Delegates to
    :func:`weld._mcp_read.stale_for_root`, so the payload is the one
    ``wd stale --json`` prints -- including the ``{branch, graph_branch}``
    pair naming which checkout answered (ADR 0083, ADR 0096 §3). In a
    federated workspace that payload carries a ``children`` list of
    ``{name, state, reason, commits_behind}`` and folds child drift into the
    top-level ``stale`` (ADR 0066 §2, ADR 0100), so an agent gating on this
    tool at a polyrepo root sees a drifted child.

    Alone among the reads it does **not** auto-refresh first (ADR 0102): the
    freshness oracle measures, it does not heal, so the answer is the state a
    caller would have found, and no graph write is made to produce it. The
    graph-backed reads still self-heal, so a stale verdict here is a report,
    not a prediction about the next read."""
    return _stale_for_root(Path(root))

def weld_callers(
    symbol_id: str, depth: int = 1, *, full_size: bool = False,
    root: Path | str = ".",
) -> dict:
    """Return direct (and optionally transitive) callers of *symbol_id*.

    In a federated workspace, prefixed symbol IDs (``child<US>local_id``)
    are resolved within the named child graph. Missing-graph guard
    applies (single-repo root only).

    A bare ``symbol_id`` can resolve to several same-named seeds; every
    response carries a top-level ``seeds`` list naming the resolved id(s),
    and at ``depth=1`` each caller also carries a ``targets`` list naming
    which seed(s) it was found calling directly -- not populated beyond
    depth 1, where a caller can be reachable via more than one seed's chain
    (bd jz65r).

    Bounded by the shared :func:`weld.read_traversal.shape_callers` (ADR 0082)
    -- the widest read weld offers, and the same shaper ``wd callers --json``
    applies (ADR 0083). Drops are reported in ``size_capped``;
    ``full_size=True`` skips the byte budget."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_callers", root=root)
    g = _load_graph(Path(root))
    if isinstance(g, _FederatedGraph):
        callers = _federated_callers(g, symbol_id, depth=depth)
    else:
        callers = g.callers(symbol_id, depth=depth)
    return _shape_callers(callers, full_size=full_size)

def weld_export(
    format: str, node_id: str | None = None, depth: int = 1,
    *, root: Path | str = ".",
) -> dict:
    """Export graph to a visualization format. Delegates to ``weld.export``.
    Missing-graph guard applies (single-repo root only)."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_export", root=root)
    from weld.export import export
    try:
        output = export(format, node_id=node_id, depth=depth, root=root)
    except json.JSONDecodeError:
        # bd tl32: json.JSONDecodeError is itself a ValueError, so it used
        # to be swallowed by the broad except below into an unstructured
        # {"error": str(exc)} with no error_code -- before ever reaching
        # _dispatch_inner's shared classify_graph_load_error, which every
        # other graph-backed tool relies on for the graph_corrupt contract.
        # Re-raising here (instead of catching it below) lets it propagate
        # to that shared classifier. The broad except ValueError below keeps
        # its original, narrower job: export()'s own argument-validation
        # errors (an unrecognized --format, or --format=wiki without
        # --output) are not graph-load failures and stay a plain
        # {"error": ...} payload.
        raise
    except ValueError as exc:
        return {"error": str(exc)}
    return {"format": format, "output": output}


def weld_references(
    symbol_name: str, *, full_size: bool = False, root: Path | str = ".",
) -> dict:
    """Return what points at *symbol_name*, plus file-index references.

    Takes a bare symbol name or a full node id. A symbol yields its
    call-graph callers; any other node type yields every node with an edge
    into it, since nothing *calls* a build target, tool or doc. An id weld
    does not know yields an ``error`` rather than an empty result -- see
    :func:`weld.graph_referrers.references` for why that distinction is the
    defect and not a nicety (bd nywd).

    A bare name can resolve to several ``matches``; each ``callers`` entry
    then carries a ``targets`` list naming which match id(s) it was found
    calling, so two same-named matches with different callers do not merge
    into one undifferentiated list (bd nyoks).

    In a federated workspace, references fan out across all present
    children with prefixed IDs. Missing-graph guard applies (single-repo
    root only).

    Bounded by the shared :func:`weld.read_traversal.shape_references`
    (ADR 0082), applied *after* ``files`` is attached because the file hits are
    the largest and lowest-priority part of the union. Same shaper as
    ``wd references --json`` (ADR 0083); ``full_size=True`` skips the budget."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_references", root=root)
    g = _load_graph(Path(root))
    if isinstance(g, _FederatedGraph):
        refs = _federated_references(g, symbol_name)
    else:
        refs = g.references(symbol_name)
    index = _load_file_index(Path(root))
    refs["files"] = _find_files(index, symbol_name).get("files", [])
    return _shape_references(refs, full_size=full_size)

def weld_diff(*, root: Path | str = ".") -> dict:
    """Return the graph diff between previous and current discovery run.
    Missing-graph guard applies (single-repo root only)."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_diff", root=root)
    return _load_and_diff(Path(root))


def weld_trace(
    *,
    term: str | None = None,
    node_id: str | None = None,
    depth: int = 2,
    seed_limit: int = 5,
    full_size: bool = False,
    root: Path | str = ".",
) -> dict:
    """Protocol-aware cross-boundary slice. Delegates to
    :func:`weld.mcp_helpers.weld_trace`, which applies the ADR 0082 byte
    budget; ``full_size=True`` skips it. Missing-graph guard applies."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_trace", root=root)
    return _weld_trace(
        term=term, node_id=node_id, depth=depth, seed_limit=seed_limit,
        full_size=full_size, root=root,
    )


def weld_impact(
    target: str, depth: int = 3, *, full_size: bool = False,
    root: Path | str = ".",
) -> dict:
    """Reverse-dependency blast radius. Delegates to
    :func:`weld.mcp_helpers.weld_impact`, which applies the ADR 0082 byte
    budget -- dependents are pruned and reported in ``warnings.size_capped``
    while ``affected_surfaces`` / ``risk_level`` stay computed over the full
    blast radius; ``full_size=True`` skips it. Missing-graph guard applies."""
    if not _graph_present(Path(root)):
        return _missing_graph_payload("weld_impact", root=root)
    return _weld_impact(target, depth=depth, full_size=full_size, root=root)


def weld_enrich(**kwargs) -> dict:
    """LLM-assisted enrichment, or the agent-direct work plan.

    Delegates to :func:`weld._mcp_enrich.weld_enrich`; missing-graph guard
    applies. Passed through as ``**kwargs`` -- the pattern
    :func:`weld.mcp_helpers.weld_review_guarded` already uses -- so the
    argument list is declared once, next to the schema that advertises it,
    instead of being restated by every guard it passes through."""
    if not _graph_present(Path(root := kwargs.get("root", "."))):
        return _missing_graph_payload("weld_enrich", root=root)
    return _weld_enrich(**kwargs)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def build_tools() -> list[Tool]:
    """Return the list of registered MCP tools.

    The order is stable to make test pinning easy.
    """
    from weld._mcp_tools import build_tools as _build_tools_impl

    return _build_tools_impl(
        weld_query=weld_query,
        weld_find=weld_find,
        weld_context=weld_context,
        weld_path=weld_path,
        weld_brief=weld_brief,
        weld_stale=weld_stale,
        weld_callers=weld_callers,
        weld_references=weld_references,
        weld_export=weld_export,
        weld_diff=weld_diff,
        weld_trace=weld_trace,
        weld_impact=weld_impact,
        weld_enrich=weld_enrich,
        weld_review=_weld_review_guarded,
        tool_cls=Tool,
    )

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
# Lives in :mod:`weld._mcp_dispatch` (line-count cap, as with the transport
# below). Neither sibling imports this module, even lazily (ADR 0130
# disposition #7): these wrappers inject ``build_tools`` at the call site.
from weld import _mcp_dispatch  # noqa: E402  (compose: inject build_tools)
from weld._mcp_dispatch import _dispatch_inner  # noqa: E402,F401  (re-export)

def dispatch(tool_name: str, arguments: dict | None, *, root: Path | str = ".") -> dict:
    """:func:`weld._mcp_dispatch.dispatch`, ``build_tools`` wired in."""
    return _mcp_dispatch.dispatch(
        tool_name, arguments, root=root, tools_provider=build_tools)

def dispatch_to_text_payload(
    tool_name: str, arguments: dict | None, *, root: Path | str = ".",
) -> str:
    """:func:`weld._mcp_dispatch.dispatch_to_text_payload`, wired the same way."""
    return _mcp_dispatch.dispatch_to_text_payload(
        tool_name, arguments, root=root, tools_provider=build_tools)

# ---------------------------------------------------------------------------
# Stdio entry point (optional; requires the ``mcp`` SDK)
# ---------------------------------------------------------------------------
# Lives in :mod:`weld._mcp_stdio`, same reason; same disposition -- it does
# not import this module either. Both launch forms compose here too.
from weld import _mcp_stdio  # noqa: E402  (compose: inject build_tools)

def run_stdio(root: Path | str = ".") -> int:
    """:func:`weld._mcp_stdio.run_stdio`, ``build_tools`` wired in."""
    return _mcp_stdio.run_stdio(root, tools_provider=build_tools)

def main(argv: list[str] | None = None, *, prog: str = _mcp_stdio.MODULE_PROG) -> int:
    """:func:`weld._mcp_stdio.main`, ``build_tools`` wired in; ``wd mcp serve``
    (:mod:`weld._mcp_cli`) passes ``prog=CONSOLE_PROG``."""
    return _mcp_stdio.main(argv, prog=prog, tools_provider=build_tools)

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
