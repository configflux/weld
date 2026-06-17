"""Static-asset assertions for the viz keyboard shortcuts (bd h6z0.16).

Tests in this module read the shipped HTML/JS/CSS under
``weld/viz/static/`` and assert presence of the keyboard-shortcut
hooks: a window-scoped keydown listener, a focus guard for editable
surfaces, references to every shortcut character, reuse of the
existing ``setPathEndpoint`` setters, browser history navigation, and
a cheatsheet modal.

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


class VizKeyboardShortcutsTest(_StaticAssetTestBase):
    """Static-asset assertions for keyboard shortcuts (bd h6z0.16)."""

    def test_index_html_carries_cheatsheet_modal(self) -> None:
        html = self._read_static("index.html")
        # The cheatsheet modal lives at the top level of the body so it
        # can overlay the shell. Ships hidden so the canvas is the
        # default first paint.
        self.assertIn('id="cheatsheet"', html)
        cheat_start = html.index('id="cheatsheet"')
        # The modal root must be hidden by default; the `hidden`
        # attribute appears within the opening tag.
        modal_open = html[cheat_start:cheat_start + 400]
        self.assertIn("hidden", modal_open)
        # A dismiss control exists so the modal can be closed with the
        # mouse, not just the keyboard.
        self.assertIn('id="cheatsheet-close"', html)
        # Lists every shortcut. Match the literal keys wrapped in any
        # tag so the cheatsheet stays in lockstep with the handler.
        cheat_block = html[cheat_start:html.index("</body>")]
        for key in ("/", "f", "l", "a", "b", "[", "]", "?"):
            self.assertIn(f">{key}<", cheat_block, f"cheatsheet missing key {key}")

    def test_app_js_binds_window_keydown(self) -> None:
        js = self._read_static("app.js")
        # A window-scoped keydown listener is the single dispatch point.
        self.assertIn('addEventListener("keydown"', js)

    def test_app_js_handler_guards_input_focus(self) -> None:
        js = self._read_static("app.js")
        # The handler must guard against typing into an input/textarea.
        self.assertIn("tagName", js)
        for tag in ("INPUT", "TEXTAREA", "SELECT"):
            self.assertIn(tag, js, f"keydown guard missing {tag}")

    def test_app_js_references_every_shortcut_key(self) -> None:
        js = self._read_static("app.js")
        # Every shortcut character from the acceptance criteria must be
        # referenced. Match the literal quoted character to keep it strict.
        for key in ('"/"', '"f"', '"l"', '"a"', '"b"', '"["', '"]"', '"?"'):
            self.assertIn(key, js, f"shortcut handler missing key {key}")
        # Escape closes the cheatsheet modal.
        self.assertIn("Escape", js)

    def test_app_js_slash_always_focuses_search(self) -> None:
        js = self._read_static("app.js")
        # "/" must focus the search input and preventDefault so the
        # literal "/" character does not land in the input that just
        # received focus.
        self.assertIn("search-input", js)
        self.assertIn("preventDefault", js)
        self.assertIn(".focus()", js)

    def test_app_js_ab_shortcuts_reuse_set_path_endpoint(self) -> None:
        js = self._read_static("app.js")
        # The a/b shortcuts must reuse the existing setPathEndpoint
        # function added by h6z0.6 rather than duplicating endpoint
        # logic. Locate the keydown handler and confirm both endpoints
        # flow through that helper.
        start = js.index('addEventListener("keydown"')
        end = js.index("\n});", start)
        handler = js[start:end]
        self.assertIn('setPathEndpoint("A")', handler)
        self.assertIn('setPathEndpoint("B")', handler)

    def test_app_js_bracket_shortcuts_call_history_navigation(self) -> None:
        js = self._read_static("app.js")
        # "[" and "]" delegate to the browser's history API. h6z0.11
        # will switch hash commits to pushState; until then this is a
        # no-op walk through whatever history entries exist.
        self.assertIn("history.back()", js)
        self.assertIn("history.forward()", js)

    def test_app_js_question_mark_toggles_cheatsheet(self) -> None:
        js = self._read_static("app.js")
        # "?" opens the cheatsheet modal; Escape (or the close button)
        # closes it. The implementation toggles the `hidden` attribute
        # on the modal root via a named helper.
        self.assertIn("cheatsheet", js)
        self.assertIn("toggleCheatsheet", js)

    def test_styles_css_defines_cheatsheet_modal(self) -> None:
        css = self._read_static("styles.css")
        # Modal chrome (overlay + dialog) must have its own rules so it
        # paints on top of the shell.
        self.assertIn(".cheatsheet", css)


if __name__ == "__main__":
    unittest.main()
