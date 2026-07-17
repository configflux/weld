"""Unit tests for enrichment persistence primitives (ADR 0079).

Covers the node-only fingerprint, record validation/extraction, and the
in-place ``reattach_enrichment`` reconciliation used by discovery.
"""

from __future__ import annotations

import unittest

from weld.enrichment_persistence import (
    enrichment_fingerprint,
    enrichment_records,
    reattach_enrichment,
    valid_enrichment,
)


def _node(node_type: str, label: str, **props: object) -> dict:
    return {"type": node_type, "label": label, "props": props}


def _record(**overrides: object) -> dict:
    record = {
        "provider": "stub",
        "model": "stub-model",
        "timestamp": "2026-07-07T00:00:00+00:00",
        "description": "What the node is.",
    }
    record.update(overrides)
    return record


def _with_id(node_id: str, node: dict) -> dict:
    return {"id": node_id, **node}


class EnrichmentFingerprintTest(unittest.TestCase):
    def test_excludes_enrichment_output_fields(self) -> None:
        base = _with_id("n1", _node("file", "n1", file="a.py", line=3))
        enriched = _with_id(
            "n1",
            _node(
                "file",
                "n1",
                file="a.py",
                line=3,
                description="desc",
                purpose="why",
                enrichment=_record(),
            ),
        )
        self.assertEqual(
            enrichment_fingerprint(base), enrichment_fingerprint(enriched)
        )

    def test_changes_when_structural_prop_changes(self) -> None:
        before = _with_id("n1", _node("file", "n1", file="a.py", line=3))
        after = _with_id("n1", _node("file", "n1", file="a.py", line=4))
        self.assertNotEqual(
            enrichment_fingerprint(before), enrichment_fingerprint(after)
        )

    def test_is_node_only_identity_not_neighbor_sensitive(self) -> None:
        # The fingerprint is a pure function of the node's own id/type/label/
        # structural props; it has no neighbor input, so a caller/callee change
        # cannot move it. Same node dict -> same fingerprint, regardless of the
        # rest of the graph.
        node = _with_id("n1", _node("file", "n1", file="a.py", line=3))
        self.assertEqual(
            enrichment_fingerprint(node), enrichment_fingerprint(dict(node))
        )


class ValidEnrichmentTest(unittest.TestCase):
    def test_accepts_complete_record(self) -> None:
        self.assertTrue(valid_enrichment(_record()))

    def test_rejects_non_dict_and_missing_or_blank_fields(self) -> None:
        self.assertFalse(valid_enrichment(None))
        self.assertFalse(valid_enrichment("nope"))
        self.assertFalse(valid_enrichment(_record(description="   ")))
        broken = _record()
        del broken["model"]
        self.assertFalse(valid_enrichment(broken))

    def test_records_extracts_valid_only(self) -> None:
        previous = {
            "nodes": {
                "n1": _node("file", "n1", enrichment=_record()),
                "n2": _node("file", "n2", enrichment={"provider": "x"}),
                "n3": _node("file", "n3"),
            }
        }
        records = enrichment_records(previous)
        self.assertEqual(set(records), {"n1"})


class ReattachEnrichmentTest(unittest.TestCase):
    def test_fast_path_no_records_is_noop(self) -> None:
        nodes = {"n1": _node("file", "n1", file="a.py")}
        before = {"n1": dict(nodes["n1"], props=dict(nodes["n1"]["props"]))}
        reattach_enrichment(nodes, {"nodes": {}})
        self.assertEqual(nodes, before)
        reattach_enrichment(nodes, None)
        self.assertEqual(nodes, before)

    def test_matching_fingerprint_reattaches_and_mirrors(self) -> None:
        nodes = {"n1": _node("file", "n1", file="a.py", line=3)}
        fingerprint = enrichment_fingerprint(_with_id("n1", nodes["n1"]))
        record = _record(fingerprint=fingerprint, purpose="Why it exists.")
        previous = {"nodes": {"n1": _node("file", "n1", enrichment=record)}}

        reattach_enrichment(nodes, previous)

        props = nodes["n1"]["props"]
        self.assertEqual(props["enrichment"], record)
        self.assertEqual(props["description"], "What the node is.")
        self.assertEqual(props["purpose"], "Why it exists.")

    def test_mismatched_fingerprint_drops_enrichment(self) -> None:
        # Node's own structural source changed (line 3 -> 9): the stored
        # fingerprint no longer matches, so enrichment is invalidated and the
        # fresh structural description stands.
        nodes = {
            "n1": _node(
                "file", "n1", file="a.py", line=9, description="Fresh structural.",
            )
        }
        record = _record(fingerprint="fingerprint-from-line-3")
        previous = {"nodes": {"n1": _node("file", "n1", enrichment=record)}}

        reattach_enrichment(nodes, previous)

        props = nodes["n1"]["props"]
        self.assertNotIn("enrichment", props)
        self.assertEqual(props["description"], "Fresh structural.")

    def test_fingerprintless_manual_record_persists_verbatim(self) -> None:
        # Agent-direct/manual enrichment stores no fingerprint; it must survive
        # rediscovery (sticky) even though the node was re-minted from source.
        nodes = {"n1": _node("file", "n1", file="a.py", line=42)}
        record = _record(provider="manual", model="agent-reviewed", purpose="P.")
        self.assertNotIn("fingerprint", record)
        previous = {"nodes": {"n1": _node("file", "n1", enrichment=record)}}

        reattach_enrichment(nodes, previous)

        props = nodes["n1"]["props"]
        self.assertEqual(props["enrichment"], record)
        self.assertEqual(props["description"], "What the node is.")
        self.assertEqual(props["purpose"], "P.")

    def test_record_without_purpose_drops_top_level_purpose(self) -> None:
        nodes = {"n1": _node("file", "n1", file="a.py", purpose="stale mirror")}
        fingerprint = enrichment_fingerprint(_with_id("n1", nodes["n1"]))
        record = _record(fingerprint=fingerprint)
        previous = {"nodes": {"n1": _node("file", "n1", enrichment=record)}}

        reattach_enrichment(nodes, previous)

        self.assertNotIn("purpose", nodes["n1"]["props"])

    def test_strips_carried_enrichment_without_matching_record(self) -> None:
        # A carried node still holds a malformed enrichment the previous graph
        # no longer validates; with some other node validly enriched, the
        # reconciliation strips the orphan so both discover paths converge.
        nodes = {
            "orphan": _node("file", "orphan", file="a.py", enrichment={"bad": True}),
            "keep": _node("file", "keep", file="b.py"),
        }
        keep_fp = enrichment_fingerprint(_with_id("keep", nodes["keep"]))
        previous = {
            "nodes": {
                "orphan": _node("file", "orphan", enrichment={"bad": True}),
                "keep": _node(
                    "file", "keep", enrichment=_record(fingerprint=keep_fp),
                ),
            }
        }

        reattach_enrichment(nodes, previous)

        self.assertNotIn("enrichment", nodes["orphan"]["props"])
        self.assertIn("enrichment", nodes["keep"]["props"])


if __name__ == "__main__":
    unittest.main()
