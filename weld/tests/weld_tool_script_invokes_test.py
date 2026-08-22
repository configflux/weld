"""``tool_script`` declares what each script runs (bd x5ec).

The gap: ``weld_impact`` on ``tools/release_mcp_handshake.py`` returned 71
transitive dependents and not one of them was the shell script that runs it,
so every release tool in this repo looked like an orphan with no runtime
caller and "who runs this" had to be answered with grep.

Split from ``weld_tool_script_strategy_test`` when the combined file crossed
the 400-line cap. The subjects were already distinct: that file is about what
a ``tool:`` node *is* (id shape, collisions, language, provenance); this one
is about the edges a script declares, which is a claim about control flow and
carries its own honesty rules. The evidence rule those rules rest on has its
own tests in ``weld_shell_refs_test``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.tool_script import extract


def _touch(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestToolScriptInvokes(unittest.TestCase):
    """A script declares the scripts it names in its body (bd x5ec).

    The gap: ``weld_impact`` on a release tool returned 71 transitive
    dependents and not one of them was the shell script that runs it, so
    every release tool looked like an orphan with no runtime caller.
    """

    def _edges_to(self, result: StrategyResult, prefix: str) -> list[dict]:
        return [e for e in result.edges if e["to"].startswith(prefix)]

    def test_invocation_becomes_an_inferred_invokes_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "helper.py", "x = 1\n")
            _touch(
                root / "tools" / "run.sh",
                '#!/bin/sh\npython3 tools/helper.py "$@"\n',
            )
            result = extract(root, {"glob": "tools/**/*.sh"}, {})
            edges = [e for e in result.edges if e["type"] == "invokes"]
            self.assertTrue(edges)
            self.assertEqual({e["from"] for e in edges}, {"tool:tools/run"})
            self.assertIn("file:tools/helper", {e["to"] for e in edges})
            for edge in edges:
                self.assertEqual(edge["props"]["confidence"], "inferred")
                self.assertEqual(edge["props"]["source_strategy"], "tool_script")

    def test_variable_indirection_is_followed(self) -> None:
        # The exact shape the gap was filed against: the path is bound to a
        # variable and invoked three lines later, so a command-position rule
        # would read the formatting of an invocation and miss this.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "handshake.py", "x = 1\n")
            _touch(
                root / "tools" / "smoke.sh",
                '#!/bin/sh\n'
                'handshake="${SCRIPT_DIR}/handshake.py"\n'
                '"${venv_python}" "${handshake}"\n',
            )
            result = extract(root, {"glob": "tools/**/*.sh"}, {})
            self.assertIn(
                "file:tools/handshake",
                {e["to"] for e in result.edges if e["type"] == "invokes"},
            )

    def test_a_commented_mention_is_not_an_invocation(self) -> None:
        # 163 of the 371 path-like words in this repo's scripts sit in
        # comments. Admitting them makes ``invokes`` mean "mentions" and
        # inflates every blast radius that joins through it.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "helper.py", "x = 1\n")
            _touch(
                root / "tools" / "run.sh",
                "#!/bin/sh\n# superseded by tools/helper.py\necho hi\n",
            )
            result = extract(root, {"glob": "tools/**/*.sh"}, {})
            self.assertEqual(
                [e for e in result.edges if e["type"] == "invokes"], []
            )

    def test_a_path_that_does_not_exist_yields_nothing(self) -> None:
        # Shell *tests* fabricate paths under temp roots. Requiring the
        # referent to exist in the worktree is what keeps a fixture from
        # claiming an invocation.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "run.sh", '#!/bin/sh\n"${WORK}/pkg/a.py"\n')
            result = extract(root, {"glob": "tools/**/*.sh"}, {})
            self.assertEqual(
                [e for e in result.edges if e["type"] == "invokes"], []
            )

    def test_a_script_never_invokes_itself(self) -> None:
        # Scripts name themselves in usage and error strings.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(
                root / "tools" / "run.sh",
                '#!/bin/sh\n: "${VENV:?run.sh: VENV not set}"\n',
            )
            result = extract(root, {"glob": "tools/**/*.sh"}, {})
            self.assertEqual(
                [e for e in result.edges if e["type"] == "invokes"], []
            )

    def test_every_plausible_spelling_of_the_referent_is_offered(self) -> None:
        # The referrer contract: the strategy that minted the target chose
        # its id class, so a referrer offers each candidate and lets the
        # dangling-edge sweep keep the one that resolved. A shell referent
        # must therefore carry the ``tool:`` spelling (bd mdvp).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "inner.sh", "#!/bin/sh\n")
            _touch(root / "tools" / "outer.sh", "#!/bin/sh\ntools/inner.sh\n")
            result = extract(root, {"glob": "tools/**/*.sh"}, {})
            targets = {
                e["to"] for e in result.edges
                if e["type"] == "invokes" and e["from"] == "tool:tools/outer"
            }
            self.assertIn("tool:tools/inner", targets)
            self.assertIn("config:tools_inner_sh", targets)

    def test_edge_order_is_a_property_of_the_tree(self) -> None:
        # ADR 0012 §3 canonical output: two runs over the same tree must
        # emit the same bytes in the same order.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "a.py", "x = 1\n")
            _touch(root / "tools" / "b.py", "x = 1\n")
            _touch(
                root / "tools" / "run.sh",
                "#!/bin/sh\npython3 tools/b.py\npython3 tools/a.py\n",
            )
            first = extract(root, {"glob": "tools/**/*.sh"}, {})
            second = extract(root, {"glob": "tools/**/*.sh"}, {})
            self.assertEqual(first.edges, second.edges)
            self.assertEqual(
                [e["to"] for e in first.edges if e["to"].startswith("file:")],
                ["file:tools/a", "file:tools/b"],
            )



if __name__ == "__main__":  # pragma: no cover
    unittest.main()
