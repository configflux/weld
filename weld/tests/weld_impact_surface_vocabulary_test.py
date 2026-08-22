"""What ``affected_surfaces.mcp_tools`` is allowed to mean.

ADR 0106 grew ``tool:`` nodes from 3 to 49 by path-qualifying their ids and
widening discovery to ``tools/**``. ``_surface_bucket`` still mapped node type
``tool`` straight to ``mcp_tools``, so 46 of the 49 -- every repo shell/python
script -- were reported as MCP tools, and ``risk_level`` scored every change to
any ``tools/`` script MEDIUM through a bucket it did not belong in (bd 7rla).

The genuine MCP tools never took that route: ``_derived_mcp_tool`` synthesises
``mcp-tool:weld_<name>`` from ``weld/mcp_helpers.py`` and ``weld/mcp_server.py``
symbols and stamps ``props.derived_from``. That stamp is the discriminator
pinned here, and the tests below say why an id-prefix test would not do.

bd hfmn later gave the derived side its own ``mcp-tool:`` id namespace, so the
two mechanisms can no longer mint the same id. That closes id *addressing*
(``wd context``); bucketing still keys on provenance, for the reason above.
"""

from __future__ import annotations

import unittest

from weld.impact_format import format_human
from weld.impact_surfaces import (
    _collect_surfaces,
    _derived_mcp_tool,
    _empty_surfaces,
    _risk_level,
    _surface_bucket,
)


def _discovered_tool(node_id: str) -> dict:
    """A ``tool:`` node as discovery emits it -- no ``derived_from``."""
    return {
        "id": node_id,
        "type": "tool",
        "label": node_id.removeprefix("tool:"),
        "props": {"file": f"{node_id.removeprefix('tool:')}.py"},
        "hop": 1,
    }


class SurfaceBucketTest(unittest.TestCase):
    def test_derived_mcp_tool_buckets_to_mcp_tools(self) -> None:
        derived = _derived_mcp_tool(
            {
                "id": "symbol:py:weld.mcp_helpers:weld_trace",
                "props": {"file": "weld/mcp_helpers.py", "qualname": "weld_trace"},
            }
        )
        self.assertIsNotNone(derived)
        self.assertEqual(_surface_bucket(derived), "mcp_tools")

    def test_discovered_script_buckets_to_repo_tools(self) -> None:
        self.assertEqual(
            _surface_bucket(_discovered_tool("tool:tools/publish")), "repo_tools"
        )

    def test_root_level_script_named_like_an_mcp_tool_is_still_a_repo_tool(
        self,
    ) -> None:
        """Why provenance, and not an id prefix, is the discriminator.

        ADR 0106 leaves a root-level script's id as a bare stem, so a script
        called ``weld_trace.sh`` at the repo root mints ``tool:weld_trace``.
        bd hfmn moved the derived side to its own namespace so the two ids no
        longer collide, but bucketing still keys on the ``derived_from`` stamp
        rather than on a spelling.
        """
        self.assertEqual(
            _surface_bucket(_discovered_tool("tool:weld_trace")), "repo_tools"
        )

    def test_derived_and_discovered_tool_ids_cannot_collide(self) -> None:
        """bd hfmn: the two minting mechanisms no longer share a namespace.

        Before this, a root-level ``weld_trace.sh`` minted ``tool:weld_trace``
        character-for-character the id the derived MCP tool already used. The
        buckets coped (they key on provenance), but anything addressing a node
        BY id -- ``wd context tool:weld_trace``, ``wd references ...`` -- could
        not say which of the two was meant.
        """
        derived = _derived_mcp_tool(
            {
                "id": "symbol:py:weld.mcp_helpers:weld_trace",
                "props": {"file": "weld/mcp_helpers.py", "qualname": "weld_trace"},
            }
        )
        discovered = _discovered_tool("tool:weld_trace")
        self.assertEqual(derived["id"], "mcp-tool:weld_trace")
        self.assertNotEqual(derived["id"], discovered["id"])
        self.assertEqual(_surface_bucket(derived), "mcp_tools")
        self.assertEqual(_surface_bucket(discovered), "repo_tools")

    def test_other_node_types_are_unchanged(self) -> None:
        self.assertEqual(_surface_bucket({"type": "command"}), "cli_commands")
        self.assertEqual(_surface_bucket({"type": "route"}), "api_endpoints")
        self.assertEqual(_surface_bucket({"type": "entrypoint"}), "entrypoints")
        self.assertEqual(_surface_bucket({"type": "boundary"}), "boundaries")
        self.assertIsNone(_surface_bucket({"type": "symbol"}))


