"""Mermaid flowchart serializer for weld graph exports.

Extracted from :mod:`weld.export` so that module stays under the 400-line
cap (CLAUDE.md line-count policy) while the Mermaid serializer grew richer
than its DOT/D2 siblings:

* per-file / -module / -package ``subgraph`` clustering,
* per-node-type ``classDef`` styling (types are visually distinct *and*
  still carry their type in the label text, so the diagram never relies on
  colour alone),
* human-readable display labels (only the node *key* is sanitized; the
  visible text stays readable and is escaped with Mermaid entity codes),
* explicit truncation annotation -- a comment plus a visible note node --
  so a capped diagram is never silently partial, with the kept slice chosen
  type-balanced and degree-anchored (not lexical-first) so it stays
  representative.

``render`` is a pure function of ``(nodes, edges)`` plus an id-sanitizer, so
it has no dependency on the rest of the runtime and is deterministic: the
same graph and arguments always produce byte-identical output.
"""

from __future__ import annotations

import hashlib
from typing import Callable, Iterable

#: Default cap on the number of nodes rendered into a single diagram.
#: Beyond a few hundred nodes Mermaid stops rendering in common Markdown
#: viewers (GitHub, etc.), so the serializer truncates *and annotates*
#: rather than emit an unreadable (or unrenderable) blob. ``None`` disables
#: the cap.
DEFAULT_MAX_NODES = 200

#: Sentinel id for the visible truncation-note node. Real node ids always
#: carry a ``type:`` prefix (sanitized to ``type_...``), so an underscore-led
#: id cannot collide with a real node.
_TRUNCATION_NODE_ID = "weld_truncation_note"

#: Namespace for weld's *reserved* (meta) classDef styles -- the truncation
#: note and the external-endpoint placeholder. ``_classdef_name`` always emits
#: the literal 5-char prefix ``weld_``, so any class name whose 5th character
#: is not ``_`` -- e.g. ``weldmeta_note`` -- is one a per-type class can never
#: produce. That keeps these meta styles collision-proof against a future node
#: type literally named ``note`` or ``external``.
_RESERVED_PREFIX = "weldmeta"
_NOTE_CLASS = f"{_RESERVED_PREFIX}_note"

#: Per-type fill / stroke / text styles. Types absent here fall back to
#: ``_DEFAULT_STYLE`` so every node still gets a visible, type-consistent
#: colour. Colours are chosen for legibility on a light canvas with dark
#: text; strokes stay saturated so they read on dark canvases too.
_TYPE_STYLES: dict[str, str] = {
    "file": "fill:#dbeafe,stroke:#2563eb,color:#1e3a8a",
    "symbol": "fill:#ede9fe,stroke:#7c3aed,color:#4c1d95",
    "package": "fill:#ccfbf1,stroke:#0d9488,color:#134e4a",
    "route": "fill:#dcfce7,stroke:#16a34a,color:#14532d",
    "rpc": "fill:#ffedd5,stroke:#ea580c,color:#7c2d12",
    "channel": "fill:#fce7f3,stroke:#db2777,color:#831843",
    "doc": "fill:#fef9c3,stroke:#ca8a04,color:#713f12",
    "config": "fill:#e2e8f0,stroke:#475569,color:#1e293b",
    "agent": "fill:#e0e7ff,stroke:#4f46e5,color:#312e81",
    "command": "fill:#cffafe,stroke:#0891b2,color:#164e63",
    "concept": "fill:#ffe4e6,stroke:#e11d48,color:#881337",
    "workflow": "fill:#ecfccb,stroke:#65a30d,color:#365314",
    "entity": "fill:#f3e8ff,stroke:#9333ea,color:#581c87",
}
_DEFAULT_STYLE = "fill:#f1f5f9,stroke:#94a3b8,color:#334155"
_NOTE_STYLE = "fill:#fee2e2,stroke:#dc2626,color:#7f1d1d"

#: Style for placeholder nodes standing in for edge endpoints that live
#: outside the exported node set (e.g. cross-repo edges into a child graph).
#: Dashed to read as "outside this view" rather than a first-class node.
_EXTERNAL_CLASS = f"{_RESERVED_PREFIX}_external"
_EXTERNAL_STYLE = (
    "fill:#f8fafc,stroke:#94a3b8,color:#475569,stroke-dasharray:4 3"
)


