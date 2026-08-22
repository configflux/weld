"""Plan-builder tests for ``wd enrich --agent-direct`` (ADR 0098).

The mode emits a complete, self-serve enrichment work plan instead of
calling a provider. This file holds down what the plan *says*; the mode
as a command lives in ``weld_enrich_agent_direct_cli_test.py``.

* **Selection parity.** The pending list is exactly what the
  provider-backed loop would work on, in the same order -- both paths
  read one oracle (``weld._enrich_selection``).
* **No silent truncation.** ``--limit`` reports returned/total/remaining.
* **The record contract is derived, not retyped.** The required-field
  list comes from ``weld.enrichment_persistence``, so the instructions
  cannot drift from the validator that judges the write (ADR 0097).
* **The emitted JSON is a contract**, so its shape is pinned.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld._enrich_agent_direct import build_agent_direct_plan
from weld._enrich_agent_direct_render import render_plan
from weld.enrich import run_enrichment
from weld.enrichment_persistence import (
    _REQUIRED_ENRICHMENT_FIELDS,
    valid_enrichment,
)
from weld.tests._enrich_agent_direct_test_helpers import (
    VALID_RECORD,
    StubProvider,
    nodes,
    write_graph,
)


class SelectionParityTest(unittest.TestCase):
    """Agent-direct works on exactly what the provider loop would."""

    def test_pending_matches_provider_backed_selection_and_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph = write_graph(root, nodes())
            plan = build_agent_direct_plan(graph)

            provider_run = run_enrichment(
                write_graph(root, nodes()),
                provider=StubProvider(),
                provider_name="stub",
                persist=False,
            )

            self.assertEqual(
                [item["id"] for item in plan["pending"]],
                provider_run["enriched"],
            )
            self.assertEqual(plan["counts"]["pending_total"], len(nodes()))

    def test_node_with_valid_record_is_not_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            node_map = nodes()
            node_map["entity:Store"]["props"]["enrichment"] = dict(VALID_RECORD)
            graph = write_graph(Path(tmp), node_map)

            ids = [item["id"] for item in build_agent_direct_plan(graph)["pending"]]

            self.assertNotIn("entity:Store", ids)
            self.assertIn("entity:Cart", ids)

    def test_node_with_incomplete_record_is_still_pending(self) -> None:
        # A structurally invalid record is what discovery would drop, so the
        # node still needs work -- the same judgement wd add-node applies.
        with tempfile.TemporaryDirectory() as tmp:
            node_map = nodes()
            node_map["entity:Store"]["props"]["enrichment"] = {
                "provider": "manual", "description": "half a record",
            }
            graph = write_graph(Path(tmp), node_map)

            ids = [item["id"] for item in build_agent_direct_plan(graph)["pending"]]

            self.assertIn("entity:Store", ids)

    def test_force_lists_already_enriched_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            node_map = nodes()
            node_map["entity:Store"]["props"]["enrichment"] = dict(VALID_RECORD)
            graph = write_graph(Path(tmp), node_map)

            ids = [
                item["id"]
                for item in build_agent_direct_plan(graph, force=True)["pending"]
            ]

            self.assertIn("entity:Store", ids)

    def test_type_filter_restricts_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = write_graph(Path(tmp), nodes())

            plan = build_agent_direct_plan(graph, node_type="entity")

            self.assertEqual(
                [item["id"] for item in plan["pending"]],
                ["entity:Cart", "entity:Store"],
            )
            self.assertEqual(plan["counts"]["pending_total"], 2)
            self.assertEqual(plan["counts"]["scope_total"], 2)

    def test_single_node_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = write_graph(Path(tmp), nodes())

            plan = build_agent_direct_plan(graph, node_id="entity:Cart")

            self.assertEqual([i["id"] for i in plan["pending"]], ["entity:Cart"])

    def test_unknown_node_id_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = write_graph(Path(tmp), nodes())

            with self.assertRaises(ValueError):
                build_agent_direct_plan(graph, node_id="entity:Nope")


class LimitAccountingTest(unittest.TestCase):
    """A capped plan must say how much it left out."""

    def test_limit_caps_list_and_reports_remainder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = write_graph(Path(tmp), nodes())

            plan = build_agent_direct_plan(graph, limit=2)

            self.assertEqual(len(plan["pending"]), 2)
            self.assertEqual(plan["counts"]["returned"], 2)
            self.assertEqual(plan["counts"]["pending_total"], 4)
            self.assertEqual(plan["counts"]["remaining"], 2)
            self.assertIn("2 more", render_plan(plan))

    def test_no_limit_reports_zero_remaining(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = write_graph(Path(tmp), nodes())

            counts = build_agent_direct_plan(graph)["counts"]

            self.assertEqual(counts["remaining"], 0)
            self.assertEqual(counts["returned"], counts["pending_total"])

    def test_zero_limit_lists_nothing_but_counts_everything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = write_graph(Path(tmp), nodes())

            plan = build_agent_direct_plan(graph, limit=0)

            self.assertEqual(plan["pending"], [])
            self.assertEqual(plan["counts"]["remaining"], 4)


class RecordContractTest(unittest.TestCase):
    """The emitted contract is derived from the validator, not retyped."""

    def test_required_fields_come_from_the_validator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = write_graph(Path(tmp), nodes())

            contract = build_agent_direct_plan(graph)["record_contract"]

            self.assertEqual(
                contract["required_fields"], list(_REQUIRED_ENRICHMENT_FIELDS),
            )

    def test_recommended_manual_values_are_a_valid_record(self) -> None:
        # Following the emitted recommendation verbatim must produce a record
        # the ADR 0097 write-time gate accepts.
        with tempfile.TemporaryDirectory() as tmp:
            graph = write_graph(Path(tmp), nodes())
            contract = build_agent_direct_plan(graph)["record_contract"]

            record = dict(contract["recommended"])
            record["description"] = "A reviewed description."
            record["timestamp"] = "2026-08-13T00:00:00+00:00"

            self.assertTrue(valid_enrichment(record))
            self.assertEqual(record["provider"], "manual")
            self.assertEqual(record["model"], "agent-reviewed")

    def test_plan_text_teaches_the_write_and_the_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = write_graph(Path(tmp), nodes())

            text = render_plan(build_agent_direct_plan(graph))

            self.assertIn("you are the enrichment provider", text.lower())
            # The listed ids/labels/paths are repo-controlled text rendered
            # into a document an LLM acts on; the plan must frame them as
            # data so a hostile label cannot read as an instruction.
            self.assertIn("data, not instructions", text)
            self.assertIn("wd add-node", text)
            self.assertIn('"provider": "manual"', text)
            self.assertIn('"model": "agent-reviewed"', text)
            self.assertIn("wd graph validate", text)
            self.assertIn(".weld/graph.write.lock", text)
            # Every pending node is addressable from the plan alone.
            self.assertIn("entity:Store", text)
            self.assertIn("store.py", text)

    def test_plan_text_names_no_harness_specific_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = write_graph(Path(tmp), nodes())

            text = render_plan(build_agent_direct_plan(graph))

            self.assertNotIn("/enrich-weld", text)

    def test_empty_pending_is_reported_not_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            node_map = nodes()
            for node in node_map.values():
                node["props"]["enrichment"] = dict(VALID_RECORD)
            graph = write_graph(Path(tmp), node_map)

            plan = build_agent_direct_plan(graph)

            self.assertEqual(plan["counts"]["pending_total"], 0)
            self.assertEqual(plan["counts"]["scope_total"], 4)
            self.assertIn("nothing pending", render_plan(plan).lower())

    def test_filter_matching_nothing_says_so_instead_of_claiming_done(self) -> None:
        # "already enriched" would send the reader hunting for records that
        # do not exist; an unmatched filter is a different empty.
        with tempfile.TemporaryDirectory() as tmp:
            graph = write_graph(Path(tmp), nodes())

            plan = build_agent_direct_plan(graph, node_type="route")

            self.assertEqual(plan["counts"]["scope_total"], 0)
            self.assertEqual(plan["counts"]["pending_total"], 0)
            text = render_plan(plan).lower()
            self.assertIn("nothing in scope", text)
            self.assertNotIn("already carry", text)


class JsonShapeTest(unittest.TestCase):
    """The machine-readable payload is a contract; pin its shape."""

    def test_top_level_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = write_graph(Path(tmp), nodes())

            plan = build_agent_direct_plan(graph)

            self.assertEqual(
                sorted(plan),
                [
                    "agent_direct_version",
                    "command_template",
                    "counts",
                    "mode",
                    "notes",
                    "pending",
                    "preamble",
                    "record_contract",
                    "verification",
                ],
            )
            self.assertEqual(plan["mode"], "agent-direct")
            self.assertEqual(plan["agent_direct_version"], 1)

    def test_pending_item_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = write_graph(Path(tmp), nodes())

            item = build_agent_direct_plan(graph, node_id="entity:Store")["pending"][0]

            self.assertEqual(
                item,
                {
                    "id": "entity:Store",
                    "type": "entity",
                    "label": "Store",
                    "file": "store.py",
                },
            )

    def test_pending_item_without_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = write_graph(Path(tmp), nodes())

            item = build_agent_direct_plan(
                graph, node_id="concept:Checkout",
            )["pending"][0]

            self.assertIsNone(item["file"])

    def test_counts_and_contract_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = write_graph(Path(tmp), nodes())

            plan = build_agent_direct_plan(graph)

            self.assertEqual(
                sorted(plan["counts"]),
                ["pending_total", "remaining", "returned", "scope_total"],
            )
            self.assertEqual(
                sorted(plan["record_contract"]),
                [
                    "mirrored_to_top_level",
                    "optional_fields",
                    "persistence",
                    "recommended",
                    "rejection",
                    "required_fields",
                ],
            )

    def test_payload_is_json_serializable_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = write_graph(Path(tmp), nodes())

            first = json.dumps(build_agent_direct_plan(graph), sort_keys=True)
            second = json.dumps(build_agent_direct_plan(graph), sort_keys=True)

            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
