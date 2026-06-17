"""Static-asset assertions for the visualizer manual layout control (bd h6z0.12).

The viz UI exposes a toolbar layout select (cose / dagre / concentric / grid /
breadthfirst) backed by a vendored ``cytoscape-dagre`` extension. The choice
persists in ``location.hash`` alongside the existing schema (bd h6z0.4).

Split out of ``weld_viz_static_test.py`` so each viz feature has its own
file -- the same cohesive split pattern used by ``weld_viz_hash_state_test``,
``weld_viz_shortcuts_test``, etc. Keeps both files under the 400-line cap.
"""

from __future__ import annotations

import unittest
from importlib.resources import files


def _read_static(name: str) -> str:
    return files("weld.viz").joinpath("static", name).read_text(encoding="utf-8")


class VizLayoutSelectTest(unittest.TestCase):
    """Static-asset assertions for manual layout control (bd h6z0.12)."""

    LAYOUT_OPTIONS = ("cose", "dagre", "concentric", "grid", "breadthfirst")

    def test_index_html_carries_layout_select_with_five_options(self) -> None:
        html = _read_static("index.html")
        # Layout select lives inside the top-bar toolbar so the picker
        # sits next to the existing fit/layout/clear buttons.
        toolbar_start = html.index('class="toolbar"')
        toolbar_end = html.index("</div>", toolbar_start)
        toolbar_block = html[toolbar_start:toolbar_end]
        self.assertIn('id="layout-select"', toolbar_block)
        # All five layout options must appear by value with cose as the
        # default selected option (default for any size, per bd h6z0.12).
        for value in self.LAYOUT_OPTIONS:
            self.assertIn(
                f'value="{value}"', toolbar_block,
                f"layout option {value!r} missing from toolbar",
            )
        self.assertIn('value="cose" selected', toolbar_block)

    def test_index_html_loads_cytoscape_dagre_vendor_script(self) -> None:
        html = _read_static("index.html")
        # The dagre extension script must appear AFTER the cytoscape core
        # script so register(window.cytoscape) succeeds at load time.
        core_pos = html.index("cytoscape-3.33.2.min.js")
        dagre_pos = html.index("cytoscape-dagre")
        self.assertLess(
            core_pos, dagre_pos,
            "cytoscape-dagre must load after cytoscape core for auto-register",
        )

    def test_app_js_run_layout_reads_select_value(self) -> None:
        js = _read_static("app.js")
        # ADR 0073: runLayout now resolves the layout name through
        # effectiveLayoutName(), which still reads the user's
        # #layout-select choice (and defaults the overview to dagre).
        # The dispatch to cytoscape stays in runLayout.
        run_start = js.index("function runLayout(")
        run_end = js.index("\n}", run_start)
        run_body = js[run_start:run_end]
        self.assertIn("effectiveLayoutName(", run_body)
        self.assertIn("cy.layout", run_body)
        # The select read lives in the resolver.
        eff_start = js.index("function effectiveLayoutName(")
        eff_end = js.index("\n}", eff_start)
        self.assertIn("layout-select", js[eff_start:eff_end])

    def test_app_js_run_layout_drops_node_count_heuristic(self) -> None:
        js = _read_static("app.js")
        start = js.index("function runLayout(")
        end = js.index("\n}", start)
        body = js[start:end]
        # The ">450 nodes -> grid" heuristic must be gone -- user picks now.
        self.assertNotIn("> 450", body)
        self.assertNotIn("450", body)

    def test_app_js_hash_state_includes_layout_key(self) -> None:
        js = _read_static("app.js")
        # Layout joins the existing HASH_KEYS list so URL hash round-trips.
        hash_keys_start = js.index("HASH_KEYS")
        hash_keys_end = js.index("]", hash_keys_start)
        hash_keys_block = js[hash_keys_start:hash_keys_end]
        self.assertIn('"layout"', hash_keys_block)
        # Reader + writer for the live view both touch the layout key.
        read_start = js.index("function readViewState(")
        read_end = js.index("\n}", read_start)
        read_body = js[read_start:read_end]
        self.assertIn("layout", read_body)
        apply_start = js.index("function applyViewState(")
        apply_end = js.index("\n}", apply_start)
        apply_body = js[apply_start:apply_end]
        self.assertIn("layout", apply_body)

    def test_app_js_layout_select_change_updates_hash(self) -> None:
        js = _read_static("app.js")
        # Changing the layout must persist to the URL hash so a refresh
        # restores the user-picked layout (bd h6z0.4 schema extension).
        bind_start = js.index("function bindEvents()")
        bind_end = js.index("\n}", bind_start)
        bind_body = js[bind_start:bind_end]
        self.assertIn("layout-select", bind_body)

    def test_vendor_cytoscape_dagre_min_js_present(self) -> None:
        resource = files("weld.viz").joinpath(
            "static", "vendor", "cytoscape-dagre-3.0.0.min.js",
        )
        self.assertTrue(
            resource.is_file(),
            "cytoscape-dagre vendor bundle missing from static/vendor/",
        )
        # Sanity-check: the bundle exposes the cytoscape-dagre auto-register
        # hook, so the file we ship is the right artefact and not a stub.
        contents = resource.read_text(encoding="utf-8")
        self.assertIn("cytoscape", contents)
        self.assertIn("dagre", contents)

    def test_vendor_readme_attributes_cytoscape_dagre_mit(self) -> None:
        readme = files("weld.viz").joinpath(
            "static", "vendor", "README.md",
        ).read_text(encoding="utf-8")
        # Attribution row carries the library, license, and source URL --
        # the three pieces required by the MIT redistribution clause.
        self.assertIn("cytoscape-dagre", readme)
        self.assertIn("MIT", readme)
        self.assertIn("cytoscape.js-dagre", readme)


if __name__ == "__main__":
    unittest.main()
