"""Federation-aware validation helpers (ADR 0137).

Centralises the federation-only judgements that ``validate_edge`` /
``validate_graph`` apply to graphs whose ``meta.schema_version == 2``
(federation root, ADR 0011 ss11 / ADR 0012 ss4). Splitting these into their
own module keeps :mod:`weld._contract_validators` under the 400-line cap and
gives the federation-scope checks a single audit point. The module stays
dependency-free on purpose -- it is imported by the contract library, which
must not reach the graph runtime.

Two graph constructs are federation-only:

* IDs of the shape ``<child-name>\\x1f<node-id>``: edge endpoints in the
  root meta-graph that point into a sibling child graph (ADR 0011 ss7).

* Edge types of the shape ``cross_repo:<suffix>``: emitted exclusively
  by the root cross-repo resolver framework. The suffix set is open, so
  the contract whitelists the prefix instead of every concrete type.

Without gating, both bypasses applied to *any* graph that happened to
contain the literal ``\\x1f`` byte or ``cross_repo:`` prefix in user
data, however pathologically. Gating on ``schema_version == 2`` closes
that gap.

**Shape is not existence (ADR 0137 ss3).** ``is_well_formed_federation_id``
answers "is this string well-formed", and that is all it ever answered
correctly. Used alone as a dangling-reference bypass it let a root graph
whose every cross-repo endpoint referenced nothing report
``{"valid": true, "errors": []}``. So a caller that *can* enumerate the ids
-- ``wd graph validate`` at a workspace root, ``wd doctor``, the discovery
merge -- builds a :class:`FederationIdIndex` and asks
:meth:`FederationIdIndex.classify_endpoint` instead. The shape check remains
the right answer where no index can exist: ``validate-fragment``, the doc
validators, any single-repo call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

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

#: The three verdicts :meth:`FederationIdIndex.classify_endpoint` returns.
ENDPOINT_OK: str = "ok"
ENDPOINT_DANGLING: str = "dangling"
ENDPOINT_UNVERIFIABLE: str = "unverifiable"

#: Reported for a registered child whose graph could not be read when the
#: builder did not say which of missing/uninitialized/corrupt it was.
UNKNOWN_CHILD_STATE: str = "unreadable"

#: Opening words of the two endpoint diagnostics. Shared because a reader of
#: the error list (``wd doctor``) has to tell the two apart, and a message
#: only one side knows the spelling of is a message the other side stops
#: recognising the first time it is reworded.
DANGLING_REF_MESSAGE_PREFIX: str = "dangling reference:"
UNVERIFIABLE_REF_MESSAGE_PREFIX: str = "unverifiable reference:"


def split_federation_id(value: object) -> tuple[str, str] | None:
    """Split a well-formed ``<child>\\x1f<node-id>`` into its two halves.

    Returns ``None`` for anything that is not well-formed -- a non-string,
    no separator, more than one separator, or an empty half -- so the single
    parser here is what both the shape check and the index agree on.
    """
    if not isinstance(value, str):
        return None
    parts = value.split(FEDERATION_ID_SEPARATOR)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def is_well_formed_federation_id(value: object) -> bool:
    """Return True iff *value* is a well-formed ``<child>\\x1f<node-id>``.

    A well-formed federation id has exactly one ASCII Unit Separator
    with a non-empty child label before it and a non-empty node id
    after it. Pathological strings (``"\\x1f"``, ``"a\\x1f"``,
    ``"\\x1fb"``, ``"a\\x1fb\\x1fc"``, non-strings) fail this check
    so the caller can keep flagging them as dangling references.

    Well-formed is *not* resolvable: see :class:`FederationIdIndex`.
    """
    return split_federation_id(value) is not None


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


@dataclass(frozen=True)
class FederationIdIndex:
    """Which node ids a federated workspace can actually resolve (ADR 0137 ss3).

    *root_ids* are the root meta-graph's own node ids -- ``repo:<name>`` and
    anything else the root minted. *child_ids* is keyed by **registered**
    child name, not merely present ones: a child named in ``workspaces.yaml``
    whose graph could not be read maps to ``None``, which is what separates
    "this endpoint is wrong" from "we cannot tell". *child_states* carries
    that child's lifecycle state (``missing`` / ``uninitialized`` /
    ``corrupt``) so the diagnostic can name the remedy.

    The index is deliberately id-only. It never holds a child's edges, and
    building it must never materialise them: the whole point of validating
    references is that it costs a set of strings per child, not a second copy
    of the workspace.
    """

    root_ids: frozenset[str]
    child_ids: Mapping[str, frozenset[str] | None]
    child_states: Mapping[str, str] = field(default_factory=dict)

    def endpoint_child(self, value: object) -> str | None:
        """Return the child label *value* names, or ``None`` for a root id."""
        parts = split_federation_id(value)
        return None if parts is None else parts[0]

    def child_state(self, child: str) -> str:
        """Return the recorded lifecycle state of an unreadable *child*."""
        return self.child_states.get(child, UNKNOWN_CHILD_STATE)

    def classify_endpoint(self, value: object) -> str:
        """Classify one edge endpoint as ok / dangling / unverifiable.

        ``unverifiable`` arises **only** from a registered child whose graph
        could not be read. The root graph is always readable, so a root-space
        id is always decidable: ``repo:<name>`` for a registered-but-absent
        child is ``dangling``, because the root mints ``repo:`` nodes for
        present children only -- its absence is a fact, not a gap. An
        unregistered child prefix is likewise ``dangling``: nothing in the
        workspace claims that name, so there is nothing to be unsure about.
        """
        if isinstance(value, str) and value in self.root_ids:
            return ENDPOINT_OK
        parts = split_federation_id(value)
        if parts is None:
            # A plain id that is not a root node, or a malformed federated
            # id. Either way the reader has nowhere to look it up.
            return ENDPOINT_DANGLING
        child, local = parts
        if child not in self.child_ids:
            return ENDPOINT_DANGLING
        ids = self.child_ids[child]
        if ids is None:
            return ENDPOINT_UNVERIFIABLE
        return ENDPOINT_OK if local in ids else ENDPOINT_DANGLING
