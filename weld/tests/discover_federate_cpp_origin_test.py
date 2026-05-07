"""Tests for the federated C++ origin re-tagging pass (ADR 0042 §Federation).

The federation rule in ADR 0042 §Federation is language-agnostic: any
cross-child reference whose target is defined in a sibling repo must
classify as ``origin='project'``. Sibling
``discover_federate_origin_test`` exercises the Python branch; this
module exercises the C++ branch and the cross-language isolation
invariants the dispatch must uphold.

The C++ branch keys off ``props.module`` exactly like Python (the
``tree_sitter`` strategy stamps a stable dotted ``module`` on every
emitted ``symbol`` and on resolved layer-2 includes via
``cpp_resolver``). The federation re-tag pass therefore unions the
``module`` strings of every C++ project node across children and
promotes external C++ symbols whose ``module`` falls in that union.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._discover_federate import (
    federated_cpp_project_modules,
    federated_python_project_modules,
    retag_external_cpp_origins,
    retag_external_python_origins,
    retag_federated_origins_on_disk,
)
from weld.tests._discover_federate_origin_fixtures import (
    cpp_external_target,
    cpp_project_symbol,
    empty_graph,
    python_external_target,
    python_project_symbol,
    state_present,
    write_child_graph,
)
from weld.workspace import ChildEntry, WorkspaceConfig


# ---------------------------------------------------------------------------
# Pure helper: federated_cpp_project_modules
# ---------------------------------------------------------------------------


class FederatedCppProjectModulesTest(unittest.TestCase):
    """``federated_cpp_project_modules`` unions every child's C++ project modules."""

    def test_collects_module_props_from_project_cpp_symbols(self) -> None:
        a_sid, a_node = cpp_project_symbol("services.alpha.foo", "do_foo")
        b_sid, b_node = cpp_project_symbol("apps.beta.main", "main")
        children = {
            "alpha": empty_graph({a_sid: a_node}),
            "beta": empty_graph({b_sid: b_node}),
        }
        modules = federated_cpp_project_modules(children)
        self.assertEqual(
            modules, frozenset({"services.alpha.foo", "apps.beta.main"})
        )

    def test_ignores_external_and_non_cpp_nodes(self) -> None:
        ext_sid, ext_node = cpp_external_target("third_party.lib", "fn")
        proj_sid, proj_node = cpp_project_symbol("svc.alpha", "do_alpha")
        py_sid, py_node = python_project_symbol("svc.alpha", "do_alpha")
        children = {
            "alpha": empty_graph(
                {proj_sid: proj_node, ext_sid: ext_node, py_sid: py_node}
            ),
        }
        modules = federated_cpp_project_modules(children)
        # Only the cpp project symbol's module is in the set; the
        # python project symbol with a colliding module string is
        # filtered out by the language check.
        self.assertEqual(modules, frozenset({"svc.alpha"}))

    def test_empty_when_no_cpp_project_nodes(self) -> None:
        children = {"alpha": empty_graph({})}
        self.assertEqual(federated_cpp_project_modules(children), frozenset())


# ---------------------------------------------------------------------------
# Pure helper: retag_external_cpp_origins
# ---------------------------------------------------------------------------


