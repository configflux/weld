"""Tests for ``_ensure_graph`` bootstrapping in the public benchmark.

Split out of ``weld_public_bench_weld_adapter_test.py`` (bd gjli), which
sat at exactly the 400-line cap. Deliberately a sibling file, in the
idiom ``weld_federation_eager_test`` already uses here: the adapter's
precondition surface and its graph-bootstrap surface are separate
mini-specs, and merging them was only ever an accident of both landing
in one change.

Root cause this file covers: ``_ensure_graph`` previously called
``weld.discover.discover`` directly without first generating a
``.weld/discover.yaml``. On a fresh nlohmann/json clone with no
``.weld/`` tree, ``discover.py`` defaulted to ``sources=[]`` and minted
zero nodes, so every C++ bench row scored F1=0.00 regardless of
extractor quality. The fix bootstraps a default config via
:func:`weld.init.init` before invoking discovery.

The cpp floor below (bd gjli) is the second half of that story. The
bootstrap case used to assert ``node_count > 0`` and concede in its own
comment that the nodes might have come from "the python_module entry on
the .py-free tempdir, etc." -- a count any strategy can satisfy, which
is no evidence at all that the thing under test (cpp extraction through
a bootstrapped config) happened. It now asserts on nodes the cpp
grammar demonstrably produced.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


from weld.bench.adapters import weld as weld_adapter  # noqa: E402

from weld.tests.bench.bench_grammar_precondition import (  # noqa: E402
    skip_or_fail_without_grammars,
)


# The two cpp sources the fresh-tempdir case writes. Held as constants
# because the assertions read them back out of ``props.file``: the floor
# is "the graph names *these* sources", not "the graph has rows".
_HEADER_REL = "include/json.hpp"
_SOURCE_REL = "src/main.cpp"

# A symbol that exists only INSIDE the header's text -- ``nlohmann``
# appears in no path component of the tempdir, unlike ``json`` and
# ``main``, which are also the file stems. That makes it the one token a
# filename-derived strategy could not invent, so asserting on it pins an
# actual grammar parse rather than a directory walk.
_BODY_ONLY_SYMBOL = "nlohmann"


class EnsureGraphBootstrapsConfigTest(unittest.TestCase):
    """``_ensure_graph`` must run ``wd init`` when no discover.yaml exists.

    Hermetic: writes a tiny cpp file inside a tempdir; the only side
    effects are the ``.weld/`` subdirectory inside that tempdir.
    """

    def _read_nodes(self, repo_root: Path) -> dict:
        graph_path = repo_root / ".weld" / "graph.json"
        payload = json.loads(graph_path.read_text(encoding="utf-8"))
        return payload.get("nodes") or {}

    def _tree_sitter_sources(self, nodes: dict) -> set[str]:
        """Source files the tree-sitter strategy minted a node from.

        Keyed off ``props.source_strategy`` rather than node type or
        count: a node minted by ``python_module``, ``graph_closure`` or
        any other strategy must not be able to satisfy a cpp floor.
        """
        return {
            props["file"]
            for props in (node.get("props") or {} for node in nodes.values())
            if props.get("source_strategy") == "tree_sitter" and props.get("file")
        }

    def _parsed_cpp_symbols(self, nodes: dict, source_rel: str) -> set[str]:
        """Symbol names the cpp grammar extracted from *source_rel*."""
        return {
            node.get("label")
            for node in nodes.values()
            if node.get("type") == "symbol"
            and (node.get("props") or {}).get("file") == source_rel
            and (node.get("props") or {}).get("language") == "cpp"
        }

    def _write_cpp_tempdir(self, root: Path) -> None:
        (root / "include").mkdir()
        (root / _HEADER_REL).write_text(
            "#pragma once\nnamespace nlohmann { class json {}; }\n",
            encoding="utf-8",
        )
        (root / "src").mkdir()
        (root / _SOURCE_REL).write_text(
            "#include \"json.hpp\"\nint main() { return 0; }\n",
            encoding="utf-8",
        )

    def test_fresh_cpp_tempdir_yields_cpp_grammar_parsed_nodes(self) -> None:
        # Renamed from ``test_fresh_cpp_tempdir_yields_nonempty_graph``
        # (bd gjli): "nonempty" was the vacuous half of the claim.
        #
        # The exact bug class: a fresh tempdir with cpp source files but
        # no .weld/ tree. Before the fix, _ensure_graph called discover()
        # with sources=[] and the resulting graph had 0 nodes. After the
        # fix, the bootstrap step generates a discover.yaml whose
        # tree-sitter cpp glob picks up both files and parses them.
        skip_or_fail_without_grammars(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_cpp_tempdir(root)
            ok = weld_adapter._ensure_graph(root)
            self.assertTrue(
                ok, "_ensure_graph must report success when discovery runs",
            )
            # The bootstrap step must have written a discover.yaml inside
            # the tempdir (containment check).
            self.assertTrue(
                (root / ".weld" / "discover.yaml").exists(),
                "_ensure_graph must bootstrap .weld/discover.yaml",
            )
            nodes = self._read_nodes(root)
            # Floor 1: the tree-sitter strategy reached BOTH cpp sources.
            # Subsumes the old node_count > 0 assertion, and unlike it
            # cannot be satisfied by a node from some other strategy.
            sources = self._tree_sitter_sources(nodes)
            for rel in (_HEADER_REL, _SOURCE_REL):
                self.assertIn(
                    rel, sources,
                    f"no tree-sitter node names {rel} as its source; the "
                    "bootstrapped discover.yaml did not reach the cpp "
                    f"glob (tree-sitter sources seen: {sorted(sources)})",
                )
            # Floor 2: a symbol read out of the header's *body*. Only a
            # real cpp parse produces ``nlohmann``; a strategy inferring
            # from the path could at best reach the ``json`` stem.
            header_symbols = self._parsed_cpp_symbols(nodes, _HEADER_REL)
            self.assertIn(
                _BODY_ONLY_SYMBOL, header_symbols,
                f"{_BODY_ONLY_SYMBOL!r} is declared only inside "
                f"{_HEADER_REL}, so its absence means nothing parsed the "
                f"file body (cpp symbols seen: {sorted(header_symbols)})",
            )

    def test_existing_graph_short_circuits_without_bootstrap(self) -> None:
        # If a graph already exists, _ensure_graph must NOT regenerate
        # it (and must NOT write a discover.yaml). Idempotency guard so
        # repeated calls in the bench loop are cheap.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir(parents=True)
            sentinel = {
                "meta": {"version": "1", "schema_version": 1},
                "nodes": {"file:sentinel": {
                    "type": "file", "label": "sentinel",
                    "props": {"file": "sentinel"},
                }},
                "edges": [],
            }
            (root / ".weld" / "graph.json").write_text(
                json.dumps(sentinel), encoding="utf-8",
            )
            ok = weld_adapter._ensure_graph(root)
            self.assertTrue(ok)
            # Sentinel preserved -> short-circuit confirmed.
            payload = json.loads(
                (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
            )
            self.assertIn("file:sentinel", payload["nodes"])
            # No discover.yaml should have been written -- the short-
            # circuit happens before the bootstrap step.
            self.assertFalse(
                (root / ".weld" / "discover.yaml").exists(),
                "short-circuit must not bootstrap discover.yaml",
            )

    def test_bootstrap_idempotent_when_discover_yaml_exists(self) -> None:
        # If discover.yaml already exists (e.g. user pre-configured the
        # tempdir), _ensure_graph must not overwrite it -- it must reuse
        # the existing config and proceed straight to discovery.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.cpp").write_text(
                "int main() { return 0; }\n", encoding="utf-8",
            )
            (root / ".weld").mkdir()
            user_yaml = "sources:\n  # user-curated\n"
            (root / ".weld" / "discover.yaml").write_text(
                user_yaml, encoding="utf-8",
            )
            ok = weld_adapter._ensure_graph(root)
            self.assertTrue(ok)
            # User's discover.yaml must be preserved verbatim.
            self.assertEqual(
                (root / ".weld" / "discover.yaml").read_text(encoding="utf-8"),
                user_yaml,
            )

    def test_bootstrap_writes_only_inside_repo_root(self) -> None:
        # Containment guard: bootstrap must only ever write into
        # ``repo_root/.weld/``, never elsewhere on the filesystem (the
        # bench tempdir is the trust boundary).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.cpp").write_text(
                "int main() { return 0; }\n", encoding="utf-8",
            )
            ok = weld_adapter._ensure_graph(root)
            self.assertTrue(ok)
            # Every file inside the tempdir must live under root/.
            # Snapshot the tree and check no escape.
            for path in root.rglob("*"):
                self.assertTrue(
                    str(path.resolve()).startswith(str(root.resolve())),
                    f"bootstrap escaped the tempdir: {path}",
                )


if __name__ == "__main__":
    unittest.main()
