"""``wd init --refresh`` wires what ``wd init --force`` wires (field eval N7).

Split from ``weld_init_refresh_test`` -- which pins the *merge* semantics
(hand edits preserved, stamp bumped, never creates) -- because this file asks a
different question of the same command: not "was the user's file respected" but
"is what got wired the whole stack".

It reproduced as a silent under-wiring. Refresh carried its own reduced table:
the tree-sitter entry for a language and its test-peer companion, and nothing
else. A full init routes the same language through its whole stack, so on a
.NET repo ``--refresh`` wired three strategies where ``--force`` wires ten --
and it *cleared* the unclaimed-source warning while doing so, leaving a
maintainer at a clean ``wd doctor`` with no way to learn a further tier of
their codebase was still invisible.

So the cases below never pin a strategy list as the expected answer -- a list
goes stale the moment a stack gains an entry, which is exactly how the two
tables drifted apart. They run both commands over the same tree from the same
starting config and compare the two outputs. The one list that *is* spelled out
(the C# tier) is asserted as set equality rather than containment, because the
finding was about a subset that looked like an answer.
"""

from __future__ import annotations

import io
import re
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from weld import init as init_mod
from weld._init_refresh import refresh
from weld._yaml import parse_yaml


def _wired_strategies(text: str) -> set[str]:
    """Strategy names of every *uncommented* source entry in ``text``."""
    data = parse_yaml(text)
    sources = data.get("sources", []) if isinstance(data, dict) else []
    return {
        s.get("strategy")
        for s in sources
        if isinstance(s, dict) and s.get("strategy")
    }


def _wired_languages(text: str) -> set[str]:
    """``language:`` values of every *uncommented* source entry in ``text``.

    Strategy names alone cannot tell a TypeScript-only refresh from a
    TypeScript-and-JavaScript one: both wire ``tree_sitter``. The language
    field is what separates them, and it is the field the JavaScript half of
    ADR 0142 D1 turns on.
    """
    data = parse_yaml(text)
    sources = data.get("sources", []) if isinstance(data, dict) else []
    return {
        s.get("language")
        for s in sources
        if isinstance(s, dict) and s.get("language")
    }


def _write(root: Path, config: str) -> Path:
    """Create ``root/.weld/discover.yaml`` with ``config`` and return its path."""
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    out = root / ".weld" / "discover.yaml"
    out.write_text(config, encoding="utf-8")
    return out


#: A .NET service laid out the way the field-eval gateway is: a solution, a web
#: project that references EF Core, an xUnit test project, controllers, and a
#: ``doc/`` directory. Every C# strategy ``wd init`` knows fires on it, which is
#: the point -- the finding was that ``--refresh`` wired 3 of the 10.
_DOTNET_SERVICE: dict[str, str] = {
    "OrderGateway.sln": "Microsoft Visual Studio Solution File\n",
    "src/Api/Api.csproj": (
        '<Project Sdk="Microsoft.NET.Sdk.Web">\n'
        "  <ItemGroup>\n"
        '    <PackageReference Include="Microsoft.EntityFrameworkCore" '
        'Version="8.0.0" />\n'
        "  </ItemGroup>\n"
        "</Project>\n"
    ),
    "tests/Api.Tests/Api.Tests.csproj": (
        '<Project Sdk="Microsoft.NET.Sdk">\n'
        "  <ItemGroup>\n"
        '    <PackageReference Include="xunit" Version="2.9.0" />\n'
        "  </ItemGroup>\n"
        "</Project>\n"
    ),
    "src/Api/Controllers/OrderController.cs": "namespace Api.Controllers;\n",
    "src/Api/Controllers/HealthController.cs": "namespace Api.Controllers;\n",
    "src/Api/Program.cs": "namespace Api;\n",
    "src/Api/OrderContext.cs": "namespace Api;\n",
    "src/Api/OrderEntity.cs": "namespace Api;\n",
    "src/Api/Replayer.cs": "namespace Api;\n",
    "src/Api/ReplayOptions.cs": "namespace Api;\n",
    "tests/Api.Tests/OrderTests.cs": "namespace Api.Tests;\n",
    "doc/order-gateway.md": "# Order Gateway\n",
}

