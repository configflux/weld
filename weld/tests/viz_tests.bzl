"""Visualization test targets (``wd viz``).

The static bundle, its browser launch path, and the interaction surfaces it
ships: properties inspector, hash state, search, diff, icons, editor open,
shortcuts, trace, export, layout, minimap, history, inspector grid, overview,
and views.

bd h6z0.9 / bd h6z0.3 / bd h6z0.13 / bd h6z0.16 / bd h6z0.10 / bd h6z0.14 /
bd h6z0.12 / bd h6z0.15 / bd h6z0.11 / bd h6z0.17 / bd yski / ADR 0073 /
ADR 0092.

Extracted verbatim from weld/tests/BUILD.bazel (bd hpv7): nineteen targets on
one folded line, the shape that has silently swallowed a target twice. Names,
srcs, and deps are unchanged, so every label stays //weld/tests:<name>.
"""

load("@rules_python//python:defs.bzl", "py_test")

# Every viz target but the first needs only ``//weld:runtime``; each target's
# srcs is its own ``<name>.py``.
_RUNTIME_ONLY = (
    "weld_viz_browser_launch_test",
    "weld_viz_static_test",
    "weld_viz_properties_test",
    "weld_viz_hash_state_test",
    "weld_viz_search_test",
    "weld_viz_diff_test",
    "weld_viz_icons_test",
    "weld_viz_open_editor_test",
    "weld_viz_shortcuts_test",
    "weld_viz_trace_test",
    "weld_viz_export_test",
    "weld_viz_layout_test",
    "weld_viz_minimap_test",
    "weld_viz_history_test",
    "weld_viz_inspector_grid_test",
    "weld_viz_overview_test",
    "weld_viz_overview_static_test",
    "weld_viz_views_test",
)

def viz_tests():
    py_test(
        name = "weld_viz_test",
        srcs = ["weld_viz_test.py"],
        deps = ["//weld:runtime", "//weld:workspace"],
    )

    [py_test(
        name = _name,
        srcs = [_name + ".py"],
        deps = ["//weld:runtime"],
    ) for _name in _RUNTIME_ONLY]
