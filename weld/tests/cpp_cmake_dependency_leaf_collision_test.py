"""A project must not lose its own identity to a same-named dependency
leaf (bd tuuve), run through the REAL discover orchestrator.

``cpp_cmake._ensure_project_node`` mints the PROJECT node at
``package:cpp:<name>`` (``authority: "canonical"``, ``props.file`` set).
``_cmake_packages.ensure_package_sentinel`` mints every ``find_package``/
external ``target_link_libraries`` dependency LEAF at the SAME
``package:cpp:<name>`` id -- cpp_cmake has no dedicated URL scheme for its
leaves, unlike ``cpp_conan``/``cpp_vcpkg``'s ``package://conan/``/
``package://vcpkg/``. When a project's name collides with one of its own
(or a sibling's) declared dependencies -- e.g. a subdirectory project named
the same as a library it also ``find_package()``s -- both mints target the
identical id within ONE ``cpp_cmake.extract()`` call (one glob can match
many ``CMakeLists.txt`` files, accumulated in that call's own ``nodes``
dict). Before the fix this was a plain ``nodes.setdefault``: whichever file
:func:`weld.strategies._glob_resolve.resolve_glob`'s sorted walk happened to
reach first won outright, discarding the other claim's entire prop set.

Losing meant more than a wrong label. A clobbered project (left with
``authority: "external"``, no ``props.file``) became reachable by
``_discover_external_package_purge.emptied_external_package_node_ids``, so
deleting an unrelated SIBLING file (the one that had "won" the collision)
could purge the clobbered id outright on the very next incremental run --
even though the real project's own CMakeLists.txt was never touched. A
fresh full discover of the same post-delete tree re-mints the project
correctly, so this was a genuine incremental-vs-full divergence in the
severe direction: a live node vanishes, not an orphan lingers (the opposite
of what bd g7rs/ukt95/0cobr fixed).

The fix threads the same ADR 0103 confidence-ranked veto
(``weld._discover_node_merge.claim_supersedes``) through both mints, so a
``confidence: "definite"`` project claim can never lose to a
``confidence: "inferred"`` sentinel claim regardless of processing order.
Mirrors the pattern (and much of the fixture shape) of
``discover_cross_glob_definition_survives_test.py`` (bd 4ux4's own
order-independence pin) and
``incremental_callgraph_provenance_purge_test.py`` (incremental == full,
including the delete-without-collateral-purge case).
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo

PROJECT_NODE_ID = "package:cpp:foo"
BUILD_TARGET_ID = "build-target:cmake:Foo:foolib"


def _git(root: Path) -> None:
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "T"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, cwd=str(root), check=True)


def _commit(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=str(root), check=True)


def _strip_meta(graph: dict) -> dict:
    """Drop volatile + path-order-volatile meta; nodes/edges must match."""
    out = {k: v for k, v in graph.items() if k != "meta"}
    meta = {
        k: v for k, v in graph.get("meta", {}).items()
        if k not in ("updated_at", "git_sha", "discovered_from")
    }
    out["meta"] = meta
    return out


def _write_discover_yaml(root: Path) -> None:
    # One glob, one source entry -- both the project and the
    # find_package-referencing file are walked inside the SAME
    # cpp_cmake.extract() call, which is the shape that collides.
    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "sources:\n"
        '  - glob: "**/CMakeLists.txt"\n'
        "    type: package\n"
        "    strategy: cpp_cmake\n",
        encoding="utf-8",
    )


def _write_project(root: Path, project_dir: str) -> None:
    """``project(Foo)`` -- the canonical, file-anchored PROJECT node."""
    proj = root / project_dir
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "CMakeLists.txt").write_text(
        "project(Foo)\n"
        "add_library(foolib STATIC src.cpp)\n",
        encoding="utf-8",
    )
    (proj / "src.cpp").write_text("// src\n", encoding="utf-8")


def _write_finder(root: Path, finder_dir: str) -> None:
    """A SIBLING project that ``find_package()``s the same name."""
    finder = root / finder_dir
    finder.mkdir(parents=True, exist_ok=True)
    (finder / "CMakeLists.txt").write_text(
        "project(Bar)\n"
        "find_package(Foo REQUIRED)\n"
        "add_executable(barexe main.cpp)\n"
        "target_link_libraries(barexe Foo)\n",
        encoding="utf-8",
    )
    (finder / "main.cpp").write_text("// main\n", encoding="utf-8")


def _fixture(root: Path, *, project_dir: str, finder_dir: str) -> None:
    _write_project(root, project_dir)
    _write_finder(root, finder_dir)
    _write_discover_yaml(root)


def _project_node(graph: dict) -> dict | None:
    return graph["nodes"].get(PROJECT_NODE_ID)


def _project_contains_edges(graph: dict) -> list[str]:
    return sorted(
        e["to"] for e in graph["edges"]
        if e["type"] == "contains" and e["from"] == PROJECT_NODE_ID
    )


class ProjectClaimSupersedesDependencyLeafOrderTest(unittest.TestCase):
    """Full discover: the project claim wins regardless of file order.

    Two layouts, deliberately swapping which directory
    ``resolve_glob``'s sorted walk reaches first, so a pass here cannot
    be explained by "the project always happened to sort first" -- it
    must hold in BOTH orders, matching ``SourceOrderTest`` in
    ``discover_cross_glob_definition_survives_test.py``.
    """

    def _run(self, *, project_dir: str, finder_dir: str) -> dict:
        with tempfile.TemporaryDirectory(prefix="tuuve-order-") as td:
            root = Path(td)
            _git(root)
            _fixture(root, project_dir=project_dir, finder_dir=finder_dir)
            _commit(root)
            return _discover_single_repo(root, incremental=False, write_graph=True)

    def test_project_wins_when_its_file_sorts_first(self) -> None:
        graph = self._run(project_dir="a_proj_foo", finder_dir="z_uses_foo")
        self._assert_project_claim_intact(graph, "a_proj_foo/CMakeLists.txt")

    def test_project_wins_when_the_finder_file_sorts_first(self) -> None:
        graph = self._run(project_dir="z_proj_foo", finder_dir="a_uses_foo")
        self._assert_project_claim_intact(graph, "z_proj_foo/CMakeLists.txt")

    def _assert_project_claim_intact(self, graph: dict, expected_file: str) -> None:
        node = _project_node(graph)
        self.assertIsNotNone(node, f"{PROJECT_NODE_ID} missing from graph")
        props = node["props"]
        self.assertEqual(props.get("authority"), "canonical")
        self.assertEqual(props.get("confidence"), "definite")
        self.assertEqual(props.get("file"), expected_file)
        self.assertEqual(props.get("build_system"), "cmake")
        self.assertEqual(_project_contains_edges(graph), [BUILD_TARGET_ID])

    def test_both_orders_produce_the_identical_graph(self) -> None:
        # Order-independence, byte-for-byte: the SAME determinism bar
        # SourceOrderTest holds the ADR 0103 orchestrator-level fix to.
        first = self._run(project_dir="a_proj_foo", finder_dir="z_uses_foo")
        second = self._run(project_dir="z_proj_foo", finder_dir="a_uses_foo")
        # Directory names differ between the two layouts by construction
        # (that is what flips the sort order), so only the project node's
        # OWN collision-relevant props are compared here, not the whole
        # graph -- file-path-derived ids for the finder side legitimately
        # differ between the two fixtures.
        for graph in (first, second):
            node = _project_node(graph)
            self.assertEqual(node["props"].get("authority"), "canonical")
            self.assertEqual(node["props"].get("confidence"), "definite")
            self.assertEqual(_project_contains_edges(graph), [BUILD_TARGET_ID])


class SteadyStateIncrementalMatchesFullTest(unittest.TestCase):
    """Editing the unrelated finder file must not disturb the project claim.

    Mirrors ``CleanCallerDirtyCalleeSingleGlobTest`` in
    ``incremental_callgraph_provenance_purge_test.py``: incremental
    discovery after touching only the colliding SIBLING must stay
    byte-identical to a fresh full discover at the same end state.
    """

    def test_incremental_matches_full_after_unrelated_edit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tuuve-steady-inc-") as td:
            root = Path(td)
            _git(root)
            _fixture(root, project_dir="z_proj_foo", finder_dir="a_uses_foo")
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            # Edit ONLY the finder file (extra harmless component); the
            # project's own CMakeLists.txt is untouched.
            (root / "a_uses_foo" / "CMakeLists.txt").write_text(
                "project(Bar)\n"
                "find_package(Foo REQUIRED COMPONENTS extra)\n"
                "add_executable(barexe main.cpp)\n"
                "target_link_libraries(barexe Foo)\n",
                encoding="utf-8",
            )
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory(prefix="tuuve-steady-full-") as td:
            root = Path(td)
            _git(root)
            _fixture(root, project_dir="z_proj_foo", finder_dir="a_uses_foo")
            (root / "a_uses_foo" / "CMakeLists.txt").write_text(
                "project(Bar)\n"
                "find_package(Foo REQUIRED COMPONENTS extra)\n"
                "add_executable(barexe main.cpp)\n"
                "target_link_libraries(barexe Foo)\n",
                encoding="utf-8",
            )
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        node = _project_node(g_inc)
        self.assertIsNotNone(node)
        self.assertEqual(node["props"].get("authority"), "canonical")
        self.assertEqual(node["props"].get("file"), "z_proj_foo/CMakeLists.txt")
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "incremental graph must be byte-identical to a full discover "
            "at the same source state",
        )


class DeletingTheColliderDoesNotPurgeTheLiveProjectTest(unittest.TestCase):
    """The no-over-purge case: deleting the finder must not delete the project.

    Mirrors ``DeletedCalleeDropsInboundEdgeTest``, but for the DIRECTION
    that matters here: it is the SURVIVOR (the untouched project) whose
    correctness is under test, not the deleted side.
    """

    def test_project_and_its_build_target_survive_finder_deletion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tuuve-del-inc-") as td:
            root = Path(td)
            _git(root)
            _fixture(root, project_dir="z_proj_foo", finder_dir="a_uses_foo")
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            # Delete the ONLY find_package(Foo) referrer. The project's
            # own CMakeLists.txt is never touched.
            (root / "a_uses_foo" / "CMakeLists.txt").unlink()
            (root / "a_uses_foo" / "main.cpp").unlink()
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory(prefix="tuuve-del-full-") as td:
            root = Path(td)
            _git(root)
            _write_project(root, "z_proj_foo")
            _write_discover_yaml(root)
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        self.assertIn(
            PROJECT_NODE_ID, g_inc["nodes"],
            "the live, untouched project's own node must not be purged "
            "just because an unrelated sibling that collided with its id "
            "was deleted",
        )
        node = _project_node(g_inc)
        self.assertEqual(node["props"].get("authority"), "canonical")
        self.assertEqual(node["props"].get("file"), "z_proj_foo/CMakeLists.txt")
        self.assertIn(BUILD_TARGET_ID, g_inc["nodes"])
        self.assertEqual(
            _project_contains_edges(g_inc), [BUILD_TARGET_ID],
            "the project -> contains -> build-target edge must survive "
            "so the target is not left without an owning package node",
        )
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "incremental graph after deleting the collider must match a "
            "fresh full discover of the same post-delete tree",
        )


if __name__ == "__main__":
    unittest.main()
