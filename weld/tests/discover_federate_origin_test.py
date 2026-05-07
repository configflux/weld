"""Tests for the federated origin re-tagging pass (ADR 0042 §Federation).

When ``wd discover`` runs at a federated workspace root, the
``python_callgraph`` strategy in each child has only seen its own glob.
A symbol defined in child A and imported from child B is therefore
speculatively minted in child B's graph with ``origin="external"``,
because child A's modules are not in child B's ``project_modules`` set.

ADR 0042 §Federation says the opposite: in a polyrepo workspace,
``project`` means *any* federated child of the active root, not only
the root itself. The federation pipeline is responsible for closing
that gap.

These tests cover the post-discovery federation pass that scans every
present child graph for project-tagged Python symbol/file/module nodes,
unions their dotted module paths, and re-tags ``symbol`` nodes whose
``origin`` is currently ``"external"`` to ``"project"`` when the
node's ``module`` falls inside that union.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._discover_federate import (
    federated_python_project_modules,
    retag_external_python_origins,
    retag_federated_origins_on_disk,
)
from weld.contract import SCHEMA_VERSION
from weld.workspace import ChildEntry, WorkspaceConfig
from weld.workspace_state import WorkspaceChildState, WorkspaceState


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _state_present(names: list[str]) -> WorkspaceState:
    return WorkspaceState(
        children={
            name: WorkspaceChildState(
                status="present",
                head_sha=None,
                head_ref=None,
                is_dirty=False,
                graph_path=f"{name}/.weld/graph.json",
                graph_sha256=None,
                last_seen_utc="2026-05-04T00:00:00+00:00",
            )
            for name in names
        },
    )


def _write_child_graph(root: Path, rel_path: str, payload: dict) -> Path:
    weld_dir = root / rel_path / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    graph_path = weld_dir / "graph.json"
    graph_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return graph_path


def _python_project_symbol(module: str, qualname: str) -> tuple[str, dict]:
    """Build a child-shaped Python project symbol node entry."""
    sid = f"symbol:py:{module}:{qualname}"
    body = {
        "type": "symbol",
        "label": qualname,
        "props": {
            "module": module,
            "qualname": qualname,
            "language": "python",
            "source_strategy": "python_callgraph",
            "authority": "derived",
            "confidence": "definite",
            "roles": ["implementation"],
            "origin": "project",
        },
    }
    return sid, body


def _python_external_target(module: str, qualname: str) -> tuple[str, dict]:
    """Build a speculative ``external``-tagged Python target node."""
    sid = f"symbol:py:{module}:{qualname}"
    body = {
        "type": "symbol",
        "label": qualname,
        "props": {
            "module": module,
            "qualname": qualname,
            "language": "python",
            "source_strategy": "python_callgraph",
            "authority": "derived",
            "confidence": "speculative",
            "roles": ["implementation"],
            "origin": "external",
        },
    }
    return sid, body


def _empty_graph(nodes: dict[str, dict] | None = None) -> dict:
    return {
        "meta": {"version": SCHEMA_VERSION, "schema_version": 1},
        "nodes": nodes or {},
        "edges": [],
    }


# ---------------------------------------------------------------------------
# Pure helper: federated_python_project_modules
# ---------------------------------------------------------------------------


class FederatedPythonProjectModulesTest(unittest.TestCase):
    """``federated_python_project_modules`` unions every child's project modules."""

    def test_collects_module_props_from_project_python_symbols(self) -> None:
        a_sid, a_node = _python_project_symbol("lib_a.foo", "bar")
        b_sid, b_node = _python_project_symbol("lib_b.app", "main")
        children = {
            "alpha": _empty_graph({a_sid: a_node}),
            "beta": _empty_graph({b_sid: b_node}),
        }
        modules = federated_python_project_modules(children)
        self.assertEqual(modules, frozenset({"lib_a.foo", "lib_b.app"}))

    def test_ignores_external_and_non_python_nodes(self) -> None:
        ext_sid, ext_node = _python_external_target("third_party.pkg", "fn")
        a_sid, a_node = _python_project_symbol("lib_a.foo", "bar")
        non_py = (
            "symbol:rs:lib_a.foo:bar",
            {
                "type": "symbol",
                "label": "bar",
                "props": {
                    "module": "lib_a.foo",
                    "language": "rust",
                    "origin": "project",
                },
            },
        )
        children = {
            "alpha": _empty_graph(
                {a_sid: a_node, ext_sid: ext_node, non_py[0]: non_py[1]}
            ),
        }
        modules = federated_python_project_modules(children)
        # Only the python project symbol's module is in the set.
        self.assertEqual(modules, frozenset({"lib_a.foo"}))

    def test_empty_when_no_python_project_nodes(self) -> None:
        children = {"alpha": _empty_graph({})}
        self.assertEqual(federated_python_project_modules(children), frozenset())