#: A Node service in both dialects, importing express: the third shape, added
#: with ADR 0142 D1. Its own case because it is the only stack whose *language
#: detection* was incomplete rather than its entry table -- ``javascript`` was
#: absent from :data:`weld._unclaimed_sources._CLAIMING_STRATEGIES`, so a repo
#: whose ``.js`` files nothing wired was not merely unreported but
#: unrefreshable: refresh wires exactly the languages that detector returns.
_NODE_SERVICE: dict[str, str] = {
    "package.json": '{"name": "api", "private": true}\n',
    "src/server.ts": (
        'import express from "express";\n\nexport const app = express();\n'
    ),
    "src/legacy.js": 'const express = require("express");\n',
    "src/server.test.ts": "export const cases = [];\n",
    "doc/api.md": "# api\n",
}

#: A Go module importing gin: covers the other half of the reduced table --
#: ``gin`` (framework) and ``go_package`` (ADR 0132) were unreachable too.
_GO_SERVICE: dict[str, str] = {
    "go.mod": "module example.com/svc\n",
    "main.go": 'package main\n\nimport (\n\t"github.com/gin-gonic/gin"\n)\n',
    "handler_test.go": "package main\n",
    "doc/svc.md": "# svc\n",
}

#: The team's hand-maintained config: a deliberately narrowed docs glob and a
#: custom entry nothing auto-detects. It wires no strategy that claims C# or
#: Go, so both languages read as unclaimed.
_HAND_EDITED = """\
# Hand-maintained config. Do not clobber.
sources:
  # Custom: deliberately narrowed by the team.
  - glob: "doc/*.md"
    type: doc
    strategy: markdown
    id_prefix: doc:doc

  # Custom entry nothing auto-detects:
  - files: ["OrderGateway.sln.DotSettings"]
    type: config
    strategy: config_file
"""


def _lay_out(root: Path, files: dict[str, str]) -> None:
    """Materialise ``{relative path: body}`` under ``root``."""
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


