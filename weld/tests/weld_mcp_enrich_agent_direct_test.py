"""``weld_enrich(agent_direct=true)`` on MCP == ``wd enrich --agent-direct``.

ADR 0083 lets the MCP server re-expose CLI capability and nothing more, so
the ADR 0098 agent-direct work plan -- the answer for a caller with no
provider configured -- cannot stay CLI-only: an MCP client is exactly the
caller most likely to need it.

Parity here is *structural*, not asserted field by field: both surfaces call
:func:`weld._enrich_agent_direct.build_agent_direct_plan` with the same
arguments, so the only way they could disagree is if one of them stopped
calling it. :class:`CliMcpEnrichParityTest` is the tripwire for that, and the
rest of the module pins the properties the mode claims:

* it takes no graph write lock and resolves no provider, so it is safe under
  any trust posture and needs no credentials (ADR 0098);
* it leaves ``graph.json`` byte-identical -- ``weld_enrich`` is otherwise the
  mutating tool, and this mode is the read-only half of it;
* a contradictory argument combination is refused by the same oracle the CLI
  uses rather than silently dropped, because a quietly ignored ``provider``
  would leave the caller believing an unattended run happened;
* ``weld_enrich`` stays in :data:`weld._mcp_tools.ROOTLESS_TOOLS`.
  ``agent_direct`` selects a *mode*, not a checkout, so it must not become a
  back door for the request-supplied ``root`` the write tools refuse.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from weld import mcp_server
from weld._enrich_agent_direct import AGENT_DIRECT_VERSION
from weld.enrich import main as enrich_main
from weld.tests._enrich_agent_direct_test_helpers import nodes, write_graph


def _cli_plan(root: Path, *extra: str) -> dict:
    """Return ``wd enrich --agent-direct --json`` parsed, for *root*."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = enrich_main(
            ["--root", str(root), "--agent-direct", "--json", *extra],
        )
    assert rc == 0, f"CLI exited {rc}"
    return json.loads(buf.getvalue())


class CliMcpEnrichParityTest(unittest.TestCase):
    """The CLI ``--json`` plan equals the MCP handler payload."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        write_graph(self.root, nodes())

    def test_default_plan_is_identical_on_both_surfaces(self) -> None:
        cli = _cli_plan(self.root)
        served = mcp_server.weld_enrich(agent_direct=True, root=str(self.root))

        self.assertEqual(cli, served)
        # Non-degenerate: a plan that listed nothing would compare equal
        # while proving nothing about the builder being reached.
        self.assertEqual(served["agent_direct_version"], AGENT_DIRECT_VERSION)
        self.assertEqual(served["mode"], "agent-direct")
        self.assertEqual(served["counts"]["pending_total"], len(nodes()))
        self.assertIn(
            "entity:Store", {entry["id"] for entry in served["pending"]},
        )

    def test_limit_and_type_pass_through_identically(self) -> None:
        # --limit caps the list but not the accounting, and --type narrows the
        # scope; both must mean the same thing through the MCP arguments.
        cli = _cli_plan(self.root, "--type", "entity", "--limit", "1")
        served = mcp_server.weld_enrich(
            agent_direct=True, node_type="entity", limit=1, root=str(self.root),
        )

        self.assertEqual(cli, served)
        self.assertEqual(served["counts"]["returned"], 1)
        self.assertEqual(served["counts"]["scope_total"], 2)
        self.assertEqual(served["counts"]["remaining"], 1)

    def test_node_id_and_force_pass_through_identically(self) -> None:
        cli = _cli_plan(self.root, "--node", "entity:Cart", "--force")
        served = mcp_server.weld_enrich(
            agent_direct=True, node_id="entity:Cart", force=True,
            root=str(self.root),
        )

        self.assertEqual(cli, served)
        self.assertEqual([e["id"] for e in served["pending"]], ["entity:Cart"])

    def test_a_legacy_node_id_resolves_identically_on_both_surfaces(self) -> None:
        # ADR 0041: an id pasted from an older transcript still names its
        # node. Resolution happens in the selection oracle both surfaces
        # read, so neither can be the one surface that has it -- which is
        # what this drifted into before (MCP resolved at its own boundary,
        # the CLI did not).
        cli = _cli_plan(self.root, "--node", "file:main")
        served = mcp_server.weld_enrich(
            agent_direct=True, node_id="file:main", root=str(self.root),
        )

        self.assertEqual(cli, served)
        self.assertEqual([e["id"] for e in served["pending"]], ["file:app/main"])


class AgentDirectIsReadOnlyTest(unittest.TestCase):
    """The mode writes nothing, locks nothing, and resolves no provider."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        write_graph(self.root, nodes())
        self.graph_path = self.root / ".weld" / "graph.json"

    def test_takes_no_write_lock_and_leaves_the_graph_untouched(self) -> None:
        before = self.graph_path.read_bytes()

        with mock.patch(
            "weld._graph_write_lock.graph_write_lock",
            side_effect=AssertionError("agent-direct must not take the lock"),
        ):
            served = mcp_server.weld_enrich(
                agent_direct=True, root=str(self.root),
            )

        self.assertNotIn("error", served)
        self.assertEqual(self.graph_path.read_bytes(), before)

    def test_resolves_no_provider_even_with_one_in_the_environment(self) -> None:
        # resolve_provider would raise for an unconfigured extra; the mode
        # must never reach it, env fallback or not.
        with mock.patch.dict(
            "os.environ", {"WELD_ENRICH_PROVIDER": "anthropic"}, clear=False,
        ), mock.patch(
            "weld.providers.resolve_provider",
            side_effect=AssertionError("provider must not be resolved"),
        ):
            served = mcp_server.weld_enrich(
                agent_direct=True, root=str(self.root),
            )

        self.assertEqual(served["mode"], "agent-direct")


