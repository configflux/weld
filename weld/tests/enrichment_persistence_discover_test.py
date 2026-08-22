"""Integration: enrichment persists across rediscovery (ADR 0079).

Drives real ``_discover_single_repo`` runs over a python_callgraph fixture and
asserts that ``props.enrichment`` survives full and incremental rediscovery,
is invalidated when the node's own source changes, activates the semantic
ranking slot, and keeps the incremental==full byte-identity contract with
enrichment present.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo
from weld.enrich import run_enrichment
from weld.enrichment_persistence import enrichment_fingerprint, valid_enrichment
from weld.graph import Graph
from weld.providers import EnrichmentResult
from weld.query_state import build_query_state

ALPHA_SYMBOL = "symbol:py:src.pkg.alpha:alpha_fn"
ALPHA_FILE = "file:src/pkg/alpha"


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


def _fixture(root: Path) -> None:
    pkg = root / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "alpha.py").write_text("def alpha_fn():\n    return 1\n", encoding="utf-8")
    (pkg / "beta.py").write_text("def beta_fn():\n    return 2\n", encoding="utf-8")
    (root / ".weld").mkdir()
    # Both strategies on one glob, mirroring the repo's own discover.yaml:
    # python_module gives file nodes their real prop vocabulary (exports /
    # constants / imports_from / line_count), which the ADR 0097 file-node
    # policy is stated in terms of.
    (root / ".weld" / "discover.yaml").write_text(
        "sources:\n  - strategy: python_module\n    glob: src/**/*.py\n"
        "    type: file\n  - strategy: python_callgraph\n    glob: src/**/*.py\n"
        "    type: file\n",
        encoding="utf-8",
    )


def _graph_json(root: Path) -> dict:
    return json.loads((root / ".weld" / "graph.json").read_text(encoding="utf-8"))


def _strip_volatile(graph: dict) -> dict:
    out = {k: v for k, v in graph.items() if k != "meta"}
    out["meta"] = {
        k: v for k, v in graph.get("meta", {}).items()
        if k not in ("updated_at", "git_sha", "discovered_from")
    }
    return out


class _StubProvider:
    DEFAULT_MODEL = "stub-model"

    def __init__(self, responses: dict[str, dict]) -> None:
        self._responses = responses

    def enrich(self, node: dict, neighbors: list[dict], *, model: str) -> EnrichmentResult:
        payload = self._responses[node["id"]]
        return EnrichmentResult(
            description=payload["description"],
            purpose=payload.get("purpose"),
            complexity_hint=payload.get("complexity_hint"),
            suggested_tags=payload.get("suggested_tags", []),
            tokens_used=0,
            cost_usd=0.0,
        )


def _enrich(root: Path, node_id: str, description: str, purpose: str | None = None) -> None:
    graph = Graph(root)
    graph.load()
    run_enrichment(
        graph,
        provider=_StubProvider({node_id: {"description": description, "purpose": purpose}}),
        provider_name="stub",
        node_id=node_id,
        persist=True,
    )


def _seed_record(root: Path, node_id: str, description: str) -> dict:
    """Write a fingerprinted provider-style record straight onto the on-disk graph.

    A fixed timestamp keeps two seeded roots byte-identical (a live ``wd enrich``
    pair would differ only in the timestamp prop, which is not volatile-stripped).
    """
    on_disk = _graph_json(root)
    node = on_disk["nodes"][node_id]
    record = {
        "provider": "stub",
        "model": "stub-model",
        "timestamp": "2026-07-07T00:00:00+00:00",
        "fingerprint": enrichment_fingerprint({"id": node_id, **node}),
        "description": description,
    }
    node["props"]["enrichment"] = record
    node["props"]["description"] = description
    (root / ".weld" / "graph.json").write_text(json.dumps(on_disk), encoding="utf-8")
    return record


class EnrichmentSurvivesRediscoveryTest(unittest.TestCase):
    def test_provider_enrichment_survives_and_activates_semantic_slot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _fixture(root)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            _enrich(root, ALPHA_SYMBOL, "Alpha computes the answer.", "Because tests.")

            graph = _discover_single_repo(root, incremental=False, write_graph=True)

            props = graph["nodes"][ALPHA_SYMBOL]["props"]
            self.assertEqual(props["enrichment"]["description"], "Alpha computes the answer.")
            self.assertEqual(props["description"], "Alpha computes the answer.")
            self.assertEqual(props["purpose"], "Because tests.")
            # Semantic ranking slot activates once a node carries enrichment.
            state = build_query_state(graph["nodes"], graph["edges"])
            self.assertIsNotNone(state.embedding_cache)

    def test_enrichment_survives_incremental_refresh_of_other_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _fixture(root)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            _enrich(root, ALPHA_SYMBOL, "Alpha computes the answer.")
            # Touch a DIFFERENT file; alpha's own source is unchanged.
            (root / "src" / "pkg" / "beta.py").write_text(
                "def beta_fn():\n    return 22\n", encoding="utf-8",
            )
            _commit(root)

            graph = _discover_single_repo(root, incremental=True, write_graph=True)

            self.assertEqual(
                graph["nodes"][ALPHA_SYMBOL]["props"]["enrichment"]["description"],
                "Alpha computes the answer.",
            )

    def test_enrichment_invalidated_when_node_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _fixture(root)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            _enrich(root, ALPHA_FILE, "The alpha module.")
            # Add an export: the FILE node's own semantic identity changes
            # (`exports` is in the fingerprint) -> enrichment must invalidate.
            (root / "src" / "pkg" / "alpha.py").write_text(
                "def alpha_fn():\n    return 1\n\n\ndef added_fn():\n    return 3\n",
                encoding="utf-8",
            )
            _commit(root)

            graph = _discover_single_repo(root, incremental=True, write_graph=True)

            self.assertNotIn("enrichment", graph["nodes"][ALPHA_FILE]["props"])

    def test_line_shift_above_symbol_retains_enrichment_on_both_paths(self) -> None:
        # ADR 0097 regression: prepending a line shifts alpha_fn from line 1 to
        # line 2 without touching it. Its enrichment must survive on the
        # incremental AND the full path, and the two graphs must stay
        # byte-identical.
        graphs = {}
        for incremental in (True, False):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                _git(root)
                _fixture(root)
                _commit(root)
                _discover_single_repo(root, incremental=False, write_graph=True)
                _seed_record(root, ALPHA_SYMBOL, "Alpha computes the answer.")
                (root / "src" / "pkg" / "alpha.py").write_text(
                    "# a comment added above\ndef alpha_fn():\n    return 1\n",
                    encoding="utf-8",
                )
                _commit(root)
                graphs[incremental] = _discover_single_repo(
                    root, incremental=incremental, write_graph=True,
                )

        for incremental, graph in graphs.items():
            props = graph["nodes"][ALPHA_SYMBOL]["props"]
            self.assertEqual(
                props.get("enrichment", {}).get("description"),
                "Alpha computes the answer.",
                f"enrichment dropped by a pure line shift (incremental={incremental})",
            )
            # The shift really happened -- the node was re-minted at a new line.
            self.assertEqual(props["line"], 2)
        self.assertEqual(
            _strip_volatile(graphs[True]), _strip_volatile(graphs[False]),
        )

    def test_file_node_enrichment_survives_line_count_only_change(self) -> None:
        # ADR 0097 file-node policy: line_count is a size measure, not identity.
        # A comment grows the file without changing its exports/constants/
        # imports_from, so the file node's enrichment must survive.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _fixture(root)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            _enrich(root, ALPHA_FILE, "The alpha module.")
            (root / "src" / "pkg" / "alpha.py").write_text(
                "# a comment added above\ndef alpha_fn():\n    return 1\n",
                encoding="utf-8",
            )
            _commit(root)

            graph = _discover_single_repo(root, incremental=True, write_graph=True)

            props = graph["nodes"][ALPHA_FILE]["props"]
            self.assertEqual(props["line_count"], 3)  # the file really grew
            self.assertEqual(props["enrichment"]["description"], "The alpha module.")
            self.assertEqual(props["description"], "The alpha module.")

    def test_manual_enrichment_without_fingerprint_is_sticky(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _fixture(root)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            # Agent-direct/manual write: no fingerprint field.
            on_disk = _graph_json(root)
            on_disk["nodes"][ALPHA_SYMBOL]["props"]["enrichment"] = {
                "provider": "manual",
                "model": "agent-reviewed",
                "timestamp": "2026-07-07T00:00:00+00:00",
                "description": "Hand-written description.",
            }
            (root / ".weld" / "graph.json").write_text(
                json.dumps(on_disk), encoding="utf-8",
            )

            graph = _discover_single_repo(root, incremental=False, write_graph=True)

            props = graph["nodes"][ALPHA_SYMBOL]["props"]
            self.assertEqual(props["enrichment"]["provider"], "manual")
            self.assertEqual(props["description"], "Hand-written description.")


class AgentDirectPlanRoundTripTest(unittest.TestCase):
    """The plan `wd enrich --agent-direct` emits actually works (ADR 0098).

    Follows the emitted contract verbatim through the real ``wd add-node``
    write path -- which judges the record at write time (ADR 0097) -- and
    then rediscovers. If the instructions ever drift from what the
    validator accepts or what discovery preserves, this fails.
    """

    def _record_from_contract(self, contract: dict, description: str) -> dict:
        record = dict(contract["recommended"])
        record["timestamp"] = "2026-08-13T00:00:00+00:00"
        record["description"] = description
        self.assertEqual(
            sorted(record), sorted(contract["required_fields"]),
            "the emitted contract no longer describes a complete record",
        )
        return record

    def test_following_the_emitted_plan_lands_and_survives_discover(self) -> None:
        from weld._enrich_agent_direct import build_agent_direct_plan
        from weld._graph_cli import main as graph_main

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _fixture(root)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)

            graph = Graph(root)
            graph.load()
            plan = build_agent_direct_plan(graph, node_id=ALPHA_SYMBOL)
            entry = plan["pending"][0]
            record = self._record_from_contract(
                plan["record_contract"], "Alpha computes the answer.",
            )
            props = {"description": record["description"], "enrichment": record}

            graph_main([
                "--root", str(root), "add-node", entry["id"],
                "--type", entry["type"], "--label", entry["label"],
                "--merge", "--props", json.dumps(props),
            ])

            written = _graph_json(root)["nodes"][ALPHA_SYMBOL]["props"]["enrichment"]
            self.assertTrue(valid_enrichment(written))

            rediscovered = _discover_single_repo(
                root, incremental=False, write_graph=True,
            )
            props_after = rediscovered["nodes"][ALPHA_SYMBOL]["props"]
            self.assertEqual(
                props_after["enrichment"]["description"], "Alpha computes the answer.",
            )
            self.assertEqual(props_after["description"], "Alpha computes the answer.")

            # The plan is also a progress report: the node it told us to
            # enrich is no longer pending afterwards.
            after = Graph(root)
            after.load()
            self.assertEqual(build_agent_direct_plan(after, node_id=ALPHA_SYMBOL)[
                "counts"]["pending_total"], 0)


class IncrementalFullByteIdentityWithEnrichmentTest(unittest.TestCase):
    def _seed_enriched(self, root: Path) -> None:
        _git(root)
        _fixture(root)
        _commit(root)
        _discover_single_repo(root, incremental=False, write_graph=True)
        _seed_record(root, ALPHA_SYMBOL, "Alpha computes the answer.")

    def test_incremental_matches_full_with_enrichment_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._seed_enriched(root)
            # Change a different file; alpha's enrichment must persist on the
            # incremental merge path.
            (root / "src" / "pkg" / "beta.py").write_text(
                "def beta_fn():\n    return 22\n", encoding="utf-8",
            )
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._seed_enriched(root)
            (root / "src" / "pkg" / "beta.py").write_text(
                "def beta_fn():\n    return 22\n", encoding="utf-8",
            )
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        self.assertEqual(
            g_inc["nodes"][ALPHA_SYMBOL]["props"].get("enrichment", {}).get("description"),
            "Alpha computes the answer.",
        )
        self.assertEqual(_strip_volatile(g_inc), _strip_volatile(g_full))


if __name__ == "__main__":
    unittest.main()
