"""``find`` refuses an index that does not exist (ADR 0134, N9).

Field eval v0.24.0's ninth finding is v0.23.1's second one wearing a
different artifact. ``wd query`` and ``wd brief`` learned to say "No Weld
graph found." and exit 1 where they cannot answer; ``wd find``, one keystroke
away in the same graph-less checkout, printed ``no matches`` and exited 0 --
from a ``.weld/file-index.json`` that had never been written. An agent
reading that cannot tell "this term is not in the tree" from "weld has
nothing to search", and the second reads as the first.

The exemption that produced it was not wrong, only over-broad: ``find``
answers off the file index, so a missing *graph* really is no obstacle to
it, and a user may build an index with ``wd build-index`` and never run
discovery. ADR 0134's rule applies to the artifact a command reads, and this
suite pins that rule for both of ``find``'s routes and both of its surfaces:

* absent index -- single-repo, and at a federation root where neither the
  root nor any child has one -- is a cannot-answer: ``error_code``
  ``file_index_missing``, a remediation, and a non-zero exit;
* an index that exists and matches nothing is a **real** negative answer and
  stays ``no matches`` at exit 0. Turning that into an error would train the
  agent to ignore the very signal this adds;
* the refusal is a refusal, not a repair: ``find`` never builds an index, so
  a read cannot silently start paying for a full discovery pass;
* CLI and MCP answer from one payload rather than two spellings of it.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from weld import mcp_server
from weld._errors import ERROR_HINTS, FILE_INDEX_MISSING
from weld._find_precondition import cannot_answer_block, missing_file_index_payload
from weld._graph_cli import main as graph_cli_main
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml

#: The artifact under test, relative to a repository root.
_INDEX_REL = Path(".weld") / "file-index.json"

#: What an agent scrapes stderr for. Spelled as the rendered prefix rather
#: than the bare code so the test fails if the shared one-line contract stops
#: being used here.
_ERROR_PREFIX = f"error[{FILE_INDEX_MISSING}]:"

#: The retry hint the MCP tool hands back is the tool name, not a ``wd``
#: command; parity is claimed about the payload, so both surfaces are
#: compared while holding this field equal.
_TOOL_RETRY = "weld_find"


def _run(argv: list[str]) -> tuple[int, str, str]:
    """Invoke the graph CLI *argv*, returning (exit_code, stdout, stderr)."""
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    exit_code = 0
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            graph_cli_main(argv)
    except SystemExit as exc:
        code = exc.code
        exit_code = 0 if code is None else code if isinstance(code, int) else 1
    return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()


def _write_index(root: Path, files: dict[str, list[str]]) -> None:
    """Write a minimal legacy-format file index at *root*."""
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / _INDEX_REL).write_text(json.dumps(files), encoding="utf-8")


def _make_federation_root(root: Path, *children: str) -> None:
    """Register *children* under ``.weld/workspaces.yaml`` at *root*.

    No graph and no index: the graph-less polyrepo worktree the finding was
    reproduced in. A registered child need not exist on disk -- a fresh
    worktree of the root has none of them checked out, which is exactly the
    state that has to be answerable-or-refused rather than silently empty.
    """
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    config = WorkspaceConfig(
        children=[
            ChildEntry(name=name, path=name, tags=(), remote=None)
            for name in children
        ],
        cross_repo_strategies=[],
    )
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")


class _FindPreconditionCase(unittest.TestCase):
    """A temp root plus the two outcomes ADR 0134 keeps apart."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".weld").mkdir()

    def find(self, term: str = "alpha", root: Path | None = None):
        target = self.root if root is None else root
        return _run(["--root", str(target), "find", term])

    def assert_cannot_answer(self, term: str = "alpha") -> str:
        exit_code, stdout, stderr = self.find(term)

        self.assertNotEqual(exit_code, 0, f"exited 0; stdout={stdout!r}")
        self.assertIn(_ERROR_PREFIX, stderr)
        self.assertIn(ERROR_HINTS[FILE_INDEX_MISSING], stderr)
        self.assertEqual(stdout, "", "a refusal must not also print a result")
        return stderr

    def assert_answered_empty(self, term: str = "nothing-matches-this") -> None:
        exit_code, stdout, stderr = self.find(term)

        self.assertEqual(exit_code, 0, f"answered-empty exited {exit_code}")
        self.assertIn("no matches", stdout)
        self.assertNotIn(_ERROR_PREFIX, stderr)


