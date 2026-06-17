"""Static-asset assertions for the local read-only graph visualizer.

Tests in this module read the shipped HTML/JS/CSS under
``weld/viz/static/`` and assert presence of structural hooks. They run
under Bazel without a browser; behavioral verification of the rendered
UI is covered by manual QA against ``wd viz``.

Split out of ``weld_viz_test.py`` to keep both files under the 400-line
default cap (see CLAUDE.md "Line-Count Policy"). The split is by
responsibility: this file covers static-asset UI features, the original
covers the Python adapter/api/server.
"""

from __future__ import annotations

import unittest


class _StaticAssetTestBase(unittest.TestCase):
    """Shared helper to read packaged static assets via ``importlib``."""

    def _read_static(self, name: str) -> str:
        from importlib.resources import files

        return files("weld.viz").joinpath("static", name).read_text(encoding="utf-8")


class VizStaleBannerTest(_StaticAssetTestBase):
    """Static-asset assertions for the stale-graph banner (bd h6z0.1)."""

    def test_index_html_carries_banner_skeleton(self) -> None:
        html = self._read_static("index.html")
        # Banner element exists above #cy so the JS only has to populate it.
        cy_index = html.index('id="cy"')
        banner_index = html.index('id="stale-banner"')
        self.assertLess(banner_index, cy_index, "stale-banner must precede #cy")
        # Dismiss control exists for per-session dismissal.
        self.assertIn('id="stale-banner-dismiss"', html)

    def test_app_js_renders_banner_from_summary_stale(self) -> None:
        js = self._read_static("app.js")
        # Reads the documented payload shape: summary.stale.stale and
        # summary.stale.commits_behind. Both must be referenced.
        self.assertIn("summary.stale", js)
        self.assertIn("commits_behind", js)
        # Per-session dismissal uses sessionStorage; key is namespaced.
        self.assertIn("sessionStorage", js)
        self.assertIn("weld-viz-stale-dismissed", js)
        # Banner text matches the acceptance criterion verbatim (up to N).
        self.assertIn("commits behind HEAD", js)
        self.assertIn("wd discover", js)

    def test_app_js_banner_branches_on_cause(self) -> None:
        js = self._read_static("app.js")
        # bd ugqa: branch the banner on the *cause*. A dirty tree trips
        # source_stale at commits_behind:0, so guard the commits-behind copy
        # on sha_behind; the dirty-only path gets its own sentence instead
        # of a misleading "0 commits behind". Both signals ride the payload
        # (the full graph.stale() dict -- ADR 0017).
        start = js.index("function staleBannerMessage")
        end = js.index("\nfunction ", start + 1)
        body = js[start:end]
        self.assertIn("sha_behind", body)
        self.assertIn("source_stale", body)
        self.assertIn("uncommitted source", body)
        self.assertIn("commits behind HEAD", body)
        self.assertLess(
            body.index("sha_behind"),
            body.index("commits behind HEAD"),
            "sha_behind guard must precede the commits-behind copy",
        )

    def test_styles_css_defines_amber_banner(self) -> None:
        css = self._read_static("styles.css")
        # Banner rule exists and uses the amber accent.
        self.assertIn(".stale-banner", css)
        self.assertIn("var(--amber)", css)


class VizLegendTest(_StaticAssetTestBase):
    """Static-asset assertions for the node-type legend (bd h6z0.2)."""

    def test_index_html_carries_legend_skeleton_in_rail(self) -> None:
        html = self._read_static("index.html")
        rail_start = html.index('class="rail"')
        rail_end = html.index("</aside>", rail_start)
        rail_block = html[rail_start:rail_end]
        # Legend container lives inside the side rail.
        self.assertIn('id="legend"', rail_block)

    def test_app_js_renders_legend_from_nodes_by_type(self) -> None:
        js = self._read_static("app.js")
        # Legend rendering function exists and reads the documented shape.
        self.assertIn("nodes_by_type", js)
        # Reuses the existing color map -- no duplication.
        self.assertIn("colorFor", js)
        # Dim class is applied to filtered (hidden) types.
        self.assertIn("legend-row", js)
        self.assertIn("dim", js)

    def test_styles_css_defines_legend_rules(self) -> None:
        css = self._read_static("styles.css")
        # Legend block, row, and swatch are styled.
        self.assertIn(".legend", css)
        self.assertIn(".legend-swatch", css)
        # Dim modifier exists for filtered-out types.
        self.assertIn(".legend-row.dim", css)