def _classdef_name(node_type: str) -> str:
    """Identifier-safe per-type ``classDef`` name: always ``weld_<type>``.

    The literal ``weld_`` prefix is load-bearing: reserved meta styles use
    ``_RESERVED_PREFIX`` (whose 5th character is not ``_``), so no node type can
    ever sanitize to a reserved style name.
    """
    safe = "".join(c if c.isalnum() else "_" for c in node_type)
    return f"weld_{safe or 'untyped'}"


def _disambiguate(items: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Map each ``identity`` to a unique diagram id, changing as little as possible.

    ``items`` is ``(identity, base)`` pairs where *base* is the char-sanitized
    id. Identities whose base is unique keep that bare base -- byte-identical to
    the pre-collision serializer. When two distinct identities sanitize to the
    same base (e.g. ``a:b`` and ``a-b`` both -> ``a_b``), each gets a short,
    deterministic ``_<sha1[:8]>`` suffix instead of silently merging. The
    width-extending loop guarantees the returned map is injective even in the
    pathological case where a suffix would clash with another id; uniques are
    assigned first so a collision's suffixed forms can never displace them.
    """
    bases: dict[str, str] = dict(items)  # dedupe by identity (base is a function of it)
    counts: dict[str, int] = {}
    for base in bases.values():
        counts[base] = counts.get(base, 0) + 1
    result: dict[str, str] = {}
    used: set[str] = set()
    for identity in sorted(bases):  # uniques first: they keep their bare base
        base = bases[identity]
        if counts[base] == 1:
            result[identity] = base
            used.add(base)
    for identity in sorted(bases):  # collisions: minimal deterministic suffix
        base = bases[identity]
        if counts[base] == 1:
            continue
        digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()
        width = 8
        candidate = f"{base}_{digest[:width]}"
        while candidate in used:
            width += 1
            candidate = (
                f"{base}_{digest[:width]}"
                if width <= len(digest)
                else f"{base}_{digest}_{len(used)}"
            )
        result[identity] = candidate
        used.add(candidate)
    return result


def _escape_label(text: str) -> str:
    """Escape a string for use inside a Mermaid ``["..."]`` label.

    Uses Mermaid entity codes (``#NN;`` / ``#name;``) so quotes and angle
    brackets survive rather than terminating the label or being parsed as
    HTML. ``#`` is escaped first so we never re-process our own codes. The
    weld cross-repo namespace separator (unit separator, ``\\x1f``) is shown
    as ``/`` so federated endpoint labels read as ``repo/local-id`` rather
    than running together where viewers strip the raw control byte.
    """
    return (
        text.replace("\x1f", "/")
        .replace("#", "#35;")
        .replace('"', "#quot;")
        .replace("<", "#lt;")
        .replace(">", "#gt;")
    )


def _group_key(node_data: dict) -> tuple[str, str] | None:
    """Cluster key for a node: its file, else module, else package.

    Returns ``(kind, value)``, or ``None`` when the node has no natural
    container -- such nodes render at the top level, outside any subgraph.
    """
    props = node_data.get("props") or {}
    for kind in ("file", "module", "package"):
        value = props.get(kind)
        if value:
            return (kind, str(value))
    return None


def _subgraph_base(key: tuple[str, str], safe_id: Callable[[str], str]) -> str:
    """Pre-disambiguation subgraph id for a group key: ``grp_<kind>_<value>``.

    Two group values that char-sanitize alike (``a/b`` vs ``a.b``) collapse to
    the same base here; ``render`` disambiguates the bases so distinct group
    values never merge into a single subgraph.
    """
    kind, value = key
    return f"grp_{kind}_{safe_id(value)}"


def _group_identity(key: tuple[str, str]) -> str:
    """Stable, unique identity string for a group key. ``kind`` is a fixed
    vocabulary (file/module/package) with no unit-separator byte, so joining on
    ``\\x1f`` never aliases two distinct keys."""
    return f"{key[0]}\x1f{key[1]}"


def _display_label(node_id: str, node_data: dict) -> str:
    """Human-readable, escaped label: ``Real Label (type)``."""
    label = node_data.get("label") or node_id
    ntype = node_data.get("type", "")
    text = f"{label} ({ntype})" if ntype else str(label)
    return _escape_label(text)


def _node_line(nid: str, node_data: dict, safe_id: Callable[[str], str]) -> str:
    return f'{safe_id(nid)}["{_display_label(nid, node_data)}"]'


def _balanced_selection(
    nodes: dict[str, dict], edges: list[dict], cap: int
) -> set[str]:
    """Pick ``cap`` representative node ids: type-balanced, degree-anchored.

    The lexical-first ``sorted(nodes)[:cap]`` slice is deterministic but can be
    type-homogeneous -- e.g. a depth-2 expansion from a file node capped to 200
    all-``file`` nodes -- which reads poorly. Instead we bucket candidates by
    node ``type`` and round-robin across types (visited in sorted order) so every
    present type is represented before any type takes a second slot. Within a
    type, nodes are ordered by descending incident-edge degree (high-connectivity
    anchors first), with the node id as a stable total tie-break.

    Pure function of ``(nodes, edges, cap)`` with a total deterministic order:
    ``render`` re-sorts the returned set for emission, so the same graph and
    arguments always yield byte-identical output. Only called when
    ``len(nodes) > cap``.
    """
    # Incident-edge degree over the candidate set. Endpoints outside ``nodes``
    # (e.g. cross-repo edges) still raise the internal endpoint's degree.
    degree: dict[str, int] = {nid: 0 for nid in nodes}
    for edge in edges:
        for endpoint in (edge.get("from"), edge.get("to")):
            if endpoint in degree:
                degree[endpoint] += 1
    # Bucket by type; each bucket ordered by (-degree, id) so the highest-degree
    # anchor comes first and equal-degree nodes fall back to a unique id order.
    buckets: dict[str, list[str]] = {}
    for nid, data in nodes.items():
        buckets.setdefault(data.get("type", ""), []).append(nid)
    for bucket in buckets.values():
        bucket.sort(key=lambda nid: (-degree[nid], nid))
    # Round-robin across types (sorted) until the cap is hit.
    ordered_types = sorted(buckets)
    selected: set[str] = set()
    depth = 0
    while len(selected) < cap:
        advanced = False
        for ntype in ordered_types:
            bucket = buckets[ntype]
            if depth < len(bucket):
                selected.add(bucket[depth])
                advanced = True
                if len(selected) >= cap:
                    return selected
        if not advanced:  # every bucket exhausted -- only if cap > total nodes
            break
        depth += 1
    return selected


def render(
    nodes: dict[str, dict],
    edges: list[dict],
    *,
    safe_id: Callable[[str], str],
    max_nodes: int | None = DEFAULT_MAX_NODES,
) -> str:
    """Serialize ``(nodes, edges)`` to a clustered, styled Mermaid flowchart.

    Deterministic: every collection is emitted in sorted order, so the same
    ``(nodes, edges, max_nodes)`` yields byte-identical output. When the node
    count exceeds ``max_nodes`` the kept slice is chosen by
    :func:`_balanced_selection` (type-balanced, degree-anchored) rather than a
    lexical-first cut, so a capped diagram stays representative without giving up
    determinism.
    """
    total = len(nodes)
    truncated = max_nodes is not None and total > max_nodes
    if truncated:
        assert max_nodes is not None  # implied by truncated; narrows for type-checkers
        # Representative slice instead of the lexical-first N: a pure, totally
        # ordered function of (nodes, edges, max_nodes), then emitted sorted so
        # output stays byte-identical across runs.
        ordered_ids = sorted(_balanced_selection(nodes, edges, max_nodes))
    else:
        ordered_ids = sorted(nodes)
    kept: set[str] = set(ordered_ids)
    present: set[str] = set(nodes)
    shown = len(ordered_ids)

    # Edges: keep those whose endpoints survive. An endpoint that was
    # *truncated away* (present but over the cap) drops its edge. An
    # endpoint that is genuinely external -- referenced but never a node in
    # this set, e.g. a cross-repo edge into a child graph -- keeps its edge
    # and is declared as a marked placeholder, so the relationship is never
    # silently lost.
    surviving: list[dict] = []
    external: set[str] = set()
    for edge in edges:
        src, dst = edge.get("from"), edge.get("to")
        if src is None or dst is None:
            continue
        if (src in present and src not in kept) or (dst in present and dst not in kept):
            continue
        surviving.append(edge)
        external.update(e for e in (src, dst) if e not in kept)

    # Collision-resistant node ids: distinct source ids that char-sanitize
    # alike get a short hash suffix instead of silently merging into one key
    # (see ``_disambiguate``). Non-colliding ids keep their bare sanitized form,
    # so output is byte-identical to the old serializer for every real graph.
    node_map = _disambiguate((nid, safe_id(nid)) for nid in set(ordered_ids) | external)
    nsid = node_map.__getitem__

    # Comments sit at column 0 on their own line: Mermaid documents that
    # form, and leading indentation before ``%%`` is not guaranteed to be
    # tolerated by the parser.
    lines: list[str] = ["flowchart LR"]
    lines.append(f"%% weld graph export: {shown} of {total} nodes shown")
    if truncated:
        lines.append(
            f"%% NOTE: diagram truncated to {max_nodes} nodes of "
            f"{total} -- see the truncation note node below"
        )

    # classDef per type present (sorted), plus external/note styles as needed.
    present_types = sorted({nodes[nid].get("type", "") for nid in ordered_ids})
    for ntype in present_types:
        style = _TYPE_STYLES.get(ntype, _DEFAULT_STYLE)
        lines.append(f"    classDef {_classdef_name(ntype)} {style};")
    if external:
        lines.append(f"    classDef {_EXTERNAL_CLASS} {_EXTERNAL_STYLE};")
    if truncated:
        lines.append(f"    classDef {_NOTE_CLASS} {_NOTE_STYLE};")

    # Partition kept nodes into groups (subgraphs) vs ungrouped (top level).
    groups: dict[tuple[str, str], list[str]] = {}
    ungrouped: list[str] = []
    for nid in ordered_ids:
        key = _group_key(nodes[nid])
        (ungrouped if key is None else groups.setdefault(key, [])).append(nid)

    # Collision-resistant subgraph ids: two distinct group values that
    # char-sanitize alike (``a/b`` vs ``a.b``) get distinct ids rather than
    # collapsing into one subgraph. Group values are disambiguated in their own
    # namespace, separate from node ids, so neither forces a suffix on the other.
    group_map = _disambiguate(
        (_group_identity(key), _subgraph_base(key, safe_id)) for key in groups
    )

    # Subgraph blocks, sorted by group key for determinism.
    for key in sorted(groups):
        lines.append(f'    subgraph {group_map[_group_identity(key)]}["{_escape_label(key[1])}"]')
        for nid in groups[key]:
            lines.append(f"        {_node_line(nid, nodes[nid], nsid)}")
        lines.append("    end")

    # Ungrouped nodes, then external endpoint placeholders (top level).
    for nid in ungrouped:
        lines.append(f"    {_node_line(nid, nodes[nid], nsid)}")
    for eid in sorted(external):
        lines.append(f'    {nsid(eid)}["{_escape_label(eid)} (external)"]')

    # Visible truncation note node (no edges), if we dropped anything.
    if truncated:
        note = _escape_label(f"... truncated: {shown} of {total} nodes shown ...")
        lines.append(f'    {_TRUNCATION_NODE_ID}["{note}"]')

    # Surviving edges, sorted for determinism.
    edge_lines: list[str] = []
    for edge in surviving:
        s, d = nsid(edge["from"]), nsid(edge["to"])
        etype = edge.get("type", "")
        edge_lines.append(f"    {s} -->|{etype}| {d}" if etype else f"    {s} --> {d}")
    lines.extend(sorted(edge_lines))

    # Class assignments grouped by type (sorted), ids sorted -- keeps the
    # styling section deterministic and compact.
    by_type: dict[str, list[str]] = {}
    for nid in ordered_ids:
        by_type.setdefault(nodes[nid].get("type", ""), []).append(nsid(nid))
    for ntype in present_types:
        ids = ",".join(sorted(by_type.get(ntype, [])))
        if ids:
            lines.append(f"    class {ids} {_classdef_name(ntype)};")
    if external:
        ids = ",".join(sorted(nsid(e) for e in external))
        lines.append(f"    class {ids} {_EXTERNAL_CLASS};")
    if truncated:
        lines.append(f"    class {_TRUNCATION_NODE_ID} {_NOTE_CLASS};")

    return "\n".join(lines) + "\n"