class ForceParityTest(unittest.TestCase):
    """``--refresh`` wires what ``--force`` wires (field eval v0.24.0 N7)."""

    def _refreshed_and_forced(
        self, root: Path, files: dict[str, str],
    ) -> tuple[str, str]:
        """Run both commands over the same tree and the same starting config.

        The hand-edited config is laid down twice -- once per command -- because
        ``--force`` overwrites it, and the comparison is only honest if both
        started from the state a real maintainer is in.
        """
        _lay_out(root, files)
        out = _write(root, _HAND_EDITED)
        result = refresh(root, out)
        assert result is not None
        out.write_text(_HAND_EDITED, encoding="utf-8")
        with redirect_stderr(io.StringIO()):
            self.assertTrue(init_mod.init(root, out, force=True))
        return result.new_text, out.read_text(encoding="utf-8")

    def _assert_parity(self, files: dict[str, str], language: str) -> set[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            refreshed_text, forced_text = self._refreshed_and_forced(root, files)
        refreshed = _wired_strategies(refreshed_text)
        forced = _wired_strategies(forced_text)
        self.assertTrue(forced, "--force wired no strategies at all")
        self.assertEqual(
            sorted(forced - refreshed), [],
            f"--refresh clears the {language} unclaimed-source warning while "
            f"wiring less than --force: refresh={sorted(refreshed)} "
            f"force={sorted(forced)}",
        )
        return refreshed

    def test_csharp_refresh_wires_every_strategy_force_wires(self) -> None:
        refreshed = self._assert_parity(_DOTNET_SERVICE, "C#")
        # Set equality on the C# tier itself, not just containment: the finding
        # was 3 strategies where a full init wires the whole stack.
        csharp = {
            s for s in refreshed
            if s == "tree_sitter" or s.startswith("csharp_")
        }
        self.assertEqual(csharp, {
            "tree_sitter", "csharp_solution", "csharp_project",
            "csharp_msbuild_targets", "csharp_test_framework",
            "csharp_aspnet_routes", "csharp_efcore", "csharp_package",
        })

    def test_go_refresh_wires_every_strategy_force_wires(self) -> None:
        refreshed = self._assert_parity(_GO_SERVICE, "Go")
        self.assertIn("go_package", refreshed)  # ADR 0132
        self.assertIn("gin", refreshed)  # ADR 0071
        self.assertIn("test_peer", refreshed)  # ADR 0046

    def test_node_refresh_wires_every_strategy_force_wires(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            refreshed_text, forced_text = self._refreshed_and_forced(
                Path(tmp), _NODE_SERVICE)
        refreshed = _wired_strategies(refreshed_text)
        forced = _wired_strategies(forced_text)
        self.assertTrue(forced, "--force wired no strategies at all")
        self.assertEqual(
            sorted(forced - refreshed), [],
            f"--refresh wires less than --force on a Node repo: "
            f"refresh={sorted(refreshed)} force={sorted(forced)}",
        )
        self.assertIn("express", refreshed)  # ADR 0071 / ADR 0142 D1
        self.assertIn("test_peer", refreshed)  # ADR 0046
        # Both dialect families, not just the one that happens to carry the
        # tree_sitter name: a refresh that saw only TypeScript would satisfy
        # every strategy-level assertion above and still leave every .js file
        # invisible, which is the JavaScript half of gap G1.
        self.assertEqual(
            _wired_languages(refreshed_text), _wired_languages(forced_text))
        self.assertIn("javascript", _wired_languages(refreshed_text))

    def test_hand_edits_survive_the_wider_wiring(self) -> None:
        """Parity must not have been bought by regenerating the file."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _lay_out(root, _DOTNET_SERVICE)
            out = _write(root, _HAND_EDITED)
            result = refresh(root, out)
            assert result is not None
        self.assertTrue(
            result.new_text.startswith("# Hand-maintained config. Do not clobber."))
        self.assertIn("# Custom: deliberately narrowed by the team.", result.new_text)
        self.assertIn("id_prefix: doc:doc", result.new_text)
        self.assertIn('- files: ["OrderGateway.sln.DotSettings"]', result.new_text)
        self.assertIsInstance(parse_yaml(result.new_text), dict)

    def test_an_already_wired_entry_is_not_appended_twice(self) -> None:
        """The artifact-keyed stacks are offered, never duplicated.

        gRPC / events / ROS2 entries are keyed on a ``.proto`` tree or a
        ``package.xml``, not on a source language, so unlike an unclaimed
        language's own entries they *can* already be in the config. The one the
        user wired stays as they wrote it; only the missing half is added.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _lay_out(root, {
                "proto/order.proto": 'syntax = "proto3";\npackage acme;\n',
                "app.py": "x = 1\n",
                "src/Api/Program.cs": "namespace Api;\n",
            })
            out = _write(root, (
                "sources:\n"
                '  - glob: "**/*.py"\n'
                "    type: file\n"
                "    strategy: python_module\n"
                '  - glob: "**/*.proto"\n'
                "    type: rpc\n"
                "    strategy: grpc_proto\n"
            ))
            result = refresh(root, out)
            assert result is not None
        self.assertEqual([w.language for w in result.wired], ["csharp"])
        self.assertEqual(
            len(re.findall(r"strategy: grpc_proto\b", result.new_text)), 1,
            result.new_text,
        )
        # ... while the half the config did *not* wire is offered.
        strategies = _wired_strategies(result.new_text)
        self.assertIn("grpc_bindings", strategies)
        self.assertIn("csharp_package", strategies)


if __name__ == "__main__":
    unittest.main()
