"""Federation-aware validation helpers (bd-5038-6zm).

Centralises the bypass logic that ``validate_edge`` / ``validate_graph``
apply to graphs whose ``meta.schema_version == 2`` (federation root, ADR
0011 ss11 / ADR 0012 ss4). Splitting these into their own module keeps
:mod:`weld._contract_validators` under the 400-line cap and gives the
federation-scope checks a single audit point.

Two graph constructs are federation-only:

* IDs of the shape ``<child-name>\\x1f<node-id>``: edge endpoints in the
  root meta-graph that point into a sibling child graph. They are not
  resolvable in the root and therefore must be skipped by the
  dangling-reference check.

* Edge types of the shape ``cross_repo:<suffix>``: emitted exclusively
  by the root cross-repo resolver framework. The suffix set is open, so
  the contract whitelists the prefix instead of every concrete type.

Without gating, both bypasses applied to *any* graph that happened to
contain the literal ``\\x1f`` byte or ``cross_repo:`` prefix in user
data, however pathologically. Gating on ``schema_version == 2`` closes
that gap. Even under federation the bypass demands a *well-formed*
id/prefix -- malformed strings remain dangling/invalid so the
diagnostic still names the offending node.
"""
from __future__ import annotations

#: ASCII Unit Separator (ADR 0011 ss7) used inside federation IDs.
FEDERATION_ID_SEPARATOR: str = "\x1f"

#: Edge-type prefix reserved for cross-repo resolver output.
CROSS_REPO_EDGE_PREFIX: str = "cross_repo:"

#: ``meta.schema_version`` value that marks a federation root graph
#: (ADR 0011 ss11 / ADR 0012 ss4). Mirrored from
#: :mod:`weld._graph_schema` to keep the validation library
#: dependency-free; the canonical definition lives in ``_graph_schema``.
#: A static-analysis cross-check is in
#: ``weld/tests/weld_validate_federation_gate_test.py``.
ROOT_FEDERATED_SCHEMA_VERSION: int = 2


def is_well_formed_federation_id(value: object) -> bool:
    """Return True iff *value* is a well-formed ``<child>\\x1f<node-id>``.

    A well-formed federation id has exactly one ASCII Unit Separator
    with a non-empty child label before it and a non-empty node id
    after it. Pathological strings (``"\\x1f"``, ``"a\\x1f"``,
    ``"\\x1fb"``, ``"a\\x1fb\\x1fc"``, non-strings) fail this check
    so the caller can keep flagging them as dangling references.
    """
    if not isinstance(value, str):
        return False
    parts = value.split(FEDERATION_ID_SEPARATOR)
    return len(parts) == 2 and bool(parts[0]) and bool(parts[1])


def is_well_formed_cross_repo_edge_type(value: object) -> bool:
    """Return True iff *value* is ``cross_repo:`` followed by a non-empty suffix.

    Bare ``"cross_repo:"`` (and non-strings, and non-prefixed values)
    fail this check so the caller can keep emitting the standard
    invalid-edge-type diagnostic that names the offending edge.
    """
    if not isinstance(value, str):
        return False
    if not value.startswith(CROSS_REPO_EDGE_PREFIX):
        return False
    return len(value) > len(CROSS_REPO_EDGE_PREFIX)
