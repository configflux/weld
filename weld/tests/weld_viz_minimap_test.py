"""Static-asset assertions for the corner minimap (bd h6z0.15).

Tests in this module read the shipped HTML/JS/CSS under
``weld/viz/static/`` and assert the toggle markup, the vendored
cytoscape-navigator bundle, and the URL-hash schema extension that
persists the minimap preference. Behavioral verification of the rendered
bird's-eye-view panel is covered by manual QA against ``wd viz``.

Split out of ``weld_viz_static_test.py`` to keep that file under the
400-line default cap (see CLAUDE.md "Line-Count Policy"). Scope:
bd h6z0.15 only.
"""

from __future__ import annotations

import re
import unittest


def _rule_body(css: str, selector: str, *, contains: str = "") -> str:
    """Return the declaration block for a ``selector { ... }`` rule.

    ``selector`` may appear more than once (e.g. a base rule plus a
    media-query variant). When ``contains`` is given, return the first
    rule whose body contains that substring so callers can disambiguate.
    """
    search_from = 0
    while True:
        start = css.index(selector, search_from)
        open_brace = css.index("{", start)
        close_brace = css.index("}", open_brace)
        body = css[open_brace + 1 : close_brace]
        if not contains or contains in body:
            return body
        search_from = close_brace + 1


def _px(value: str) -> int:
    """Sum the integer pixel literals in a CSS length expression.

    ``calc(320px + 12px)`` -> ``332``. Callers use this to assert the
    minimap right-offset clears the 320px inspector column without pinning
    the exact ``calc()`` text.
    """
    return sum(int(n) for n in re.findall(r"(\d+)px", value))


class _StaticAssetTestBase(unittest.TestCase):
    """Shared helper to read packaged static assets via ``importlib``."""

    def _read_static(self, name: str) -> str:
        from importlib.resources import files

        return files("weld.viz").joinpath("static", name).read_text(encoding="utf-8")

    def _read_vendor(self, name: str) -> bytes:
        from importlib.resources import files

        return files("weld.viz").joinpath("static", "vendor", name).read_bytes()