# ---------------------------------------------------------------------------
# Pure helper: retag_external_python_origins
# ---------------------------------------------------------------------------


class RetagExternalPythonOriginsTest(unittest.TestCase):
    """``retag_external_python_origins`` re-tags external -> project in-place.

    Returns the count of nodes mutated so callers can decide whether to
    write the graph back.
    """

    def test_retags_external_when_module_is_in_federated_set(self) -> None:
        ext_sid, ext_node = _python_external_target("lib_a.foo", "bar")
        graph = _empty_graph({ext_sid: ext_node})
        federated = frozenset({"lib_a.foo"})
        changed = retag_external_python_origins(graph, federated)
        self.assertEqual(changed, 1)
        self.assertEqual(graph["nodes"][ext_sid]["props"]["origin"], "project")

    def test_does_not_retag_when_module_outside_federated_set(self) -> None:
        ext_sid, ext_node = _python_external_target("third_party.pkg", "fn")
        graph = _empty_graph({ext_sid: ext_node})
        federated = frozenset({"lib_a.foo"})
        changed = retag_external_python_origins(graph, federated)
        self.assertEqual(changed, 0)
        self.assertEqual(
            graph["nodes"][ext_sid]["props"]["origin"], "external"
        )

    def test_leaves_non_external_origins_untouched(self) -> None:
        proj_sid, proj_node = _python_project_symbol("lib_a.foo", "bar")
        graph = _empty_graph({proj_sid: proj_node})
        federated = frozenset({"lib_a.foo"})
        changed = retag_external_python_origins(graph, federated)
        self.assertEqual(changed, 0)
        self.assertEqual(graph["nodes"][proj_sid]["props"]["origin"], "project")

    def test_skips_non_python_external_nodes(self) -> None:
        # A non-Python external node with a colliding module name must
        # not be re-tagged: the federated module set is Python-only.
        sid = "symbol:rs:lib_a.foo:bar"
        graph = _empty_graph(
            {
                sid: {
                    "type": "symbol",
                    "label": "bar",
                    "props": {
                        "module": "lib_a.foo",
                        "language": "rust",
                        "origin": "external",
                    },
                }
            }
        )
        federated = frozenset({"lib_a.foo"})
        changed = retag_external_python_origins(graph, federated)
        self.assertEqual(changed, 0)
        self.assertEqual(graph["nodes"][sid]["props"]["origin"], "external")

    def test_empty_federated_set_is_a_noop(self) -> None:
        ext_sid, ext_node = _python_external_target("lib_a.foo", "bar")
        graph = _empty_graph({ext_sid: ext_node})
        changed = retag_external_python_origins(graph, frozenset())
        self.assertEqual(changed, 0)
        self.assertEqual(
            graph["nodes"][ext_sid]["props"]["origin"], "external"
        )


# ---------------------------------------------------------------------------
# Disk pass: retag_federated_origins_on_disk
# ---------------------------------------------------------------------------