class RetagExternalCppOriginsTest(unittest.TestCase):
    """``retag_external_cpp_origins`` re-tags external -> project in-place."""

    def test_retags_external_when_module_is_in_federated_set(self) -> None:
        ext_sid, ext_node = cpp_external_target("services.alpha.foo", "do_foo")
        graph = empty_graph({ext_sid: ext_node})
        federated = frozenset({"services.alpha.foo"})
        changed = retag_external_cpp_origins(graph, federated)
        self.assertEqual(changed, 1)
        self.assertEqual(graph["nodes"][ext_sid]["props"]["origin"], "project")

    def test_does_not_retag_when_module_outside_federated_set(self) -> None:
        ext_sid, ext_node = cpp_external_target("third_party.lib", "fn")
        graph = empty_graph({ext_sid: ext_node})
        federated = frozenset({"services.alpha.foo"})
        changed = retag_external_cpp_origins(graph, federated)
        self.assertEqual(changed, 0)
        self.assertEqual(
            graph["nodes"][ext_sid]["props"]["origin"], "external"
        )

    def test_leaves_non_external_origins_untouched(self) -> None:
        proj_sid, proj_node = cpp_project_symbol("services.alpha.foo", "do_foo")
        graph = empty_graph({proj_sid: proj_node})
        federated = frozenset({"services.alpha.foo"})
        changed = retag_external_cpp_origins(graph, federated)
        self.assertEqual(changed, 0)
        self.assertEqual(graph["nodes"][proj_sid]["props"]["origin"], "project")

    def test_skips_unresolved_origin(self) -> None:
        # ADR 0042 §C++ uses ``unresolved`` as a layer-1 sentinel.
        # The federation pass only promotes definite ``external`` tags;
        # an ``unresolved`` sentinel stays unresolved (layer-2 owns its
        # upgrade path via ``upgrade_origin``).
        sid = "symbol:unresolved:do_foo"
        graph = empty_graph(
            {
                sid: {
                    "type": "symbol",
                    "label": "do_foo",
                    "props": {
                        "module": "services.alpha.foo",
                        "language": "cpp",
                        "origin": "unresolved",
                    },
                }
            }
        )
        federated = frozenset({"services.alpha.foo"})
        changed = retag_external_cpp_origins(graph, federated)
        self.assertEqual(changed, 0)
        self.assertEqual(graph["nodes"][sid]["props"]["origin"], "unresolved")

    def test_skips_non_cpp_external_nodes(self) -> None:
        # A Python external node with a colliding module name must
        # not be re-tagged by the C++ pass.
        sid, node = python_external_target("services.alpha.foo", "do_foo")
        graph = empty_graph({sid: node})
        federated = frozenset({"services.alpha.foo"})
        changed = retag_external_cpp_origins(graph, federated)
        self.assertEqual(changed, 0)
        self.assertEqual(graph["nodes"][sid]["props"]["origin"], "external")

    def test_empty_federated_set_is_a_noop(self) -> None:
        ext_sid, ext_node = cpp_external_target("services.alpha.foo", "do_foo")
        graph = empty_graph({ext_sid: ext_node})
        changed = retag_external_cpp_origins(graph, frozenset())
        self.assertEqual(changed, 0)
        self.assertEqual(
            graph["nodes"][ext_sid]["props"]["origin"], "external"
        )


# ---------------------------------------------------------------------------
# Cross-language isolation: python and cpp do not bleed into each other
# ---------------------------------------------------------------------------


class CrossLanguageIsolationTest(unittest.TestCase):
    """The Python and C++ helpers must stay independent.

    A C++ project symbol must never feed the Python module set, and a
    Python external target must never be re-tagged by the C++ pass —
    the language check is the only gate that prevents accidental
    promotion when ``module`` strings happen to collide across
    languages.
    """

    def test_python_helper_ignores_cpp_project_nodes(self) -> None:
        cpp_sid, cpp_node = cpp_project_symbol("services.alpha.foo", "do_foo")
        children = {"alpha": empty_graph({cpp_sid: cpp_node})}
        self.assertEqual(
            federated_python_project_modules(children), frozenset()
        )

    def test_cpp_helper_ignores_python_project_nodes(self) -> None:
        py_sid, py_node = python_project_symbol("services.alpha.foo", "do_foo")
        children = {"alpha": empty_graph({py_sid: py_node})}
        self.assertEqual(
            federated_cpp_project_modules(children), frozenset()
        )

    def test_python_retag_ignores_cpp_external_with_matching_module(self) -> None:
        ext_sid, ext_node = cpp_external_target("services.alpha.foo", "do_foo")
        graph = empty_graph({ext_sid: ext_node})
        federated = frozenset({"services.alpha.foo"})
        changed = retag_external_python_origins(graph, federated)
        self.assertEqual(changed, 0)
        self.assertEqual(
            graph["nodes"][ext_sid]["props"]["origin"], "external"
        )


# ---------------------------------------------------------------------------
# Disk pass: retag_federated_origins_on_disk now handles cpp + python
# ---------------------------------------------------------------------------