class VizClickableNeighborsTest(_StaticAssetTestBase):
    """Static-asset assertions for clickable neighbor IDs (bd h6z0.5)."""

    def test_app_js_renders_node_link_helper(self) -> None:
        js = self._read_static("app.js")
        # A helper produces inspector-link anchor markup tagged with the
        # node id so the delegated handler can resolve clicks.
        self.assertIn("inspector-link", js)
        self.assertIn("data-node-id", js)

    def test_show_edge_links_endpoints_to_load_slice(self) -> None:
        js = self._read_static("app.js")
        # Locate the showEdge function body and confirm it renders the
        # from/to endpoints through the nodeLink helper.
        start = js.index("function showEdge(")
        end = js.index("\nfunction ", start + 1)
        body = js[start:end]
        # The helper that produces inspector-link anchor markup is the
        # single dispatch point for clickable neighbor ids.
        self.assertIn("nodeLink(", body)
        # Both endpoint ids must flow into the rendered markup.
        self.assertIn("data.source", body)
        self.assertIn("data.target", body)

    def test_show_node_links_known_node_ids(self) -> None:
        js = self._read_static("app.js")
        start = js.index("function showNode(")
        end = js.index("\nfunction ", start + 1)
        body = js[start:end]
        # The ID/Display fields are turned into anchors via the helper.
        self.assertIn("nodeLink(", body)
        self.assertIn("data.id", body)

    def test_node_link_helper_emits_inspector_link_class(self) -> None:
        js = self._read_static("app.js")
        # The nodeLink helper is the single place that builds the
        # anchor markup. It must emit the inspector-link class so the
        # delegated click handler can resolve clicks.
        start = js.index("function nodeLink(")
        end = js.index("\nfunction ", start + 1)
        body = js[start:end]
        self.assertIn("inspector-link", body)
        self.assertIn("data-node-id", body)
        # Escapes the rendered label and the attribute value so a
        # malicious node id (e.g. one carrying quotes or angle
        # brackets) cannot break out of the markup.
        self.assertGreaterEqual(body.count("escapeHtml"), 2)

    def test_delegated_click_loads_slice_with_depth_one(self) -> None:
        js = self._read_static("app.js")
        # A single delegated click handler on the inspector resolves
        # .inspector-link anchors and dispatches loadSlice with depth 1.
        self.assertIn("inspect-body", js)
        self.assertIn("loadSlice", js)
        self.assertIn("depth: 1", js)

    def test_styles_css_defines_inspector_link_hover(self) -> None:
        css = self._read_static("styles.css")
        # Inspector link style exists and the hover/focus state reuses
        # the teal accent applied to buttons (border + outline).
        self.assertIn(".inspector-link", css)
        # Hover/focus visible rule referencing the teal accent variable.
        self.assertIn("var(--teal)", css)


