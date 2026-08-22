"""A loaded ``.bzl`` is a graph input, so freshness must see it (bd a4q8).

ADR 0109 resolves ``load()``, so a constant a ``.bzl`` exports evaluates to a
target's real ``srcs``: editing one changes which files a ``build-target`` node
contains. The bazel strategy reports the ``.bzl`` in ``discovered_from`` and
anchors a ``file:`` node at it, but the ADR 0008 inventory was built only from
the glob-resolved source set, so the ``.bzl`` was recorded nowhere.

Two consequences, both measured on this repo before the fix:

* ADR 0017's working-tree dimension (bd 0jay) compares a dirty path's content
  against that inventory. An unrecorded path that is on disk falls to
  "out of scope -> not a graph input, never stale", so editing a ``.bzl``
  produced **no** staleness signal at all -- while ``.weld/discover.yaml``'s
  own bazel-entry comment still claimed it did.
* The ADR 0008 delta is computed over the same inventory, so an incremental
  run reported "no files changed, graph is up to date" and left the
  ``contains`` edge pointing at the *previous* ``srcs``.

These tests drive real discovery over a BUILD file that loads a ``.bzl``, and
pin the whole loop: recorded, stale on edit, settled by one discover, with the
graph body actually re-extracted. The last part is what separates a fix from a
cover-up -- re-stamping the hash without re-reading the BUILD files would
satisfy every freshness assertion here and still serve a wrong graph.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from weld._graph_meta_sidecar import load_graph_meta  # noqa: E402
from weld._staleness import compute_stale_info  # noqa: E402
from weld.discover import _discover_single_repo  # noqa: E402


CONFIG = """sources:
  - glob: "src/*.py"
    type: file
    strategy: python_module
  - glob: "**/BUILD.bazel"
    type: build-target
    strategy: bazel
"""

BUILD = 'load(":srcs.bzl", "LIB_SRCS")\n\npy_library(name = "lib", srcs = LIB_SRCS)\n'

TREE: dict[str, str] = {
    "src/a.py": "def a():\n    return 1\n",
    "src/b.py": "def b():\n    return 2\n",
    "src/srcs.bzl": 'LIB_SRCS = ["a.py"]\n',
    "src/BUILD.bazel": BUILD,
}

BZL = "src/srcs.bzl"


def _run(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=30,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{' '.join(cmd)} failed (rc={result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


class _LoadedBzlFixture(unittest.TestCase):
    """A committed repo whose BUILD file loads its ``srcs`` from a ``.bzl``."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".weld").mkdir(parents=True, exist_ok=True)
        _run(["git", "init", "--quiet"], self.root)
        _run(["git", "config", "user.email", "test@test.com"], self.root)
        _run(["git", "config", "user.name", "Test"], self.root)
        _run(["git", "config", "commit.gpgsign", "false"], self.root)
        (self.root / ".weld" / "discover.yaml").write_text(
            CONFIG, encoding="utf-8"
        )
        for rel, body in TREE.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        _run(["git", "add", "-A"], self.root)
        _run(["git", "commit", "-m", "initial", "--quiet"], self.root)
        self.graph = self.discover()
        # Anti-vacuity: every assertion below is about a file the strategy
        # reads through ``load()``. If the fixture ever stops exercising that
        # path, the whole module would pass by describing nothing.
        self.assertIn(
            BZL, self.graph["meta"]["discovered_from"],
            "fixture must make the .bzl a real graph input",
        )

    def discover(self, *, incremental: bool | None = None) -> dict:
        """Run real discovery, writing graph.json and the inventory."""
        return _discover_single_repo(
            self.root, incremental=incremental, write_graph=True,
        )

    @property
    def graph_path(self) -> Path:
        return self.root / ".weld" / "graph.json"

    def inventory(self) -> dict[str, str]:
        state = json.loads(
            (self.root / ".weld" / "discovery-state.json").read_text(
                encoding="utf-8",
            )
        )
        return state["files"]

    def stale(self) -> dict:
        meta = dict(load_graph_meta(self.graph_path))
        return compute_stale_info(self.graph_path, meta)

    def contained_files(self, graph: dict) -> set[str]:
        """Files the ``//src:lib`` target contains -- the loaded ``srcs``."""
        return {
            edge["to"] for edge in graph["edges"]
            if edge["from"] == "build-target://src:lib"
            and edge["type"] == "contains"
        }

    def edit_bzl(self, srcs: str) -> None:
        (self.root / BZL).write_text(
            f"LIB_SRCS = {srcs}\n", encoding="utf-8",
        )


