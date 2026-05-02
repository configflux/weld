"""Bare-slash-command (`_BARE_COMMAND_RE`) behavior tests.

Split out of weld_agent_graph_inferred_refs_test.py (ukk8) so the parent
file stays under the 400-line cap. Cohesion: every test here exercises the
bare-`/command` extraction in weld/agent_graph_metadata_utils.py --
known_commands filtering, lookbehind-based path rejection, and the
terminator class extended in ukk8 (!, ?, ], }).
"""

from __future__ import annotations

import sys
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.agent_graph_metadata_utils import (  # noqa: E402
    extract_inferred_references,
)


class BareSlashCommandExtractionTest(unittest.TestCase):
    """Unit tests for the bare-`/command` regex inside extract_inferred_references."""

    def test_filtered_by_known_commands(self) -> None:
        text = textwrap.dedent(
            """\
            Run /plan first, then /tmp/foo for tempfiles.
            Use /cycle for parallel work, but /path/to/repo is just a path.
            Trailing /push.
            """
        )
        refs = extract_inferred_references(
            text, start_line=1, known_commands=frozenset({"plan", "cycle", "push"})
        )
        commands = sorted({r.target_name for r in refs if r.edge_type == "uses_command"})
        # Only the names actually in known_commands; /tmp and /path are paths
        # and rejected by the negative lookbehind anyway, but the filter is
        # the load-bearing line.
        self.assertEqual(commands, ["cycle", "plan", "push"])
        for r in refs:
            if r.edge_type == "uses_command":
                self.assertEqual(r.confidence, "inferred")
                self.assertEqual(r.target_type, "command")

    def test_dropped_when_known_commands_none(self) -> None:
        # When known_commands is None we cannot safely emit any /command edges
        # because we would not be able to distinguish /tmp/foo from /plan.
        text = "Run /plan and /cycle.\n"
        refs = extract_inferred_references(text, start_line=1, known_commands=None)
        self.assertEqual([r for r in refs if r.edge_type == "uses_command"], [])

    def test_dropped_when_known_commands_empty(self) -> None:
        text = "Run /plan and /cycle.\n"
        refs = extract_inferred_references(
            text, start_line=1, known_commands=frozenset()
        )
        self.assertEqual([r for r in refs if r.edge_type == "uses_command"], [])

    def test_rejects_path_lookalikes(self) -> None:
        # /tmp/foo, /usr/local, https://x have a leading word-char or another
        # slash; the negative lookbehind keeps them out even if we whitelisted
        # "tmp" or "usr" by accident.
        text = "Path /tmp/foo and url https://example/cycle and weird /a/b/cycle.\n"
        refs = extract_inferred_references(
            text, start_line=1, known_commands=frozenset({"cycle", "tmp", "usr"})
        )
        commands = [r.target_name for r in refs if r.edge_type == "uses_command"]
        # None of these positions match the bare slash lookbehind.
        self.assertEqual(commands, [])

    def test_extended_terminators(self) -> None:
        # ukk8: !, ?, ], } terminate the command name in prose. One positive
        # case per added terminator + one negative that URL-path lookalikes
        # are still rejected by the negative lookbehind.
        for label, text, expected in [
            ("exclamation", "Try /push!\n", "push"),
            ("question", "Should I /execute?\n", "execute"),
            ("close_bracket", "See [/plan](url)\n", "plan"),
            ("close_brace", "Use `/cycle` for work}\n", "cycle"),
        ]:
            with self.subTest(terminator=label):
                refs = extract_inferred_references(
                    text, start_line=1, known_commands=frozenset({expected})
                )
                names = [r.target_name for r in refs if r.edge_type == "uses_command"]
                self.assertEqual(names, [expected])
        neg = "GET https://example.com/api/v1! and path/to/v1? plus dir/etc].\n"
        refs = extract_inferred_references(
            neg, start_line=1, known_commands=frozenset({"api", "v1", "etc", "to"})
        )
        self.assertEqual(
            [r.target_name for r in refs if r.edge_type == "uses_command"], []
        )


if __name__ == "__main__":
    unittest.main()