class VizPathPillsTest(_StaticAssetTestBase):
    """Static-asset assertions for the A/B path pills (bd h6z0.6)."""

    def test_index_html_carries_path_pills_skeleton(self) -> None:
        html = self._read_static("index.html")
        # Pills container lives at the top of the inspector head.
        head_start = html.index('class="inspector-head"')
        head_end = html.index("</div>", head_start)
        head_block = html[head_start:head_end]
        self.assertIn('id="path-pills"', head_block)

    def test_index_html_carries_path_swap_button(self) -> None:
        html = self._read_static("index.html")
        # Swap A/B button lives alongside the A/B set-endpoint buttons.
        self.assertIn('id="path-swap-button"', html)

    def test_app_js_renders_path_pills_with_clear(self) -> None:
        js = self._read_static("app.js")
        # A single render function paints the two pills from state.
        self.assertIn("renderPathPills", js)
        # Both pill ids are wired so clicks can clear independently.
        self.assertIn("path-pill-a", js)
        self.assertIn("path-pill-b", js)
        # Each pill carries an explicit clear button (the "x").
        self.assertIn("path-pill-clear", js)

    def test_app_js_marks_path_endpoints_on_canvas(self) -> None:
        js = self._read_static("app.js")
        # The cytoscape elements are tagged with a path-endpoint class so
        # the canvas ring style applies. Both ends are addressed by name.
        self.assertIn("path-a", js)
        self.assertIn("path-b", js)
        # The marker is reapplied after loadSlice so the rings persist
        # across slice reloads -- this is the core acceptance contract.
        self.assertIn("markPathEndpoints", js)

    def test_loadslice_persists_path_endpoint_rings(self) -> None:
        js = self._read_static("app.js")
        # The post-render hook reapplies endpoint markers; lexically the
        # marker call must appear inside loadSlice() so it runs on every
        # slice swap, not only after path queries.
        start = js.index("async function loadSlice(")
        end = js.index("\n}\n", start)
        body = js[start:end]
        self.assertIn("markPathEndpoints", body)

    def test_swap_endpoints_handler_swaps_state(self) -> None:
        js = self._read_static("app.js")
        # A swap handler exchanges state.pathA and state.pathB and
        # re-runs the path query when both are set.
        self.assertIn("swapPathEndpoints", js)
        start = js.index("function swapPathEndpoints")
        end = js.index("\n}", start)
        body = js[start:end]
        # Both endpoints are touched -- the swap moves A->B and B->A.
        self.assertIn("state.pathA", body)
        self.assertIn("state.pathB", body)

    def test_app_js_defines_ab_endpoint_ring_styles(self) -> None:
        js = self._read_static("app.js")
        # Cytoscape paints node borders from its own style block (not CSS),
        # so the ring styles for A/B live in the cytoscape style array.
        # Both selectors must appear so the rings render on canvas.
        self.assertIn('selector: ".path-a"', js)
        self.assertIn('selector: ".path-b"', js)

    def test_styles_css_defines_path_pill_chrome(self) -> None:
        css = self._read_static("styles.css")
        # The pill row container is styled so the two pills sit at the
        # top of the inspector head.
        self.assertIn(".path-pills", css)


class VizSearchSuggestTest(_StaticAssetTestBase):
    """Static-asset assertions for the search-suggest dropdown (bd h6z0.8)."""

    def test_index_html_carries_suggest_skeleton(self) -> None:
        html = self._read_static("index.html")
        # Dropdown list lives inside the search form, anchored under
        # the search input so the JS only has to populate it.
        form_start = html.index('id="search-form"')
        form_end = html.index("</form>", form_start)
        form_block = html[form_start:form_end]
        self.assertIn('id="search-suggest"', form_block)
        # ARIA wiring lets screen readers announce the dropdown as a
        # listbox controlled by the search input.
        self.assertIn('role="listbox"', form_block)
        self.assertIn("aria-controls=\"search-suggest\"", form_block)

    def test_app_js_debounces_suggest_requests(self) -> None:
        js = self._read_static("app.js")
        # Suggest is debounced at 200ms per the bd spec; both the
        # scheduler and the endpoint reference must be present.
        self.assertIn("setTimeout", js)
        self.assertIn("200", js)
        self.assertIn("/api/search-suggest", js)

    def test_app_js_renders_suggest_items(self) -> None:
        js = self._read_static("app.js")
        # Each row carries data-suggest-id so the delegated mousedown
        # handler can resolve which suggestion was picked.
        self.assertIn("data-suggest-id", js)
        # Renders id, label, type triple per the API contract.
        self.assertIn("suggest-label", js)
        self.assertIn("suggest-type", js)

    def test_app_js_renders_empty_state_entry_points(self) -> None:
        js = self._read_static("app.js")
        # When a slice has zero visible nodes, the empty-state fallback
        # hits /api/search-suggest with q="" (top-degree path) and
        # paints the inspector body with clickable entry points.
        self.assertIn("maybeRenderEmptyState", js)
        self.assertIn("empty-state-suggestions", js)
        self.assertIn("visible_nodes", js)

    def test_styles_css_defines_suggest_dropdown(self) -> None:
        css = self._read_static("styles.css")
        # Dropdown chrome anchors below the search input.
        self.assertIn(".search-suggest", css)
        # Empty-state list inside the inspector also has its own rule.
        self.assertIn(".empty-state-suggestions", css)


