"""The stored graph spells its path props one way, whatever wrote them.

Node *ids* have been POSIX since ADR 0041, but ``props.file``,
``props.declared_in`` and ``props.provenance.file`` were left to whichever
strategy wrote them, and roughly half of the ~40 strategies write
``as_posix()`` while half write ``str()``. Identical on POSIX; off it, one
graph carries both spellings for sibling files. A graph is meant to be
portable -- read on another platform, or federated as a child of a POSIX root
-- and there those anchors reach a reader that correctly declines to rewrite
them, because a POSIX reader has no business folding a backslash. So the read
side each solved it locally and inconsistently: ``impact_surfaces`` and
``_graph_strategy_pair`` replaced unconditionally and documented accepting the
misread, while ``query_index``, ``_sqlite_writer``, ``_coverage_admission``
and others do not normalize at all (bd 244j).

``impact_surfaces`` now delegates its separator fold to
``weld._rel_path.canonical_rel_path`` instead of hand-rolling it, which makes
it the identity on POSIX; ``_graph_strategy_pair`` deliberately keeps the
unconditional form, because it feeds a gate-failing lint rather than a search
result. See ``ReadSideFoldFollowsThePlatformTest`` at the end of this file for
that split and why it is one (bd 3x85).

The fix canonicalizes once, at the boundary every node and edge already
funnels through (``_discover_postprocess.post_process``), which is what
preserves the incremental==full byte-identity contract -- both discover paths
run it.

HONEST LIMITATION. Weld has no Windows lane, so the platform is simulated the
way ``incremental_rel_path_form_test`` simulates it: by patching
``weld._rel_path._FOREIGN_SEPARATORS`` to the tuple that module would compute
there. What that proves is the form contract, not that weld runs on Windows.

The unsimulated class is the more important half of this file. On POSIX the
pass is skipped outright, so the stored bytes are unchanged -- that is what
lets this land with no migration (ADR 0065, ADR 0012 §3) -- and a file
legitimately named ``a\\b.py`` keeps its name.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weld import _rel_path
from weld._discover_postprocess import post_process

#: The value ``weld._rel_path`` computes on Windows.
_WINDOWS_SEPARATORS = ("\\",)


def _simulated_non_posix():
    """Patch the separator set to the one a non-POSIX host would compute."""
    return mock.patch.object(
        _rel_path, "_FOREIGN_SEPARATORS", _WINDOWS_SEPARATORS
    )


def _graph_with(nodes: dict, edges: list, discovered_from: list[str]) -> dict:
    """Run *nodes*/*edges* through the real post-processing boundary."""
    with tempfile.TemporaryDirectory() as tmp:
        return post_process(nodes, edges, {}, {}, Path(tmp), discovered_from)


def _native_nodes() -> dict:
    return {
        "file:lib/thing": {
            "type": "file",
            "label": "thing",
            "props": {
                "file": "lib\\thing.py",
                "declared_in": "lib\\thing.py",
                "source_strategy": "python_module",
                "confidence": "definite",
            },
        },
        "file:lib/other": {
            "type": "file",
            "label": "other",
            "props": {
                "file": "lib\\other.py",
                "source_strategy": "python_module",
                "confidence": "definite",
            },
        },
    }


def _native_edge() -> dict:
    return {
        "from": "file:lib/thing",
        "to": "file:lib/other",
        "type": "depends_on",
        "props": {
            "confidence": "definite",
            "source_strategy": "python_module",
            # ADR 0074 stamps the producing file here.
            "provenance": {"file": "lib\\thing.py", "line": 12},
        },
    }


class NonPosixGraphIsCanonicalizedTest(unittest.TestCase):
    """Off POSIX, every stored path prop comes out POSIX-spelled."""

    def test_node_file_and_declared_in_are_folded(self) -> None:
        with _simulated_non_posix():
            graph = _graph_with(_native_nodes(), [], [])

        props = graph["nodes"]["file:lib/thing"]["props"]
        self.assertEqual("lib/thing.py", props["file"])
        self.assertEqual("lib/thing.py", props["declared_in"])
        self.assertEqual(
            "lib/other.py", graph["nodes"]["file:lib/other"]["props"]["file"]
        )

    def test_edge_provenance_file_is_folded(self) -> None:
        with _simulated_non_posix():
            graph = _graph_with(_native_nodes(), [_native_edge()], [])

        edge = next(e for e in graph["edges"] if e["type"] == "depends_on")
        self.assertEqual("lib/thing.py", edge["props"]["provenance"]["file"])
        # Everything else on the edge is untouched.
        self.assertEqual(12, edge["props"]["provenance"]["line"])

    def test_meta_discovered_from_is_folded(self) -> None:
        # An artifact canonical in its props and native in its manifest would
        # be a worse state than either.
        with _simulated_non_posix():
            graph = _graph_with({}, [], ["lib\\thing.py", "lib\\other.py"])

        self.assertEqual(
            ["lib/thing.py", "lib/other.py"], graph["meta"]["discovered_from"]
        )

    def test_non_string_path_prop_is_left_alone(self) -> None:
        # canonical_rel_path answers "" for a non-string -- right for a
        # comparison, destructive for a rewrite.
        nodes = {
            "file:lib/thing": {
                "type": "file",
                "label": "thing",
                "props": {"file": None, "confidence": "definite"},
            },
        }
        with _simulated_non_posix():
            graph = _graph_with(nodes, [], [])

        self.assertIsNone(graph["nodes"]["file:lib/thing"]["props"]["file"])

    def test_package_dir_prop_is_folded(self) -> None:
        # bd mzv1. props.dir is the fourth path-shaped prop, on package:
        # nodes. It was left out of the first pass because no read-side
        # consumer matched on it, which made an artifact with canonical file
        # anchors and native directory anchors -- the one state worse than
        # either spelling used consistently.
        nodes = {
            "package:lib/sub": {
                "type": "package",
                "label": "sub",
                "props": {"dir": "lib\\sub", "confidence": "definite"},
            },
        }
        with _simulated_non_posix():
            graph = _graph_with(nodes, [], [])

        self.assertEqual("lib/sub", graph["nodes"]["package:lib/sub"]["props"]["dir"])

    def test_already_posix_props_are_unchanged(self) -> None:
        nodes = {
            "file:lib/thing": {
                "type": "file",
                "label": "thing",
                "props": {"file": "lib/thing.py", "confidence": "definite"},
            },
        }
        with _simulated_non_posix():
            graph = _graph_with(nodes, [], [])

        self.assertEqual(
            "lib/thing.py", graph["nodes"]["file:lib/thing"]["props"]["file"]
        )


class PosixGraphIsUntouchedTest(unittest.TestCase):
    """On POSIX the pass is skipped, so no stored byte moves."""

    def test_literal_backslash_filename_survives(self) -> None:
        # A real, distinct file here. Folding it would claim lib/thing.py,
        # which is a different file -- the trade the read-side paths take and
        # a write-side canonicalization must not.
        nodes = {
            "file:lib/thing": {
                "type": "file",
                "label": "thing",
                "props": {
                    "file": "lib\\thing.py",
                    "declared_in": "lib\\thing.py",
                    "confidence": "definite",
                },
            },
        }
        graph = _graph_with(nodes, [], ["lib\\thing.py"])

        props = graph["nodes"]["file:lib/thing"]["props"]
        self.assertEqual("lib\\thing.py", props["file"])
        self.assertEqual("lib\\thing.py", props["declared_in"])
        self.assertEqual(["lib\\thing.py"], graph["meta"]["discovered_from"])

    def test_edge_provenance_is_untouched(self) -> None:
        graph = _graph_with(_native_nodes(), [_native_edge()], [])

        edge = next(e for e in graph["edges"] if e["type"] == "depends_on")
        self.assertEqual("lib\\thing.py", edge["props"]["provenance"]["file"])

    def test_package_dir_prop_is_untouched(self) -> None:
        nodes = {
            "package:lib/sub": {
                "type": "package",
                "label": "sub",
                "props": {"dir": "lib\\sub", "confidence": "definite"},
            },
        }
        graph = _graph_with(nodes, [], [])

        self.assertEqual("lib\\sub", graph["nodes"]["package:lib/sub"]["props"]["dir"])


class ReadSideFoldFollowsThePlatformTest(unittest.TestCase):
    """``impact_surfaces`` folds separators the way the write side does.

    ``impact_surfaces._normalize_path`` and
    ``_graph_strategy_pair._emitted_file_anchors`` each carried their own
    unconditional ``replace("\\\\", "/")``, and each documented the trade it
    bought: a POSIX file legitimately named ``a\\b.py`` read as ``a/b.py``.
    That was the right trade while the stored artifact could hand them either
    spelling. It no longer can -- the artifact is canonicalized where it is
    written -- so on POSIX the fold repairs nothing and only misreads
    (bd 3x85).

    The fold itself is *not* retired. Off POSIX it is still what an artifact
    written by a pre-canonicalization weld needs, so ``_normalize_path`` now
    delegates to ``weld._rel_path.canonical_rel_path`` rather than dropping
    the fold: identity on POSIX, still folding where a foreign spelling can
    actually arise.

    **Only one of the two sites moved, deliberately.**
    ``_graph_strategy_pair`` keeps the unconditional form because it feeds a
    lint that fails the gate rather than a search result: there, losing the
    tolerance turns a pre-244j off-POSIX graph into a false-positive storm
    that blocks somebody, which is a worse failure than misreading a
    pathological filename. ``weld_graph_strategy_pair_test``'s
    ``test_windows_style_anchor_compares_against_posix_declaration`` is that
    contract, and it is deliberately left passing unchanged.

    Note what these assertions are about: the *separator* fold only. The rest
    of ``_normalize_path`` -- ``strip()``, ``posixpath.normpath``, ``"."`` to
    ``""`` -- is not a spelling rule and is unchanged.
    """

    def test_posix_keeps_a_literal_backslash_filename(self) -> None:
        from weld.impact_surfaces import _normalize_path

        self.assertEqual("a\\b.py", _normalize_path("a\\b.py"))

    def test_non_posix_still_folds_a_native_spelling(self) -> None:
        from weld.impact_surfaces import _normalize_path

        with _simulated_non_posix():
            self.assertEqual("a/b.py", _normalize_path("a\\b.py"))

    def test_normalize_path_still_strips_and_normalizes(self) -> None:
        from weld.impact_surfaces import _normalize_path

        self.assertEqual("a/b.py", _normalize_path("  ./a/b.py  "))
        self.assertEqual("", _normalize_path("."))
        self.assertEqual("", _normalize_path("   "))

    def test_lint_site_still_folds_unconditionally(self) -> None:
        """The site that did NOT move, pinned so the split stays deliberate.

        Without this, a later reader finding one hand-rolled replace left in
        the tree would reasonably assume bd 3x85 simply missed it.
        """
        from weld._graph_strategy_pair import _emitted_file_anchors

        nodes = {
            "file:a": {
                "props": {"source_strategy": "python_callgraph", "file": "a\\b.py"}
            }
        }
        anchors = _emitted_file_anchors(nodes, Path("/repo"))

        self.assertEqual({"python_callgraph": {"a/b.py"}}, anchors)


if __name__ == "__main__":
    unittest.main()
