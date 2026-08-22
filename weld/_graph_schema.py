"""Schema versioning for the connected structure graph format.

Handles federation schema version detection and gating for graph.json files.
See ADR 0011 and ADR 0012 for the federation versioning design.
"""
from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Federation schema versioning (ADR 0011 ss11, ADR 0012 ss4)
#
# ``meta.schema_version`` is orthogonal to the contract's ``meta.version``
# field (``weld.contract.SCHEMA_VERSION``, currently ``4``). The contract
# version describes the vocabulary of valid node/edge types; the schema
# version describes the *layout* of ``graph.json`` and whether it carries
# federation constructs.
#
# * ``CHILD_SCHEMA_VERSION = 1``  -- single-repo graph or a child graph
#   under federation. No ``repo:*`` nodes, no ``\x1f``-prefixed IDs.
# * ``ROOT_FEDERATED_SCHEMA_VERSION = 2`` -- root meta-graph under
#   federation. Contains one ``repo:*`` node per registered child and
#   (eventually) cross-repo edges using prefixed IDs.
#
# ``Graph.save`` stamps ``meta.schema_version`` based on the presence of
# any ``repo:*`` node in the in-memory graph. Readers that advertise an
# older maximum (via :func:`load_graph_file`) refuse newer graphs with a
# human-readable error that names both the literal ``schema_version`` and
# the word ``upgrade`` so an old ``wd`` install fails loudly rather than
# silently misinterpreting federation constructs.
# ---------------------------------------------------------------------------

CHILD_SCHEMA_VERSION: int = 1
ROOT_FEDERATED_SCHEMA_VERSION: int = 2


class SchemaVersionError(Exception):
    """Raised when an old reader encounters a newer ``meta.schema_version``.

    ADR 0012 ss4 mandates a human-readable error message naming both the
    literal ``schema_version`` and the word ``upgrade`` so operators can
    read the mismatch off a log line. The message also quotes the
    observed version so it can be diagnosed without re-running.
    """


class GraphShapeError(ValueError):
    """Raised when a graph payload is not a JSON object, or lacks the
    minimal ``nodes``/``edges`` shape.

    A ``ValueError`` subclass on purpose: every existing
    ``except (..., ValueError)`` tuple written for a graph-load failure
    (:func:`weld.federation_child_loader.load_child_from_json`,
    :func:`weld.federation_child_probe.probe_child_status`) already catches
    it with zero changes to those tuples, while
    :func:`weld._errors.classify_graph_load_error` can still recognize it
    precisely via ``isinstance`` rather than blanket-catching every
    ``ValueError`` a load might raise -- which would risk mislabeling an
    unrelated bug as a corrupt graph.
    """


def validate_dict_payload(data: object) -> None:
    """Raise :class:`GraphShapeError` unless *data* is a JSON object (dict).

    ``json.loads`` happily parses a top-level JSON array or scalar
    (``'[]'``, ``'42'``, ``'"oops"'``) -- the result is a ``list``/``int``/
    ``str`` with no ``.get`` method, so the very next line in both
    :func:`load_graph_file` and :func:`weld.federation_support.load_graph_bytes`
    (``meta = data.get("meta") or {}``) would raise an uncaught
    ``AttributeError`` instead of the classifiable :class:`GraphShapeError`
    every other malformed-``graph.json`` case produces. Both call sites run
    this check as the *first* statement after parsing, before touching
    ``meta`` or ``schema_version`` -- unlike :func:`validate_graph_shape`
    (which stays after the ``schema_version`` gate so an old reader facing
    a newer, legitimately different-shaped schema still gets a
    :class:`SchemaVersionError` "upgrade weld" message rather than a
    misleading "corrupt, run wd discover" one), dict-ness is a hard
    prerequisite for that very next ``.get()`` call, not a style choice.

    Single-sourcing this guard (bd 5038-w0r4) replaces
    ``load_graph_bytes``'s previous bespoke, differently-worded
    ``ValueError`` check -- the exact kind of drift that let
    :func:`load_graph_file` go without any check at all.

    The message names only the observed Python type -- never file content
    or a filesystem path -- so it is safe to surface verbatim on the
    CLI/MCP error contract (the same no-leak bar :class:`SchemaVersionError`
    messages hold to).
    """
    if not isinstance(data, dict):
        raise GraphShapeError(
            f"graph payload must be a JSON object, got {type(data).__name__}"
        )


