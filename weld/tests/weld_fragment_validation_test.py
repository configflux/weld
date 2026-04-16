"""Tests for graph fragment validation (strategy, topology, adapter)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import validate_fragment  # noqa: E402

# -- helpers ---------------------------------------------------------------

_N = {"type": "service", "label": "API", "props": {"file": "services/api/main.py"}}
_N2 = {"type": "package", "label": "Domain", "props": {}}
_E = {"from": "service:api", "to": "package:domain", "type": "depends_on", "props": {}}

def _frag(*, nodes=None, edges=None, **extra):
    """Build a fragment dict; defaults to a valid two-node, one-edge shape."""
    f = {
        "nodes": {"service:api": dict(_N), "package:domain": dict(_N2)}
        if nodes is None else nodes,
        "edges": [dict(_E)] if edges is None else edges,
    }
    f.update(extra)
    return f

# -- Top-level structure ---------------------------------------------------

class FragmentStructureTest(unittest.TestCase):

    def test_valid_fragment_passes(self):
        self.assertEqual(validate_fragment(_frag()), [])

    def test_missing_nodes(self):
        f = _frag()
        del f["nodes"]
        self.assertTrue(any("nodes" in e.field for e in validate_fragment(f)))

    def test_missing_edges(self):
        f = _frag()
        del f["edges"]
        self.assertTrue(any("edges" in e.field for e in validate_fragment(f)))

    def test_nodes_must_be_dict(self):
        self.assertTrue(any(
            "nodes" in e.field for e in validate_fragment(_frag(nodes=[]))
        ))

    def test_edges_must_be_list(self):
        self.assertTrue(any(
            "edges" in e.field for e in validate_fragment(_frag(edges={}))
        ))

    def test_empty_fragment_valid(self):
        self.assertEqual(validate_fragment(_frag(nodes={}, edges=[])), [])

    def test_none_rejected(self):
        self.assertTrue(len(validate_fragment(None)) > 0)  # type: ignore[arg-type]

    def test_non_dict_rejected(self):
        self.assertTrue(len(validate_fragment("x")) > 0)  # type: ignore[arg-type]

# -- Source label in diagnostics -------------------------------------------

class FragmentSourceLabelTest(unittest.TestCase):

    def test_source_label_in_path(self):
        errs = validate_fragment(
            _frag(nodes=[], edges={}), source_label="strategy:sqlalchemy",
        )
        self.assertTrue(all("strategy:sqlalchemy" in e.path for e in errs), errs)

    def test_default_source_label(self):
        errs = validate_fragment(_frag(nodes=[], edges={}))
        self.assertTrue(all("fragment" in e.path for e in errs), errs)

# -- Node validation delegation -------------------------------------------

class FragmentNodeValidationTest(unittest.TestCase):

    def test_invalid_node_type(self):
        errs = validate_fragment(_frag(
            nodes={"bad:x": {"type": "spaceship", "label": "X", "props": {}}},
            edges=[],
        ))
        self.assertTrue(any("type" in e.field for e in errs))

    def test_missing_label(self):
        errs = validate_fragment(_frag(
            nodes={"service:api": {"type": "service", "props": {}}}, edges=[],
        ))
        self.assertTrue(any("label" in e.field for e in errs))

    def test_missing_props(self):
        errs = validate_fragment(_frag(
            nodes={"service:api": {"type": "service", "label": "API"}}, edges=[],
        ))
        self.assertTrue(any("props" in e.field for e in errs))

    def test_invalid_authority(self):
        errs = validate_fragment(_frag(
            nodes={"service:api": {
                "type": "service", "label": "API",
                "props": {"authority": "supreme"},
            }},
            edges=[],
        ))
        self.assertTrue(any("authority" in e.field for e in errs))

    def test_invalid_confidence(self):
        errs = validate_fragment(_frag(
            nodes={"service:api": {
                "type": "service", "label": "API",
                "props": {"confidence": "maybe"},
            }},
            edges=[],
        ))
        self.assertTrue(any("confidence" in e.field for e in errs))

    def test_multiple_bad_nodes_all_reported(self):
        errs = validate_fragment(_frag(
            nodes={
                "bad:a": {"type": "spaceship", "label": "A", "props": {}},
                "bad:b": {"type": "ufo", "label": "B", "props": {}},
            },
            edges=[],
        ))
        paths = [e.path for e in errs]
        self.assertTrue(any("bad:a" in p for p in paths), paths)
        self.assertTrue(any("bad:b" in p for p in paths), paths)

# -- Edge validation delegation -------------------------------------------

class FragmentEdgeValidationTest(unittest.TestCase):

    def _two_nodes(self):
        return {
            "service:api": {"type": "service", "label": "API", "props": {}},
            "package:domain": {"type": "package", "label": "Domain", "props": {}},
        }

    def test_invalid_edge_type(self):
        errs = validate_fragment(_frag(
            nodes=self._two_nodes(),
            edges=[{"from": "service:api", "to": "package:domain",
                    "type": "teleports_to", "props": {}}],
        ))
        self.assertTrue(any("type" in e.field for e in errs))

    def test_dangling_from(self):
        errs = validate_fragment(_frag(
            nodes={"package:domain": _N2},
            edges=[{"from": "service:ghost", "to": "package:domain",
                    "type": "depends_on", "props": {}}],
        ))
        self.assertTrue(any("from" in e.field for e in errs))

    def test_dangling_to(self):
        errs = validate_fragment(_frag(
            nodes={"service:api": dict(_N)},
            edges=[{"from": "service:api", "to": "package:ghost",
                    "type": "depends_on", "props": {}}],
        ))
        self.assertTrue(any("to" in e.field for e in errs))

    def test_missing_edge_props(self):
        errs = validate_fragment(_frag(
            nodes=self._two_nodes(),
            edges=[{"from": "service:api", "to": "package:domain",
                    "type": "depends_on"}],
        ))
        self.assertTrue(any("props" in e.field for e in errs))

    def test_allow_dangling_skips_ref_check(self):
        errs = validate_fragment(
            _frag(
                nodes={"service:api": dict(_N)},
                edges=[{"from": "service:api", "to": "package:ext",
                        "type": "depends_on", "props": {}}],
            ),
            allow_dangling_edges=True,
        )
        self.assertFalse(
            any(e.field == "to" and "dangling" in e.message for e in errs), errs,
        )

    def test_allow_dangling_still_validates_types(self):
        errs = validate_fragment(
            _frag(
                nodes={"service:api": dict(_N)},
                edges=[{"from": "service:api", "to": "package:ext",
                        "type": "teleports_to", "props": {}}],
            ),
            allow_dangling_edges=True,
        )
        self.assertTrue(any("type" in e.field for e in errs))

# -- discovered_from validation -------------------------------------------

class FragmentDiscoveredFromTest(unittest.TestCase):

    def test_valid_discovered_from(self):
        self.assertEqual(
            validate_fragment(_frag(discovered_from=["libs/domain/"])), [],
        )

    def test_must_be_list(self):
        errs = validate_fragment(_frag(discovered_from="services/api/"))
        self.assertTrue(any("discovered_from" in e.field for e in errs))

    def test_entries_must_be_strings(self):
        errs = validate_fragment(_frag(discovered_from=["ok", 42]))
        self.assertTrue(any("discovered_from" in e.field for e in errs))

    def test_missing_is_ok(self):
        self.assertEqual(validate_fragment(_frag(nodes={}, edges=[])), [])

# -- Integration: realistic fragment shapes --------------------------------

class FragmentIntegrationTest(unittest.TestCase):

    def test_strategy_result_shape(self):
        frag = _frag(
            nodes={"file:api/main": {
                "type": "file", "label": "main",
                "props": {"file": "services/api/main.py", "exports": ["app"],
                           "source_strategy": "python_module",
                           "authority": "canonical", "confidence": "definite"},
            }},
            edges=[], discovered_from=["services/api/"],
        )
        self.assertEqual(
            validate_fragment(frag, source_label="strategy:python_module"), [],
        )

    def test_topology_overlay_shape(self):
        frag = _frag(
            nodes={
                "service:api": {
                    "type": "service", "label": "API Service",
                    "props": {"authority": "manual"},
                },
                "service:worker": {
                    "type": "service", "label": "Worker Service",
                    "props": {"authority": "manual"},
                },
            },
            edges=[{"from": "service:worker", "to": "service:api",
                    "type": "depends_on",
                    "props": {"confidence": "definite"}}],
        )
        self.assertEqual(validate_fragment(frag, source_label="topology"), [])

    def test_adapter_fragment_shape(self):
        frag = _frag(
            nodes={"tool:custom-lint": {
                "type": "tool", "label": "Custom Lint",
                "props": {"source_strategy": "external_json",
                           "authority": "external"},
            }},
            edges=[], discovered_from=["tools/"],
        )
        self.assertEqual(
            validate_fragment(frag, source_label="adapter:custom-lint"), [],
        )

    def test_mixed_errors_all_reported(self):
        frag = {
            "nodes": {"bad:x": {"type": "spaceship", "label": "X", "props": {}}},
            "edges": [{"from": "bad:x", "to": "ghost:y",
                       "type": "depends_on", "props": {}}],
            "discovered_from": 42,
        }
        errs = validate_fragment(frag, source_label="test")
        # node type + dangling edge to + bad discovered_from
        self.assertTrue(len(errs) >= 3, f"Expected >= 3 errors, got {errs}")

if __name__ == "__main__":
    unittest.main()
