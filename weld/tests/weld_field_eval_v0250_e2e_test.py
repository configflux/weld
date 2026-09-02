"""The four field-eval v0.25.0 findings, as probes through the real CLI.

Each ``test_mN_`` method is a port of one probe from the evaluator's
``fixture/run-all-repros.sh`` for round three, run against the workspace their
``make-fixture.sh`` builds, through ``python -m weld`` in a subprocess -- the
same three things they did, so their report and our gate assert the same claim
rather than two that merely sound alike. The sibling
``weld_field_eval_e2e_test`` is round two's nine; this file is round three's
four.

**Every probe here landed as an expected failure**, on purpose: ADR 0141 D5
puts the red probes in before any fix, so each fix flips its own marker and
cannot be called done without one. A probe that starts passing with its marker
still on is an *unexpected success*, which unittest fails the target for --
that is the mechanism, not an accident of it. M1-M4's markers are all off,
the round having landed its four fixes. None may be deleted, skipped or
weakened to make this file green: ``weld_field_eval_probe_inventory_test``
fails if one is.

What each asserts is the **invariant the finding instances**, not the
transcript (docs/testing-hygiene.md "Fixing a field finding"). M1: a verdict
names its basis, not that one payload has one field. M4: the edge set a known
workspace must yield -- recall against ground truth -- rather than that the
edges which happened to form resolve, the shape that let a whole ecosystem go
missing unnoticed. M2 and M3: the consequence a user meets, a definition that
answers "who calls me" and a root that reports healthy when it is.

Grammar-independent by construction: the C# children are read as manifests
(``.csproj`` text) and as files, never as syntax, so nothing here needs the
ambient ``tree_sitter_c_sharp`` grammar this repo does not pin (ADR 0069).
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from weld.tests._field_eval_corpus_fixture import BILLING, GATEWAY, NOTIFY, SCHEMA
from weld.tests._field_eval_corpus_sources import GATEWAY_TOUCH_FILE
from weld.tests._field_eval_corpus_sources_csharp import (
    BILLING_PACKAGE_ID,
    ORDER_SCHEMA_PACKAGE_REFERENCE,
)
from weld.tests._field_eval_e2e_harness import (
    FieldEvalWorkspace,
    callers_in_graph,
    cross_repo_joins,
)
from weld.tests._graph_invariants import assert_edges_resolve, graph_nodes
from weld.tests._staleness_invariants import (
    assert_stale_verdict_names_its_basis,
    stale_verdict_basis,
)

#: The bd issue that owns each fix -- issue-id suffixes, the full ledger ids
#: being tracker-internal. M2's owner is outside this round's epic: the
#: symbol-identity issue round two deferred as P3, which this finding's
#: system-level consequence re-prices (ADR 0141 D4).
#:
#: The round's complete ledger, not a roster of live markers: an entry stays
#: after its probe is flipped, so this keeps answering "who fixed M1" and the
#: inventory guard keeps admitting every owner the round has.
_BD_FIXES = {
    "M1": "lcq0c.3",
    "M2": "z98p7",
    "M3": "lcq0c.5",
    "M4": "lcq0c.4",
}

#: The function M2 is about, and the file it is written in -- the pair the
#: probe finds its definition by. Everything else about the node (which
#: spelling its id uses) is what the finding disputes, so none of it is a
#: precondition here.
_LOAD_CONFIG = "load_config"
_LOAD_CONFIG_FILE = "src/acme_notify/config.py"
#: Both spellings of its module end in this; neither prefix is assumed.
_LOAD_CONFIG_MODULE = "acme_notify.config"

#: The two ids the evaluator's transcript names: the definition, which answers
#: "no callers", and the speculative twin that holds them. Used only to gather
#: what the graph attributes today -- never as an assumption about which id
#: survives the fix.
_DEFINITE = "symbol:py:src.acme_notify.config:load_config"
_IMPORT_SPELLING = "symbol:py:acme_notify.config:load_config"

#: Ground truth for M4: every cross-repo dependency the manifests on disk
#: declare, and nothing else -- two producer-declaration styles (a ``.proto``
#: package, an MSBuild ``<PackageId>``) consumed identically, plus the
#: notifier's ``pyproject`` dependency by ``[project].name``. Asserted as an
#: equality: a subset check is what let a missing ecosystem read as success.
_GROUND_TRUTH_JOINS = {
    (GATEWAY[0], SCHEMA[0], ORDER_SCHEMA_PACKAGE_REFERENCE),
    (GATEWAY[0], BILLING[0], BILLING_PACKAGE_ID),
    (NOTIFY[0], SCHEMA[0], "order-schema"),
}

#: One workspace, bootstrapped once: ``wd init`` + ``wd discover`` in each of
#: the five children and a federated discover at the root, and every probe
#: below reads the same tree the evaluator did.
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


class FederationRootProbes(unittest.TestCase):
    """M2-M3: what a read answers at the shipped default, no resolver wired.

    Both run against the workspace as ``bootstrap`` leaves it -- M2 inside the
    notifier, M3 at the root -- so neither depends on the config edit the
    resolver probes make. M3 is here rather than beside M1 for that reason:
    ADR 0141 D3 makes doctor's verdict a question about the root's
    configuration, not about whether a resolver is wired, and pinning it to
    the clean state keeps the probe asking only that.
    """

    ws: FieldEvalWorkspace

    @classmethod
    def setUpClass(cls) -> None:
        cls.ws = workspace()

    def test_m2_src_layout_definition_reports_its_callers(self) -> None:
        """The definition of a first-party function answers "who calls me".

        The consequence a user meets, which round two's deferral was priced
        without: ``wd callers`` on the id that *is* the definition -- the one
        with the file -- returned nothing, while a node with no file held them.
        Green since ADR 0143 bound the reference to the module the source root
        names instead of respelling the identity (bd z98p7): the twin is not
        minted, so the callers and the file are on one id.
        """
        graph = self.ws.graph(NOTIFY[1])
        nodes = graph_nodes(graph)

        # The definition is found by *what it is* -- the node that knows the
        # file this function is written in -- not by a spelled id. ADR 0141 D4
        # leaves open which spelling the convergence keeps, and a probe keyed
        # on the losing one would go red on the fix that repairs it.
        definitions = sorted(
            node_id
            for node_id, node in nodes.items()
            if node.get("label") == _LOAD_CONFIG
            and (node.get("props") or {}).get("file") == _LOAD_CONFIG_FILE
        )
        self.assertEqual(
            len(definitions), 1,
            f"expected one node to hold the definition in {_LOAD_CONFIG_FILE}, "
            f"got {definitions}",
        )
        definition_id = definitions[0]

        # Every caller the graph attributes to any identity of this function.
        # Derived rather than listed: today one spelling holds the callers and
        # the other holds the file, and after the fix a single id holds both.
        attributed: set[str] = set()
        for spelling in (definition_id, _DEFINITE, _IMPORT_SPELLING):
            attributed |= callers_in_graph(graph, spelling)
        self.assertTrue(
            attributed,
            "the fixture records no first-party call to load_config at all; "
            "this probe would pass without asserting anything",
        )

        result = self.ws.wd(
            "callers", definition_id, "--json", cwd=self.ws.root / NOTIFY[1]
        )
        reported = {
            str(caller.get("id")) for caller in result.check().json().get("callers", [])
        }
        self.assertEqual(
            reported, attributed,
            f"`wd callers {definition_id}` does not report the callers the "
            f"graph attributes to that function: {result.output}",
        )

        # And the other half of one-spelling-per-module: no second node means
        # the same function (ADR 0141 D4). Counted, not named, for the same
        # reason the definition is found rather than spelled -- and scoped to
        # this module, so a same-named function elsewhere is not a violation.
        identities = sorted(
            node_id
            for node_id, node in nodes.items()
            if node_id.startswith("symbol:py:")
            and node.get("label") == _LOAD_CONFIG
            and node_id.split(":")[2].endswith(_LOAD_CONFIG_MODULE)
        )
        self.assertEqual(
            identities, [definition_id],
            "one function, more than one symbol id -- a speculative twin under "
            f"the import spelling shadows the definition: {identities}",
        )

    def test_m3_doctor_is_healthy_at_a_pure_federation_root(self) -> None:
        """A root that federates and discovers nothing of its own is healthy.

        Nothing here is about the *content* of doctor's report: the claim is
        that a root holding ``workspaces.yaml`` and no ``discover.yaml`` is a
        configuration weld itself writes, and grading it a failure makes every
        agent that gates on doctor's exit code refuse a healthy workspace.
        Green since ADR 0141 D3 made doctor ask the read path's own federation
        question -- ``find_workspaces_yaml`` -- before condemning the absent
        config (bd lcq0c.5): a root that federates gets a note, a plain
        repository still gets the failure.
        """
        weld_dir = self.ws.root / ".weld"
        self.assertTrue(
            (weld_dir / "workspaces.yaml").is_file(), "the root does not federate"
        )
        self.assertFalse(
            (weld_dir / "discover.yaml").exists(),
            "the root has sources of its own; this is not the shape M3 is about",
        )

        result = self.ws.wd("doctor")
        failures = [
            line for line in result.output.splitlines() if line.lstrip().startswith("[fail]")
        ]
        blamed = [line for line in failures if "discover.yaml" in line]
        self.assertEqual(
            blamed, [],
            f"doctor fails a pure federation root for having no sources of its "
            f"own:\n{result.output}",
        )
        # The verdict *token*, not the whole footer: that line ends with a
        # count ("0 errors") on a healthy run too, so a substring test would
        # keep this probe red after the fix and blame the wrong thing.
        status = next(
            (line for line in result.output.splitlines() if line.startswith("Status:")),
            "",
        )
        verdict = re.match(r"Status:\s+(\S+)", status)
        self.assertIsNotNone(verdict, f"no Status footer in:\n{result.output}")
        self.assertNotEqual(
            verdict.group(1), "errors", f"{status!r}\n{result.output}"
        )
        self.assertEqual(
            result.returncode, 0,
            f"doctor exits unhealthy at a healthy federation root:\n{result.output}",
        )


class ResolverWiredRootProbes(unittest.TestCase):
    """M1 and M4: the root once ``cross_repo_strategies: [package_graph]``.

    The two share one enabled state -- the config edit the evaluator makes with
    ``sed`` plus the federated discover it needs -- and the class restores the
    shipped default afterwards. That the edit leaves ``workspaces.yaml``
    uncommitted-dirty is not incidental to M1: it is the input, and it is what
    the first user action this feature invites produces.
    """

    ws: FieldEvalWorkspace

    @classmethod
    def setUpClass(cls) -> None:
        cls.ws = workspace()
        cls.ws.set_strategies("package_graph")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.ws.set_strategies()

    def test_m1_discover_then_stale_is_fresh_with_a_resolver_wired(self) -> None:
        """Discover, then ask: the answer is fresh, and impact will answer.

        Three claims, in the order a user meets them. The *second* is not that
        this payload is fresh but that a stale verdict -- this one or any
        other -- names something a reader can act on: the invariant M1
        instances (ADR 0141 D1). The first and third are the repro.

        Marker flipped by ``_BD_FIXES["M1"]``: a federated root discover now
        records the ``workspaces.yaml`` content it read
        (:mod:`weld._federation_basis`), so the working-tree check vouches
        against a basis instead of condemning on the absence of one.
        """
        payload = self.ws.wd("stale", "--json").check().json()

        # The invariant first, so a still-stale run says *why* it is stale
        # rather than only that it is.
        assert_stale_verdict_names_its_basis(payload, where="resolver wired")

        self.assertFalse(
            payload.get("stale"),
            f"the root reports stale immediately after the discover that is "
            f"supposed to clear it: {payload}",
        )

        result = self.ws.wd("impact", f"repo:{SCHEMA[0]}", "--json")
        self.assertEqual(
            result.returncode, 0,
            f"`wd impact` refuses at a freshly discovered root without "
            f"--allow-stale:\n{result.output}",
        )

    def test_m4_csproj_producer_joins_the_package_graph(self) -> None:
        """The resolver yields the edge set this workspace's manifests declare.

        Recall against ground truth, not against whatever formed: "the edges
        that formed resolve" is true of a graph missing every .NET producer,
        and was -- which is how the evaluator found three of twelve consuming
        repos on their own workspace and read it as correct.

        Marker flipped by ``_BD_FIXES["M4"]``: the manifest scan now reads
        producers through a per-ecosystem registry with MSBuild among its
        entries (:mod:`weld.cross_repo._package_manifest_scan`), so a library
        that declares its package name only in a ``.csproj`` can be joined to
        rather than only from.
        """
        root = self.ws.graph()

        # Still the standing half: nothing unresolvable may reach the root
        # graph (ADR 0137 ss4). It has to hold for the edge M4 is missing too.
        assert_edges_resolve(root, self.ws.child_graphs())

        self.assertEqual(
            cross_repo_joins(root), _GROUND_TRUTH_JOINS,
            "the resolver's edge set is not the one the manifests on disk "
            "declare",
        )


class StaleBasisInvariantTest(unittest.TestCase):
    """The guard on M1's invariant: it can be satisfied, by this product.

    A check that has only ever seen the input it rejects is indistinguishable
    from one that rejects everything -- and M1's probe is the only place
    ``assert_stale_verdict_names_its_basis`` runs today, on the one payload
    that fails it. So the helper is run here against verdicts weld emits that
    *do* name their basis. This passes today and must keep passing: if the M1
    fix satisfies the invariant by weakening what counts as a basis, this is
    what stops it. Both payloads come from the CLI (ADR 0139 mechanism 1).
    """

    ws: FieldEvalWorkspace

    @classmethod
    def setUpClass(cls) -> None:
        cls.ws = workspace()

    def test_a_verdict_that_names_a_file_satisfies_the_invariant(self) -> None:
        gateway = self.ws.root / GATEWAY[1]

        fresh = self.ws.wd("stale", "--json", cwd=gateway).check().json()
        assert_stale_verdict_names_its_basis(fresh, where="child, untouched")
        self.assertFalse(
            fresh.get("stale"), f"the child is stale before the probe edits it: {fresh}"
        )

        touched = gateway / GATEWAY_TOUCH_FILE
        self.addCleanup(
            self.ws.git, "checkout", "--", GATEWAY_TOUCH_FILE, cwd=gateway
        )
        touched.write_text(
            touched.read_text(encoding="utf-8") + "// touch\n", encoding="utf-8"
        )

        edited = self.ws.wd("stale", "--json", cwd=gateway).check().json()
        self.assertTrue(
            edited.get("stale"), f"an edited tracked source left the child fresh: {edited}"
        )
        assert_stale_verdict_names_its_basis(edited, where="child, edited")

        # And specifically through the field M1 finds empty: a pass here that
        # came from some other basis would leave the interesting half unproven.
        basis = stale_verdict_basis(edited)
        self.assertTrue(
            any(item.startswith("stale_sources=") for item in basis),
            f"the verdict names a basis, but not the edited file: {basis}",
        )


if __name__ == "__main__":
    unittest.main()