class VizMinimapTest(_StaticAssetTestBase):
    """Static-asset assertions for the corner minimap (bd h6z0.15)."""

    def test_vendor_navigator_js_is_present(self) -> None:
        body = self._read_vendor("cytoscape-navigator-2.0.2.js")
        # Body is non-trivial (>1 KB) and self-registers against the
        # global cytoscape namespace so the script tag in index.html is
        # enough to wire it up. No CDN fallback is allowed.
        self.assertGreater(len(body), 1024)
        self.assertIn(b"cytoscape", body)

    def test_vendor_navigator_license_is_verbatim_mit(self) -> None:
        body = self._read_vendor("cytoscape-navigator-2.0.2.LICENSE").decode("utf-8")
        self.assertIn("Copyright", body)
        # MIT permissions language is present (the upstream LICENSE does
        # not literally say "MIT" but uses the canonical permission
        # text); guard against accidental swap by checking the AS IS
        # disclaimer and the permission grant verb.
        self.assertIn("Permission is hereby granted", body)
        self.assertIn("AS IS", body.upper())

    def test_vendor_navigator_css_is_present(self) -> None:
        css = self._read_vendor("cytoscape-navigator-2.0.2.css").decode("utf-8")
        # Upstream CSS defines the .cytoscape-navigator panel chrome.
        self.assertIn(".cytoscape-navigator", css)

    def test_vendor_readme_lists_navigator(self) -> None:
        readme = self._read_vendor("README.md").decode("utf-8")
        self.assertIn("cytoscape-navigator-2.0.2", readme)
        # Source URL stays in the table so downstream packagers can
        # trace provenance.
        self.assertIn("github.com/cytoscape/cytoscape.js-navigator", readme)

    def test_index_html_carries_toggle_button(self) -> None:
        html = self._read_static("index.html")
        # Toggle lives inside .toolbar so it shares the same focus ring
        # chrome as the other toolbar buttons.
        toolbar_start = html.index('class="toolbar"')
        toolbar_end = html.index("</div>\n      </header>", toolbar_start)
        block = html[toolbar_start:toolbar_end]
        self.assertIn('id="minimap-toggle"', block)
        # aria-pressed exposes the toggle state to assistive tech and
        # starts as "false" because the default is closed.
        self.assertIn('aria-pressed="false"', block)
        # Title survives the icon swap (icons-test parity).
        self.assertIn('title="Toggle minimap"', block)

    def test_index_html_carries_minimap_container(self) -> None:
        html = self._read_static("index.html")
        # Container is hidden by default so the toggle owns visibility.
        self.assertIn('id="cy-minimap"', html)
        cm_start = html.index('id="cy-minimap"')
        cm_close = html.index(">", cm_start)
        cm_tag = html[cm_start:cm_close]
        self.assertIn("hidden", cm_tag)

    def test_index_html_loads_navigator_after_cytoscape(self) -> None:
        html = self._read_static("index.html")
        cy_at = html.index("/vendor/cytoscape-3.33.2.min.js")
        nav_at = html.index("/vendor/cytoscape-navigator-2.0.2.js")
        # navigator self-registers against window.cytoscape, so cytoscape
        # core must already be parsed by the time the navigator tag runs.
        self.assertGreater(nav_at, cy_at)
        # The CSS lives next to the JS so the panel chrome is consistent
        # whether or not the user opens it.
        self.assertIn("/vendor/cytoscape-navigator-2.0.2.css", html)

    def test_app_js_extends_hash_schema_with_minimap(self) -> None:
        js = self._read_static("app.js")
        # The boolean is appended to the existing HASH_KEYS list (h6z0.4
        # schema) -- no parallel persistence path.
        keys_start = js.index("const HASH_KEYS")
        keys_end = js.index("];", keys_start)
        keys_block = js[keys_start:keys_end]
        self.assertIn('"minimap"', keys_block)
        # Booleans are not in HASH_LIST_KEYS (which is for comma-joined
        # arrays); guard against accidental inclusion.
        list_start = js.index("HASH_LIST_KEYS = new Set(")
        list_end = js.index(")", list_start)
        self.assertNotIn("minimap", js[list_start:list_end])

    def test_app_js_persists_and_reads_minimap_flag(self) -> None:
        js = self._read_static("app.js")
        # Serialization is "1" when open, "" when closed -- so an
        # unmodified URL stays clean.
        self.assertIn('state.minimap ? "1" : ""', js)
        # Rehydration accepts "1" only; "0" / absent / empty => closed.
        self.assertIn('view.minimap === "1"', js)

    def test_app_js_wires_toggle_and_visibility_helper(self) -> None:
        js = self._read_static("app.js")
        # A single helper dispatches both init() and the toggle click so
        # state.minimap and DOM visibility never drift.
        self.assertIn("applyMinimapVisibility", js)
        # The cytoscape-navigator extension is invoked lazily off the
        # live state.cy instance (no module import needed).
        self.assertIn("state.cy.navigator", js)
        # Container must be passed as an id-selector string: upstream
        # _initPanel() falls through to its create-new-div branch when
        # the option is a DOM element, which would orphan our
        # #cy-minimap and inject a second panel on document.body.
        self.assertIn('container: "#cy-minimap"', js)
        # Toggle click flips state.minimap, applies, then persists.
        toggle_at = js.index('"minimap-toggle"')
        block = js[toggle_at: toggle_at + 240]
        self.assertIn("state.minimap = !state.minimap", block)
        self.assertIn("updateHash", block)

    def test_index_html_attaches_navigator_class_to_container(self) -> None:
        html = self._read_static("index.html")
        # Upstream child selectors (.cytoscape-navigator > canvas /
        # View / Overlay) only paint correctly when the parent carries
        # the `cytoscape-navigator` class -- that is all this class is
        # for. It does not win the size cascade: /styles.css loads
        # *before* the vendor CSS, so the 220x220 override that beats the
        # upstream 400x400 default is pinned on the `#cy-minimap` id
        # (specificity 1,0,0), which wins regardless of load order.
        cm_start = html.index('id="cy-minimap"')
        cm_close = html.index(">", cm_start)
        cm_tag = html[cm_start:cm_close]
        self.assertIn("cy-minimap", cm_tag)
        self.assertIn("cytoscape-navigator", cm_tag)

    def test_styles_css_defines_minimap_chrome(self) -> None:
        css = self._read_static("styles.css")
        # Active-state rule for the toggle keeps the icon readable as a
        # toggle (not just a momentary button).
        self.assertIn("#minimap-toggle.active", css)
        # Corner panel positioning + hidden fallback.
        self.assertIn(".cy-minimap", css)
        self.assertIn(".cy-minimap[hidden]", css)

    def test_minimap_right_offset_clears_the_inspector(self) -> None:
        # bd jko2 criterion 1: the fixed-position minimap must not paint
        # over the inspector column. The inspector is grid-column 3 at a
        # fixed 320px width; the minimap is position:fixed so a bare
        # right:12px would dock it 12px from the *viewport* edge, on top
        # of the inspector. The offset rule is keyed on the #cy-minimap id
        # (specificity 1,0,0) so it wins over the single-class vendor
        # `.cytoscape-navigator { right: 0 }` regardless of which sheet
        # loads last.
        css = self._read_static("styles.css")
        self.assertIn("#cy-minimap", css)
        body = _rule_body(css, "#cy-minimap {", contains="right")
        right = re.search(r"right:\s*([^;]+);", body)
        self.assertIsNotNone(right, "#cy-minimap must declare a right offset")
        offset_text = right.group(1).strip()
        # The offset must account for the full 320px inspector width plus a
        # gutter, so the 220px panel sits entirely left of the inspector's
        # left edge (inside the #cy canvas region). 320px alone only
        # reaches the inspector edge; require strictly more for the gutter.
        self.assertGreater(
            _px(offset_text),
            320,
            f"minimap right offset {offset_text!r} must clear the 320px inspector",
        )

    def test_minimap_returns_to_corner_when_inspector_stacks(self) -> None:
        # bd jko2 criterion 1 (narrow layout): below 920px the shell
        # collapses to a single column and the inspector stacks *below*
        # the canvas (grid-row 4), not to the right. The inspector-clearing
        # offset would then push the minimap off the left/bottom, so the
        # media query must reset #cy-minimap back to the 12px corner.
        css = self._read_static("styles.css")
        media_at = css.index("@media (max-width: 920px)")
        media_end = css.index("\n}\n", css.index("{", media_at))
        block = css[media_at:media_end]
        self.assertIn("#cy-minimap", block)
        body = _rule_body(block, "#cy-minimap {", contains="right")
        right = re.search(r"right:\s*([^;]+);", body)
        self.assertIsNotNone(right, "narrow layout must reset #cy-minimap right")
        # Back to a small corner gutter (<= 12px); the inspector no longer
        # occupies the right edge so no clearance is needed.
        self.assertLessEqual(_px(right.group(1).strip()), 12)

    def test_view_rectangle_is_clamped_to_the_panel(self) -> None:
        # bd jko2 criterion 2: the navigator view-rectangle
        # (.cytoscape-navigatorView) gets width/height set inline by the
        # vendor JS as panelDim / cyZoom * thumbnailZoom. When the graph is
        # zoomed out (small cyZoom) those values balloon past the panel, so
        # the rectangle reads as oversized. The vendor CSS sets no max
        # bound, so clamping max-width/max-height to 100% of the panel caps
        # the rendered box no matter what inline size the JS writes.
        css = self._read_static("styles.css")
        self.assertIn(".cytoscape-navigatorView", css)
        body = _rule_body(css, ".cytoscape-navigatorView")
        self.assertIn("max-width: 100%", body)
        self.assertIn("max-height: 100%", body)


if __name__ == "__main__":
    unittest.main()
