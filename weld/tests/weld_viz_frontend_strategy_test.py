"""Tests for the viz_frontend static-frontend extraction strategy.

Covers the three asset kinds (HTML/CSS/JS), the ``props.headings``
searchable-channel contract that makes a static single-page frontend
queryable, node shape, exclusion, and the no-edge invariant.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._node_ids import file_id
from weld.contract import validate_node
from weld.strategies.viz_frontend import extract

# A trimmed but faithful slice of the real viz inspector DOM: the
# inspector aside, its tab strip, and the body / changes panels named in
# the gap report.
_INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <body>
    <main class="shell">
      <section id="cy" aria-label="Graph canvas"></section>
      <aside id="inspector" class="inspector">
        <div id="inspect-tabs" class="inspect-tabs" role="tablist">
          <button id="tab-details" class="inspect-tab active" data-tab="details">Details</button>
          <button id="tab-changes" class="inspect-tab" data-tab="changes">Changes</button>
        </div>
        <div id="inspect-body" class="inspect-body" role="tabpanel"></div>
        <div id="inspect-changes" class="inspect-body inspect-changes" role="tabpanel" hidden></div>
        <div id="trace-tray" class="trace-tray" hidden></div>
        <div class="inspect-actions"></div>
      </aside>
    </main>
  </body>
</html>
"""

_STYLES_CSS = """\
.shell {
  display: grid;
}
.inspector {
  grid-column: 3;
}
#inspect-kind {
  color: gray;
}
.inspect-tabs {
  display: flex;
}
.inspect-tab:hover,
.inspect-tab:focus-visible {
  background: #eee;
}
@media (max-width: 700px) {
  .inspector { grid-column: 1; }
}
body {
  margin: 0;
}
"""

_APP_JS = """\
'use strict';

function showNode(data) {
  return data;
}

async function loadDiff() {
  return null;
}

function setActiveTab(name) {
  return name;
}

function renderChangesPanel(diff) {
  return diff;
}

const arrow = () => 42;
"""


def _write_frontend(root: Path) -> Path:
    """Create ``weld/viz/static`` with the three asset files under *root*."""
    static = root / "weld" / "viz" / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text(_INDEX_HTML)
    (static / "styles.css").write_text(_STYLES_CSS)
    (static / "app.js").write_text(_APP_JS)
    return static


class TestVizFrontendHtml(unittest.TestCase):
    """HTML element-id / class extraction into props.headings."""

    def _html_headings(self) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_frontend(root)
            result = extract(root, {"glob": "weld/viz/static/*.html"}, {})
        nid = file_id("weld/viz/static/index.html")
        self.assertIn(nid, result.nodes)
        return result.nodes[nid]["props"]["headings"]

    def test_element_ids_present_bare_and_anchored(self) -> None:
        headings = self._html_headings()
        for ident in ("inspector", "inspect-tabs", "inspect-body",
                      "inspect-changes", "trace-tray"):
            self.assertIn(ident, headings, f"missing bare id {ident}")
            self.assertIn("#" + ident, headings, f"missing #id {ident}")

    def test_class_tokens_present(self) -> None:
        headings = self._html_headings()
        for cls in ("inspector", "inspect-tabs", "inspect-tab",
                    "inspect-changes", "inspect-actions"):
            self.assertIn(cls, headings)

    def test_node_shape_is_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_frontend(root)
            result = extract(root, {"glob": "weld/viz/static/*.html"}, {})
        node = result.nodes[file_id("weld/viz/static/index.html")]
        self.assertEqual(node["type"], "file")
        self.assertEqual(node["label"], "index.html")
        self.assertEqual(node["props"]["roles"], ["doc"])
        self.assertEqual(node["props"]["file"], "weld/viz/static/index.html")


