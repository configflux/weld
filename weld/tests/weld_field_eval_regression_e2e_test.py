"""The v0.23.1 field-eval fixes, still holding -- the evaluator's own suite.

``fixture/verify-previous-fixes.sh`` is what the external evaluator re-ran
against 0.24.0 before reporting anything new: nine checks, one per finding
they had filed against 0.23.1. It passed 9/9. This file is that script, ported
to run against the same workspace through ``python -m weld`` in a subprocess,
so the next release finds out from our own gate rather than from a report.

Unlike its sibling ``weld_field_eval_e2e_test`` (nine *new* findings, all
expected failures), everything here must pass **today**. Two checks cannot:
03a and 08 read symbol-level C# extraction, which needs an ambient
``tree_sitter_c_sharp`` grammar that this repo deliberately does not pin
(ADR 0069). They self-skip, together -- 03b is the negative half of 03a's
contrast and proves nothing on its own when the grammar is absent.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from weld.tests._field_eval_corpus_fixture import DOCS, GATEWAY, NOTIFY, SCHEMA
from weld.tests._field_eval_e2e_harness import FieldEvalWorkspace
from weld.tests._graph_invariants import assert_cannot_answer, graph_nodes

_HAS_CSHARP_GRAMMAR = all(
    importlib.util.find_spec(name) is not None
    for name in ("tree_sitter", "tree_sitter_c_sharp")
)

#: The stale-config shape check 05 writes over the gateway's generated config.
#: Narrower than the N6/N7 one on purpose -- this is the evaluator's own
#: verify-script text, and the checks are ports, not paraphrases.
_MARKDOWN_ONLY_CONFIG = (
    "sources:\n"
    '  - glob: "doc/*.md"\n'
    "    type: doc\n"
    "    strategy: markdown\n"
)

_WS: FieldEvalWorkspace | None = None
_TMP: tempfile.TemporaryDirectory | None = None


def setUpModule() -> None:
    global _WS, _TMP
    _TMP = tempfile.TemporaryDirectory()
    _WS = FieldEvalWorkspace.materialize(Path(_TMP.name))
    _WS.bootstrap()


def tearDownModule() -> None:
    if _TMP is not None:
        _TMP.cleanup()


class PreviousFixesStillHoldTest(unittest.TestCase):
    """One method per check in ``verify-previous-fixes.sh``, same numbering."""

    ws: FieldEvalWorkspace

    @classmethod
    def setUpClass(cls) -> None:
        assert _WS is not None, "setUpModule did not run"
        cls.ws = _WS

    def test_01_brief_federates_at_a_polyrepo_root(self) -> None:
        payload = self.ws.wd("brief", "OrderReplayer").check().json()
        self.assertTrue(
            payload.get("primary"),
            f"brief at the federation root found nothing: {payload}",
        )

    def test_02_graph_backed_reads_refuse_at_a_graphless_root(self) -> None:
        worktree = self._worktree("v02", "verify/02")
        result = self.ws.wd("query", "OrderReplayer", cwd=worktree)
        assert_cannot_answer(result.returncode, result.stderr, result.stdout)

    @unittest.skipUnless(
        _HAS_CSHARP_GRAMMAR, "needs the ambient tree_sitter_c_sharp grammar"
    )
    def test_03_capabilities_attributes_only_present_languages(self) -> None:
        # 03a: symbols=yes where the C# source is; 03b: file=no where it is
        # not. The pair is one check -- 03b alone passes vacuously without the
        # grammar, which is why both sit behind the same guard.
        self.assertEqual(
            self._capability(GATEWAY[1], "csharp", "symbols"), "yes",
            "capabilities does not report csharp symbols in the C# repo",
        )
        self.assertEqual(
            self._capability(NOTIFY[1], "csharp", "file"), "no",
            "capabilities claims csharp files in a Python-only repo",
        )

    def test_04_python_package_ids_keep_the_full_dotted_path(self) -> None:
        ids = " ".join(graph_nodes(self.ws.graph(NOTIFY[1])))
        for version in ("order.schema.v1.event_pb2", "order.schema.v2.event_pb2"):
            self.assertIn(
                version, ids, f"{version} collapsed out of the notifier's ids"
            )

    def test_05_doctor_warns_about_unwired_source(self) -> None:
        gateway = self.ws.root / GATEWAY[1]
        original = self.ws.config_text(GATEWAY[1])
        self.addCleanup(self._restore_gateway_config, original)
        (gateway / ".weld" / "discover.yaml").write_text(
            _MARKDOWN_ONLY_CONFIG, encoding="utf-8"
        )
        self.ws.discover(cwd=gateway)

        self.assertIn(
            "unclaimed-source-csharp",
            self.ws.wd("doctor", cwd=gateway).output,
            "doctor reports healthy while 100% of the C# source is unclaimed",
        )

    def test_06_impact_refuses_to_fabricate_a_verdict(self) -> None:
        # No resolver is wired at the shipped default, so a measured 0 is
        # impossible and impact must say so rather than score a LOW.
        result = self.ws.wd("impact", f"repo:{SCHEMA[0]}", "--allow-stale")
        self.assertIn("Risk: UNKNOWN", result.output, result.output)
        self.assertNotIn("Risk: LOW", result.output)

    def test_07_a_markdown_only_repo_produces_a_non_empty_graph(self) -> None:
        nodes = graph_nodes(self.ws.graph(DOCS[1]))
        self.assertTrue(nodes, "the docs-only repo discovered nothing")

    @unittest.skipUnless(
        _HAS_CSHARP_GRAMMAR, "needs the ambient tree_sitter_c_sharp grammar"
    )
    def test_08_brief_ranks_the_exact_identifier_first(self) -> None:
        payload = self.ws.wd(
            "brief", "OrderReplayer", cwd=self.ws.root / GATEWAY[1]
        ).check().json()
        primary = payload.get("primary") or []
        self.assertTrue(primary, f"brief found nothing: {payload}")
        self.assertEqual(primary[0].get("relevance"), "exact match", primary[0])

    def test_09_worktree_seeding_names_its_missing_prerequisite(self) -> None:
        gateway = self.ws.root / GATEWAY[1]
        # Faithful port: the evaluator untracks the config first and ignores
        # the result. In this fixture `wd init` wrote it after the child's only
        # commit, so it is untracked already and both commands are no-ops --
        # the state under test (no tracked discover.yaml to seed from) is the
        # same either way, so neither is checked.
        self.ws.git("rm", "-q", "--cached", ".weld/discover.yaml", cwd=gateway)
        self.ws.git("commit", "-q", "-m", "untrack", cwd=gateway)

        worktree = self._worktree("v09", "verify/09", repo=gateway)
        result = self.ws.wd("query", "OrderReplayer", cwd=worktree)

        assert_cannot_answer(result.returncode, result.stderr, result.stdout)
        self.assertIn(
            "discover.yaml", result.output,
            "the no-graph message does not name the missing prerequisite",
        )

    # -- helpers ---------------------------------------------------------

    def _worktree(self, name: str, branch: str, repo: Path | None = None) -> Path:
        """Add a linked worktree of *repo* (default: the root) and clean it up."""
        base = repo if repo is not None else self.ws.root
        path = self.ws.root / ".worktrees" / name
        self.ws.git("worktree", "add", "-q", str(path), "-b", branch, cwd=base).check()
        self.addCleanup(self.ws.git, "branch", "-D", branch, cwd=base)
        self.addCleanup(
            self.ws.git, "worktree", "remove", "--force", str(path), cwd=base
        )
        return path

    def _restore_gateway_config(self, original: str) -> None:
        gateway = self.ws.root / GATEWAY[1]
        (gateway / ".weld" / "discover.yaml").write_text(original, encoding="utf-8")
        self.ws.discover(cwd=gateway)

    def _capability(self, rel: str, language: str, column: str) -> str:
        """Read one cell out of the ``wd capabilities`` language table."""
        text = self.ws.wd("capabilities", cwd=self.ws.root / rel).check().stdout
        header: list[str] = []
        for line in text.splitlines():
            fields = line.split()
            if fields[:1] == ["language"]:
                header = fields
            elif header and fields[:1] == [language]:
                return fields[header.index(column)]
        raise AssertionError(
            f"no {language!r} row with a {column!r} column in:\n{text}"
        )


if __name__ == "__main__":
    unittest.main()
