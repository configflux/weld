"""Static-asset assertions for the viz "open in editor" links (bd h6z0.13).

Lives in its own module so :mod:`weld_viz_static_test` stays under the
400-line cap and the open-in-editor surface keeps a dedicated test
landing site.
"""

from __future__ import annotations

import unittest
from importlib.resources import files


class _StaticAssetTestBase(unittest.TestCase):
    """Read shipped static assets via ``importlib`` (mirrors the static-test base)."""

    def _read_static(self, name: str) -> str:
        return files("weld.viz").joinpath("static", name).read_text(encoding="utf-8")


class VizOpenInEditorTest(_StaticAssetTestBase):
    """Inspector renders vscode:// + git-remote/blob/ link affordances."""

    def test_app_js_defines_open_in_editor_helper(self) -> None:
        js = self._read_static("app.js")
        # A single helper produces both editor links so showNode stays
        # readable and the URL-shaping logic lives in one place.
        self.assertIn("openInEditorMarkup", js)
        # Both protocols are referenced verbatim per the acceptance
        # contract on bd h6z0.13.
        self.assertIn("vscode://file/", js)
        self.assertIn("/blob/", js)
        # The remote link opens in a new tab and disables window.opener.
        self.assertIn('rel="noopener"', js)

    def test_app_js_renders_open_links_only_for_file_or_symbol(self) -> None:
        js = self._read_static("app.js")
        start = js.index("function openInEditorMarkup(")
        end = js.index("\n}\n", start)
        body = js[start:end]
        # Helper gates on node type AND on props.file presence, so
        # entity/route/etc. nodes never get a broken link affordance.
        self.assertIn('"file"', body)
        self.assertIn('"symbol"', body)
        # Reads the absolute root from summary so the vscode URI points
        # at the real on-disk file rather than the redacted "." root.
        self.assertIn("abs_root", body)
        # Optional line suffix uses props.line when present.
        self.assertIn("props.line", body)
        # Remote URL composes summary.remote_url + summary.head_sha.
        self.assertIn("remote_url", body)
        self.assertIn("head_sha", body)
        # Escapes both rendered URLs so a malicious file path cannot
        # break out of the markup.
        self.assertGreaterEqual(body.count("escapeHtml"), 2)

    def test_show_node_calls_open_in_editor_helper(self) -> None:
        js = self._read_static("app.js")
        start = js.index("function showNode(")
        end = js.index("\nfunction ", start + 1)
        body = js[start:end]
        # showNode dispatches to the helper rather than inlining URL
        # shaping; the helper returns "" for non-file/symbol nodes.
        self.assertIn("openInEditorMarkup(data)", body)


if __name__ == "__main__":
    unittest.main()