class TestVizFrontendCss(unittest.TestCase):
    """CSS selector extraction into props.headings."""

    def _css_headings(self) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_frontend(root)
            result = extract(root, {"glob": "weld/viz/static/*.css"}, {})
        return result.nodes[file_id("weld/viz/static/styles.css")]["props"]["headings"]

    def test_selectors_present(self) -> None:
        headings = self._css_headings()
        for sel in (".inspector", "#inspect-kind", ".inspect-tabs",
                    ".inspect-tab"):
            self.assertIn(sel, headings)

    def test_bare_element_and_atrule_selectors_dropped(self) -> None:
        headings = self._css_headings()
        # Bare element selectors (body) carry no domain signal; at-rule
        # prelude (@media ...) must not leak a heading entry.
        self.assertNotIn("body", headings)
        self.assertFalse(any(h.startswith("@") for h in headings))

    def test_layout_keywords_present(self) -> None:
        # The ``.inspector { display: grid; grid-column: ... }`` rule must
        # make "grid" queryable so "inspector grid" resolves to the
        # stylesheet (closes the "inspector grid" half of the gap).
        headings = self._css_headings()
        self.assertIn("grid", headings)
        self.assertIn("flex", headings)

    def test_css_role_is_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_frontend(root)
            result = extract(root, {"glob": "weld/viz/static/*.css"}, {})
        node = result.nodes[file_id("weld/viz/static/styles.css")]
        self.assertEqual(node["props"]["roles"], ["implementation"])


class TestVizFrontendJs(unittest.TestCase):
    """Top-level JS function extraction into props.headings."""

    def test_top_level_functions_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_frontend(root)
            result = extract(root, {"glob": "weld/viz/static/*.js"}, {})
        headings = result.nodes[file_id("weld/viz/static/app.js")]["props"]["headings"]
        for fn in ("showNode", "loadDiff", "setActiveTab", "renderChangesPanel"):
            self.assertIn(fn, headings)


class TestVizFrontendContract(unittest.TestCase):
    """Normalized-metadata contract, no-edge invariant, and validation."""

    def _all_nodes(self) -> dict:
        nodes: dict = {}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_frontend(root)
            for ext in ("html", "css", "js"):
                result = extract(
                    root, {"glob": f"weld/viz/static/*.{ext}"}, {}
                )
                self.assertEqual(result.edges, [], "strategy must emit no edges")
                nodes.update(result.nodes)
        return nodes

    def test_metadata_contract(self) -> None:
        for nid, node in self._all_nodes().items():
            props = node["props"]
            self.assertEqual(props["source_strategy"], "viz_frontend")
            self.assertEqual(props["authority"], "derived")
            self.assertEqual(props["confidence"], "definite")
            self.assertEqual(props["origin"], "project")
            self.assertIsInstance(props["roles"], list)
            self.assertTrue(props["roles"])

    def test_nodes_pass_contract_validation(self) -> None:
        for nid, node in self._all_nodes().items():
            errors = validate_node(nid, node)
            self.assertEqual(errors, [], f"{nid}: {[str(e) for e in errors]}")

    def test_three_distinct_file_ids(self) -> None:
        nodes = self._all_nodes()
        self.assertEqual(
            set(nodes),
            {
                file_id("weld/viz/static/index.html"),
                file_id("weld/viz/static/styles.css"),
                file_id("weld/viz/static/app.js"),
            },
        )

    def test_discovered_from_populated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_frontend(root)
            result = extract(root, {"glob": "weld/viz/static/*.html"}, {})
        self.assertIn("weld/viz/static/index.html", result.discovered_from)


class TestVizFrontendEdgeCases(unittest.TestCase):
    """Exclusion, missing directory, and empty-glob handling."""

    def test_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = extract(Path(tmp), {"glob": "weld/viz/static/*.html"}, {})
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])

    def test_empty_glob(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_frontend(root)
            result = extract(root, {"glob": ""}, {})
        self.assertEqual(result.nodes, {})

    def test_exclude_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_frontend(root)
            result = extract(
                root,
                {
                    "glob": "weld/viz/static/*.html",
                    "exclude": ["weld/viz/static/index.html"],
                },
                {},
            )
        self.assertEqual(result.nodes, {})


if __name__ == "__main__":
    unittest.main()
