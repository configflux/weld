"""``wd stale`` says there is no graph, instead of answering about one (bd 0nqy).

Every other field :func:`weld._staleness.compute_stale_info` returns is a
comparison *against* a recorded basis. With no graph there is no basis and no
comparison, but the arithmetic still produced answers: ``sha_behind`` computed
``False`` and ``commits_behind`` computed the ``-1`` "unknown" sentinel. Read
off the CLI those two lines say "not behind" about a graph that does not
exist -- the reassuring shape, in the one state that most needs a rebuild.

Worse, they are the *same* two values a graph that does exist without a
recorded ``git_sha`` produces, and the two states want opposite remedies: a
missing graph needs ``wd discover``, a basis-less graph needs only ``wd
touch`` (or an ADR 0096 Mode B sidecar synthesis). Nothing in the payload told
them apart. ``reason`` does, and it is the key the non-git-root branch has
always used for exactly this -- "here is why there is no freshness answer".

The numeric shape is deliberately untouched: ``commits_behind == -1`` is the
established unknown sentinel that :func:`weld.warnings.check_freshness`
branches on and :mod:`weld._mcp_read` documents, so the missing fact is stated
additively rather than by retyping a field every consumer already reads.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld._staleness import compute_stale_info


def _git_repo(root: Path) -> None:
    """Initialize a git repo with one commit, quietly and hermetically."""
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
    }
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    (root / "a.py").write_text("X = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "init"],
        cwd=root, check=True, env={**env, "PATH": "/usr/bin:/bin"},
    )


class StaleWithNoGraphTest(unittest.TestCase):
    """A root that has never held a graph."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        _git_repo(self.root)
        (self.root / ".weld").mkdir()
        self.graph_path = self.root / ".weld" / "graph.json"

    def test_the_payload_says_there_is_no_graph(self) -> None:
        info = compute_stale_info(self.graph_path, {})
        self.assertEqual("no graph", info["reason"])

    def test_it_still_reports_stale(self) -> None:
        """The remedy has not changed, only the explanation."""
        info = compute_stale_info(self.graph_path, {})
        self.assertTrue(info["stale"])
        self.assertTrue(info["source_stale"])

    def test_a_basis_less_graph_is_distinguishable(self) -> None:
        """The conflation this fixes: same numbers, different remedy.

        A graph on disk with no recorded ``git_sha`` reports the identical
        ``sha_behind`` / ``commits_behind`` / ``graph_sha`` triple, so
        ``reason`` is the only thing that separates "run discover" from
        "this graph just has no basis recorded".
        """
        self.graph_path.write_text(
            '{"meta": {}, "nodes": {}, "edges": []}', encoding="utf-8",
        )
        present = compute_stale_info(self.graph_path, {})

        self.graph_path.unlink()
        absent = compute_stale_info(self.graph_path, {})

        for key in ("sha_behind", "commits_behind", "graph_sha"):
            self.assertEqual(
                present[key], absent[key],
                f"{key} cannot tell the two states apart; that is why "
                f"'reason' has to",
            )
        self.assertNotIn("reason", present)
        self.assertEqual("no graph", absent["reason"])

    def test_a_recorded_basis_outranks_the_missing_file(self) -> None:
        """A caller holding a basis has a graph, wherever its body is.

        A library caller with a graph in memory, or a run whose body went to
        ``--output`` elsewhere, passes a real ``git_sha`` for a path that
        holds nothing. Answering "no graph" there would throw away the basis
        it just handed over.
        """
        info = compute_stale_info(
            self.graph_path, {"git_sha": "0" * 40, "discovered_from": ["a.py"]},
        )
        self.assertNotIn("reason", info)

    def test_the_sentinel_contract_is_unchanged(self) -> None:
        """Consumers keying on the numeric shape must not have to change."""
        info = compute_stale_info(self.graph_path, {})
        self.assertEqual(-1, info["commits_behind"])
        self.assertIs(False, info["sha_behind"])
        self.assertIsNone(info["graph_sha"])

    def test_a_non_git_root_keeps_its_own_reason(self) -> None:
        """Ordering: the git probe still answers first.

        A non-git root has no freshness answer whether or not a graph is
        present, and its long-standing ``stale=False`` shape is not this
        fix's to flip.
        """
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp)
            (plain / ".weld").mkdir()
            info = compute_stale_info(plain / ".weld" / "graph.json", {})
            self.assertEqual("not a git repo", info["reason"])
            self.assertFalse(info["stale"])


if __name__ == "__main__":
    unittest.main()
