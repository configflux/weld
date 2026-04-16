"""Tests for the package_import_resolver cross-repo resolver.

Covers all acceptance criteria: matched import edges, edge props,
deterministic output, unresolved import skip, missing child graceful
degradation, strategy gate, self-import exclusion, and node type filters.
"""

from __future__ import annotations

import unittest

from weld.cross_repo.base import (
    CrossRepoEdge,
    ResolverContext,
    get_resolver,
    resolver_names,
    run_resolvers,
)

import weld.cross_repo.package_import_resolver  # noqa: F401 -- registration side effect

SEP = "\x1f"


class _G:
    """Minimal graph stand-in with a nodes property."""

    def __init__(self, nodes: list[dict]) -> None:
        self._nodes = nodes

    @property
    def nodes(self) -> list[dict]:
        return list(self._nodes)


def _ctx(children: dict[str, tuple[_G, bytes]], strategies: list[str] | None = None):
    """Shorthand context builder."""
    loaded = {n: g for n, (g, _) in children.items()}
    hashes = {n: ResolverContext.hash_bytes(r) for n, (_, r) in children.items()}
    return ResolverContext(
        workspace_root="/tmp/ws",
        cross_repo_strategies=list(strategies if strategies is not None else ["package_import_resolver"]),
        children=loaded,
        child_hashes=hashes,
    )


def _mod(node_id: str, imports: list[str]) -> dict:
    return {"id": node_id, "type": "python_module", "imports_from": imports}


def _pkg(node_id: str, name: str) -> dict:
    return {"id": node_id, "type": "package", "name": name}


# Reusable fixture pair: repo-a imports shared_utils from libs-shared-utils.
_REPO_A = (_G([_mod("app.main", ["shared_utils"])]), b'a')
_LIBS_SU = (_G([_pkg("shared_utils", "shared_utils")]), b'b')


class RegistrationTests(unittest.TestCase):
    def test_registered(self) -> None:
        self.assertIn("package_import_resolver", resolver_names())

    def test_retrievable(self) -> None:
        self.assertEqual(get_resolver("package_import_resolver").name, "package_import_resolver")


class BasicMatchTests(unittest.TestCase):
    """Core matching: python_module imports_from -> package node."""

    def test_emits_depends_on_edge(self) -> None:
        edges = run_resolvers(_ctx({"repo-a": _REPO_A, "libs-su": _LIBS_SU}))
        self.assertEqual(len(edges), 1)
        e = edges[0]
        self.assertEqual(e.type, "depends_on")
        self.assertEqual(e.from_id, f"repo-a{SEP}app.main")
        self.assertEqual(e.to_id, f"libs-su{SEP}shared_utils")

    def test_edge_props(self) -> None:
        edges = run_resolvers(_ctx({"repo-a": _REPO_A, "libs-su": _LIBS_SU}))
        p = edges[0].props
        self.assertEqual(p["import_name"], "shared_utils")
        self.assertEqual(p["source_child"], "repo-a")

    def test_multiple_imports(self) -> None:
        ga = _G([_mod("app.main", ["shared_utils", "auth_lib"])])
        gs = _G([_pkg("shared_utils", "shared_utils")])
        gauth = _G([_pkg("auth_lib", "auth_lib")])
        edges = run_resolvers(_ctx({
            "repo-a": (ga, b'a'), "lib-s": (gs, b's'), "lib-a": (gauth, b'c'),
        }))
        self.assertEqual(len(edges), 2)
        self.assertEqual(sorted(e.props["import_name"] for e in edges), ["auth_lib", "shared_utils"])

    def test_edge_is_typed_dataclass(self) -> None:
        edges = run_resolvers(_ctx({"repo-a": _REPO_A, "libs-su": _LIBS_SU}))
        self.assertIsInstance(edges[0], CrossRepoEdge)

    def test_edge_ids_contain_unit_separator(self) -> None:
        edges = run_resolvers(_ctx({"repo-a": _REPO_A, "libs-su": _LIBS_SU}))
        self.assertIn(SEP, edges[0].from_id)
        self.assertIn(SEP, edges[0].to_id)


class DeterminismTests(unittest.TestCase):
    def test_identical_output_on_repeat(self) -> None:
        ch = {"repo-a": _REPO_A, "libs-su": _LIBS_SU}
        e1 = [e.to_dict() for e in run_resolvers(_ctx(ch))]
        e2 = [e.to_dict() for e in run_resolvers(_ctx(ch))]
        self.assertEqual(e1, e2)

    def test_stable_across_child_insertion_order(self) -> None:
        ga = _G([_mod("app.main", ["px", "py"])])
        gx, gy = _G([_pkg("px", "px")]), _G([_pkg("py", "py")])
        o1 = {"repo-a": (ga, b'a'), "lx": (gx, b'x'), "ly": (gy, b'y')}
        o2 = {"ly": (gy, b'y'), "repo-a": (ga, b'a'), "lx": (gx, b'x')}
        self.assertEqual(
            [e.to_dict() for e in run_resolvers(_ctx(o1))],
            [e.to_dict() for e in run_resolvers(_ctx(o2))],
        )


class UnmatchedImportTests(unittest.TestCase):
    def test_no_edge_for_unmatched(self) -> None:
        g = _G([_mod("m", ["no_such_pkg"])])
        self.assertEqual(run_resolvers(_ctx({"r": (g, b'r')})), [])

    def test_empty_imports_from(self) -> None:
        g = _G([_mod("m", [])])
        self.assertEqual(run_resolvers(_ctx({"r": (g, b'r')})), [])

    def test_missing_imports_from_key(self) -> None:
        g = _G([{"id": "m", "type": "python_module"}])
        self.assertEqual(run_resolvers(_ctx({"r": (g, b'r')})), [])


class MissingChildTests(unittest.TestCase):
    def test_only_present_children(self) -> None:
        g = _G([_mod("m", ["shared_utils"])])
        self.assertEqual(run_resolvers(_ctx({"repo-a": (g, b'a')})), [])

    def test_empty_children(self) -> None:
        self.assertEqual(run_resolvers(_ctx({})), [])


class StrategyGateTests(unittest.TestCase):
    def test_not_invoked_when_absent(self) -> None:
        ctx = _ctx({"repo-a": _REPO_A, "libs-su": _LIBS_SU}, strategies=[])
        self.assertEqual(run_resolvers(ctx), [])


class SelfImportTests(unittest.TestCase):
    def test_no_self_edges(self) -> None:
        g = _G([_mod("app.main", ["shared_utils"]), _pkg("shared_utils", "shared_utils")])
        self.assertEqual(run_resolvers(_ctx({"repo-a": (g, b'a')})), [])


class NodeTypeFilterTests(unittest.TestCase):
    def test_non_python_module_ignored(self) -> None:
        ga = _G([{"id": "f", "type": "function", "imports_from": ["shared_utils"]}])
        gs = _G([_pkg("shared_utils", "shared_utils")])
        self.assertEqual(run_resolvers(_ctx({"r": (ga, b'a'), "s": (gs, b'b')})), [])

    def test_non_package_target_ignored(self) -> None:
        ga = _G([_mod("m", ["shared_utils"])])
        gs = _G([{"id": "shared_utils", "type": "module", "name": "shared_utils"}])
        self.assertEqual(run_resolvers(_ctx({"r": (ga, b'a'), "s": (gs, b'b')})), [])


if __name__ == "__main__":
    unittest.main()
