"""Unit tests for the intra-repo origin reconciliation pass (ADR 0042).

A single-repo ``wd discover`` run partitions its sources into independent
globs (``weld/*.py``, ``weld/strategies/*.py``, ...). Each
``python_callgraph.extract()`` call only knows the modules its *own*
glob matched, so a cross-glob call -- e.g. a ``weld/strategies/*.py``
symbol calling ``weld.discover.discover`` -- mints the resolved target
node with ``origin="external"`` because ``weld.discover`` is not in the
strategies-batch project-module set. The orchestrator then merges
batches with ``dict.update`` (last-batch-wins), so that speculative
``external`` node clobbers the earlier definite ``project`` node the
``weld/*.py`` batch walked.

ADR 0042's Python rule is "Target resolves to a path inside any project
file set discovered **by this run** -> ``project``". The run-level
project file set is exactly the set of ``module`` props on every node
already tagged ``origin="project"``. ``reconcile_intra_repo_origins``
unions those (per language, to keep cross-language collisions
impossible) and promotes any same-language ``external`` symbol whose
``module`` falls in that set back to ``project``.

These tests pin that contract directly on the node mapping, including
the non-goal guarantees: genuine third-party ``external`` symbols and
``stdlib`` / ``unresolved`` symbols are never touched, the pass is a
no-op (returns 0) when nothing collides, and the language partition
holds.
"""

from __future__ import annotations

import unittest

from weld._discover_origin_reconcile import reconcile_intra_repo_origins
from weld._graph_origin import classify_node


def _symbol(module: str, origin: str, *, language: str = "python") -> dict:
    return {
        "type": "symbol",
        "label": module.split(".")[-1],
        "props": {
            "module": module,
            "language": language,
            "origin": origin,
        },
    }


class ReconcileIntraRepoOriginsTest(unittest.TestCase):
    def test_cross_batch_first_party_external_promoted_to_project(self) -> None:
        # The walked definite-project symbol establishes that
        # ``weld.discover`` is a project module for this run; the
        # speculative cross-batch target that clobbered it must be
        # promoted back to project.
        nodes = {
            "symbol:py:weld.discover:discover": _symbol(
                "weld.discover", "external"
            ),
            "symbol:py:weld.discover:_helper": _symbol(
                "weld.discover", "project"
            ),
        }
        changed = reconcile_intra_repo_origins(nodes)
        self.assertEqual(changed, 1)
        self.assertEqual(
            nodes["symbol:py:weld.discover:discover"]["props"]["origin"],
            "project",
        )
        # And the canonical classifier now agrees.
        self.assertEqual(
            classify_node(nodes["symbol:py:weld.discover:discover"]),
            "project",
        )

    def test_genuine_external_is_left_alone(self) -> None:
        # ``numpy.array`` has no project-tagged sibling, so it stays
        # external -- the pass must not reclassify real third-party code.
        nodes = {
            "symbol:py:numpy:array": _symbol("numpy", "external"),
            "symbol:py:weld.cli:main": _symbol("weld.cli", "project"),
        }
        changed = reconcile_intra_repo_origins(nodes)
        self.assertEqual(changed, 0)
        self.assertEqual(
            classify_node(nodes["symbol:py:numpy:array"]), "external"
        )

    def test_stdlib_and_unresolved_untouched(self) -> None:
        nodes = {
            "symbol:py:os.path:join": _symbol("os.path", "stdlib"),
            "symbol:unresolved:print": {
                "type": "symbol",
                "label": "print",
                "props": {"module": "", "language": "python", "origin": "unresolved"},
            },
            "symbol:py:weld.discover:run": _symbol("weld.discover", "project"),
        }
        changed = reconcile_intra_repo_origins(nodes)
        self.assertEqual(changed, 0)
        self.assertEqual(classify_node(nodes["symbol:py:os.path:join"]), "stdlib")
        self.assertEqual(
            classify_node(nodes["symbol:unresolved:print"]), "unresolved"
        )

    def test_language_partition_prevents_cross_language_promotion(self) -> None:
        # A C++ symbol whose dotted module string collides with a Python
        # project module must NOT be promoted off the Python project set.
        nodes = {
            "symbol:py:shared:thing": _symbol("shared", "project"),
            "symbol:cpp:shared:thing": _symbol(
                "shared", "external", language="cpp"
            ),
        }
        changed = reconcile_intra_repo_origins(nodes)
        self.assertEqual(changed, 0)
        self.assertEqual(
            classify_node(nodes["symbol:cpp:shared:thing"]), "external"
        )

    def test_run_level_context_recovers_clobbered_single_symbol_module(
        self,
    ) -> None:
        # The single-symbol-module case: the only project node for
        # ``core.engine`` was the same symbol that got clobbered to
        # external, so no surviving project node names the module. The
        # run-level set the strategy published into context (keyed on the
        # source file set, not node survival) still names it, so the
        # promotion must still happen.
        nodes = {
            "symbol:py:core.engine:run": _symbol("core.engine", "external"),
        }
        context = {"python_project_modules": {"core.engine"}}
        changed = reconcile_intra_repo_origins(nodes, context)
        self.assertEqual(changed, 1)
        self.assertEqual(
            classify_node(nodes["symbol:py:core.engine:run"]), "project"
        )

    def test_run_level_context_does_not_promote_external_modules(self) -> None:
        # A module absent from the run-level set stays external even when
        # context is supplied.
        nodes = {"symbol:py:numpy:array": _symbol("numpy", "external")}
        context = {"python_project_modules": {"core.engine"}}
        self.assertEqual(reconcile_intra_repo_origins(nodes, context), 0)
        self.assertEqual(
            classify_node(nodes["symbol:py:numpy:array"]), "external"
        )

    def test_is_idempotent(self) -> None:
        nodes = {
            "symbol:py:weld.discover:discover": _symbol(
                "weld.discover", "external"
            ),
            "symbol:py:weld.discover:_helper": _symbol(
                "weld.discover", "project"
            ),
        }
        first = reconcile_intra_repo_origins(nodes)
        second = reconcile_intra_repo_origins(nodes)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)

    def test_empty_or_no_project_modules_is_noop(self) -> None:
        self.assertEqual(reconcile_intra_repo_origins({}), 0)
        nodes = {"symbol:py:numpy:array": _symbol("numpy", "external")}
        self.assertEqual(reconcile_intra_repo_origins(nodes), 0)

    def test_only_symbol_nodes_promoted_not_files_or_modules(self) -> None:
        # The clobber bug only affects speculatively-minted ``symbol``
        # target nodes; file/module nodes carry their own correct origin
        # from their owning strategy and are out of scope for promotion.
        nodes = {
            "file:weld/discover.py": {
                "type": "file",
                "label": "discover.py",
                "props": {
                    "module": "weld.discover",
                    "language": "python",
                    "origin": "external",
                },
            },
            "symbol:py:weld.discover:run": _symbol("weld.discover", "project"),
        }
        changed = reconcile_intra_repo_origins(nodes)
        self.assertEqual(changed, 0)
        self.assertEqual(
            nodes["file:weld/discover.py"]["props"]["origin"], "external"
        )


if __name__ == "__main__":
    unittest.main()
