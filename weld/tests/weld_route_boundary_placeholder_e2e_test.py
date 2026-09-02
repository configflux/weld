"""A route strategy must not erase the file node it lands on (bd iurvv).

Every route strategy that cannot name a router *symbol* hangs its diagnostic
``exposes`` edge off the boundary **file** node, and mints a thin ``file:``
placeholder so that edge survives the dangling-edge post-pass when the strategy
runs without a tree-sitter entry beside it. That placeholder states no
``props.confidence``, so :func:`weld._discover_node_merge.claim_supersedes`
cannot rank it -- both sides must state a comparable confidence for its veto to
fire -- and the orchestrator falls back to last-writer-wins. A route entry
declared *after* the tree-sitter entry therefore **replaces** the canonical file
node with the stub, and every fact the tree-sitter pass recorded about that file
(``exports``, ``imports_from``, ``import_targets``, ``types``, ``line_count``)
is gone.

The consequence lands on exactly the files a user is most likely to ask about:
their HTTP handlers. ``wd context`` on one under-reports, and any consumer
reading ``props.exports`` sees none.

This is the system-level probe -- a real git repo, a real ``python -m weld
discover`` in a subprocess, real TypeScript / Rust / Go grammars, and the
``graph.json`` that run wrote -- because no in-process layer can see it. Each
strategy is correct alone; the defect is in what the *orchestrator* does with
two correct claims on one node id, so only a whole run over a config that wires
both exhibits it.

Three runs of one corpus (:mod:`weld.tests._route_boundary_corpus`), differing
only in the order the entries are declared:

* ``ROUTES_LAST`` -- the defect. The evidence assertions below are red here
  until the fix lands.
* ``ROUTES_FIRST`` -- the order ``wd init`` emits (ADR 0071), which is why this
  has been live since ADR 0103 without anyone meeting it. Green today; asserted
  so the fix is proven *order-independent* rather than merely re-ordered.
* ``ROUTES_ONLY`` -- no tree-sitter entry at all. Green today, and the control
  that matters most: a "fix" that simply stopped minting the placeholder would
  satisfy every other case here and silently drop the ``exposes`` edge from
  every routes-only config in the field.

The unit-level counterpart -- that no route boundary placeholder anywhere in
``weld/strategies`` may state a rankable-as-strong confidence -- is
``weld_route_boundary_placeholder_test``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from weld.tests._graph_invariants import assert_edges_resolve, graph_edges, graph_nodes
from weld.tests._route_boundary_corpus import (
    BOUNDARIES,
    ROUTES_FIRST,
    ROUTES_LAST,
    ROUTES_ONLY,
    Boundary,
    materialize,
)

#: Every subprocess here is bounded. Generous: the assertion is never about how
#: long a command took, only that it terminated (test-hygiene "wallclock").
CLI_TIMEOUT_SECONDS = 180

#: The grammars this corpus needs the CLI to have. Absent them the tree-sitter
#: pass mints no file node at all and every probe below would pass vacuously,
#: which is the one outcome a defect-reproducing probe must never have -- so
#: this fails loudly rather than skipping, the way the Node readiness corpus
#: does. Under Bazel they arrive as declared deps of the target.
REQUIRED_GRAMMAR_MODULES = (
    "tree_sitter",
    "tree_sitter_typescript",
    "tree_sitter_rust",
    "tree_sitter_go",
)


def weld_import_root() -> str:
    """Directory to put on ``PYTHONPATH`` so ``-m weld`` finds this checkout.

    Under Bazel that is the runfiles tree; from a plain source checkout it is
    the repo root. Both are ``weld/tests/<this file>`` minus three components
    -- the resolved form is tried second because a runfiles entry is a symlink
    into the source tree and only one of the two spellings has ``weld/`` under
    it in every layout.
    """
    here = Path(__file__).absolute()
    for candidate in (here.parents[2], here.resolve().parents[2]):
        if (candidate / "weld" / "__main__.py").is_file():
            return str(candidate)
    raise RuntimeError(  # pragma: no cover - a broken runfiles tree
        f"cannot locate weld/__main__.py above {here}"
    )


def cli_env(home: Path) -> dict[str, str]:
    """The pinned environment every subprocess here runs under.

    ``PYTHONPATH`` is *extended* rather than replaced: under Bazel it is how
    the grammar wheels reach the subprocess at all, and a probe that ran
    without them would assert nothing. ``HOME`` is redirected into the tempdir
    so no ambient git or weld config is read, and ``WELD_AUTO_REFRESH=0`` so
    ADR 0051's auto-refresh-on-read cannot rewrite a graph mid-probe.
    """
    inherited = [
        os.path.abspath(entry)
        for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep)
        if entry
    ]
    return {
        "PATH": "/usr/bin:/usr/local/bin:/bin",
        "HOME": str(home),
        "PYTHONPATH": os.pathsep.join([weld_import_root(), *inherited]),
        "PYTHONPYCACHEPREFIX": str(home / "pycache"),
        "PYTHONHASHSEED": "0",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "WELD_TELEMETRY": "off",
        "WELD_AUTO_REFRESH": "0",
        "WELD_SOURCE_CHECKOUT_NOTICE": "off",
    }


def specifiers(raw: object) -> set[str]:
    """``props.imports_from`` as bare specifiers.

    The tree-sitter ``imports`` capture keeps the source string literal's own
    quote characters for TypeScript and Rust; Go is stripped in the strategy.
    Normalising here lets the corpus state one expected set per boundary
    instead of one per language's quoting habit.
    """
    if not isinstance(raw, list):
        return set()
    return {str(item).strip('"\'') for item in raw if item}


class _DiscoveredCorpus(unittest.TestCase):
    """One materialised corpus, one ``wd discover``, then the assertions."""

    #: Overridden per subclass with a ``_route_boundary_corpus`` config.
    CONFIG: str = ""

    graph: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        tmp = Path(cls._tmp.name)
        home = tmp / "home"
        home.mkdir(parents=True, exist_ok=True)
        cls.root = tmp / "repo"
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.env = cli_env(home)
        materialize(cls.root, cls.CONFIG)
        cls._assert_cli_is_this_checkout()
        cls._assert_grammars_available()
        cls._wd("discover", "--output", ".weld/graph.json")
        cls.graph = json.loads(
            (cls.root / ".weld" / "graph.json").read_text(encoding="utf-8")
        )

    # -- running the CLI -------------------------------------------------

    @classmethod
    def _wd(cls, *args: str) -> subprocess.CompletedProcess:
        argv = [sys.executable, "-m", "weld", *args]
        proc = subprocess.run(
            argv, cwd=str(cls.root), env=cls.env, capture_output=True,
            text=True, input="", timeout=CLI_TIMEOUT_SECONDS,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"`wd {' '.join(args)}` failed (rc={proc.returncode}):\n"
                f"{proc.stdout}\n{proc.stderr}"
            )
        return proc

    @classmethod
    def _assert_cli_is_this_checkout(cls) -> None:
        """The subprocess must import weld from the tree under test.

        A ``python -m weld`` resolving to some other installed weld runs green
        and proves nothing about this branch.
        """
        proc = subprocess.run(
            [sys.executable, "-c", "import weld; print(weld.__file__)"],
            cwd=str(cls.root), env=cls.env, capture_output=True, text=True,
            timeout=CLI_TIMEOUT_SECONDS,
        )
        expected = (Path(weld_import_root()) / "weld").resolve()
        loaded = Path(proc.stdout.strip() or "/nonexistent").parent.resolve()
        if proc.returncode != 0 or loaded != expected:
            raise AssertionError(
                f"the CLI subprocess imports weld from {loaded}, not the tree "
                f"under test ({expected}); rc={proc.returncode}\n{proc.stderr}"
            )

    @classmethod
    def _assert_grammars_available(cls) -> None:
        missing = [
            module
            for module in REQUIRED_GRAMMAR_MODULES
            if subprocess.run(
                [sys.executable, "-c", f"import {module}"], env=cls.env,
                capture_output=True, text=True, timeout=CLI_TIMEOUT_SECONDS,
            ).returncode != 0
        ]
        if missing:
            raise AssertionError(
                f"{sys.executable} cannot import {', '.join(missing)}. This "
                "probe asserts what weld records with real grammars loaded, "
                "so it fails rather than skips: without them the tree-sitter "
                "pass mints no file node and every case here would pass "
                "vacuously. Under Bazel the grammars are declared deps of "
                "the target -- check them."
            )

    # -- reading the graph -----------------------------------------------

    def file_node_id(self, boundary: Boundary) -> str:
        """The single ``file`` node id the graph holds for *boundary*."""
        hits = sorted(
            node_id
            for node_id, node in graph_nodes(self.graph).items()
            if node.get("type") == "file"
            and (node.get("props") or {}).get("file") == boundary.path
        )
        self.assertEqual(
            len(hits), 1,
            f"expected exactly one file node for {boundary.path}, got {hits}",
        )
        return hits[0]

    def file_props(self, boundary: Boundary) -> dict:
        """``props`` of that node."""
        node = graph_nodes(self.graph)[self.file_node_id(boundary)]
        return node.get("props") or {}

    def exposes_targets(self, boundary: Boundary) -> set[str]:
        """Route ids the boundary file node ``exposes``."""
        node_id = self.file_node_id(boundary)
        return {
            str(edge.get("to"))
            for edge in graph_edges(self.graph)
            if edge.get("from") == node_id and edge.get("type") == "exposes"
        }


class RoutesDeclaredLastTest(_DiscoveredCorpus):
    """The defect: framework entries appended after the language entries."""

    CONFIG = ROUTES_LAST

    def test_boundary_file_keeps_its_tree_sitter_evidence(self) -> None:
        """Every fact the tree-sitter pass recorded survives the route pass."""
        for boundary in BOUNDARIES:
            with self.subTest(strategy=boundary.strategy):
                props = self.file_props(boundary)
                self.assertEqual(
                    set(props.get("exports") or ()), set(boundary.exports),
                    f"{boundary.path}: props.exports lost; the node reads "
                    f"{sorted(props)}",
                )
                self.assertGreater(
                    props.get("line_count", 0), 0,
                    f"{boundary.path}: props.line_count lost",
                )
                self.assertLessEqual(
                    boundary.imports, specifiers(props.get("imports_from")),
                    f"{boundary.path}: props.imports_from lost",
                )
                self.assertEqual(
                    props.get("confidence"), "definite",
                    f"{boundary.path}: the definite tree-sitter claim lost "
                    f"the node id to the {boundary.strategy} placeholder",
                )

    def test_boundary_file_keeps_its_first_party_import_targets(self) -> None:
        """ADR 0142 D3's alias-bound import map survives the route pass."""
        for boundary in BOUNDARIES:
            if not boundary.import_targets:
                continue
            with self.subTest(strategy=boundary.strategy):
                props = self.file_props(boundary)
                self.assertEqual(
                    props.get("import_targets"), boundary.import_targets,
                    f"{boundary.path}: props.import_targets lost",
                )

    def test_the_exposes_edge_still_binds(self) -> None:
        """The edge the placeholder exists for resolves onto the winner."""
        for boundary in BOUNDARIES:
            with self.subTest(strategy=boundary.strategy):
                self.assertEqual(
                    self.exposes_targets(boundary), set(boundary.routes),
                )
        assert_edges_resolve(self.graph, {})

    def test_the_boundary_file_keeps_the_implementation_role(self) -> None:
        """``roles`` is not what the placeholder contributed; it is shared."""
        for boundary in BOUNDARIES:
            with self.subTest(strategy=boundary.strategy):
                props = self.file_props(boundary)
                self.assertIn("implementation", props.get("roles") or ())


class RoutesDeclaredFirstTest(RoutesDeclaredLastTest):
    """The ``wd init`` order. Same corpus, same assertions, entries swapped.

    Green before the fix and after it, and inherited rather than restated on
    purpose: "the two orders agree" is only a claim if both are asked exactly
    the same questions.
    """

    CONFIG = ROUTES_FIRST


class RoutesOnlyTest(_DiscoveredCorpus):
    """No tree-sitter entry: the placeholder is the whole reason it exists."""

    CONFIG = ROUTES_ONLY

    def test_the_placeholder_still_mints_the_boundary_file_node(self) -> None:
        for boundary in BOUNDARIES:
            with self.subTest(strategy=boundary.strategy):
                props = self.file_props(boundary)
                self.assertEqual(props.get("file"), boundary.path)
                self.assertEqual(
                    props.get("source_strategy"), boundary.strategy,
                )

    def test_the_exposes_edge_still_binds(self) -> None:
        for boundary in BOUNDARIES:
            with self.subTest(strategy=boundary.strategy):
                self.assertEqual(
                    self.exposes_targets(boundary), set(boundary.routes),
                )
        assert_edges_resolve(self.graph, {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
