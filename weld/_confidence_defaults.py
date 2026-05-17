"""Static ``source_strategy -> confidence`` defaults map (ADR 0050).

The map encodes the per-class taxonomy from ADR 0050:

* ``definite``    -- tree-sitter / AST / build-system parsers, parsed
                     config files, and graph-closure relationships
                     derived from those parsers' output.
* ``inferred``    -- heuristic / filename / name-similarity matchers
                     that may over-include without obvious evidence,
                     plus regex-driven post-processing.
* ``speculative`` -- LLM-emitted edges, cross-repo resolvers without
                     disambiguating evidence, and any path with a
                     material false-positive risk.

Strategies that do not appear in this map fall back to
``"speculative"``. The intent is friction: an unmapped strategy is a
strategy whose author has not declared a stance, and the safer default
in the absence of a stance is "do not trust without review".

The map is consumed by:

1. :mod:`weld._graph_migrate` -- backfills missing ``confidence`` props
   on legacy graphs by looking each edge's ``source_strategy`` up here.
2. :mod:`weld.tests.weld_confidence_defaults_test` -- pins the map
   shape so a regression that demotes ``tree_sitter`` to ``inferred``
   (or, worse, promotes ``anthropic_enrichment`` to ``definite``) fails
   with a pointed diagnostic.

The map intentionally does **not** drive runtime emission. Strategies
must stamp ``confidence`` explicitly at the call site -- that is where
the producing author can attach disambiguating context (a method-match
versus a name-match, a parsed AST versus a regex fallback). The map
is the *fallback* when no stamp is present.
"""

from __future__ import annotations

# ADR 0050 §"Decision". Producers grouped by class and listed
# alphabetically within each block so a refactor that touches one
# strategy is mechanically obvious in diff review.

_DEFINITE_STRATEGIES: tuple[str, ...] = (
    # AST / tree-sitter parsers. Every edge minted from a parsed AST
    # node is by definition deterministic.
    "tree_sitter",
    "tree_sitter_calls",
    "python_callgraph",
    "python_module",
    "python_package",
    "_typescript_tree_sitter",
    "typescript_exports",
    "_csharp_tree_sitter",
    "_cpp_tree_sitter",
    "_java_tree_sitter",
    "_rust_tree_sitter",

    "csharp_package",

    # Build-system label parsers.
    "bazel",
    "csharp_project",
    "csharp_solution",
    "manifest",
    "ros2_cmake",
    "ros2_package",
    "ros2_interfaces",
    "ros2_launch",

    # Parsed-config strategies (YAML / dockerfile / proto / compose).
    "compose",
    "config_file",
    "dockerfile",
    "fastapi",
    "frontmatter_md",
    "firstline_md",
    "gh_workflow",
    "grpc_proto",
    "http_client",
    "markdown",
    "pydantic",
    "runbook",
    "runtime_contract",
    "sqlalchemy",
    "tool_script",
    "worker_stage",
    "events_shared",
    "yaml_meta",

    # Cross-repo resolvers with disambiguating evidence (matched
    # service+method or matched host+method+path or parsed compose
    # depends_on).
    "compose_topology",
    "grpc_service_binding",
    "service_graph",
    "manual_override",

    # Discovery post-processing closures derived from the above.
    "graph_closure",

    # User-supplied topology declarations from .weld/discover.yaml.
    "topology",
)

_INFERRED_STRATEGIES: tuple[str, ...] = (
    # Heuristic and name-similarity matchers.
    "test_peer",
    "events_bindings",
    "events_callsite",
    "events_config",
    "events",
    "grpc_bindings",
    "ros2_topology",
    "ros2_topology_cpp",
    "_ros2_py",
    "_ros2_cpp",
    "boundary_entrypoint",
    "deploy_surface",
    "concept_from_bd",

    # Regex-driven post-processing edges (agent-name detection in
    # command text, etc).
    "post_processing",
)

_SPECULATIVE_STRATEGIES: tuple[str, ...] = (
    # LLM-emitted enrichment edges. Provider names match
    # weld.providers.* + the agent-direct path used by
    # /enrich-weld (provider == "manual" but emits via LLM judgement).
    "anthropic_enrichment",
    "openai_enrichment",
    "ollama_enrichment",
    "copilot_cli_enrichment",
    "manual_enrichment",
    "agent_enrichment",

    # Cross-repo resolvers that rely on bare name match alone.
    "package_import_resolver",
)


def _build_map() -> dict[str, str]:
    """Assemble the ``{strategy: confidence}`` lookup once at import time."""
    out: dict[str, str] = {}
    for name in _DEFINITE_STRATEGIES:
        out[name] = "definite"
    for name in _INFERRED_STRATEGIES:
        out[name] = "inferred"
    for name in _SPECULATIVE_STRATEGIES:
        out[name] = "speculative"
    return out


#: Frozen lookup. Callers should treat this as read-only; mutation is
#: only safe in tests that snapshot and restore the original.
STRATEGY_DEFAULT_CONFIDENCE: dict[str, str] = _build_map()


def classify_strategy(
    source_strategy: str | None,
    *,
    default: str = "speculative",
) -> str:
    """Return the default confidence for *source_strategy*.

    Unknown / empty / ``None`` strategies fall back to *default* (which
    itself defaults to ``"speculative"`` per ADR 0050: an unmapped
    strategy is one whose author has not declared a stance, and the
    honest default in the absence of a stance is to mark the edge for
    review).

    *default* must be a member of
    :data:`weld.contract.CONFIDENCE_VALUES`. The function does not
    validate this -- callers that pass a literal must pass a real one.
    """
    if not source_strategy:
        return default
    return STRATEGY_DEFAULT_CONFIDENCE.get(source_strategy, default)


__all__ = [
    "STRATEGY_DEFAULT_CONFIDENCE",
    "classify_strategy",
]