class SingleRepoFindTest(_FindPreconditionCase):
    """The plain repository route."""

    def test_no_index_is_a_cannot_answer(self) -> None:
        self.assertFalse((self.root / _INDEX_REL).exists())

        self.assert_cannot_answer()

    def test_an_index_that_matches_nothing_still_answers(self) -> None:
        """The distinction the whole ADR rests on: precondition-absent is not
        match-absent, and only the first is an error."""
        _write_index(self.root, {"alpha.py": ["alpha"]})

        self.assert_answered_empty()

    def test_an_index_that_matches_is_untouched(self) -> None:
        _write_index(self.root, {"alpha.py": ["alpha"]})

        exit_code, stdout, _stderr = self.find("alpha")

        self.assertEqual(exit_code, 0)
        self.assertIn("alpha.py", stdout)

    def test_the_refusal_builds_nothing(self) -> None:
        """``find`` refuses; it does not quietly run discovery instead. A read
        that started building an index would make the fix cost a full pass."""
        self.assert_cannot_answer()

        self.assertFalse((self.root / _INDEX_REL).exists())
        self.assertFalse((self.root / ".weld" / "graph.json").exists())

    def test_the_block_names_the_command_the_user_typed(self) -> None:
        """The retry line is the one guidance blocks elsewhere print, with
        this search term in it -- not a generic 'try again'."""
        stderr = self.assert_cannot_answer("OrderReplayer")

        self.assertIn('wd find "OrderReplayer"', stderr)


class FederationRootFindTest(_FindPreconditionCase):
    """The polyrepo route, where the fan-out has several indexes to miss."""

    def test_no_index_at_the_root_or_any_child_cannot_answer(self) -> None:
        _make_federation_root(self.root, "child-a", "child-b")

        self.assert_cannot_answer()

    def test_one_child_index_is_enough_to_answer(self) -> None:
        """The root has no index of its own, and does not need one: the
        fan-out reads the children's (ADR 0089), so this is answerable."""
        _make_federation_root(self.root, "child-a", "child-b")
        _write_index(self.root / "child-a", {"c.py": ["alpha"]})

        exit_code, stdout, _stderr = self.find("alpha")

        self.assertEqual(exit_code, 0)
        self.assertIn("child-a/c.py", stdout)

    def test_a_child_index_that_matches_nothing_still_answers(self) -> None:
        _make_federation_root(self.root, "child-a")
        _write_index(self.root / "child-a", {"c.py": ["alpha"]})

        self.assert_answered_empty()

    def test_the_root_index_alone_is_enough(self) -> None:
        _make_federation_root(self.root, "child-a")
        _write_index(self.root, {"r.py": ["alpha"]})

        exit_code, stdout, _stderr = self.find("alpha")

        self.assertEqual(exit_code, 0)
        self.assertIn("r.py", stdout)

    def test_a_child_directory_that_is_not_checked_out_holds_no_index(
        self,
    ) -> None:
        """The reported shape: a fresh worktree of the root, whose children
        are nested repositories git did not bring along."""
        _make_federation_root(self.root, "absent-child")

        self.assertFalse((self.root / "absent-child").exists())
        self.assert_cannot_answer()


class SurfaceParityTest(_FindPreconditionCase):
    """One payload; the CLI block is rendered from it, not written beside it."""

    def test_the_tool_returns_the_shared_payload(self) -> None:
        served = mcp_server.weld_find("alpha", root=str(self.root))

        self.assertEqual(
            served, missing_file_index_payload(self.root, _TOOL_RETRY),
        )
        self.assertEqual(served["error_code"], FILE_INDEX_MISSING)
        self.assertEqual(served["hint"], ERROR_HINTS[FILE_INDEX_MISSING])

    def test_the_cli_block_is_the_rendered_payload(self) -> None:
        """The strongest form of "MCP is a thin wrapper of the product": not
        that the two agree, but that there is only one thing to agree with."""
        _, _, stderr = self.find("alpha")

        self.assertEqual(
            stderr,
            cannot_answer_block(
                missing_file_index_payload(self.root, 'wd find "alpha"'),
            ),
        )

    def test_the_tool_refuses_at_a_federation_root_too(self) -> None:
        _make_federation_root(self.root, "child-a")

        served = mcp_server.weld_find("alpha", root=str(self.root))

        self.assertEqual(served["error_code"], FILE_INDEX_MISSING)

    def test_the_tool_answers_when_an_index_exists(self) -> None:
        """The guard is not newly firing: with an index the tool answers, and
        an honest miss is still a result payload rather than an error."""
        _write_index(self.root, {"alpha.py": ["alpha"]})

        hit = mcp_server.weld_find("alpha", root=str(self.root))
        miss = mcp_server.weld_find("no-such-token", root=str(self.root))

        self.assertEqual([f["path"] for f in hit["files"]], ["alpha.py"])
        self.assertEqual(miss["files"], [])
        self.assertNotIn("error_code", miss)

    def test_the_refusal_echoes_no_path(self) -> None:
        """ADR 0035: the summary and hint are constants, so a refused call
        says nothing about what does or does not exist on disk. The retry
        carries the caller's own term and nothing else."""
        served = mcp_server.weld_find("alpha", root=str(self.root))

        for field in ("error", "hint"):
            with self.subTest(field=field):
                self.assertNotIn(str(self.root), served[field])
        self.assertNotIn(str(self.root), served["retry"])


if __name__ == "__main__":
    unittest.main()