class RetagFederatedOriginsOnDiskTest(unittest.TestCase):
    """End-to-end disk pass: child A defines a module, child B imports it.

    The pass loads each child graph, builds the federated project module
    set, re-tags any speculative external Python target whose module lives
    in that set, and atomically rewrites changed children.
    """

    def test_cross_child_external_target_is_retagged_to_project(self) -> None:
        config = WorkspaceConfig(
            children=[
                ChildEntry(name="alpha", path="alpha"),
                ChildEntry(name="beta", path="beta"),
            ],
            cross_repo_strategies=[],
        )
        state = _state_present(["alpha", "beta"])

        # Child alpha: defines lib_a.foo:bar as a project symbol.
        a_sid, a_node = _python_project_symbol("lib_a.foo", "bar")

        # Child beta: imports lib_a.foo:bar; python_callgraph speculatively
        # mints a target node tagged origin="external" because lib_a.foo
        # is not in beta's local project module set.
        b_caller_sid, b_caller_node = _python_project_symbol(
            "app.main", "f"
        )
        ext_sid, ext_node = _python_external_target("lib_a.foo", "bar")

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_child_graph(root, "alpha", _empty_graph({a_sid: a_node}))
            beta_path = _write_child_graph(
                root,
                "beta",
                _empty_graph(
                    {
                        b_caller_sid: b_caller_node,
                        ext_sid: ext_node,
                    }
                ),
            )

            changed = retag_federated_origins_on_disk(root, config, state)

            self.assertEqual(changed, {"beta": 1})

            beta_graph = json.loads(beta_path.read_text(encoding="utf-8"))
            self.assertEqual(
                beta_graph["nodes"][ext_sid]["props"]["origin"],
                "project",
            )
            # Caller stays project.
            self.assertEqual(
                beta_graph["nodes"][b_caller_sid]["props"]["origin"],
                "project",
            )

    def test_no_changes_does_not_rewrite_files(self) -> None:
        """When nothing needs re-tagging, child graph bytes stay identical."""
        config = WorkspaceConfig(
            children=[
                ChildEntry(name="alpha", path="alpha"),
                ChildEntry(name="beta", path="beta"),
            ],
            cross_repo_strategies=[],
        )
        state = _state_present(["alpha", "beta"])

        a_sid, a_node = _python_project_symbol("lib_a.foo", "bar")
        b_sid, b_node = _python_project_symbol("lib_b.app", "main")
        # An external node whose module is genuinely third-party: must
        # remain external after the pass.
        ext_sid, ext_node = _python_external_target("requests", "get")

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha_path = _write_child_graph(
                root, "alpha", _empty_graph({a_sid: a_node})
            )
            beta_path = _write_child_graph(
                root,
                "beta",
                _empty_graph({b_sid: b_node, ext_sid: ext_node}),
            )

            alpha_before = alpha_path.read_bytes()
            beta_before = beta_path.read_bytes()

            changed = retag_federated_origins_on_disk(root, config, state)

            # No retags expected.
            self.assertEqual(changed, {})
            self.assertEqual(alpha_path.read_bytes(), alpha_before)
            self.assertEqual(beta_path.read_bytes(), beta_before)

    def test_skips_missing_and_corrupt_children(self) -> None:
        """Children with non-present status / unparseable graph are skipped."""
        config = WorkspaceConfig(
            children=[
                ChildEntry(name="alpha", path="alpha"),
                ChildEntry(name="missing", path="missing"),
                ChildEntry(name="corrupt", path="corrupt"),
            ],
            cross_repo_strategies=[],
        )
        state = WorkspaceState(
            children={
                "alpha": WorkspaceChildState(
                    status="present",
                    head_sha=None,
                    head_ref=None,
                    is_dirty=False,
                    graph_path="alpha/.weld/graph.json",
                    graph_sha256=None,
                    last_seen_utc="2026-05-04T00:00:00+00:00",
                ),
                "missing": WorkspaceChildState(
                    status="missing",
                    head_sha=None,
                    head_ref=None,
                    is_dirty=False,
                    graph_path="missing/.weld/graph.json",
                    graph_sha256=None,
                    last_seen_utc="2026-05-04T00:00:00+00:00",
                ),
                "corrupt": WorkspaceChildState(
                    status="present",
                    head_sha=None,
                    head_ref=None,
                    is_dirty=False,
                    graph_path="corrupt/.weld/graph.json",
                    graph_sha256=None,
                    last_seen_utc="2026-05-04T00:00:00+00:00",
                ),
            },
        )

        a_sid, a_node = _python_project_symbol("lib_a.foo", "bar")

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_child_graph(root, "alpha", _empty_graph({a_sid: a_node}))
            # Corrupt graph for the corrupt child.
            corrupt_dir = root / "corrupt" / ".weld"
            corrupt_dir.mkdir(parents=True, exist_ok=True)
            (corrupt_dir / "graph.json").write_text(
                "{not json", encoding="utf-8"
            )

            # Should not raise.
            changed = retag_federated_origins_on_disk(root, config, state)
            self.assertEqual(changed, {})


if __name__ == "__main__":
    unittest.main()
