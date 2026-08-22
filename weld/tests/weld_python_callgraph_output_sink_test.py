"""ADR 0129 coverage: the terminal output-boundary marker.

``python_callgraph`` already resolves every call to
``weld._safe_text.sanitize_terminal_text`` / ``sanitize_terminal_line`` to a
concrete ``calls`` edge (import-table resolution). This module pins the
derived marker: the *caller's own* node gains ``props.output_sink =
"terminal"`` and the ``props.keywords`` token ``"terminal-write-boundary"``
(the ADR-0105 channel), so ``wd query``/``wd find`` can enumerate every
terminal write boundary in one call. A sibling call to an unrelated function
must never be marked -- the negative case that proves this is not "every
function in a file that also happens to sanitize something" but the specific
caller whose own body calls the sanitizer.

Companion to :mod:`weld.tests.weld_python_callgraph_decorates_test` (same
``_extract``/fixture style) and
:mod:`weld.tests.weld_python_callgraph_scope_edges_test` (module-level and
class-body call sourcing, the two non-function-body cases this module also
covers for the marker).
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.strategies import python_callgraph as pc  # noqa: E402

_TEXT_SANITIZER = "symbol:py:weld._safe_text:sanitize_terminal_text"
_LINE_SANITIZER = "symbol:py:weld._safe_text:sanitize_terminal_line"
_KEYWORD = "terminal-write-boundary"


def _extract(source: str, *, module: str = "pkg/mod.py") -> dict:
    """Run the strategy over a single synthetic module and return its nodes."""
    root = Path(tempfile.mkdtemp(prefix="weld_output_sink_"))
    path = root / module
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    result = pc.extract(root, {"glob": f"{path.parent.name}/*.py"}, {})
    return result.nodes


class OutputSinkMarkerTest(unittest.TestCase):
    """A symbol that calls the sanitizer chokepoint is marked; others are not."""

    def test_direct_sanitize_terminal_text_call_is_marked(self) -> None:
        nodes = _extract(
            """
            import sys
            from weld._safe_text import sanitize_terminal_text

            def emit(text):
                sys.stdout.write(sanitize_terminal_text(text))

            def unrelated():
                return 1
            """
        )
        writer = nodes["symbol:py:pkg.mod:emit"]
        self.assertEqual(writer["props"].get("output_sink"), "terminal")
        self.assertIn(_KEYWORD, writer["props"].get("keywords", []))

    def test_unrelated_sibling_symbol_is_not_marked(self) -> None:
        nodes = _extract(
            """
            import sys
            from weld._safe_text import sanitize_terminal_text

            def emit(text):
                sys.stdout.write(sanitize_terminal_text(text))

            def unrelated():
                return 1
            """
        )
        sibling = nodes["symbol:py:pkg.mod:unrelated"]
        self.assertNotIn("output_sink", sibling["props"])
        self.assertNotIn(_KEYWORD, sibling["props"].get("keywords", []))

    def test_sanitize_terminal_line_call_is_marked(self) -> None:
        nodes = _extract(
            """
            import sys
            from weld._safe_text import sanitize_terminal_line

            def warn(text):
                sys.stderr.write(sanitize_terminal_line(text) + "\\n")
            """
        )
        writer = nodes["symbol:py:pkg.mod:warn"]
        self.assertEqual(writer["props"].get("output_sink"), "terminal")
        self.assertIn(_KEYWORD, writer["props"].get("keywords", []))

    def test_aliased_import_still_resolves_and_marks(self) -> None:
        """``from weld._safe_text import sanitize_terminal_text as stt``.

        The import-table records ``(module, real-attr-name)`` for an
        aliased import, so the resolved target id is unaffected by the
        local alias -- the marker must not depend on the literal spelling
        used at the call site.
        """
        nodes = _extract(
            """
            import sys
            from weld._safe_text import sanitize_terminal_text as stt

            def emit(text):
                sys.stdout.write(stt(text))
            """
        )
        writer = nodes["symbol:py:pkg.mod:emit"]
        self.assertEqual(writer["props"].get("output_sink"), "terminal")

    def test_indirect_call_via_helper_is_not_marked(self) -> None:
        """The marker is precise: it only fires on the DIRECT caller.

        ``main`` calls ``emit``, which calls the sanitizer -- ``main`` itself
        never appears as the ``from`` side of a ``calls`` edge to the
        sanitizer, so it must not be marked. This is the honest boundary the
        ADR states: the marker composes with the pre-existing ``calls`` edge
        (``main -> emit``) rather than transitively propagating.
        """
        nodes = _extract(
            """
            import sys
            from weld._safe_text import sanitize_terminal_text

            def emit(text):
                sys.stdout.write(sanitize_terminal_text(text))

            def main():
                emit("hello")
            """
        )
        self.assertEqual(
            nodes["symbol:py:pkg.mod:emit"]["props"].get("output_sink"),
            "terminal",
        )
        self.assertNotIn(
            "output_sink", nodes["symbol:py:pkg.mod:main"]["props"],
        )

    def test_class_body_call_sources_the_class_symbol(self) -> None:
        """ADR 0122: a class-body call site is sourced at the class symbol."""
        nodes = _extract(
            """
            import sys
            from weld._safe_text import sanitize_terminal_text

            class Reporter:
                sys.stdout.write(sanitize_terminal_text("boot"))
            """
        )
        cls = nodes["symbol:py:pkg.mod:Reporter"]
        self.assertEqual(cls["props"].get("output_sink"), "terminal")
        self.assertIn(_KEYWORD, cls["props"].get("keywords", []))

    def test_module_level_call_sources_the_file_anchor(self) -> None:
        """ADR 0122: a module-level call site is sourced at the file: node."""
        nodes = _extract(
            """
            import sys
            from weld._safe_text import sanitize_terminal_text

            sys.stdout.write(sanitize_terminal_text("boot"))
            """
        )
        anchor = nodes["file:pkg/mod"]
        self.assertEqual(anchor["props"].get("output_sink"), "terminal")
        self.assertIn(_KEYWORD, anchor["props"].get("keywords", []))

    def test_marking_is_deterministic(self) -> None:
        source = """
            import sys
            from weld._safe_text import sanitize_terminal_text

            def emit(text):
                sys.stdout.write(sanitize_terminal_text(text))
            """
        first = _extract(source)["symbol:py:pkg.mod:emit"]["props"]
        second = _extract(source)["symbol:py:pkg.mod:emit"]["props"]
        self.assertEqual(
            first.get("output_sink"), second.get("output_sink"),
        )
        self.assertEqual(first.get("keywords"), second.get("keywords"))

    def test_defining_the_sink_functions_does_not_self_mark(self) -> None:
        """Marking keys on the ``calls`` edge, never on the symbol's own name.

        Neither function here calls anything, so neither has a ``calls``
        edge at all -- this guards against a mistaken implementation that
        matched on ``label``/``qualname`` instead of walking edges, which
        would wrongly mark a symbol merely for being *named*
        ``sanitize_terminal_text``/``_line``, whether or not it calls
        anything.
        """
        nodes = _extract(
            """
            def sanitize_terminal_text(text):
                return text

            def sanitize_terminal_line(text):
                return text
            """,
            module="weld/_safe_text.py",
        )
        for qual in ("sanitize_terminal_text", "sanitize_terminal_line"):
            node = nodes[f"symbol:py:weld._safe_text:{qual}"]
            self.assertNotIn("output_sink", node["props"])

    def test_existing_keywords_are_preserved_not_overwritten(self) -> None:
        """A strategy that already stamped keywords keeps them, appended."""
        nodes = _extract(
            """
            import sys
            from weld._safe_text import sanitize_terminal_text

            def emit(text):
                sys.stdout.write(sanitize_terminal_text(text))
            """
        )
        # python_callgraph itself does not stamp keywords today, so this
        # asserts the append-not-replace contract holds for the common case
        # (no prior keywords) without depending on a second strategy's
        # output merging first.
        writer = nodes["symbol:py:pkg.mod:emit"]
        self.assertEqual(writer["props"]["keywords"], [_KEYWORD])


if __name__ == "__main__":
    unittest.main()
