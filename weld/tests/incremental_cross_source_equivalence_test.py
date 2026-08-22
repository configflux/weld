"""Incremental == full when the edge's producer and its endpoint differ (bd cpkp).

``incremental_refresh_equivalence_test`` pins ADR 0008/0074's byte-identity
invariant, but every fixture it uses declares **one strategy family over one
glob**, so the file that produces an edge is always in the same dirty set as
the file the edge points at. The invariant it pins is therefore blind to the
cross-source shape, and the real repo diverged under it: editing any
``weld/*.py`` dropped that file's ``build-target -> contains -> file:`` edges
(measured 40126 full vs 40124 incremental) and no later incremental run
restored them, while ``wd stale`` reported clean.

The mechanism is structural, not bazel-specific. ADR 0074's purge keeps an
edge that names its producing file in ``props.provenance.file`` and falls back
to endpoint membership for one that does not. An unstamped edge whose producer
(here a BUILD file) is clean while its endpoint (a source file) is dirty is
purged by the endpoint floor, and the producing source entry never re-runs to
re-mint it -- the glob that would have re-run holds no dirty file. Any strategy
that emits an edge into a file it does not itself own can land in this shape,
so the fixture here is deliberately multi-source and the test sweeps the edit
across *every* file in it rather than pinning the one path that regressed.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo  # noqa: E402

#: Every fixture file the sweep edits in turn. Each one is a producer for some
#: edge and an endpoint for another, so whichever side of the purge is wrong,
#: one of these rounds sees it. The two python files are the regression itself
#: (clean producer, dirty endpoint); ``BUILD.bazel`` is the reverse (dirty
#: producer -- its edges must be purged AND re-minted, not stale-retained); and
#: ``srcs.bzl`` is a graph input no glob resolves, which settles by forcing a
#: full run (bd a4q8) -- included so a change to that rule shows up here.
EDITABLE = ("src/alpha.py", "src/beta.py", "src/BUILD.bazel", "src/srcs.bzl")


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


def _write_fixture(
    root: Path, edited: str | None, *, alpha_body: str | None = None,
) -> None:
    """Write the fixture, giving *edited* (if any) its changed-content variant.

    The BUILD file declares ``srcs`` it does not itself contain, reads them
    through a loaded ``.bzl`` constant (ADR 0109), and names one of them in
    ``data`` -- so ``contains``, ``depends_on``, ``tests`` and the deferred
    ``data`` edge all cross from a BUILD-file producer into a python endpoint.
    That crossing is the whole point of the fixture. ``beta_test`` also
    declares an external-workspace dep (``@pypi//tree_sitter``, ADR 0121):
    its ``depends_on`` edge into the shared ``external-dep:`` node is a
    third producer/endpoint pairing the fixture's clean-BUILD-file sweep
    must purge-and-remint identically to a full discover, same as the
    in-repo edges above it.

    *alpha_body*, when given, replaces ``src/alpha.py``'s default body. Used
    by the symbol-rename round (bd znzu): a plain content edit isn't enough
    there, ``alpha.py`` must stop *defining* ``alpha_fn`` while ``beta.py``'s
    import of that name stays untouched.
    """
    def body(rel: str, text: str) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel == edited:
            text = text + "\n# edited\n"
        path.write_text(text, encoding="utf-8")

    body("src/__init__.py", "")
    body("src/alpha.py", alpha_body or "def alpha_fn():\n    return 1\n")
    body("src/beta.py", "from src.alpha import alpha_fn\n\n\ndef beta_fn():\n    return alpha_fn()\n")
    body("src/srcs.bzl", 'LIB_SRCS = ["alpha.py", "beta.py"]\n')
    body(
        "src/BUILD.bazel",
        'load(":srcs.bzl", "LIB_SRCS")\n\n'
        "py_library(\n"
        '    name = "lib",\n'
        "    srcs = LIB_SRCS,\n"
        ")\n\n"
        "py_test(\n"
        '    name = "beta_test",\n'
        '    srcs = ["beta.py"],\n'
        '    deps = [":lib", "@pypi//tree_sitter"],\n'
        '    data = ["alpha.py"],\n'
        ")\n",
    )
    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "sources:\n"
        "  - strategy: python_module\n    glob: src/**/*.py\n    type: file\n"
        "  - strategy: python_callgraph\n    glob: src/**/*.py\n    type: symbol\n"
        '  - strategy: bazel\n    glob: "**/BUILD.bazel"\n    type: build-target\n',
        encoding="utf-8",
    )


def _strip_meta(graph: dict) -> dict:
    """Drop the volatile keys, plus ``discovered_from`` (order differs
    between the two construction paths even though the SET now matches,
    bd 8084; see ``incremental_discovered_from_equivalence_test`` for that
    pin)."""
    out = {k: v for k, v in graph.items() if k != "meta"}
    out["meta"] = {
        k: v for k, v in graph.get("meta", {}).items()
        if k not in ("updated_at", "git_sha", "discovered_from")
    }
    return out


def _edge_set(graph: dict) -> set[tuple[str, str, str]]:
    return {(e["from"], e["type"], e["to"]) for e in graph.get("edges", [])}


def _incremental_graph(edited: str | None, *, alpha_body: str | None = None) -> dict:
    """Seed a full discover, edit one file, return the incremental graph."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, None)
        _commit(root)
        _discover_single_repo(root, incremental=False, write_graph=True)
        _write_fixture(root, edited, alpha_body=alpha_body)
        _commit(root)
        return _discover_single_repo(root, incremental=True, write_graph=True)


def _full_graph(edited: str | None, *, alpha_body: str | None = None) -> dict:
    """Return a full discover over a tree already in the post-edit state."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, edited, alpha_body=alpha_body)
        _commit(root)
        return _discover_single_repo(root, incremental=False, write_graph=True)


class CrossSourceEdgeEquivalenceTest(unittest.TestCase):
    def test_every_single_file_edit_matches_full(self) -> None:
        for edited in EDITABLE:
            with self.subTest(edited=edited):
                inc, full = _incremental_graph(edited), _full_graph(edited)
                inc_e, full_e = _edge_set(inc), _edge_set(full)
                self.assertEqual(
                    inc_e, full_e,
                    f"editing {edited} diverged the incremental edge set from "
                    f"a full discover over the identical tree "
                    f"(full-only={sorted(full_e - inc_e)}, "
                    f"inc-only={sorted(inc_e - full_e)})",
                )
                self.assertEqual(
                    _strip_meta(inc), _strip_meta(full),
                    f"editing {edited} diverged the incremental graph "
                    "nodes/edges from a full discover",
                )

    def test_deleted_source_still_matches_full(self) -> None:
        # The other direction, and the one a provenance stamp could get wrong:
        # retaining an edge a full discover would not produce. Deleting a
        # declared ``srcs`` file leaves the clean BUILD file still declaring
        # it, so the retained edge must end up dropped as dangling -- exactly
        # as it is on a full run, where the endpoint is never minted either.
        #
        # Pinned on the WHOLE edge set (not narrowed to the bazel family):
        # deleting src/alpha.py also exercises python_callgraph's import
        # fallback -- the clean beta.py's edge into the purged alpha_fn node
        # survives the provenance purge and then dangles, because nothing
        # re-parses beta.py to re-mint a stand-in the way a full discover
        # would. Same clean-producer/vanished-target shape as the bazel one
        # above, on a different strategy (ADR 0074 fourth amendment, bd znzu).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root, None)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            (root / "src" / "alpha.py").unlink()
            _commit(root)
            inc = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root, None)
            (root / "src" / "alpha.py").unlink()
            _commit(root)
            full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_e, full_e = _edge_set(inc), _edge_set(full)
        self.assertEqual(
            inc_e, full_e,
            f"deleting a declared src diverged the incremental edge set "
            f"(full-only={sorted(full_e - inc_e)}, "
            f"inc-only={sorted(inc_e - full_e)})",
        )
        self.assertEqual(
            _strip_meta(inc), _strip_meta(full),
            "deleting a declared src diverged the incremental graph "
            "nodes/edges from a full discover",
        )
        self.assertNotIn(
            ("build-target://src:lib", "contains", "file:src/alpha"), inc_e,
            "a provenance-retained edge into a deleted file must not survive "
            "the dangling sweep",
        )

    def test_edited_source_removing_imported_symbol_still_matches_full(self) -> None:
        # The non-deletion half of bd znzu's mechanism: alpha.py survives but
        # stops defining the symbol beta.py imports (renamed alpha_fn ->
        # gamma_fn). Content-hash diffing marks alpha.py dirty (it changed)
        # and beta.py clean (it did not) -- the purged alpha_fn node is never
        # re-minted under that id, and beta.py, whose clean-provenance edge
        # into it survived the purge, is never re-parsed to notice. Same
        # dangling-edge shape as the delete case, reached without any file
        # disappearing.
        renamed_alpha = "def gamma_fn():\n    return 1\n"
        inc = _incremental_graph(None, alpha_body=renamed_alpha)
        full = _full_graph(None, alpha_body=renamed_alpha)

        inc_e, full_e = _edge_set(inc), _edge_set(full)
        self.assertEqual(
            inc_e, full_e,
            f"renaming an imported symbol diverged the incremental edge set "
            f"from a full discover (full-only={sorted(full_e - inc_e)}, "
            f"inc-only={sorted(inc_e - full_e)})",
        )
        self.assertEqual(
            _strip_meta(inc), _strip_meta(full),
            "renaming an imported symbol diverged the incremental graph "
            "nodes/edges from a full discover",
        )

    def test_build_target_keeps_contains_edge_for_edited_source(self) -> None:
        # The named regression, asserted directly rather than only through the
        # set diff: the producer (src/BUILD.bazel) is clean, the endpoint
        # (src/alpha.py) is dirty, and the edge between them must survive.
        inc = _incremental_graph("src/alpha.py")
        self.assertIn(
            ("build-target://src:lib", "contains", "file:src/alpha"),
            _edge_set(inc),
            "incremental refresh dropped the build-target -> contains -> file "
            "edge for the edited source; the clean BUILD file never re-ran to "
            "re-mint it (bd cpkp)",
        )

    def test_external_dep_edge_and_node_survive_unrelated_edit(self) -> None:
        # ADR 0121's edge kind, pinned directly the same way: the producer
        # (src/BUILD.bazel) is clean, the endpoint this time is not a file
        # this repo owns at all (external-dep:pypi:tree_sitter is minted by
        # the same clean BUILD file), and an edit elsewhere in the fixture
        # must neither drop the edge nor fail to mint the node.
        inc = _incremental_graph("src/alpha.py")
        inc_e = _edge_set(inc)
        self.assertIn(
            ("test-target://src:beta_test", "depends_on", "external-dep:pypi:tree_sitter"),
            inc_e,
            "incremental refresh dropped the test-target -> depends_on -> "
            "external-dep edge for an edit unrelated to the BUILD file that "
            "declares it",
        )
        self.assertIn("external-dep:pypi:tree_sitter", inc["nodes"])


if __name__ == "__main__":
    unittest.main()