class CollectSurfacesTest(unittest.TestCase):
    def test_the_two_kinds_of_tool_land_in_different_buckets(self) -> None:
        surfaces = _collect_surfaces(
            [
                _discovered_tool("tool:tools/publish"),
                _discovered_tool("tool:tools/release_smoke"),
                {
                    "id": "symbol:py:weld.mcp_server:weld_impact",
                    "type": "symbol",
                    "label": "weld_impact",
                    "props": {
                        "file": "weld/mcp_server.py",
                        "qualname": "weld_impact",
                    },
                    "hop": 1,
                },
            ]
        )
        self.assertEqual(
            [node["id"] for node in surfaces["mcp_tools"]], ["mcp-tool:weld_impact"]
        )
        self.assertEqual(
            [node["id"] for node in surfaces["repo_tools"]],
            ["tool:tools/publish", "tool:tools/release_smoke"],
        )

    def test_repo_tools_is_deduplicated_like_every_other_bucket(self) -> None:
        node = _discovered_tool("tool:tools/publish")
        surfaces = _collect_surfaces([node, dict(node)])
        self.assertEqual(len(surfaces["repo_tools"]), 1)

    def test_empty_surfaces_carries_the_new_bucket(self) -> None:
        self.assertEqual(
            sorted(_empty_surfaces()),
            [
                "api_endpoints",
                "boundaries",
                "cli_commands",
                "entrypoints",
                "mcp_tools",
                "repo_tools",
                "tests",
            ],
        )


class RiskLevelTest(unittest.TestCase):
    def test_repo_tools_alone_does_not_earn_medium(self) -> None:
        """The reported harm: an unearned MEDIUM on every ``tools/`` script.

        Repo tooling is internal development infrastructure, not a published
        contract, so it does not raise risk on its own.
        """
        surfaces = _empty_surfaces()
        surfaces["repo_tools"] = [_discovered_tool("tool:tools/publish")]
        self.assertEqual(_risk_level(surfaces), "LOW")

    def test_a_real_mcp_tool_still_earns_medium(self) -> None:
        surfaces = _empty_surfaces()
        surfaces["mcp_tools"] = [
            {"id": "tool:weld_trace", "props": {"derived_from": "symbol:x"}}
        ]
        self.assertEqual(_risk_level(surfaces), "MEDIUM")


class FormatHumanTest(unittest.TestCase):
    def _render(self, surfaces: dict) -> str:
        return format_human(
            {
                "target": {"input": "tools/publish.py", "resolved_nodes": ["a"]},
                "risk_level": _risk_level(surfaces),
                "direct_dependents": [],
                "transitive_dependents": [],
                "affected_surfaces": surfaces,
                "warnings": {},
            }
        )

    def test_repo_tools_is_reported_on_its_own_line(self) -> None:
        surfaces = _empty_surfaces()
        surfaces["repo_tools"] = [_discovered_tool("tool:tools/publish")]
        rendered = self._render(surfaces)
        self.assertIn("- Repo tools: 1", rendered)
        self.assertIn("- MCP tools: 0", rendered)
        self.assertIn("Risk: LOW", rendered)

    def test_a_pre_existing_envelope_without_the_bucket_still_renders(self) -> None:
        """``format_human`` is fed envelopes from older callers and fixtures."""
        surfaces = _empty_surfaces()
        del surfaces["repo_tools"]
        surfaces["cli_commands"] = [{"id": "command:wd query"}]
        self.assertIn("- Repo tools: 0", self._render(surfaces))


if __name__ == "__main__":
    unittest.main()
