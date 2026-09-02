"""The nine field-eval v0.24.0 findings, as probes through the real CLI.

Each ``test_nN_`` method is a port of one probe from the evaluator's
``fixture/run-all-repros.sh``, run against the workspace their
``make-fixture.sh`` builds, through ``python -m weld`` in a subprocess -- the
same three things they did, so their report and our gate assert the same
claim rather than two that merely sound alike.

**Every probe here is currently an expected failure.** All nine reproduce on
``main``; the marker names the finding and the bd issue that owns the fix, and
that fix flips the marker off -- it has to, because a probe that starts passing
with its marker still on is an *unexpected success*, and unittest fails the
target for it. A probe is never to be deleted, skipped or weakened to make this
file green: ``weld_field_eval_probe_inventory_test`` fails if one is. The
0.23.1 checks that must pass *today* are the sibling
``weld_field_eval_regression_e2e_test``.

Grammar-independent by construction: weld wires tree-sitter strategies for the
C# gateway, but nothing asserted here needs the grammar -- verified by running
the evaluator's own scripts under an interpreter with no ``tree_sitter``
installed, where all nine still reproduce.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from weld.tests._field_eval_corpus_fixture import (
    BILLING,
    DOCS,
    GATEWAY,
    NOTIFY,
    SCHEMA,
)
from weld.tests._field_eval_corpus_sources import (
    GATEWAY_TOUCH_FILE,
    HAND_EDITED_GATEWAY_CONFIG,
)
from weld.tests._field_eval_corpus_sources_csharp import BILLING_PACKAGE_ID
from weld.tests._field_eval_e2e_harness import FieldEvalWorkspace, cross_repo_joins
from weld.tests._graph_invariants import (
    assert_cannot_answer,
    assert_edges_resolve,
    assert_no_first_party_external,
    assert_roster_matches_json,
    graph_edges,
    graph_nodes,
)
from weld.workspace import UNIT_SEPARATOR

_BD = "d76r1"  # issue-id suffix -- the full ledger id is tracker-internal

#: One workspace, bootstrapped once: ``wd init`` + ``wd discover`` in each
#: child and a federated discover at the root is a dozen subprocesses, and
#: every probe below reads the same tree the evaluator did.
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


def workspace() -> FieldEvalWorkspace:
    assert _WS is not None, "setUpModule did not run"
    return _WS


#: N2 is about *which repos* get joined. The reader moved to the harness when
#: the v0.25.0 probes needed the same one (ADR 0137 ss1 on why it reads
#: endpoints child-name-first); this alias keeps the call sites below.
_joins = cross_repo_joins


class CrossRepoResolverProbes(unittest.TestCase):
    """N1-N3: what the ``package_graph`` resolver writes into the root graph.

    The three share one enabled state (``cross_repo_strategies:
    [package_graph]`` plus the federated discover it needs) -- one config edit
    and one discover for three probes rather than three of each -- and the
    class restores the shipped default afterwards.
    """

    ws: FieldEvalWorkspace

    @classmethod
    def setUpClass(cls) -> None:
        cls.ws = workspace()
        cls.ws.set_strategies("package_graph")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.ws.set_strategies()

    def test_n1_resolver_edges_resolve_and_impact_measures_dependents(self) -> None:
        root = self.ws.graph()

        # Half one: the edges must point at nodes somebody can look up.
        assert_edges_resolve(root, self.ws.child_graphs())

        # Half two, the consequence the evaluator reported: with a resolver
        # named in the file impact points at, it must stop saying none is wired
        # -- and name the consumers it measured, and what measured them. Read
        # from ``--json`` because the human render carries counts, not ids.
        #
        # No ``--allow-stale``. It was here because wiring the resolver used to
        # leave the root permanently stale (field-eval M1, bd lcq0c.3) -- the
        # gate had inherited the evaluator's workaround for a bug instead of
        # questioning it, which is why no test noticed. Answering without it is
        # now part of what these probes assert.
        result = self.ws.wd("impact", f"repo:{SCHEMA[0]}", "--json")
        payload = result.check().json()
        self.assertNotEqual(payload["risk_level"], "UNKNOWN", result.output)
        self.assertNotIn("cannot_answer", payload)
        self.assertEqual(payload.get("measured_by"), ["package_graph"], result.output)
        direct = {node["id"] for node in payload["direct_dependents"]}
        for consumer in (GATEWAY[0], NOTIFY[0]):
            self.assertIn(f"repo:{consumer}", direct, result.output)

        # ``check()`` is load-bearing now that the flag is gone: a refusal
        # exits non-zero and prints an error, and neither of the two
        # ``assertNotIn``s below would notice it.
        human = self.ws.wd("impact", f"repo:{SCHEMA[0]}").check()
        self.assertNotIn("Risk: UNKNOWN", human.output, human.output)
        self.assertNotIn("cross_repo_strategies is empty", human.output)

    def test_n2_no_join_is_fabricated_from_the_vendored_tree(self) -> None:
        # Three real joins, not the report's two: the corpus grew a C#-only
        # producer child for round three's M4 (bd lcq0c.4), and the gateway
        # consumes it exactly as it consumes the proto library. What N2 asserts
        # is unchanged -- these are the joins the manifests on disk declare,
        # and no vendored one is among them.
        expected = {
            (GATEWAY[0], SCHEMA[0], "Acme.Platform.Order.Schema"),
            (GATEWAY[0], BILLING[0], BILLING_PACKAGE_ID),
            (NOTIFY[0], SCHEMA[0], "order-schema"),
        }
        fabricated = {"pandas", "Google.Protobuf"}

        # Both settings: the report's sharpest line is that respect_gitignore
        # made no difference, because the resolver never asked git.
        for respect_gitignore in (False, True):
            self.ws.set_strategies(
                "package_graph", respect_gitignore=respect_gitignore
            )
            joins = _joins(self.ws.graph())
            where = f"respect_gitignore={respect_gitignore}"
            self.assertEqual(
                {package for _f, _t, package in joins} & fabricated,
                set(),
                f"{where}: vendored packages produced joins: {sorted(joins)}",
            )
            self.assertEqual(joins, expected, where)

    def test_n3_graph_validate_rejects_a_dangling_federated_edge(self) -> None:
        # A hand-dangled edge rather than the resolver's own output, so this
        # probe keeps meaning the same thing after N1 lands: the claim is that
        # validate *can* see a dangling federated endpoint.
        payload = self.ws.graph()
        edges = graph_edges(payload)
        edges.append(
            {
                "from": f"repo:{DOCS[0]}",
                "to": f"{NOTIFY[0]}{UNIT_SEPARATOR}symbol:py:does.not:exist",
                "type": "cross_repo:depends_on",
                "props": {"package": "fabricated-for-this-probe"},
            }
        )
        payload["edges"] = edges
        (self.ws.root / ".weld" / "graph.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

        result = self.ws.wd("graph", "validate")

        self.assertNotEqual(
            result.returncode, 0,
            f"validate passed a graph with a dangling federated edge:\n"
            f"{result.output}",
        )
        self.assertIs(result.json().get("valid"), False, result.output)


class DiscoveryAndReadProbes(unittest.TestCase):
    """N4-N9: what discovery mints and what the read commands report."""

    ws: FieldEvalWorkspace

    @classmethod
    def setUpClass(cls) -> None:
        cls.ws = workspace()

    def _write_gateway_config(self, text: str) -> None:
        (self.ws.root / GATEWAY[1] / ".weld" / "discover.yaml").write_text(
            text, encoding="utf-8"
        )
        self.ws.discover(cwd=self.ws.root / GATEWAY[1])

    def _hand_edited_gateway_config(self) -> None:
        """Put the team's narrowed, hand-maintained config in place.

        Captured and restored **once** per test however many times the probe
        rewrites it: N7 lays this config down twice, and a cleanup per call
        would restore an intermediate state on the way back out.
        """
        if not getattr(self, "_gateway_config_pinned", False):
            self._gateway_config_pinned = True
            self.addCleanup(
                self._write_gateway_config, self.ws.config_text(GATEWAY[1])
            )
        self._write_gateway_config(HAND_EDITED_GATEWAY_CONFIG)

    def test_n4_first_party_python_imports_stay_first_party(self) -> None:
        graph = self.ws.graph(NOTIFY[1])

        assert_no_first_party_external(graph)

        minted = sorted(
            node_id for node_id in graph_nodes(graph)
            if node_id.startswith(("package:python:acme_notify", "package:python:broker"))
        )
        self.assertEqual(minted, [], f"external nodes for first-party modules: {minted}")

    def test_query_ranks_the_definite_symbol_over_the_stub(self) -> None:
        """N4's strategy-side half, fixed on its own issue (bd z98p7).

        Red until the source root bound the reference rather than the identity
        (ADR 0143): the stub the ranking had to beat is no longer minted, so
        this now asserts the stronger claim -- there is nothing left to rank
        against, and every representation of the function is the definite one.
        """
        matches = self.ws.wd(
            "query", "load_config", "--json", cwd=self.ws.root / NOTIFY[1]
        ).check().json()["matches"]
        self.assertTrue(matches, "query load_config returned nothing")
        first = matches[0]
        self.assertEqual(
            first.get("props", {}).get("confidence"), "definite",
            f"the top match is not the definite symbol: {first.get('id')}",
        )
        speculative = [
            match.get("id") for match in matches
            if match.get("label") == "load_config"
            and match.get("props", {}).get("confidence") == "speculative"
        ]
        self.assertEqual(
            speculative, [],
            f"a speculative twin of load_config is still minted: {speculative}",
        )

    def test_n5_stale_roster_agrees_with_its_own_json(self) -> None:
        self._assert_roster_consistent("all four children fresh")

        touched = self.ws.root / GATEWAY[1] / GATEWAY_TOUCH_FILE
        self.addCleanup(
            self.ws.git, "checkout", "--", GATEWAY_TOUCH_FILE,
            cwd=self.ws.root / GATEWAY[1],
        )
        touched.write_text(
            touched.read_text(encoding="utf-8") + "// touch\n", encoding="utf-8"
        )

        self._assert_roster_consistent("one child edited")

    def _assert_roster_consistent(self, label: str) -> None:
        text = self.ws.wd("stale", "--no-refresh").stdout
        payload = self.ws.wd("stale", "--no-refresh", "--json").json()
        assert_roster_matches_json(text, payload)

        # The other half: the two commands reporting the same roster must not
        # disagree with each other either.
        status = self.ws.wd("workspace", "status").check().stdout
        roster = re.search(r"(\d+) present", text)
        counts = re.search(r"present=(\d+)", status)
        self.assertIsNotNone(roster, f"{label}: no present count in:\n{text}")
        self.assertIsNotNone(counts, f"{label}: no present count in:\n{status}")
        self.assertEqual(
            roster.group(1), counts.group(1),
            f"{label}: `wd stale` and `wd workspace status` disagree on "
            f"present:\n{text}\n{status}",
        )

    def test_n6_unclaimed_source_remedy_offers_refresh_first(self) -> None:
        self._hand_edited_gateway_config()
        gateway = self.ws.root / GATEWAY[1]

        doctor = self.ws.wd("doctor", cwd=gateway).output
        warning = next(
            (line for line in doctor.splitlines() if "unclaimed-source" in line), ""
        )
        self.assertTrue(warning, f"no unclaimed-source warning in doctor:\n{doctor}")
        self.assertIn("--refresh", warning, warning)
        self.assertLess(
            warning.index("--refresh"), warning.index("--force"),
            f"the destructive remedy is offered first: {warning}",
        )

        prime = self.ws.wd("prime", cwd=gateway).output
        _, _, next_steps = prime.partition("Next steps:")
        self.assertIn("--refresh", next_steps, prime)
        self.assertLess(
            next_steps.index("--refresh"),
            next_steps.index("--force") if "--force" in next_steps else len(next_steps),
            f"prime lists --force before --refresh:\n{next_steps}",
        )

    def test_n7_refresh_wires_everything_force_wires(self) -> None:
        gateway = self.ws.root / GATEWAY[1]

        self._hand_edited_gateway_config()
        self.ws.wd("init", "--refresh", cwd=gateway).check()
        refreshed = self._strategies(self.ws.config_text(GATEWAY[1]))

        self._hand_edited_gateway_config()
        self.ws.wd("init", "--force", cwd=gateway).check()
        forced = self._strategies(self.ws.config_text(GATEWAY[1]))

        self.assertTrue(forced, "--force wired no strategies at all")
        self.assertEqual(
            sorted(forced - refreshed), [],
            "--refresh silences the unclaimed-source warning while wiring "
            f"less than --force: refresh={sorted(refreshed)} force={sorted(forced)}",
        )

    @staticmethod
    def _strategies(config_text: str) -> set[str]:
        return set(re.findall(r"^\s*(?:-\s+)?strategy:\s*(\S+)", config_text, re.M))

    def test_n8_markdown_fallback_discovers_readme(self) -> None:
        """N8 fixed: the docs repo's index file is a node you can search for."""
        docs = self.ws.root / DOCS[1]
        graph = self.ws.graph(DOCS[1])

        docs_nodes = {
            node_id: node for node_id, node in graph_nodes(graph).items()
            if node.get("type") == "doc"
        }
        self.assertEqual(
            len(docs_nodes), 5,
            f"expected one doc node per markdown file, got {sorted(docs_nodes)}",
        )
        self.assertIn(
            "README.md", graph.get("meta", {}).get("discovered_from", []),
            "README.md is not in discovered_from",
        )

        found = self.ws.wd(
            "query", "Platform Documentation", "--json", cwd=docs
        ).check().json()
        labels = [m.get("label") for m in found.get("matches", [])]
        self.assertIn("Platform Documentation", labels, f"matches: {labels}")

    def test_n9_find_refuses_from_a_missing_file_index(self) -> None:
        worktree = self.ws.root / ".worktrees" / "n9"
        self.ws.git("worktree", "add", "-q", str(worktree), "-b", "repro/n9").check()
        self.addCleanup(self.ws.git, "branch", "-D", "repro/n9")
        self.addCleanup(self.ws.git, "worktree", "remove", "--force", str(worktree))

        self.assertFalse(
            (worktree / ".weld" / "file-index.json").exists(),
            "the probe needs a worktree with no file index",
        )

        # query and brief already refuse here; find is the one that does not.
        for command in ("query", "brief", "find"):
            result = self.ws.wd(command, "OrderReplayer", cwd=worktree)
            try:
                assert_cannot_answer(result.returncode, result.stderr, result.stdout)
            except AssertionError as exc:
                raise AssertionError(f"`wd {command}`: {exc}") from exc


if __name__ == "__main__":
    unittest.main()
