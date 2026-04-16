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
    """
    data = json.loads(path.read_text(encoding="utf-8"))
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
    return data
