"""Tests for federation-gated bypasses in ``validate_graph`` (tracked issue).

The ASCII Unit Separator (``\\x1f``) bypass for dangling-reference checks
and the ``cross_repo:<suffix>`` bypass for invalid-edge-type checks are
gated on ``meta.schema_version == 2`` (federation root). These tests
lock the gate so:

* a single-repo graph that pathologically contains either construct
  still fails validation with a diagnostic that names the offending
  edge endpoint;
* a federation root graph accepts well-formed federation IDs and
  cross-repo edge types;
* even under federation, malformed strings (bare separator,
  multi-separator, bare ``cross_repo:`` prefix) remain dangling /
  invalid and the diagnostic still names the offender.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._federation_validate import (  # noqa: E402
    ROOT_FEDERATED_SCHEMA_VERSION,
    is_well_formed_cross_repo_edge_type,
    is_well_formed_federation_id,
)
from weld._graph_schema import (  # noqa: E402
    ROOT_FEDERATED_SCHEMA_VERSION as _GRAPH_SCHEMA_ROOT_VERSION,
)
from weld.contract import (  # noqa: E402
    SCHEMA_VERSION,
    validate_edge,
    validate_graph,
)


# --- helpers --------------------------------------------------------------

_SEP = "\x1f"


def _service_node(label: str = "api") -> dict:
    return {"type": "service", "label": label, "props": {}}


def _graph(
    *,
    schema_version: int | None,
    edges: list[dict],
    nodes: dict | None = None,
) -> dict:
    """Build a minimal valid graph; *schema_version* may be omitted."""
    meta: dict = {"version": SCHEMA_VERSION, "updated_at": "2026-04-25T00:00Z"}
    if schema_version is not None:
        meta["schema_version"] = schema_version
    g = {
        "meta": meta,
        "nodes": (
            {"service:api": _service_node("api")} if nodes is None else nodes
        ),
        "edges": edges,
    }
    return g


def _edge(from_id: str, to_id: str, *, edge_type: str = "depends_on") -> dict:
    return {"from": from_id, "to": to_id, "type": edge_type, "props": {}}


# --- helper-function unit tests -------------------------------------------

class FederationConstantsLockTest(unittest.TestCase):
    """The federation root schema version must agree across modules."""

    def test_schema_version_matches_graph_schema(self):
        # ``_federation_validate`` mirrors the constant for layering
        # reasons; this lock keeps the two definitions in sync so a
        # bump in ``_graph_schema`` cannot silently disable the gate.
        self.assertEqual(
            ROOT_FEDERATED_SCHEMA_VERSION, _GRAPH_SCHEMA_ROOT_VERSION,
        )


class FederationIdShapeTest(unittest.TestCase):
    """``is_well_formed_federation_id`` shape acceptance/rejection."""

    def test_accepts_canonical_child_id(self):
        self.assertTrue(is_well_formed_federation_id(f"child{_SEP}service:api"))

    def test_rejects_bare_separator(self):
        self.assertFalse(is_well_formed_federation_id(_SEP))

    def test_rejects_leading_separator(self):
        self.assertFalse(is_well_formed_federation_id(f"{_SEP}service:api"))

    def test_rejects_trailing_separator(self):
        self.assertFalse(is_well_formed_federation_id(f"child{_SEP}"))

    def test_rejects_multiple_separators(self):
        self.assertFalse(
            is_well_formed_federation_id(f"a{_SEP}b{_SEP}c")
        )

    def test_rejects_no_separator(self):
        self.assertFalse(is_well_formed_federation_id("plain"))

    def test_rejects_non_string(self):
        self.assertFalse(is_well_formed_federation_id(None))
        self.assertFalse(is_well_formed_federation_id(42))
        self.assertFalse(is_well_formed_federation_id(["a", "b"]))


class CrossRepoEdgeTypeShapeTest(unittest.TestCase):
    """``is_well_formed_cross_repo_edge_type`` shape acceptance/rejection."""

    def test_accepts_suffixed_cross_repo_type(self):
        self.assertTrue(
            is_well_formed_cross_repo_edge_type("cross_repo:calls")
        )

    def test_rejects_bare_prefix(self):
        self.assertFalse(is_well_formed_cross_repo_edge_type("cross_repo:"))

    def test_rejects_partial_prefix(self):
        self.assertFalse(is_well_formed_cross_repo_edge_type("cross_rep"))
        self.assertFalse(is_well_formed_cross_repo_edge_type("cross_repo"))

    def test_rejects_unrelated_string(self):
        self.assertFalse(is_well_formed_cross_repo_edge_type("calls"))

    def test_rejects_non_string(self):
        self.assertFalse(is_well_formed_cross_repo_edge_type(None))
        self.assertFalse(is_well_formed_cross_repo_edge_type(42))


# --- validate_graph integration tests -------------------------------------

class ValidateGraphFederationBypassTest(unittest.TestCase):
    """``validate_graph`` honours the bypass only at ``schema_version == 2``."""

    def test_federation_root_accepts_separator_and_cross_repo_edge(self):
        g = _graph(
            schema_version=2,
            edges=[_edge(
                "service:api",
                f"child{_SEP}service:auth",
                edge_type="cross_repo:calls",
            )],
        )
        self.assertEqual(validate_graph(g), [])

    def test_missing_schema_version_disables_bypass(self):
        g = _graph(
            schema_version=None,
            edges=[_edge(
                "service:api",
                f"child{_SEP}service:auth",
                edge_type="cross_repo:calls",
            )],
        )
        errs = validate_graph(g)
        # both the federation id and the cross_repo edge type must be rejected
        self.assertTrue(
            any(e.field == "to" and "dangling" in e.message for e in errs),
            f"expected dangling-ref error, got {errs!r}",
        )
        self.assertTrue(
            any(e.field == "type" and "invalid edge type" in e.message for e in errs),
            f"expected invalid-edge-type error, got {errs!r}",
        )

    def test_single_repo_schema_version_disables_bypass(self):
        g = _graph(
            schema_version=1,
            edges=[_edge(
                "service:api",
                f"child{_SEP}service:auth",
                edge_type="cross_repo:calls",
            )],
        )
        errs = validate_graph(g)
        self.assertTrue(
            any(e.field == "to" and "dangling" in e.message for e in errs)
        )
        self.assertTrue(
            any(e.field == "type" and "invalid edge type" in e.message for e in errs)
        )

    def test_non_integer_schema_version_disables_bypass(self):
        # A non-integer schema_version is itself an error and must not
        # accidentally enable the federation bypasses.
        g = _graph(
            schema_version=None,
            edges=[_edge(
                "service:api",
                f"child{_SEP}service:auth",
                edge_type="cross_repo:calls",
            )],
        )
        # inject a string schema_version to mimic a tampered/legacy file
        g["meta"]["schema_version"] = "2"
        errs = validate_graph(g)
        self.assertTrue(
            any(e.field == "to" and "dangling" in e.message for e in errs)
        )
        self.assertTrue(
            any(e.field == "type" and "invalid edge type" in e.message for e in errs)
        )

    def test_unrelated_schema_version_disables_bypass(self):
        g = _graph(
            schema_version=99,
            edges=[_edge(
                "service:api",
                f"child{_SEP}service:auth",
                edge_type="cross_repo:calls",
            )],
        )
        errs = validate_graph(g)
        self.assertTrue(
            any(e.field == "to" and "dangling" in e.message for e in errs)
        )

    def test_bypass_does_not_apply_to_in_graph_dangling_refs(self):
        # Even under federation, a non-federation-shaped dangling ref
        # (no separator at all) is still flagged.
        g = _graph(
            schema_version=2,
            edges=[_edge("service:api", "service:nope")],
        )
        errs = validate_graph(g)
        self.assertTrue(
            any(e.field == "to" and "dangling" in e.message for e in errs)
        )


class ValidateGraphMalformedScopeTest(unittest.TestCase):
    """Malformed federation constructs are rejected even when federated.

    These cover the bypass-tightening from tracked issue: the bypass only
    applies to *well-formed* federation IDs / ``cross_repo:`` edge types.
    Pathological strings remain dangling/invalid so the diagnostic
    still names the offending edge.
    """

    def test_bare_separator_id_still_dangling_under_federation(self):
        g = _graph(
            schema_version=2,
            edges=[_edge("service:api", _SEP)],
        )
        errs = validate_graph(g)
        msg = "; ".join(str(e) for e in errs)
        self.assertTrue(
            any(e.field == "to" and "dangling" in e.message for e in errs),
            f"bare separator should be dangling, got {msg!r}",
        )

    def test_leading_separator_id_still_dangling_under_federation(self):
        g = _graph(
            schema_version=2,
            edges=[_edge("service:api", f"{_SEP}service:auth")],
        )
        errs = validate_graph(g)
        self.assertTrue(
            any(e.field == "to" and "dangling" in e.message for e in errs)
        )

    def test_trailing_separator_id_still_dangling_under_federation(self):
        g = _graph(
            schema_version=2,
            edges=[_edge("service:api", f"child{_SEP}")],
        )
        errs = validate_graph(g)
        self.assertTrue(
            any(e.field == "to" and "dangling" in e.message for e in errs)
        )

    def test_multi_separator_id_still_dangling_under_federation(self):
        g = _graph(
            schema_version=2,
            edges=[_edge("service:api", f"a{_SEP}b{_SEP}c")],
        )
        errs = validate_graph(g)
        self.assertTrue(
            any(e.field == "to" and "dangling" in e.message for e in errs)
        )

    def test_bare_cross_repo_prefix_still_invalid_under_federation(self):
        nodes = {
            "service:api": _service_node("api"),
            "service:auth": _service_node("auth"),
        }
        g = _graph(
            schema_version=2,
            edges=[_edge(
                "service:api", "service:auth", edge_type="cross_repo:",
            )],
            nodes=nodes,
        )
        errs = validate_graph(g)
        type_errs = [e for e in errs if e.field == "type"]
        self.assertTrue(
            any("invalid edge type" in e.message for e in type_errs),
            f"bare cross_repo: should be invalid, got {errs!r}",
        )

    def test_diagnostic_names_offending_edge_endpoints(self):
        # The "names the offending node" requirement from the issue:
        # the dangling-ref diagnostic must include the bad id verbatim.
        bad = f"a{_SEP}b{_SEP}c"
        g = _graph(
            schema_version=2,
            edges=[_edge("service:api", bad)],
        )
        errs = validate_graph(g)
        self.assertTrue(
            any(repr(bad) in e.message for e in errs),
            f"diagnostic must name {bad!r}; got {[str(e) for e in errs]!r}",
        )


class ValidateEdgeFederationFlagTest(unittest.TestCase):
    """``validate_edge`` honours the explicit ``federation`` keyword."""

    def test_default_federation_false_rejects_separator_id(self):
        nids = {"service:api"}
        e = _edge("service:api", f"child{_SEP}svc")
        errs = validate_edge(e, nids)
        self.assertTrue(any(err.field == "to" for err in errs))

    def test_federation_true_accepts_well_formed_separator_id(self):
        nids = {"service:api"}
        e = _edge("service:api", f"child{_SEP}svc")
        errs = validate_edge(e, nids, federation=True)
        # No dangling-ref error on ``to`` -- but the unknown ``service:api``
        # to-end is fine and the type ``depends_on`` is in vocab.
        self.assertEqual(errs, [])

    def test_federation_true_still_flags_bare_separator(self):
        nids = {"service:api"}
        e = _edge("service:api", _SEP)
        errs = validate_edge(e, nids, federation=True)
        self.assertTrue(any(err.field == "to" for err in errs))

    def test_federation_true_still_flags_bare_cross_repo_prefix(self):
        nids = {"service:api", "service:auth"}
        e = _edge("service:api", "service:auth", edge_type="cross_repo:")
        errs = validate_edge(e, nids, federation=True)
        self.assertTrue(any(err.field == "type" for err in errs))


if __name__ == "__main__":
    unittest.main()
