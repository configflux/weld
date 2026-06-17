"""Static-asset assertions for the placeholder-glyph -> SVG icon swap.

Tests in this module read the shipped HTML/CSS under ``weld/viz/static/``
and assert that the toolbar / inspector / banner buttons render inline
Lucide-MIT SVG icons in place of the old placeholder characters
(``[]``, ``@``, ``x``, ``+``, ``A``, ``B``, ``?``), while preserving
their title and ARIA hooks. Behavioral verification of the rendered UI
is covered by manual QA against ``wd viz``.

Split out of ``weld_viz_static_test.py`` to keep the parent file under
the 400-line default cap (see CLAUDE.md "Line-Count Policy"). Scope:
bd h6z0.3.
"""

from __future__ import annotations

import re
import unittest


class _StaticAssetTestBase(unittest.TestCase):
    """Shared helper to read packaged static assets via ``importlib``."""

    def _read_static(self, name: str) -> str:
        from importlib.resources import files

        return files("weld.viz").joinpath("static", name).read_text(encoding="utf-8")


class VizPlaceholderIconsTest(_StaticAssetTestBase):
    """Static-asset assertions for placeholder-glyph -> SVG icons (bd h6z0.3)."""

    # Buttons that used to carry literal placeholder characters. Each must
    # render an inline SVG icon while keeping its title attribute.
    _BUTTON_IDS = (
        "fit-button",
        "layout-button",
        "clear-button",
        "expand-button",
        "path-a-button",
        "path-b-button",
        "stale-banner-dismiss",
    )

    # Titles that must survive the icon swap (accessibility contract).
    _PRESERVED_TITLES = (
        'title="Search"',
        'title="Fit view"',
        'title="Run layout"',
        'title="Clear selection"',
        'title="Expand neighborhood"',
        'title="Set path start"',
        'title="Set path end"',
        'title="Dismiss for this session"',
    )

    def _button_body(self, html: str, button_id: str) -> str:
        """Return the inner markup of a button identified by its id."""
        start = html.index(f'id="{button_id}"')
        open_end = html.index(">", start) + 1
        close_start = html.index("</button>", open_end)
        return html[open_end:close_start]

    def test_each_placeholder_button_renders_inline_svg(self) -> None:
        html = self._read_static("index.html")
        for button_id in self._BUTTON_IDS:
            body = self._button_body(html, button_id)
            # Body holds inline SVG markup, not the old placeholder character.
            self.assertTrue(
                "<svg" in body or "<use " in body,
                f"{button_id} must render an inline SVG icon, got: {body!r}",
            )
            # Defensive: no placeholder glyph leaks back as direct text. We
            # tolerate the character inside SVG path data (e.g. "x" appears
            # in viewBox / d= attribute keywords), only the literal text
            # content of the button is forbidden.
            text = body
            while "<svg" in text:
                open_at = text.index("<svg")
                close_at = text.index("</svg>", open_at) + len("</svg>")
                text = text[:open_at] + text[close_at:]
            self.assertNotRegex(
                text.strip(),
                r"^[\[\]@x+AB?]$",
                f"{button_id} still has placeholder text outside svg: {text!r}",
            )

    def test_search_submit_renders_inline_svg(self) -> None:
        # The search submit button has no id; locate it via type="submit"
        # inside the search form.
        html = self._read_static("index.html")
        form_start = html.index('id="search-form"')
        form_end = html.index("</form>", form_start)
        form_block = html[form_start:form_end]
        # The submit button now wraps an inline SVG, not the "?" placeholder.
        submit_start = form_block.index('type="submit"')
        submit_close = form_block.index("</button>", submit_start)
        submit_body = form_block[submit_start:submit_close]
        self.assertTrue(
            "<svg" in submit_body or "<use " in submit_body,
            "search submit button must render an inline SVG",
        )

    def test_all_button_titles_preserved(self) -> None:
        html = self._read_static("index.html")
        for title in self._PRESERVED_TITLES:
            self.assertIn(
                title, html, f"accessibility regression: {title} dropped"
            )

    def test_stale_banner_dismiss_keeps_aria_label(self) -> None:
        html = self._read_static("index.html")
        # The dismiss button needs an aria-label fallback because its
        # SVG body has no rendered text.
        start = html.index('id="stale-banner-dismiss"')
        button_open = html.rindex("<button", 0, start)
        button_end = html.index(">", start) + 1
        button_tag = html[button_open:button_end]
        self.assertIn('aria-label="Dismiss"', button_tag)

    def test_no_external_icon_cdn(self) -> None:
        # Loopback-only discipline: no <script src=> / <link href=> /
        # <img src=> attributes may point off-host. The cytoscape vendor
        # file ships locally so the only allowed url() is /vendor/ or
        # relative. xmlns and HTML-comment provenance lines (e.g. the
        # Lucide attribution pointing at lucide.dev) are namespace /
        # documentation references, not runtime fetches, so they are
        # explicitly ignored here.
        html = self._read_static("index.html")
        stripped = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
        # Strip xmlns attribute namespace identifiers (never fetched).
        stripped = stripped.replace('xmlns="http://www.w3.org/2000/svg"', "")
        stripped = stripped.replace('xmlns="https://www.w3.org/2000/svg"', "")
        for attr in ('src="https://', 'src="http://', 'href="https://', 'href="http://'):
            self.assertNotIn(attr, stripped, f"loopback regression: {attr}")

    def test_lucide_attribution_present(self) -> None:
        # When inlining Lucide MIT icons, we must keep a one-line
        # attribution comment so the MIT license is satisfied without
        # needing a separate icons/README.md.
        html = self._read_static("index.html")
        self.assertIn("Lucide", html)
        self.assertIn("MIT", html)

    def test_styles_css_defines_icon_chrome(self) -> None:
        css = self._read_static("styles.css")
        # The hidden sprite container removes itself from layout, and
        # each icon ships at a constrained pixel size.
        self.assertIn(".icon-sprite", css)
        self.assertIn(".icon", css)


if __name__ == "__main__":
    unittest.main()
