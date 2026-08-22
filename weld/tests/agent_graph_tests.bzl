"""Agent-graph test targets (``wd agents`` / the ``weld_agent_graph`` surface).

Schema, storage, discovery, metadata, inferred references, edge weights, the
CLI surfaces (audit, plan, impact, diagnostics), and the demo fixture -- one
subject, declared in one place.

Extracted verbatim from weld/tests/BUILD.bazel (bd hpv7): these thirty-odd
targets lived inside a single 2,900-character folded line, the shape that has
silently swallowed a target twice. Names, srcs, and deps are unchanged, so
every label stays //weld/tests:<name>.
"""

load("@rules_python//python:defs.bzl", "py_test")

# (target name, deps). Every target's srcs is its own ``<name>.py``.
_TARGETS = (
    ("weld_edge_types_test", ["//weld:contract", "//weld:runtime"]),
    ("weld_agent_viz_test", ["//weld:runtime"]),
    ("weld_agent_graph_schema_test", ["//weld:contract"]),
    ("weld_agent_graph_storage_test", ["//weld:runtime"]),
    ("weld_agent_graph_discovery_test", ["//weld:runtime"]),
    ("weld_agent_graph_metadata_test", ["//weld:runtime"]),
    ("weld_agent_graph_path_extension_test", ["//weld:runtime"]),
    ("weld_graph_write_lock_test", ["//weld:runtime"]),
    ("weld_agent_graph_inferred_refs_test", ["//weld:runtime"]),
    ("weld_agent_graph_inferred_refs_bare_command_test", ["//weld:runtime"]),
    ("weld_agent_graph_inferred_refs_description_test", ["//weld:runtime"]),
    ("weld_agent_graph_inferred_refs_dedupe_test", ["//weld:runtime"]),
    ("weld_agent_graph_frontmatter_invokes_test", ["//weld:runtime"]),
    ("weld_agent_graph_cli_test", ["//weld:runtime"]),
    ("weld_agent_graph_cli_diagnostics_test", ["//weld:runtime"]),
    ("weld_agent_graph_impact_cli_test", ["//weld:runtime"]),
    ("weld_agent_graph_audit_cli_test", ["//weld:runtime"]),
    ("weld_agent_graph_audit_strict_test", ["//weld:runtime"]),
    ("weld_agent_graph_constants_test", ["//weld:runtime"]),
    ("weld_agent_graph_edge_weights_test", ["//weld:runtime"]),
    ("weld_agent_graph_unused_skill_test", ["//weld:runtime"]),
    ("weld_agent_graph_plan_cli_test", ["//weld:runtime"]),
    ("weld_agent_graph_plan_weights_test", ["//weld:runtime"]),
    ("weld_agent_graph_authority_test", ["//weld:runtime"]),
    ("weld_agent_graph_alias_roundtrip_test", ["//weld:runtime"]),
    ("weld_agent_graph_render_test", ["//weld:runtime"]),
    ("weld_agent_graph_terminal_safety_test", ["//weld:runtime"]),
    ("weld_agent_graph_render_pairs_authority_test", ["//weld:runtime"]),
    ("weld_agent_graph_demo_fixture_test", ["//weld:runtime"]),
    ("weld_agent_graph_demo_coverage_test", ["//weld:runtime"]),
    ("weld_agent_graph_permission_explode_test", ["//weld:runtime"]),
    ("weld_agent_graph_implicit_scope_test", ["//weld:runtime"]),
)

def agent_graph_tests():
    [py_test(
        name = _name,
        srcs = [_name + ".py"],
        deps = _deps,
    ) for _name, _deps in _TARGETS]