class VizConsistentFilterApplyTest(_StaticAssetTestBase):
    """Static-asset assertions for consistent filter apply (bd h6z0.7)."""

    def test_index_html_drops_apply_filters_button(self) -> None:
        html = self._read_static("index.html")
        # The Apply button is gone: filters reload automatically.
        self.assertNotIn('id="apply-filters"', html)

    def test_app_js_defines_filter_reload_debouncer(self) -> None:
        js = self._read_static("app.js")
        # A named debounce scheduler is the single dispatch point so
        # every filter handler stays a one-liner. 250 ms matches the
        # acceptance criterion in the bd issue.
        self.assertIn("scheduleFilterReload", js)
        self.assertIn("250", js)
        # The scheduler must clear a pending timer so rapid edits
        # coalesce into one /api/slice call.
        start = js.index("function scheduleFilterReload")
        end = js.index("\n}", start)
        body = js[start:end]
        self.assertIn("clearTimeout", body)
        self.assertIn("setTimeout", body)

    def test_app_js_routes_every_filter_control_through_debouncer(self) -> None:
        js = self._read_static("app.js")
        bind_start = js.index("function bindEvents()")
        bind_end = js.index("\n}", bind_start)
        bind_body = js[bind_start:bind_end]
        # All six filter controls fire the same scheduler so behavior
        # is consistent (the bd h6z0.7 acceptance criterion).
        for control in (
            "scope-select",
            "node-type-select",
            "edge-type-select",
            "hide-stdlib-check",
            "hide-external-check",
            "limit-input",
        ):
            self.assertIn(control, bind_body, f"{control} missing from bindEvents")
            # Each filter control invokes the debouncer rather than
            # calling loadSlice() directly.
        self.assertGreaterEqual(bind_body.count("scheduleFilterReload"), 6)
        # The Apply button id no longer appears anywhere in bindEvents.
        self.assertNotIn("apply-filters", bind_body)


class VizChangesTabTest(_StaticAssetTestBase):
    """Static-asset assertions for the in-UI diff view (bd h6z0.9)."""

    def test_index_html_carries_tab_skeleton(self) -> None:
        html = self._read_static("index.html")
        # Two tab buttons + a paired changes panel sit inside the
        # inspector aside so the existing inspect-body and action row
        # stay untouched.
        inspector_start = html.index('id="inspector"')
        inspector_end = html.index("</aside>", inspector_start)
        block = html[inspector_start:inspector_end]
        self.assertIn('id="tab-details"', block)
        self.assertIn('id="tab-changes"', block)
        self.assertIn('id="inspect-changes"', block)
        # The Changes panel ships hidden so Details remains the default.
        self.assertIn('aria-selected="true"', block)
        self.assertIn('aria-selected="false"', block)

    def test_app_js_wires_changes_tab(self) -> None:
        js = self._read_static("app.js")
        # Tab toggle wires both panels and the diff fetch on activation.
        self.assertIn("setActiveTab", js)
        self.assertIn("/api/diff", js)
        # Rendering reads the stable contract keys directly.
        self.assertIn("added_nodes", js)
        self.assertIn("removed_nodes", js)
        self.assertIn("modified_nodes", js)
        self.assertIn("added_edges", js)
        self.assertIn("removed_edges", js)
        # Empty-state copy matches the acceptance criterion verbatim.
        self.assertIn("No changes since last", js)
        self.assertIn("wd discover", js)

    def test_app_js_applies_diff_tints_on_canvas(self) -> None:
        js = self._read_static("app.js")
        # Cytoscape style block paints green / red / amber tints for
        # added / removed / modified nodes selected from the Changes tab.
        self.assertIn(".diff-added", js)
        self.assertIn(".diff-removed", js)
        self.assertIn(".diff-modified", js)
        # Click handler re-centers the node and applies the tint class.
        self.assertIn("applyDiffHighlight", js)

    def test_styles_css_defines_changes_tab(self) -> None:
        css = self._read_static("styles.css")
        # Tab strip + per-section accent rules exist for added/removed/
        # modified so the diff is scannable at a glance.
        self.assertIn(".inspect-tab", css)
        self.assertIn(".changes-section.added", css)
        self.assertIn(".changes-section.removed", css)
        self.assertIn(".changes-section.modified", css)
        # Friendly empty-state row has its own muted styling.
        self.assertIn(".changes-empty", css)


if __name__ == "__main__":
    unittest.main()
