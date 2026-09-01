"""Normalized metadata contract and graph validation for the connected structure.

tracked project
"""

from __future__ import annotations

# -- Schema version, closed vocabularies, ValidationError -------------------
# Defined in weld._contract_types (bd 5038-l24d9, ADR 0130 disposition #4): a
# dependency-free leaf so the validator siblings below can import
# ValidationError/the vocabulary constants from it directly instead of from
# this module, breaking the contract <-> _contract_validators <->
# _graph_doc_validators <-> _validate_diagnostics import cycle that existed
# when they imported these symbols back from here. Re-exported below so
# every existing ``from weld.contract import ROLE_VALUES`` (etc.) caller
# keeps working unchanged.
from weld._contract_types import (
    AUTHORITY_VALUES,
    BOUNDARY_KIND_VALUES,
    CONFIDENCE_VALUES,
    DOC_KIND_VALUES,
    PROTOCOL_TRANSPORT_COMPATIBILITY,
    PROTOCOL_VALUES,
    ROLE_VALUES,
    SCHEMA_VERSION,
    SECTION_KIND_VALUES,
    SURFACE_KIND_VALUES,
    TRANSPORT_VALUES,
    VALID_EDGE_TYPES,
    VALID_NODE_TYPES,
    ValidationError,
)

NODE_OPTIONAL_PROPS: tuple[str, ...] = (
    "source_strategy", "authority", "confidence", "roles", "file", "span",
    "doc_kind", "section_kind",
    # Interaction-surface metadata (ADR 0086).
    "protocol", "surface_kind", "transport", "boundary_kind", "declared_in",
)
EDGE_OPTIONAL_PROPS: tuple[str, ...] = (
    "source_strategy", "confidence", "resolved", "provenance", "raw",
    "resolution",
)

# -- Validators (re-exported from sibling private modules) -----------------
# Implementation is split across ``_contract_validators`` (node/meta),
# ``_contract_edge_validators`` (edges) and ``_graph_doc_validators``
# (graph/fragment aggregators) to keep every file under the 400-line
# default. Public names are re-exported here so existing callers
# (``from weld.contract import validate_graph``) work unchanged.
from weld._contract_edge_validators import validate_edge  # noqa: E402
from weld._contract_validators import (  # noqa: E402
    validate_meta,
    validate_node,
)
from weld._graph_doc_validators import (  # noqa: E402
    validate_fragment,
    validate_graph,
)

__all__ = [
    "SCHEMA_VERSION",
    "VALID_NODE_TYPES",
    "VALID_EDGE_TYPES",
    "AUTHORITY_VALUES",
    "CONFIDENCE_VALUES",
    "ROLE_VALUES",
    "DOC_KIND_VALUES",
    "SECTION_KIND_VALUES",
    "PROTOCOL_VALUES",
    "SURFACE_KIND_VALUES",
    "TRANSPORT_VALUES",
    "BOUNDARY_KIND_VALUES",
    "NODE_OPTIONAL_PROPS",
    "EDGE_OPTIONAL_PROPS",
    "PROTOCOL_TRANSPORT_COMPATIBILITY",
    "ValidationError",
    "validate_meta",
    "validate_node",
    "validate_edge",
    "validate_graph",
    "validate_fragment",
]
