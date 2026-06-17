"""Static-asset assertions for the curated cold-open overview (ADR 0073).

The default ``wd viz`` view is now a curated architecture slice rendered
hierarchically (dagre) with the inspector seeded by real project entry
points. These checks pin the frontend wiring of that behavior without a
browser, mirroring the per-feature static-asset test split already used
by ``weld_viz_layout_test`` etc.
"""

from __future__ import annotations

import unittest
from importlib.resources import files


def _read_static(name: str) -> str:
    return files("weld.viz").joinpath("static", name).read_text(encoding="utf-8")


class VizOverviewDefaultLayoutTest(unittest.TestCase):
    """The overview defaults to dagre unless the user pins a layout."""

    def test_layout_select_keeps_cose_as_stored_default(self) -> None:
        # ADR 0073 is explicit: the <select> default value stays cose so
        # non-overview slices and the persisted hash schema are unchanged;
        # only the *applied* overview layout is overridden to dagre.
        html = _read_static("index.html")
        self.assertIn('value="cose" selected', html)

    def test_effective_layout_defaults_overview_to_dagre(self) -> None:
        js = _read_static("app.js")
        start = js.index("function effectiveLayoutName(")
        end = js.index("\n}", start)
        body = js[start:end]
        # Overview view + no pinned layout => dagre; otherwise the select.
        self.assertIn("dagre", body)
        self.assertIn("isOverviewView()", body)
        self.assertIn("layoutPinnedInHash()", body)
        self.assertIn("layout-select", body)

    def test_is_overview_view_excludes_node_query_and_path(self) -> None:
        js = _read_static("app.js")
        start = js.index("function isOverviewView(")
        end = js.index("\n}", start)
        body = js[start:end]
        # The cold open is "nothing pinned": no selected node, no query,
        # no path endpoints. Any of those => not the overview.
        self.assertIn("state.nodeId", body)
        self.assertIn("search-input", body)
        self.assertIn("state.pathA", body)
        self.assertIn("state.pathB", body)

    def test_pinned_layout_hash_key_wins(self) -> None:
        js = _read_static("app.js")
        start = js.index("function layoutPinnedInHash(")
        end = js.index("\n}", start)
        body = js[start:end]
        # Detects an explicit layout choice persisted in the URL hash.
        self.assertIn('"layout"', body)
        self.assertIn("location.hash", body)


class VizInspectorEntryPointsTest(unittest.TestCase):
    """The inspector is seeded with real entry points on the cold open."""

    def test_seed_helper_calls_search_suggest_empty_query(self) -> None:
        js = _read_static("app.js")
        start = js.index("function seedInspectorEntryPoints(")
        end = js.index("\n}", start)
        body = js[start:end]
        # Reuses the q="" search-suggest set (CLI commands / MCP tools /
        # top packages) -- the same authority the empty-state hint uses.
        self.assertIn("/api/search-suggest", body)
        self.assertIn("inspector-link", body)
        self.assertIn("entry-points", body)

    def test_init_seeds_entry_points_when_no_node_selected(self) -> None:
        js = _read_static("app.js")
        start = js.index("async function init(")
        end = js.index("\n}", start)
        body = js[start:end]
        self.assertIn("seedInspectorEntryPoints(", body)

    def test_clear_inspector_reseeds_entry_points(self) -> None:
        js = _read_static("app.js")
        start = js.index("function clearInspector(")
        end = js.index("\n}", start)
        body = js[start:end]
        self.assertIn("seedInspectorEntryPoints(", body)

    def test_styles_define_entry_points_list(self) -> None:
        css = _read_static("styles.css")
        self.assertIn(".entry-points", css)
        self.assertIn(".entry-point-type", css)


if __name__ == "__main__":
    unittest.main()