class RetagFederatedOriginsOnDiskCppTest(unittest.TestCase):
    """The on-disk pass re-tags both Python and C++ external symbols."""

    def test_cross_child_external_cpp_target_is_retagged_to_project(self) -> None:
        config = WorkspaceConfig(
            children=[
                ChildEntry(name="alpha", path="alpha"),
                ChildEntry(name="beta", path="beta"),
            ],
            cross_repo_strategies=[],
        )
        state = state_present(["alpha", "beta"])

        a_sid, a_node = cpp_project_symbol("services.alpha.foo", "do_foo")
        b_caller_sid, b_caller_node = cpp_project_symbol(
            "apps.beta.main", "main"
        )
        ext_sid, ext_node = cpp_external_target("services.alpha.foo", "do_foo")

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_child_graph(root, "alpha", empty_graph({a_sid: a_node}))
            beta_path = write_child_graph(
                root,
                "beta",
                empty_graph(
                    {b_caller_sid: b_caller_node, ext_sid: ext_node}
                ),
            )

            changed = retag_federated_origins_on_disk(root, config, state)

            self.assertEqual(changed, {"beta": 1})

            beta_graph = json.loads(beta_path.read_text(encoding="utf-8"))
            self.assertEqual(
                beta_graph["nodes"][ext_sid]["props"]["origin"],
                "project",
            )
            self.assertEqual(
                beta_graph["nodes"][b_caller_sid]["props"]["origin"],
                "project",
            )

    def test_mixed_language_workspace_retags_each_language_independently(
        self,
    ) -> None:
        """Python and C++ federated sets are unioned per language.

        Child alpha ships a Python project symbol and a C++ project
        symbol whose dotted modules happen to share a string. Child
        beta's external nodes get re-tagged on a per-language basis.
        The disk-pass change count for beta is the sum across both
        language passes.
        """
        config = WorkspaceConfig(
            children=[
                ChildEntry(name="alpha", path="alpha"),
                ChildEntry(name="beta", path="beta"),
            ],
            cross_repo_strategies=[],
        )
        state = state_present(["alpha", "beta"])

        a_py_sid, a_py_node = python_project_symbol("lib_a.foo", "py_fn")
        a_cpp_sid, a_cpp_node = cpp_project_symbol("lib_a.foo", "cpp_fn")
        b_py_ext_sid, b_py_ext_node = python_external_target(
            "lib_a.foo", "py_fn"
        )
        b_cpp_ext_sid, b_cpp_ext_node = cpp_external_target(
            "lib_a.foo", "cpp_fn"
        )

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_child_graph(
                root,
                "alpha",
                empty_graph({a_py_sid: a_py_node, a_cpp_sid: a_cpp_node}),
            )
            beta_path = write_child_graph(
                root,
                "beta",
                empty_graph(
                    {
                        b_py_ext_sid: b_py_ext_node,
                        b_cpp_ext_sid: b_cpp_ext_node,
                    }
                ),
            )

            changed = retag_federated_origins_on_disk(root, config, state)

            self.assertEqual(changed, {"beta": 2})

            beta_graph = json.loads(beta_path.read_text(encoding="utf-8"))
            self.assertEqual(
                beta_graph["nodes"][b_py_ext_sid]["props"]["origin"],
                "project",
            )
            self.assertEqual(
                beta_graph["nodes"][b_cpp_ext_sid]["props"]["origin"],
                "project",
            )

    def test_cpp_only_workspace_does_not_break_when_no_python_nodes(self) -> None:
        """A workspace with only C++ children still re-tags correctly."""
        config = WorkspaceConfig(
            children=[
                ChildEntry(name="alpha", path="alpha"),
                ChildEntry(name="beta", path="beta"),
            ],
            cross_repo_strategies=[],
        )
        state = state_present(["alpha", "beta"])

        a_sid, a_node = cpp_project_symbol("services.alpha.foo", "do_foo")
        ext_sid, ext_node = cpp_external_target(
            "services.alpha.foo", "do_foo"
        )

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_child_graph(root, "alpha", empty_graph({a_sid: a_node}))
            beta_path = write_child_graph(
                root, "beta", empty_graph({ext_sid: ext_node})
            )

            changed = retag_federated_origins_on_disk(root, config, state)

            self.assertEqual(changed, {"beta": 1})
            beta_graph = json.loads(beta_path.read_text(encoding="utf-8"))
            self.assertEqual(
                beta_graph["nodes"][ext_sid]["props"]["origin"],
                "project",
            )


if __name__ == "__main__":
    unittest.main()
