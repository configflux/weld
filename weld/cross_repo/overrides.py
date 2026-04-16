"""Manual override file for cross-repo edges.

Users may place a ``.weld/cross_repo_overrides.yaml`` file in the workspace
root to declare explicit cross-repo edges that resolvers miss or to suppress
false-positive edges that resolvers emit.

The override file is loaded **after** resolvers run and patches the aggregate
edge list before it is committed to the root ``graph.json``.

File schema (YAML)::

    overrides:
      - from: "child-a\x1fnode-id-1"
        to: "child-b\x1fnode-id-2"
        type: invokes
        action: add
        props:
          source_strategy: manual
      - from: "child-a\x1fnode-id-3"
        to: "child-b\x1fnode-id-4"
        type: cross_repo:calls
        action: remove

Each entry must contain ``from``, ``to``, ``type``, and ``action``.
``action`` is either ``add`` (inject an edge) or ``remove`` (suppress a
resolver-emitted edge). ``props`` is optional and defaults to an empty
mapping.

Override-added edges carry ``{"source": "manual_override"}`` in their
props (merged with any user-supplied props) so downstream consumers can
distinguish them from resolver-emitted edges.

Entries that reference a child name absent from the workspace produce a
warning on stderr and are silently skipped -- the override file must not
crash discovery. The override file itself is optional; when absent,
the loader returns an empty list and the merge is a no-op.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from weld._yaml import parse_yaml
from weld.cross_repo.base import CrossRepoEdge

__all__ = [
    "Override",
    "OverrideParseError",
    "apply_overrides",
    "load_overrides",
]

OVERRIDE_FILENAME = "cross_repo_overrides.yaml"

# Valid action values -- kept as a frozenset so membership checks are O(1).
_VALID_ACTIONS: frozenset[str] = frozenset({"add", "remove"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OverrideParseError(ValueError):
    """The override file could not be parsed or contains invalid entries.

    Raised by :func:`load_overrides` when the YAML structure does not
    match the expected schema. The message identifies the specific
    problem so users can fix their override file without reading the
    framework source.
    """


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Override:
    """A single manual override entry.

    ``from_id`` and ``to_id`` use the same federated-ID format as
    :class:`~weld.cross_repo.base.CrossRepoEdge`:
    ``<child-name>\\x1f<node-id>``.

    ``action`` is ``"add"`` to inject a new edge or ``"remove"`` to
    suppress a resolver-emitted edge.
    """

    from_id: str
    to_id: str
    type: str
    action: str
    props: Mapping[str, Any] = field(default_factory=dict)

    def to_edge(self) -> CrossRepoEdge:
        """Convert an ``add`` override into a :class:`CrossRepoEdge`.

        The returned edge merges user-supplied props with a ``source``
        marker so downstream consumers can identify manual overrides.
        """
        merged = dict(self.props)
        merged.setdefault("source", "manual_override")
        return CrossRepoEdge(
            from_id=self.from_id,
            to_id=self.to_id,
            type=self.type,
            props=merged,
        )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _parse_entry(raw: Mapping[str, Any], index: int) -> Override:
    """Validate and construct an :class:`Override` from a raw YAML mapping.

    Raises :class:`OverrideParseError` on any schema violation.
    """
    required = ("from", "to", "type", "action")
    missing = [k for k in required if k not in raw]
    if missing:
        raise OverrideParseError(
            f"override entry {index}: missing required keys: {missing}",
        )

    action = str(raw["action"]).strip().lower()
    if action not in _VALID_ACTIONS:
        raise OverrideParseError(
            f"override entry {index}: action must be 'add' or 'remove', "
            f"got {raw['action']!r}",
        )

    props = raw.get("props", {})
    if props is None:
        props = {}
    if not isinstance(props, Mapping):
        raise OverrideParseError(
            f"override entry {index}: 'props' must be a mapping, "
            f"got {type(props).__name__}",
        )

    return Override(
        from_id=str(raw["from"]),
        to_id=str(raw["to"]),
        type=str(raw["type"]),
        action=action,
        props=dict(props),
    )


def load_overrides(workspace_root: str | Path) -> list[Override]:
    """Load the override file from ``.weld/cross_repo_overrides.yaml``.

    Returns an empty list when the file does not exist. Raises
    :class:`OverrideParseError` when the file exists but cannot be
    parsed or contains invalid entries.
    """
    path = Path(workspace_root) / ".weld" / OVERRIDE_FILENAME
    if not path.is_file():
        return []

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OverrideParseError(
            f"could not read override file {path}: {exc}",
        ) from exc

    if not text.strip():
        return []

    try:
        data = parse_yaml(text)
    except Exception as exc:
        raise OverrideParseError(
            f"could not parse override file {path}: {exc}",
        ) from exc

    if not isinstance(data, dict):
        raise OverrideParseError(
            f"override file top-level must be a mapping, "
            f"got {type(data).__name__}",
        )

    entries = data.get("overrides", [])
    if not isinstance(entries, list):
        raise OverrideParseError(
            f"'overrides' must be a sequence, got {type(entries).__name__}",
        )

    result: list[Override] = []
    for i, raw_entry in enumerate(entries):
        if not isinstance(raw_entry, Mapping):
            raise OverrideParseError(
                f"override entry {i}: expected a mapping, "
                f"got {type(raw_entry).__name__}",
            )
        result.append(_parse_entry(raw_entry, i))
    return result


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def _extract_child_name(federated_id: str) -> str | None:
    """Return the child name prefix from a federated ID, or None."""
    sep = "\x1f"
    if sep not in federated_id:
        return None
    return federated_id.split(sep, 1)[0]


def _edge_matches(
    edge: CrossRepoEdge,
    override: Override,
) -> bool:
    """Return True when ``edge`` matches the override's identity triple."""
    return (
        edge.from_id == override.from_id
        and edge.to_id == override.to_id
        and edge.type == override.type
    )


def apply_overrides(
    edges: list[CrossRepoEdge],
    overrides: list[Override],
    *,
    known_children: frozenset[str] | set[str] | None = None,
) -> list[CrossRepoEdge]:
    """Patch ``edges`` according to ``overrides``.

    ``known_children`` is the set of child names present in the
    workspace. Override entries that reference an unknown child name
    produce a warning on stderr and are skipped.

    Returns a new list; the input ``edges`` list is not modified.
    """
    if not overrides:
        return list(edges)

    # Partition overrides into add and remove lists, filtering out
    # entries that reference unknown children.
    adds: list[Override] = []
    removes: list[Override] = []

    for entry in overrides:
        # Validate child references when known_children is provided.
        if known_children is not None:
            from_child = _extract_child_name(entry.from_id)
            to_child = _extract_child_name(entry.to_id)
            skip = False
            for child_name in (from_child, to_child):
                if child_name is not None and child_name not in known_children:
                    print(
                        f"[weld] warning: override references unknown "
                        f"child {child_name!r}; skipping",
                        file=sys.stderr,
                    )
                    skip = True
                    break
            if skip:
                continue

        if entry.action == "add":
            adds.append(entry)
        elif entry.action == "remove":
            removes.append(entry)

    # Apply removals: filter out edges that match any remove override.
    result: list[CrossRepoEdge] = []
    for edge in edges:
        if any(_edge_matches(edge, r) for r in removes):
            continue
        result.append(edge)

    # Apply additions: append edges from add overrides.
    for entry in adds:
        result.append(entry.to_edge())

    return result
