"""Static-asset assertions for viz session history (bd h6z0.11).

Tests in this module read the shipped HTML/JS/CSS under
``weld/viz/static/`` and assert presence of the session-history hooks:
toolbar back/forward buttons, ``history.pushState`` writes through the
single ``updateHash`` dispatch point, a window-scoped ``popstate``
listener, and a rehydrate path that reads ``location.hash`` and
re-runs ``loadSlice()``.

Carved out of ``weld_viz_static_test.py`` to keep both files under the
400-line default cap (see CLAUDE.md "Line-Count Policy").
"""

from __future__ import annotations

import unittest


class _StaticAssetTestBase(unittest.TestCase):
    """Shared helper to read packaged static assets via ``importlib``."""

    def _read_static(self, name: str) -> str:
        from importlib.resources import files

        return files("weld.viz").joinpath("static", name).read_text(encoding="utf-8")


class VizSessionHistoryTest(_StaticAssetTestBase):
    """Static-asset assertions for browser session history (bd h6z0.11)."""

    def test_index_html_carries_toolbar_back_forward_buttons(self) -> None:
        html = self._read_static("index.html")
        # Visible back/forward buttons mirror the browser stack so the
        # affordance survives iframe embeds where browser chrome hides.
        # Both buttons sit inside the existing .toolbar block.
        toolbar_start = html.index('class="toolbar"')
        toolbar_end = html.index("</div>", toolbar_start)
        toolbar_block = html[toolbar_start:toolbar_end]
        self.assertIn('id="history-back-button"', toolbar_block)
        self.assertIn('id="history-forward-button"', toolbar_block)

    def test_index_html_carries_arrow_icon_sprites(self) -> None:
        html = self._read_static("index.html")
        # New arrow icons are inlined alongside the existing Lucide
        # sprite block so the toolbar back/forward buttons render
        # without a CDN.
        self.assertIn('id="icon-arrow-left"', html)
        self.assertIn('id="icon-arrow-right"', html)

    def test_app_js_commits_hash_state_via_push_state(self) -> None:
        js = self._read_static("app.js")
        # The single hash-write dispatch point uses pushState so back/
        # forward walk through the recorded view-state stack. The init
        # rehydrate still uses replaceState so the first paint does not
        # land an extra history entry the user cannot pop out of.
        self.assertIn("history.pushState", js)
        self.assertIn("history.replaceState", js)

    def test_app_js_update_hash_uses_push_state(self) -> None:
        js = self._read_static("app.js")
        # Confirm the dispatch happens inside updateHash() itself, not
        # via a side path. Single hash-write surface is the acceptance
        # criterion -- every loadSlice / filter / pill / layout /
        # minimap commit funnels here.
        start = js.index("function updateHash()")
        end = js.index("\n}", start)
        body = js[start:end]
        self.assertIn("history.pushState", body)
        # The guards that skip no-op writes must stay in place so that
        # rapid filter debounces do not flood the history stack with
        # duplicate entries.
        self.assertIn("suppressHashUpdate", body)
        self.assertIn("window.location.hash === hash", body)

    def test_app_js_registers_popstate_handler(self) -> None:
        js = self._read_static("app.js")
        # popstate is wired window-scope so back/forward in the browser
        # (and the toolbar buttons that delegate to history.back/forward)
        # rehydrate the view from the active location.hash entry.
        self.assertIn('"popstate"', js)
        self.assertIn("addEventListener", js)

    def test_app_js_popstate_rehydrates_view_state(self) -> None:
        js = self._read_static("app.js")
        # The popstate handler delegates to rehydrateFromHash, which is
        # the single dispatch point that re-parses the active hash,
        # pushes the parsed view onto DOM controls, and re-runs
        # loadSlice() so the canvas matches.
        self.assertIn("rehydrateFromHash", js)
        start = js.index("function rehydrateFromHash")
        end = js.index("\n}", start)
        body = js[start:end]
        self.assertIn("window.location.hash", body)
        self.assertIn("applyViewState", body)
        self.assertIn("loadSlice", body)
        # The rehydrate must suppress the inverse hash write so the
        # popstate-driven restore does not push a brand-new entry on
        # top of the one the user just popped to.
        self.assertIn("suppressHashUpdate", body)

    def test_app_js_wires_toolbar_back_forward_to_history(self) -> None:
        js = self._read_static("app.js")
        # The toolbar back/forward buttons delegate to history.back /
        # history.forward, the same calls the "[" / "]" shortcuts use.
        # Asserting both ids appear in bindEvents() proves they were
        # not left unwired.
        bind_start = js.index("function bindEvents()")
        bind_end = js.index("\n}", bind_start)
        bind_body = js[bind_start:bind_end]
        self.assertIn("history-back-button", bind_body)
        self.assertIn("history-forward-button", bind_body)
        # Both buttons reuse history.back / history.forward (the same
        # calls bd h6z0.16 "[" / "]" use) so the dispatch surface
        # stays single.
        self.assertIn("history.back", bind_body)
        self.assertIn("history.forward", bind_body)

    def test_app_js_init_seeds_initial_entry_via_replace_state(self) -> None:
        js = self._read_static("app.js")
        # init() seeds the very first history entry with replaceState
        # so popstate has a canonical target to land on. Pushing on
        # init would leave an extra entry the user cannot pop out of.
        start = js.index("async function init()")
        end = js.index("\nasync function ", start + 1)
        body = js[start:end]
        self.assertIn("history.replaceState", body)


if __name__ == "__main__":
    unittest.main()
