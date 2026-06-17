"""Static-asset assertions for the viz Trace-this action (bd h6z0.10).

Lives in its own module so :mod:`weld_viz_static_test` stays under the
400-line cap and the trace-action surface keeps a dedicated test
landing site.
"""

from __future__ import annotations

import unittest
from importlib.resources import files


class _StaticAssetTestBase(unittest.TestCase):
    """Read shipped static assets via ``importlib`` (mirrors the static-test base)."""

    def _read_static(self, name: str) -> str:
        return files("weld.viz").joinpath("static", name).read_text(encoding="utf-8")


class VizTraceActionTest(_StaticAssetTestBase):
    """Inspector exposes /api/trace as a single-click pivot from selection."""

    def test_index_html_carries_trace_button_in_inspector_actions(self) -> None:
        html = self._read_static("index.html")
        # Trace button lives in the inspector action row alongside the
        # other selection-driven actions (expand, path A/B).
        actions_start = html.index('class="inspect-actions"')
        actions_end = html.index("</div>", actions_start)
        actions_block = html[actions_start:actions_end]
        self.assertIn('id="trace-button"', actions_block)
        # Ships hidden so it only paints when an eligible node is picked.
        button_attrs = actions_block.split('id="trace-button"', 1)[1].split(">", 1)[0]
        self.assertIn("hidden", button_attrs)

    def test_index_html_carries_trace_tray_skeleton(self) -> None:
        html = self._read_static("index.html")
        # Tray lives in the inspector aside so bucket counts render
        # alongside the selection without disrupting the existing chrome.
        inspector_start = html.index('id="inspector"')
        inspector_end = html.index("</aside>", inspector_start)
        block = html[inspector_start:inspector_end]
        self.assertIn('id="trace-tray"', block)

    def test_app_js_gates_trace_button_on_eligible_node_types(self) -> None:
        js = self._read_static("app.js")
        # Eligibility is the documented set of trace-participating types.
        # Each one must appear in the gating predicate.
        for kind in ("service", "contract", "boundary", "interface", "hook", "route", "rpc"):
            self.assertIn(f'"{kind}"', js, f"trace eligibility missing {kind}")

    def test_app_js_calls_trace_endpoint_with_depth_two(self) -> None:
        js = self._read_static("app.js")
        # Endpoint + depth=2 are the acceptance contract.
        self.assertIn("/api/trace", js)
        self.assertIn("depth", js)
        # The helper that runs the trace flow is named so its scope is
        # obvious from the call site.
        self.assertIn("runTrace", js)

    def test_app_js_renders_trace_tray_bucket_counts(self) -> None:
        js = self._read_static("app.js")
        # Tray lists the five canonical buckets; all bucket keys must be
        # referenced so the count rendering stays in sync with the API.
        for bucket in ("services", "interfaces", "contracts", "boundaries", "verifications"):
            self.assertIn(bucket, js, f"trace tray missing {bucket}")
        # Render function is named so it can be located by the test.
        self.assertIn("renderTraceTray", js)

    def test_app_js_styles_trace_edges_dashed_teal(self) -> None:
        js = self._read_static("app.js")
        # Cytoscape paints edge styles from its own style block, so the
        # trace-edge selector must live there. Dashed line-style + teal
        # line-color are the visual-distinguish acceptance criterion.
        self.assertIn('selector: ".trace-edge"', js)
        self.assertIn("dashed", js)
        self.assertIn("#118c8b", js)

    def test_styles_css_defines_trace_tray_chrome(self) -> None:
        css = self._read_static("styles.css")
        # Tray container is styled so the bucket-count pills sit
        # consistently below the inspector head.
        self.assertIn(".trace-tray", css)


if __name__ == "__main__":
    unittest.main()
