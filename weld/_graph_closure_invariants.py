"""Structural invariants for the discovered graph (ADR 0041, Layer 3).

This module hosts three lint rules that catch the bug class ADR 0041
calls out -- duplicate canonical IDs (the skill-suffix duplication
symptom), file anchors with outgoing children but no inbound edge (the
``_ros2_py`` strategy-pair drift symptom), and strategy pairs that visit
different file sets (the underlying cause of both symptoms).

The rules sit alongside ``weld.arch_lint_orphan`` rather than extending
it. The orphan rule is deliberately narrow: it flags zero-edge nodes
only and many existing consumers rely on that semantics. The three new
rules are siblings, not replacements.

Each rule is a pure function over the graph data dict (the shape
returned by ``weld.graph.Graph.dump``) so the rules can be unit-tested
without a full ``Graph`` object. They register with the existing
``wd lint`` runner via ``weld.arch_lint``.

See ``docs/adrs/0041-graph-closure-determinism.md`` for the contract,
the migration-alias scheme, and the per-rule rationale.
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Iterable, Iterator, Mapping, Sequence

from weld._graph_strategy_pair import check_strategy_pair_consistency
from weld._node_ids import canonical_slug

if TYPE_CHECKING:
    from weld.arch_lint import Violation


#: Built-in entrypoint basename allow-list for ``file-anchor-symmetry``.
#: Files whose basename matches one of these patterns are exempt from the
#: inbound-edge requirement -- they are by-design entry points. Repo-specific
#: extensions live in ``.weld/discover.yaml`` under
#: ``file_anchor_symmetry_allowlist``.
_ENTRYPOINT_BASENAME_PATTERNS: tuple[str, ...] = (
    "__main__.py",
    "cli.py",
    "*_cli.py",
)

#: Edge types that count as "this file anchors a typed child" for the
#: file-anchor-symmetry rule. The orphan rule already covers nodes with
#: zero edges; this rule is specifically for nodes that emit ``contains``
#: edges to symbols/classes/functions but receive nothing back.
_CHILD_EDGE_TYPES: frozenset[str] = frozenset({"contains"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_violation(
    rule: str, node_id: str, message: str, severity: str = "error"
) -> "Violation":
    """Build a :class:`weld.arch_lint.Violation` (late import breaks cycle)."""
    from weld.arch_lint import Violation  # noqa: WPS433 (local import OK)

    return Violation(
        rule=rule,
        node_id=node_id,
        message=message,
        severity=severity,
    )


def _id_segments(node_id: str) -> tuple[str, str | None, str]:
    """Split *node_id* into ``(type, platform, name)`` components.

    Three-segment IDs (``skill:generic:architecture-decision``) yield a
    real platform; two-segment IDs (``file:weld/foo``) return
    ``platform=None`` and put the entire remainder in ``name``. IDs with
    more than three colon-separated segments (``symbol:py:pkg.mod:sym``)
    treat segment 2 as the platform and join everything after it as the
    name so IDs with embedded colons round-trip into a stable canonical
    base.
    """
    parts = node_id.split(":", 2)
    if len(parts) == 1:
        return parts[0], None, ""
    if len(parts) == 2:
        return parts[0], None, parts[1]
    type_, platform, rest = parts
    return type_, platform, rest


def _canonical_base(node_id: str, node: Mapping) -> str:
    """Return the canonical slug used to compare two nodes for collision.

    The base composes the node's effective ``(type, platform, name)``
    via :func:`weld._node_ids.canonical_slug` so two IDs that differ
    only in slug-irrelevant punctuation (the ``architecture--decision``
    vs ``architecture-decision`` case) collapse to the same key.
    """
    node_type = str(node.get("type") or _id_segments(node_id)[0])
    _, platform, name = _id_segments(node_id)
    if platform is None:
        composed = f"{node_type}:{name}"
    else:
        composed = f"{node_type}:{platform}:{name}"
    return canonical_slug(composed)


# ---------------------------------------------------------------------------
# Rule 1: canonical-id-uniqueness
# ---------------------------------------------------------------------------


def check_canonical_id_uniqueness(
    nodes: Mapping[str, Mapping],
) -> Iterator["Violation"]:
    """Flag pairs of nodes that share a canonical base but no alias link.

    Two nodes share a canonical base when
    :func:`weld._node_ids.canonical_slug` returns the same value for
    each node's effective ``(type, platform, name)`` tuple. When two
    such nodes exist and neither lists the other in ``props.aliases``,
    a duplicate is being silently maintained -- the bug class ADR 0041
    tracks. When at least one side lists the other as an alias, the
    nodes are logically merged (the alias index resolves either ID to
    the live node) and the rule passes.

    Yields :class:`weld.arch_lint.Violation` objects in deterministic
    order (sorted by lowest-ID-in-group, then by group contents) so
    repeated runs produce byte-identical output.
    """
    if not nodes:
        return

    groups: dict[str, list[str]] = {}
    for node_id, node in nodes.items():
        if not isinstance(node, Mapping):
            continue
        base = _canonical_base(node_id, node)
        groups.setdefault(base, []).append(node_id)

    for base in sorted(groups):
        members = sorted(groups[base])
        if len(members) < 2:
            continue
        # If any pair within the group is alias-linked, the whole group
        # is considered logically merged -- aliases are transitive
        # enough for the purpose of this rule (Layer 1 + Layer 2 take
        # care of merge mechanics; Layer 3 only needs to see that an
        # alias relationship exists).
        if _any_pair_aliased(members, nodes):
            continue
        # Emit one violation per member so editors can navigate to
        # both halves; identify the partner set in the message.
        for node_id in members:
            partners = [m for m in members if m != node_id]
            yield _make_violation(
                rule="canonical-id-uniqueness",
                node_id=node_id,
                message=(
                    f"node {node_id!r} shares canonical base {base!r} "
                    f"with {partners}; merge via aliases or rename"
                ),
            )


def _any_pair_aliased(
    members: Iterable[str], nodes: Mapping[str, Mapping]
) -> bool:
    """Return True when any two ``members`` are linked via ``props.aliases``."""
    member_set = set(members)
    for nid in member_set:
        node = nodes.get(nid) or {}
        props = node.get("props") or {}
        aliases = props.get("aliases") or []
        if not isinstance(aliases, list):
            continue
        for alias in aliases:
            if alias in member_set and alias != nid:
                return True
    return False


# ---------------------------------------------------------------------------
# Rule 2: file-anchor-symmetry
# ---------------------------------------------------------------------------


def _basename(path: str) -> str:
    """Return the basename of *path* (POSIX-aware, never raises)."""
    if not path:
        return ""
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _is_entrypoint_basename(path: str) -> bool:
    """Return True when *path*'s basename matches the built-in entrypoint
    allow-list (``__main__.py``, ``cli.py``, ``*_cli.py``)."""
    base = _basename(path)
    if not base:
        return False
    return any(
        fnmatch.fnmatchcase(base, pat) for pat in _ENTRYPOINT_BASENAME_PATTERNS
    )


def _matches_allowlist(
    path: str, allowlist: Sequence[Mapping] | None
) -> bool:
    """Return True when *path* matches any ``path``-style entry in *allowlist*."""
    if not allowlist:
        return False
    for entry in allowlist:
        if not isinstance(entry, Mapping):
            continue
        pattern = entry.get("path")
        if not pattern:
            continue
        if fnmatch.fnmatchcase(path, str(pattern)):
            return True
        if path == str(pattern):
            return True
    return False


def check_file_anchor_symmetry(
    data: Mapping,
    *,
    allowlist: Sequence[Mapping] | None = None,
) -> Iterator["Violation"]:
    """Flag ``file:`` nodes with outgoing children but no inbound edge.

    A ``file:*`` node that emits a ``contains`` edge to a typed child
    (``symbol``, ``class``, ``function``, ...) but has no inbound edge is
    structurally orphaned -- the file's parent strategy never imported
    from it. This is the exact ``_ros2_py`` shape ADR 0041 calls out.

    Exemptions (built-in):

    - ``props.roles`` includes the literal ``"entrypoint"``.
    - The file's basename matches the built-in entrypoint allow-list
      (``__main__.py``, ``cli.py``, ``*_cli.py``).

    Exemptions (repo-specific): callers may pass an *allowlist* of
    ``{"path": <glob>, "reason": <str>}`` entries (loaded from
    ``.weld/discover.yaml`` by the runner) to whitelist intentional
    asymmetries with a justifying comment.

    Yields :class:`weld.arch_lint.Violation` objects in deterministic
    sorted-by-node-id order so repeated runs produce byte-identical
    output.
    """
    nodes = data.get("nodes", {}) or {}
    edges = data.get("edges", []) or []

    inbound: set[str] = set()
    outgoing_contains: set[str] = set()
    for edge in edges:
        if not isinstance(edge, Mapping):
            continue
        frm = edge.get("from")
        to = edge.get("to")
        etype = edge.get("type")
        if isinstance(to, str):
            inbound.add(to)
        if (
            isinstance(frm, str)
            and isinstance(etype, str)
            and etype in _CHILD_EDGE_TYPES
            and frm.startswith("file:")
        ):
            outgoing_contains.add(frm)

    for node_id in sorted(outgoing_contains):
        if node_id in inbound:
            continue
        node = nodes.get(node_id) or {}
        props = node.get("props") or {}
        roles = props.get("roles") or []
        if isinstance(roles, list) and "entrypoint" in roles:
            continue
        file_path = str(props.get("file") or "")
        if _is_entrypoint_basename(file_path):
            continue
        if _matches_allowlist(file_path, allowlist):
            continue
        yield _make_violation(
            rule="file-anchor-symmetry",
            node_id=node_id,
            message=(
                f"file anchor {node_id!r} has outgoing 'contains' edges "
                f"but no inbound edge; either add the missing strategy "
                f"or list the path in file_anchor_symmetry_allowlist "
                f"with a reason"
            ),
        )


# ---------------------------------------------------------------------------
# Rule 3: strategy-pair-consistency
# ---------------------------------------------------------------------------
#
# Implementation lives in ``weld._graph_strategy_pair`` to keep this
# module under the 400-line cap. The function is re-exported here so
# callers can import all three checks from one place.


__all__ = [
    "check_canonical_id_uniqueness",
    "check_file_anchor_symmetry",
    "check_strategy_pair_consistency",
]
