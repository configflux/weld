"""Static-asset assertions for the inspector CSS grid track mapping.

The ``.inspector`` aside is a CSS grid. It always renders four visible
children -- ``.inspector-head``, ``#inspect-tabs``, a body panel
(``#inspect-body`` or ``#inspect-changes``, swapped via the ``hidden``
attribute), and ``.inspect-actions``. ``#inspect-changes`` and
``#trace-tray`` ship ``hidden`` and resolve to ``display: none`` until
used, so they hold no grid track.

The stretch row (``minmax(0, 1fr)``) must bind to the *body* panel so it
fills and scrolls while ``#inspect-tabs`` keeps its natural height. The
grid therefore needs four tracks -- ``auto auto minmax(0, 1fr) auto`` --
placing the 1fr on the third track where the body panel auto-places. A
three-track declaration binds the 1fr to ``#inspect-tabs`` instead,
collapsing the tab strip and overlapping the body so a real click on the
Changes tab lands on ``#inspect-body`` (bd yski).

Split into its own file per the cohesive viz-feature test pattern (see
``weld_viz_layout_test``); keeps every file under the 400-line cap.
"""

from __future__ import annotations

import re
import unittest
from importlib.resources import files


def _read_static(name: str) -> str:
    return files("weld.viz").joinpath("static", name).read_text(encoding="utf-8")


def _rule_body(css: str, selector: str, *, contains: str = "") -> str:
    """Return the declaration block for a ``selector { ... }`` rule.

    ``selector`` may appear more than once (the inspector has a shared
    ``.rail, .inspector`` chrome rule and a separate grid rule, plus a
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


class VizInspectorGridTest(unittest.TestCase):
    """Grid-track invariants for the inspector aside (bd yski)."""

    def test_inspector_declares_four_track_grid(self) -> None:
        css = _read_static("styles.css")
        # Disambiguate from the shared ``.rail, .inspector`` chrome rule by
        # selecting the block that actually declares the row tracks.
        body = _rule_body(css, ".inspector {", contains="grid-template-rows")
        # Display:grid with four explicit rows so the stretch row lands on
        # the body panel, not the tab strip.
        self.assertIn("display: grid", body)
        rows = re.search(r"grid-template-rows:\s*([^;]+);", body)
        self.assertIsNotNone(rows, "inspector must declare grid-template-rows")
        track_text = rows.group(1).strip()
        # Exactly four tracks: head (auto), tabs (auto), body (1fr), actions
        # (auto). Collapse the minmax(...) so the comma inside it does not
        # split a track.
        normalized = re.sub(r"minmax\([^)]*\)", "STRETCH", track_text)
        tracks = normalized.split()
        self.assertEqual(
            tracks,
            ["auto", "auto", "STRETCH", "auto"],
            f"expected 4-track auto/auto/1fr/auto, got {track_text!r}",
        )
        # The stretch row must be the body's track (3rd), bounded by
        # minmax(0, ...) so overflow scrolls rather than blowing out height.
        self.assertIn("minmax(0,", track_text.replace(" ", ""))

    def test_inspector_has_four_always_visible_children_in_order(self) -> None:
        html = _read_static("index.html")
        start = html.index('id="inspector"')
        block = html[start : html.index("</aside>", start)]
        # The always-visible grid children appear head -> tabs -> body ->
        # actions. The two on-demand panels sit between body and actions and
        # ship hidden, so they do not occupy a track at rest.
        order = [
            'class="inspector-head"',
            'id="inspect-tabs"',
            'id="inspect-body"',
            'id="inspect-changes"',
            'id="trace-tray"',
            'class="inspect-actions"',
        ]
        positions = [block.index(marker) for marker in order]
        self.assertEqual(
            positions,
            sorted(positions),
            "inspector children must stay head/tabs/body/changes/tray/actions",
        )
        # The changes panel and trace tray are hidden at rest.
        changes = block[block.index('id="inspect-changes"') :]
        self.assertIn("hidden", changes[: changes.index(">")])
        tray = block[block.index('id="trace-tray"') :]
        self.assertIn("hidden", tray[: tray.index(">")])
        # inspect-body itself is NOT hidden at rest (it holds the 1fr row).
        body = block[block.index('id="inspect-body"') :]
        self.assertNotIn("hidden", body[: body.index(">")])

    def test_hidden_panels_resolve_to_display_none(self) -> None:
        css = _read_static("styles.css")
        # The hidden body-change panel and trace tray must collapse to
        # display:none so they hold no grid track; otherwise the four-track
        # mapping would shift when they ship hidden.
        self.assertIn("display: none", _rule_body(css, ".trace-tray[hidden]"))
        # inspect-changes ships hidden via the global [hidden] panel rule or
        # its own; the browser default for [hidden] is display:none, and the
        # inspect-body base must scroll the 1fr row it occupies.
        self.assertIn("overflow: auto", _rule_body(css, ".inspect-body {"))


if __name__ == "__main__":
    unittest.main()
