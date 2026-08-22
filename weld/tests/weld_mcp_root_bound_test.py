"""What a per-request MCP ``root`` may *not* do (ADR 0096 §4).

A request root is untrusted input reaching a long-lived process, so this
suite is written as a bound rather than as a feature: everything that is not
an existing directory of the server's own repository is refused, the refusal
says nothing a caller could learn from, mutating tools decline the argument
in the dispatcher and not merely in a schema a client may skip validating,
and none of it takes the transport down.

The companion suite (:mod:`weld_mcp_request_root_test`) covers what the
argument does when it is accepted. The checkouts both use are built in
:mod:`_request_root_fixture`.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from weld import mcp_server
from weld._errors import ROOT_OUT_OF_BOUNDS
from weld.tests._request_root_fixture import (
    ROOT_TOOLS,
    ROOTLESS_TOOLS,
    FrozenRefreshFixture,
)


class RootBoundTests(FrozenRefreshFixture):
    """Same-repository checkouts only, and no filesystem oracle."""

    def _rejections(self) -> dict[str, Path]:
        """Every shape of root the server must refuse.

        A ``git clone`` of the very repository being served leads the list on
        purpose: its files are identical, so accepting it would mean the
        bound is content or convenience rather than repository identity. The
        server may answer from its own checkouts, and from nothing else.
        """
        return {
            "a clone of the same project": self.clone(),
            "a directory in no repository": self._plain_dir(),
            "a path that does not exist": self.tmp / "nowhere",
            "a regular file": self.origin / "alpha.py",
        }

    def _plain_dir(self) -> Path:
        plain = self.tmp / "plain"
        plain.mkdir()
        return plain

    def _query(self, candidate: Path) -> dict:
        return mcp_server.dispatch(
            "weld_query", {"term": "alpha", "root": str(candidate)},
            root=str(self.origin),
        )

    def test_out_of_bounds_roots_are_refused(self) -> None:
        for label, candidate in self._rejections().items():
            with self.subTest(root=label):
                served = self._query(candidate)

                self.assertEqual(served.get("error_code"), ROOT_OUT_OF_BOUNDS)
                self.assertIn("hint", served)
                self.assertNotIn(
                    "matches", served, "a refused root must not answer at all",
                )

    def test_refusal_is_identical_for_every_reason(self) -> None:
        """Distinguishing "no such directory" from "outside the repository"
        would answer filesystem-existence questions for whoever drives the
        server, so every rejection returns the same bytes."""
        rendered = {
            json.dumps(self._query(candidate), sort_keys=True)
            for candidate in self._rejections().values()
        }

        self.assertEqual(len(rendered), 1)

    def test_refusal_never_echoes_the_requested_path(self) -> None:
        secret = self.tmp / "not-a-real-project-Sekr3t"

        served = json.dumps(self._query(secret))

        self.assertNotIn("Sekr3t", served)
        self.assertNotIn(str(secret), served)

    def test_transport_survives_a_refused_root(self) -> None:
        """The stdio seam must hand back a parseable payload, not an
        exception that would tear down a long-lived session."""
        text = mcp_server.dispatch_to_text_payload(
            "weld_query", {"term": "alpha", "root": str(self.tmp / "nowhere")},
            root=str(self.origin),
        )

        self.assertEqual(json.loads(text).get("error_code"), ROOT_OUT_OF_BOUNDS)

    def test_a_write_cannot_be_redirected_to_another_checkout(self) -> None:
        """An in-bounds root is still refused for the tools that mutate.

        Schemas are enforced by the client, so a caller that does not
        validate could otherwise steer a graph write at a checkout the
        operator never named -- and the worktree here is one the read tools
        would happily accept, which is what makes the refusal meaningful.
        """
        worktree = self.branch_worktree()
        before = (worktree / ".weld" / "graph.json").read_bytes()

        served = mcp_server.dispatch(
            "weld_enrich", {"root": str(worktree)}, root=str(self.origin),
        )

        self.assertEqual(served.get("error_code"), ROOT_OUT_OF_BOUNDS)
        self.assertEqual(
            (worktree / ".weld" / "graph.json").read_bytes(), before,
            "the refused write must not have touched the named checkout",
        )

    def test_a_subdirectory_of_the_server_root_is_in_bounds(self) -> None:
        """The bound is the repository, not the exact directory -- the same
        latitude ``wd --root`` already gives an operator."""
        served = self._query(self.origin / ".weld")

        self.assertNotEqual(served.get("error_code"), ROOT_OUT_OF_BOUNDS)


class ToolSurfaceTests(unittest.TestCase):
    """What the schemas advertise -- checked without touching a repository."""

    def setUp(self) -> None:
        self.by_name = {t.name: t for t in mcp_server.build_tools()}

    def test_read_tools_accept_an_optional_root_string(self) -> None:
        for name in sorted(ROOT_TOOLS):
            with self.subTest(tool=name):
                schema = self.by_name[name].input_schema
                self.assertEqual(
                    schema["properties"].get("root", {}).get("type"), "string",
                )
                self.assertNotIn(
                    "root", schema.get("required", []),
                    "root is optional: omitting it must keep working",
                )

    def test_mutating_tools_do_not_accept_a_root(self) -> None:
        for name in sorted(ROOTLESS_TOOLS):
            with self.subTest(tool=name):
                schema = self.by_name[name].input_schema
                self.assertNotIn("root", schema["properties"])

    def test_the_enforced_set_matches_the_advertised_schemas(self) -> None:
        """Dispatch refuses a root by consulting a constant, so that constant
        has to stay equal to the set of schemas that decline the property --
        otherwise a future tool is advertised one way and enforced another."""
        from weld._mcp_tools import ROOTLESS_TOOLS as ENFORCED

        advertised = {
            name for name, tool in self.by_name.items()
            if "root" not in tool.input_schema["properties"]
        }
        self.assertEqual(ENFORCED, advertised)
        self.assertEqual(ENFORCED, ROOTLESS_TOOLS)

    def test_every_schema_still_refuses_unknown_properties(self) -> None:
        """``additionalProperties: False`` is what makes the exclusion above
        an actual bound rather than a documentation preference."""
        for name, tool in sorted(self.by_name.items()):
            with self.subTest(tool=name):
                self.assertIs(tool.input_schema.get("additionalProperties"), False)

    def test_root_property_documents_the_freshness_branch_signal(self) -> None:
        """bd thau: the schema, not just the response, names the checkout tell.

        ``freshness.branch`` (ADR 0096 §3) has always been in the response
        data; nothing a client reads before calling -- the schema -- pointed
        at it, so a wrong-root answer was visible only to a caller who
        already knew to look. Every root-accepting tool advertises the same
        text because they share one ``_ROOT_PROPERTY`` object; a per-tool
        divergence here would mean a future edit forked it instead of
        editing the shared source in ``weld._mcp_tool_props``.
        """
        for name in sorted(ROOT_TOOLS):
            with self.subTest(tool=name):
                description = self.by_name[name].input_schema["properties"]["root"][
                    "description"
                ]
                self.assertIn("freshness.branch", description)


if __name__ == "__main__":
    unittest.main()