class ArgumentRefusalTest(unittest.TestCase):
    """Incoherent combinations are refused, never silently reinterpreted."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        write_graph(self.root, nodes())

    def _enrich(self, **kwargs) -> dict:
        return mcp_server.weld_enrich(root=str(self.root), **kwargs)

    def test_provider_side_arguments_are_refused_with_agent_direct(self) -> None:
        # A dropped provider is the dangerous failure: the caller would read
        # a plan as evidence that an unattended provider run had happened.
        for kwargs in (
            {"provider": "anthropic"},
            {"model": "claude"},
            {"max_tokens": 10},
            {"max_cost": 1.0},
        ):
            with self.subTest(**kwargs):
                served = self._enrich(agent_direct=True, **kwargs)
                self.assertIn("error", served)
                self.assertIn("agent-direct", served["error"])

    def test_plan_shaping_arguments_are_refused_without_agent_direct(self) -> None:
        for kwargs in ({"node_type": "entity"}, {"limit": 1}):
            with self.subTest(**kwargs):
                served = self._enrich(**kwargs)
                self.assertIn("error", served)
                self.assertIn("agent-direct", served["error"])

    def test_unknown_node_type_names_the_valid_types(self) -> None:
        # The CLI's argparse ``choices`` refuses a typo with the valid list
        # rather than rendering an empty plan; the MCP surface owes the same
        # answer, since a client need not honour the schema's enum.
        served = self._enrich(agent_direct=True, node_type="entty")

        self.assertIn("error", served)
        self.assertIn("entity", served["error"])

    def test_negative_limit_is_refused(self) -> None:
        # A negative slice would drop nodes off the *end* of the plan while
        # the counts still claimed them -- worse than an error.
        served = self._enrich(agent_direct=True, limit=-1)

        self.assertIn("error", served)

    def test_unknown_node_id_reports_the_miss(self) -> None:
        served = self._enrich(agent_direct=True, node_id="entity:Nope")

        self.assertIn("error", served)
        self.assertIn("entity:Nope", served["error"])


class SchemaAndRootBoundTest(unittest.TestCase):
    """The advertised schema matches the handler, and writes stay rootless."""

    @staticmethod
    def _enrich_schema() -> dict:
        for tool in mcp_server.build_tools():
            if tool.name == "weld_enrich":
                return tool.input_schema
        raise AssertionError("weld_enrich is not registered")

    def test_schema_advertises_the_agent_direct_arguments(self) -> None:
        properties = self._enrich_schema()["properties"]

        for name in ("agent_direct", "node_type", "limit"):
            self.assertIn(name, properties)
        self.assertEqual(properties["agent_direct"]["type"], "boolean")
        # Everything stays optional: the provider-backed call shape is
        # unchanged for existing clients.
        self.assertEqual(self._enrich_schema().get("required", []), [])

    def test_schema_still_declines_a_request_root(self) -> None:
        schema = self._enrich_schema()

        self.assertNotIn("root", schema["properties"])
        self.assertFalse(schema["additionalProperties"])

    def test_agent_direct_is_not_a_back_door_for_root(self) -> None:
        # weld_enrich is a mutating tool and stays in ROOTLESS_TOOLS; the
        # refusal is made at dispatch, before the mode is even inspected.
        from weld._mcp_tools import ROOTLESS_TOOLS

        self.assertIn("weld_enrich", ROOTLESS_TOOLS)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_graph(root, nodes())
            served = mcp_server.dispatch(
                "weld_enrich",
                {"agent_direct": True, "root": str(root)},
                root=str(root),
            )

        self.assertEqual(served.get("error_code"), "root_out_of_bounds")


if __name__ == "__main__":
    unittest.main()