def validate_graph_shape(data: dict) -> None:
    """Raise :class:`GraphShapeError` unless *data* has a graph's minimal shape.

    A syntactically valid JSON object -- passes ``isinstance(data, dict)``,
    even a valid ``meta.schema_version`` -- can still be missing ``nodes``
    or ``edges``, or hold the wrong type for either (a hand-edited or
    partially-corrupted ``graph.json``). Both :func:`load_graph_file` (the
    single-repo / ``Graph.load`` surface) and
    :func:`weld.federation_support.load_graph_bytes` (the federated child
    surface) call this before returning their parsed payload, because
    ``Graph._build_inverted_index`` / ``weld.query_state.build_query_state``
    index straight into ``data["nodes"]`` and ``data["edges"]`` with no
    further check of their own -- a missing or malformed key would
    otherwise raise an uncaught ``KeyError``/``TypeError`` deep inside
    index construction instead of being classified as a corrupt graph at
    the load boundary.

    The message names only the fixed key ("nodes"/"edges") and the
    observed Python type -- never file content or a filesystem path -- so
    it is safe to surface verbatim on the CLI/MCP error contract (the same
    no-leak bar :class:`SchemaVersionError` messages hold to).
    """
    nodes = data.get("nodes")
    if not isinstance(nodes, dict):
        raise GraphShapeError(
            f"graph payload 'nodes' must be an object, got {type(nodes).__name__}"
        )
    edges = data.get("edges")
    if not isinstance(edges, list):
        raise GraphShapeError(
            f"graph payload 'edges' must be an array, got {type(edges).__name__}"
        )


def has_repo_nodes(nodes: dict[str, dict]) -> bool:
    """Return ``True`` when any node is a federation ``repo:*`` entry.

    The federation ADR (0011 ss4) reserves the ``repo`` node type for the
    root meta-graph. Presence is the trigger for ``schema_version = 2``
    (ADR 0012 ss4). The check tolerates a malformed ``nodes`` dict by
    treating missing/non-string types as non-repo.
    """
    for node in nodes.values():
        if isinstance(node, dict) and node.get("type") == "repo":
            return True
    return False


def schema_version_for(nodes: dict[str, dict]) -> int:
    """Choose the schema version to stamp based on content.

    The decision is content-driven, not path-driven: a root graph that
    loses its last ``repo:*`` node downgrades back to ``1`` so a rolled-back
    workspace (``workspaces.yaml`` deleted) produces output byte-identical
    to legacy single-repo ``weld`` (ADR 0011 ss9 rollback, ss13 OSS-split).
    """
    if has_repo_nodes(nodes):
        return ROOT_FEDERATED_SCHEMA_VERSION
    return CHILD_SCHEMA_VERSION


def load_graph_file(
    path: Path,
    *,
    max_supported_schema_version: int = ROOT_FEDERATED_SCHEMA_VERSION,
) -> dict:
    """Load ``graph.json`` with an explicit schema-version gate.

    *max_supported_schema_version* mirrors the contract from ADR 0012 ss4.
    The default accepts every version this build understands. Callers
    that want to simulate an older reader (for testing, for the
    ``weld`` install running on a legacy ref) pass ``1`` and receive a
    :class:`SchemaVersionError` on newer artifacts.

    A missing ``meta.schema_version`` is treated as ``1`` for backward
    compatibility with pre-federation ``graph.json`` files.

    Also validates that the top level is a JSON object (see
    :func:`validate_dict_payload`) and the minimal ``nodes``/``edges``
    shape (see :func:`validate_graph_shape`), so a syntactically valid but
    structurally wrong file -- a bare list/scalar top level, or e.g.
    ``{"meta": {...}}`` alone -- raises a classifiable
    :class:`GraphShapeError` here rather than an uncaught
    ``AttributeError``/``KeyError`` later.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_dict_payload(data)
    meta = data.get("meta") or {}
    observed = meta.get("schema_version", CHILD_SCHEMA_VERSION)
    if not isinstance(observed, int):
        raise SchemaVersionError(
            f"graph.json at {path} has non-integer meta.schema_version "
            f"{observed!r}; upgrade weld to read this artifact."
        )
    if observed > max_supported_schema_version:
        raise SchemaVersionError(
            f"graph.json at {path} has schema_version {observed}; this "
            f"build of weld supports up to schema_version "
            f"{max_supported_schema_version}. Please upgrade weld to "
            f"read federated root graphs."
        )
    validate_graph_shape(data)
    return data