class LoadedBzlIsInventoriedTest(_LoadedBzlFixture):
    """The inventory must record every file the graph names as an input."""

    def test_loaded_bzl_is_recorded_in_the_inventory(self) -> None:
        # The root cause in one assertion: recorded, so the working-tree
        # dimension has content to compare against instead of reading the
        # path as a file discovery would never touch.
        self.assertIn(
            BZL, self.inventory(),
            "a .bzl the BUILD file loads must be inventoried like any "
            "other ingested source",
        )

    def test_globbed_sources_are_still_inventoried(self) -> None:
        # The extras are additive: nothing the glob resolves may drop out.
        inventory = self.inventory()
        for rel in ("src/a.py", "src/b.py", "src/BUILD.bazel"):
            self.assertIn(rel, inventory, rel)

    def test_inventory_stays_in_path_order(self) -> None:
        # Every inventory before ingested inputs existed was sorted by
        # construction (``current_file_set`` is a sorted list), and ADR 0110
        # tracks this file. Appending the extras would key the same content
        # in two different orders depending on which path wrote it, which
        # reads as a diff nobody made.
        recorded = list(self.inventory())
        self.assertEqual(recorded, sorted(recorded))

    def test_inventory_holds_only_files(self) -> None:
        # ``discovered_from`` also carries directory prefixes; hashing those
        # is impossible and recording them would put a permanently
        # unresolvable key in the delta basis.
        for rel in self.inventory():
            self.assertTrue(
                (self.root / rel).is_file(),
                f"inventory recorded a non-file: {rel}",
            )


class EditingLoadedBzlIsStaleThenSettlesTest(_LoadedBzlFixture):
    """The reported bug, end to end."""

    def test_clean_tree_is_fresh(self) -> None:
        self.assertFalse(self.stale()["source_stale"], self.stale())

    def test_edited_bzl_is_stale(self) -> None:
        self.edit_bzl('["b.py"]')
        info = self.stale()
        self.assertTrue(
            info["source_stale"],
            f"an uncommitted .bzl edit must mark the graph stale: {info}",
        )
        # HEAD never moved, so the working-tree dimension is the only signal
        # in play -- the exact shape bd hmaz's probe E measured as silent.
        self.assertFalse(info["sha_behind"], info)
        self.assertEqual(info["commits_behind"], 0, info)

    def test_discover_settles_an_edited_bzl(self) -> None:
        self.edit_bzl('["b.py"]')
        self.assertTrue(self.stale()["source_stale"])
        self.discover()
        info = self.stale()
        self.assertFalse(
            info["source_stale"],
            f"discover must settle an edited .bzl, not latch it: {info}",
        )

    def test_settled_bzl_stays_settled(self) -> None:
        # A second discover over an unchanged (still dirty) tree must not
        # re-latch: that was the bd 0jay failure mode this area exists to
        # keep out, and an inventory entry that is written but not re-read
        # would reproduce it.
        self.edit_bzl('["b.py"]')
        self.discover()
        self.discover()
        self.assertFalse(self.stale()["source_stale"], self.stale())

    def test_settling_discover_re_extracts_the_target(self) -> None:
        # The assertion that makes the rest mean something. Freshness could
        # be satisfied by recording the new hash alone; the graph would then
        # report fresh while still holding the previous ``srcs``.
        self.assertEqual(self.contained_files(self.graph), {"file:src/a"})
        self.edit_bzl('["b.py"]')
        graph = self.discover()
        self.assertEqual(
            self.contained_files(graph), {"file:src/b"},
            "the settling discover must re-read the BUILD files that load "
            "the edited .bzl, not just re-stamp its hash",
        )

    def test_incremental_run_sees_a_bzl_edit(self) -> None:
        # Same defect from the ADR 0008 side: before the fix this path
        # printed "no files changed, graph is up to date" and left the
        # target pointing at the old srcs.
        self.edit_bzl('["b.py"]')
        graph = self.discover(incremental=True)
        self.assertEqual(self.contained_files(graph), {"file:src/b"})
        self.assertIn(BZL, self.inventory())

    def test_deleting_the_bzl_settles_rather_than_latching(self) -> None:
        # A vanished input cannot be hashed, so it must leave the inventory
        # rather than sit there as a permanently divergent record.
        (self.root / BZL).unlink()
        self.discover()
        self.assertNotIn(BZL, self.inventory())
        self.assertFalse(self.stale()["source_stale"], self.stale())


class UnchangedTreeStaysCheapTest(_LoadedBzlFixture):
    """Recording extra inputs must not disturb the no-change fast path."""

    def test_second_discover_writes_byte_identical_state(self) -> None:
        # bd lrfu: two runs over an unchanged tree write equal bytes. An
        # extra input re-hashed into a differently-ordered dict, or carried
        # only on one path, would show up here as a diff per discover.
        state_path = self.root / ".weld" / "discovery-state.json"
        before = state_path.read_bytes()
        self.discover()
        self.assertEqual(before, state_path.read_bytes())

    def test_unchanged_tree_takes_the_no_change_path(self) -> None:
        # The extras take part in the delta, so they must also *match* on an
        # unchanged tree -- otherwise every refresh would report a change and
        # re-run every strategy forever.
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            self.discover(incremental=True)
        self.assertIn("no files changed", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
