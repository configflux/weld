"""Human-readable rendering helpers for the `wd` retrieval surface.

ADR 0040 codifies the convention that all `wd` commands default to
human-readable text and accept ``--json`` for scripted callers. This
module hosts the small, testable formatters those commands share so
each individual command file stays under the line-count cap.

The helpers are intentionally pure: each takes a payload (the same
dict the JSON path emits) and returns a string. No I/O. Callers in
``weld/_graph_cli.py`` decide whether to write the JSON envelope or
the rendered text to stdout.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from weld._cli_render_freshness import (
    child_roster_lines, seed_blocked_lines, stale_sources_lines,
    stats_workspace_lines,
)
from weld._cli_render_prose import prose_line
from weld._cli_render_seeds import callers_seeds_lines
from weld._cli_render_trust import stats_trust_lines


def render_query(payload: Mapping[str, Any]) -> str:
    """Render a ``wd query`` / OR-fallback envelope as sectioned text.

    Layout matches the brief / agents-explain template: a header, then
    one indented block per match with ``id``, ``type``, optional
    ``label``, and a one-line description excerpt when present.
    """
    term = str(payload.get("query", ""))
    matches = list(payload.get("matches") or [])
    degraded = payload.get("degraded_match")
    lines: list[str] = []
    lines.append(_header(f"query: {term}"))
    if degraded:
        lines.append(f"  (degraded match: {degraded})")
    if not matches:
        lines.append("  no matches")
        return _join(lines)
    lines.append(f"  matches ({len(matches)}):")
    for idx, match in enumerate(matches, start=1):
        lines.extend(_match_block(idx, match))
    neighbors = list(payload.get("neighbors") or [])
    if neighbors:
        lines.append(f"  neighbors ({len(neighbors)}):")
        for neighbor in neighbors[:10]:
            lines.append(f"    - {_node_one_line(neighbor)}")
        if len(neighbors) > 10:
            lines.append(f"    (+{len(neighbors) - 10} more)")
    return _join(lines)


def render_find(payload: Mapping[str, Any]) -> str:
    """Render a ``wd find`` envelope as a fixed-width table."""
    term = str(payload.get("query", ""))
    files = list(payload.get("files") or [])
    lines: list[str] = []
    lines.append(_header(f"find: {term}"))
    if not files:
        lines.append("  no matches")
        return _join(lines)
    rows: list[tuple[str, str, str]] = []
    for entry in files:
        path = str(entry.get("path", ""))
        score = str(entry.get("score", ""))
        tokens = list(entry.get("tokens") or [])
        top = ", ".join(tokens[:3])
        if len(tokens) > 3:
            top = f"{top}, +{len(tokens) - 3}"
        rows.append((path, score, top))
    path_w = max(len("path"), max(len(r[0]) for r in rows))
    score_w = max(len("score"), max(len(r[1]) for r in rows))
    header = f"  {'path':<{path_w}}  {'score':>{score_w}}  top tokens"
    lines.append(header)
    lines.append(f"  {'-' * path_w}  {'-' * score_w}  {'-' * 11}")
    for path, score, top in rows:
        lines.append(f"  {path:<{path_w}}  {score:>{score_w}}  {top}")
    return _join(lines)


def render_context(payload: Mapping[str, Any]) -> str:
    """Render a ``wd context`` envelope as node header + grouped neighbours."""
    if "error" in payload:
        return _header(f"context: error - {payload['error']}") + "\n"
    node = payload.get("node") or {}
    lines: list[str] = []
    # Canonical id drives neighbor/edge grouping (it must match the raw
    # ``from``/``to`` values on edges). The header uses display_id so the
    # invisible UNIT_SEPARATOR (federation prefix glue) does not show up
    # in user output.
    nid = str(node.get("id") or node.get("label") or "")
    nid_display = _node_display_id(node) or nid
    lines.append(_header(f"context: {nid_display}"))
    lines.append(f"  type:  {node.get('type', '')}")
    label = node.get("label")
    if label and label != nid_display:
        lines.append(f"  label: {label}")
    prose = prose_line(node.get("props") or {}, 200)
    if prose:
        lines.append(f"  {prose}")
    resolved = payload.get("resolved_from") or {}
    if resolved:
        matched = resolved.get("matched_id")
        from_q = resolved.get("query")
        if matched and from_q:
            lines.append(f"  resolved-from: {from_q!r} -> {matched}")
    neighbors = list(payload.get("neighbors") or [])
    edges = list(payload.get("edges") or [])
    grouped = _group_neighbors_by_edge_type(nid, neighbors, edges)
    if not grouped:
        lines.append("  neighbors: none")
        return _join(lines)
    lines.append(f"  neighbors ({len(neighbors)}):")
    for edge_type in sorted(grouped):
        items = grouped[edge_type]
        lines.append(f"    {edge_type} ({len(items)}):")
        for direction, other_id, other in items[:10]:
            lines.append(
                f"      {direction} {_node_one_line({'id': other_id, **(other or {})})}"
            )
        if len(items) > 10:
            lines.append(f"      (+{len(items) - 10} more)")
    return _join(lines)


def render_path(payload: Mapping[str, Any]) -> str:
    """Render a ``wd path`` envelope as a chain `a -> b -> c`."""
    nodes = list(payload.get("path") or [])
    lines: list[str] = []
    lines.append(_header("path"))
    if not nodes:
        reason = payload.get("reason") or "no path found"
        lines.append(f"  {reason}")
        return _join(lines)
    chain_ids = [_node_display_id(n) for n in nodes]
    lines.append(f"  {' -> '.join(chain_ids)}")
    edges = list(payload.get("edges") or [])
    if edges:
        lines.append(f"  edges ({len(edges)}):")
        for edge in edges:
            etype = edge.get("type", "")
            src = str(edge.get("from_display") or edge.get("from", ""))
            dst = str(edge.get("to_display") or edge.get("to", ""))
            lines.append(f"    {src} --[{etype}]--> {dst}")
    return _join(lines)


def render_callers(payload: Mapping[str, Any]) -> str:
    """Render a ``wd callers`` envelope as sectioned matches."""
    symbol = str(payload.get("symbol", ""))
    depth = payload.get("depth", 1)
    lines: list[str] = [_header(f"callers: {symbol} (depth {depth})")]
    if "error" in payload:
        lines.append(f"  error: {payload['error']}")
        return _join(lines)
    lines.extend(callers_seeds_lines(payload))
    callers = list(payload.get("callers") or [])
    if not callers:
        lines.append("  no callers")
        return _join(lines)
    lines.append(f"  callers ({len(callers)}):")
    for idx, caller in enumerate(callers, start=1):
        lines.extend(_match_block(idx, caller))
    return _join(lines)


def render_references(payload: Mapping[str, Any]) -> str:
    """Render a ``wd references`` envelope as graph + textual sections.

    ``error`` is surfaced first, the way :func:`render_callers` surfaces
    it. ``Graph.references`` learned to distinguish "weld does not know
    this name" from "weld knows it and nothing points at it" -- the
    envelope has carried an ``error`` key for the first case since bd
    nywd -- but this renderer read neither key and printed ``no
    references`` for both, so the human surface the defect was reported
    from still gave an unknown id the answer that means "nothing uses
    this" (bd ily7). A JSON-only fix leaves the default output lying.

    ``error`` no longer *returns*, though (bd 2peg): it means no graph node
    carries the name, while ``files`` is attached by the CLI from the file
    index regardless, so returning early threw away hits the envelope already
    held -- ``wd references created_at`` printed a bare "node not found" while
    ``--json`` returned twelve. A dataclass field is exactly that shape.
    """
    name = str(payload.get("symbol", ""))
    lines: list[str] = [_header(f"references: {name}")]
    matches = list(payload.get("matches") or [])
    callers = list(payload.get("callers") or [])
    files = list(payload.get("files") or [])
    if "error" in payload:
        lines.append(f"  error: {payload['error']}")
        if not files:
            return _join(lines)
    elif not matches and not callers and not files:
        lines.append("  no references")
        return _join(lines)
    if matches:
        lines.append(f"  graph matches ({len(matches)}):")
        for idx, match in enumerate(matches, start=1):
            lines.extend(_match_block(idx, match))
    if callers:
        lines.append(f"  callers ({len(callers)}):")
        for idx, caller in enumerate(callers, start=1):
            lines.extend(_match_block(idx, caller))
    if files:
        lines.append(f"  textual hits ({len(files)}):")
        for entry in files[:20]:
            score = entry.get("score", "")
            lines.append(f"    - {entry.get('path', '')}  (score {score})")
        if len(files) > 20:
            lines.append(f"    (+{len(files) - 20} more)")
    return _join(lines)


def render_stale(payload: Mapping[str, Any]) -> str:
    """Render a ``wd stale`` envelope as key:value pairs.

    At a federated root (ADR 0066 §2) the payload carries a ``children``
    array; the renderer adds a one-line child summary plus one line per
    stale child (fresh children are counted, not enumerated, so output
    stays bounded on large workspaces).

    ``branch`` / ``graph_branch`` (ADR 0096 §3) sit beside their SHA
    counterparts: live checkout identity next to recorded graph identity,
    so a wrong-branch answer is readable without ``--json``. ``stale_sources``
    names the diverging path(s) and why (:mod:`weld._cli_render_freshness`).
    ``seed_blocked_reason`` follows ``reason``: it elaborates the one value
    ``reason`` takes that points at the wrong remedy -- ``no graph`` in a
    worktree that can never seed one (ADR 0100 amendment).
    """
    lines = [_header("stale")]
    keys = (
        "stale", "source_stale", "sha_behind", "graph_sha",
        "current_sha", "commits_behind", "graph_branch", "branch", "reason",
    )
    for key in keys:
        if key in payload:
            lines.append(f"  {key}: {_format_scalar(payload[key])}")
    lines.extend(seed_blocked_lines(payload))
    lines.extend(stale_sources_lines(payload))
    children = payload.get("children")
    if isinstance(children, list):
        lines.extend(child_roster_lines(children))
    return _join(lines)


def render_stats(payload: Mapping[str, Any]) -> str:
    """Render a ``wd stats`` envelope as sectioned key:value pairs."""
    lines: list[str] = [_header("stats")]
    lines.append(f"  total_nodes: {payload.get('total_nodes', 0)}")
    lines.append(f"  total_edges: {payload.get('total_edges', 0)}")
    cov = payload.get("description_coverage_pct")
    if cov is not None:
        lines.append(f"  description_coverage_pct: {cov}")
    meaningful = payload.get("description_coverage_meaningful") or {}
    if meaningful:
        lines.append(
            "  description_coverage (meaningful types): "
            f"{meaningful.get('coverage_pct', 0.0)}% "
            f"({meaningful.get('with_description', 0)}/{meaningful.get('total', 0)})"
        )
    nbt = payload.get("nodes_by_type") or {}
    if nbt:
        lines.append("  nodes_by_type:")
        for key in sorted(nbt):
            lines.append(f"    {key}: {nbt[key]}")
    ebt = payload.get("edges_by_type") or {}
    if ebt:
        lines.append("  edges_by_type:")
        for key in sorted(ebt):
            lines.append(f"    {key}: {ebt[key]}")
    top = list(payload.get("top_authority_nodes") or [])
    if top:
        lines.append(f"  top_authority_nodes (top {payload.get('top', len(top))}):")
        for entry in top:
            lines.append(
                f"    - {entry.get('id', '')}  "
                f"(type {entry.get('type', '')}, degree {entry.get('degree', 0)})"
            )
    lines.extend(stats_trust_lines(payload.get("per_language_trust") or {}))
    stale = payload.get("stale") or {}
    if stale:
        is_stale = stale.get("stale") if isinstance(stale, Mapping) else None
        lines.append(f"  stale: {_format_scalar(is_stale)}")
    lines.extend(stats_workspace_lines(payload.get("workspaces") or {}))
    return _join(lines)


# --- internal helpers --------------------------------------------------


def _header(title: str) -> str:
    return f"# {title}"


def _join(lines: Sequence[str]) -> str:
    return "\n".join(lines).rstrip() + "\n"


def _node_display_id(node: Mapping[str, Any]) -> str:
    """Pick the user-facing ID for a node payload.

    Federation decorates every node with ``display_id`` (``child::id``),
    using the printable ``::`` form instead of the canonical ``\\x1f``
    UNIT_SEPARATOR that joins child name and child-local id internally.
    The canonical ``id`` field stays machine-readable for splitters and
    sqlite consumers; text rendering prefers the printable form so users
    do not see (or copy-paste) the invisible control character.

    Non-federated payloads never carry ``display_id``; we fall back to
    ``id`` and then ``label`` so single-repo output is unchanged.
    """
    display = node.get("display_id")
    if display:
        return str(display)
    return str(node.get("id") or node.get("label") or "")


def _match_block(idx: int, match: Mapping[str, Any]) -> list[str]:
    nid = _node_display_id(match)
    ntype = str(match.get("type") or "")
    label = match.get("label")
    props = match.get("props") or {}
    out = [f"    {idx}. {nid}  [type: {ntype or 'unknown'}]"]
    if label and label != nid:
        out.append(f"       label: {label}")
    confidence = props.get("confidence")
    if confidence:
        out.append(f"       confidence: {confidence}")
    score = match.get("score")
    if score is not None:
        out.append(f"       score: {score}")
    targets = match.get("targets")
    if targets:
        out.append(f"       targets: {', '.join(str(t) for t in targets)}")
    prose = prose_line(props, 160)
    if prose:
        out.append(f"       {prose}")
    return out


def _node_one_line(node: Mapping[str, Any]) -> str:
    nid = _node_display_id(node)
    ntype = node.get("type") or ""
    return f"{nid}  [type: {ntype}]" if ntype else nid


def _format_scalar(value: object) -> str:
    if value is None:
        return "(none)"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _group_neighbors_by_edge_type(
    center_id: str,
    neighbors: Iterable[Mapping[str, Any]],
    edges: Iterable[Mapping[str, Any]],
) -> dict[str, list[tuple[str, str, dict]]]:
    """Bucket neighbours by the edge type connecting them to *center_id*.

    Returns ``{edge_type: [(direction, neighbor_id, neighbor_dict), ...]}``
    where ``direction`` is ``"->"`` for an outgoing edge and ``"<-"`` for
    an incoming edge. Neighbours unreferenced by any incident edge fall
    under ``"(unknown)"`` so the renderer never silently drops them.
    """
    by_id: dict[str, dict] = {}
    for neighbor in neighbors:
        nid = str(neighbor.get("id") or "")
        if nid:
            by_id[nid] = dict(neighbor)
    grouped: dict[str, list[tuple[str, str, dict]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for edge in edges:
        src = str(edge.get("from", ""))
        dst = str(edge.get("to", ""))
        etype = str(edge.get("type", "")) or "(unknown)"
        if src == center_id and dst != center_id:
            other = dst
            direction = "->"
        elif dst == center_id and src != center_id:
            other = src
            direction = "<-"
        else:
            continue
        key = (etype, direction, other)
        if key in seen:
            continue
        seen.add(key)
        grouped.setdefault(etype, []).append(
            (direction, other, by_id.get(other, {}))
        )
    return grouped
