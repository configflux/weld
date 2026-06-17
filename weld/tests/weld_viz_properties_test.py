"""Static-asset assertions for the readable inspector Properties view.

The inspector Properties section used to dump ``data.props`` as a raw
``<pre>${JSON.stringify(...)}`` blob -- fine for power users, unreadable
as the default. ``renderProperties()`` replaces it with a styled
key/value view: labelled rows, scalars as text, arrays as chips, nested
objects as nested rows, and the full JSON demoted behind a collapsed
``<details>`` "Raw" disclosure so no information is lost (bd 1bfc).

These tests read the shipped JS/CSS under ``weld/viz/static/`` and assert
the structural hooks; behavioral rendering + HTML-escaping are exercised
by manual QA against ``wd viz``. Split into its own file per the cohesive
viz-feature test pattern (see ``weld_viz_inspector_grid_test``) so every
file stays under the 400-line cap.
"""

from __future__ import annotations

import unittest
from importlib.resources import files


def _read_static(name: str) -> str:
    return files("weld.viz").joinpath("static", name).read_text(encoding="utf-8")


def _function_body(js: str, signature: str) -> str:
    start = js.index(signature)
    end = js.index("\nfunction ", start + 1)
    return js[start:end]


class VizPropertiesViewTest(unittest.TestCase):
    """Static-asset assertions for the readable Properties view (bd 1bfc)."""

    def test_app_js_defines_render_properties_helper(self) -> None:
        js = _read_static("app.js")
        # A single renderer turns a props object into the labelled
        # key/value markup. Both showNode and showEdge route through it.
        self.assertIn("function renderProperties", js)

    def test_show_node_uses_readable_properties_not_raw_json_dump(self) -> None:
        js = _read_static("app.js")
        body = _function_body(js, "function showNode(")
        # The default Properties render goes through renderProperties.
        self.assertIn("renderProperties(", body)
        # The old raw default -- a <pre> wrapping JSON.stringify of the
        # props directly in showNode -- must be gone from showNode.
        self.assertNotIn("<pre>", body)

    def test_show_edge_uses_readable_properties_not_raw_json_dump(self) -> None:
        js = _read_static("app.js")
        body = _function_body(js, "function showEdge(")
        self.assertIn("renderProperties(", body)
        self.assertNotIn("<pre>", body)

    def test_render_properties_emits_labelled_rows_and_chips(self) -> None:
        js = _read_static("app.js")
        rows_body = _function_body(js, "function renderProperties(")
        value_body = _function_body(js, "function renderPropValue(")
        # The top-level renderer emits labelled key/value rows; the
        # per-value renderer turns scalar arrays into chips. Both reuse
        # the inspector's prop-* visual language.
        self.assertIn("prop-row", rows_body)
        self.assertIn("prop-chip", value_body)
        # Keys are humanized (imports_from -> "Imports From") via a
        # dedicated transform rather than printed raw.
        self.assertIn("humanizePropKey", js)

    def test_render_properties_keeps_raw_json_behind_disclosure(self) -> None:
        js = _read_static("app.js")
        # No information loss: the full JSON is still reachable, but
        # demoted into a collapsed native <details>/<summary> disclosure
        # so the readable view is the default.
        self.assertIn("<details", js)
        self.assertIn("<summary", js)
        self.assertIn("JSON.stringify", js)
        # The empty-props case renders a friendly placeholder, not an
        # empty "{}" dump.
        self.assertIn("No properties", js)

    def test_render_properties_escapes_all_untrusted_values(self) -> None:
        js = _read_static("app.js")
        body = _function_body(js, "function renderProperties(")
        value_body = _function_body(js, "function renderPropValue(")
        # Every key and value path is escaped before it reaches innerHTML
        # so a prop carrying angle brackets/quotes cannot inject markup
        # (no XSS via untracked graph content). Assert the renderer leans
        # on escapeHtml across both the row builder and the value builder.
        self.assertGreaterEqual(body.count("escapeHtml"), 1)
        self.assertGreaterEqual(value_body.count("escapeHtml"), 1)
        # The raw <pre> fallback also escapes the serialized JSON.
        self.assertIn("escapeHtml(JSON.stringify", js)

    def test_styles_css_defines_property_view_rules(self) -> None:
        css = _read_static("styles.css")
        # The readable view reuses the inspector visual language with its
        # own row + chip + raw-disclosure rules.
        self.assertIn(".prop-row", css)
        self.assertIn(".prop-chip", css)
        self.assertIn(".prop-raw", css)


if __name__ == "__main__":
    unittest.main()
