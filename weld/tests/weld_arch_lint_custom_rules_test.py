"""Tests for custom ``wd lint`` rules loaded from ``.weld/lint-rules.yaml``."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import SCHEMA_VERSION  # noqa: E402


def _write_graph(root: Path, nodes: dict, edges: list) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "graph.json").write_text(
        json.dumps(
            {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "git_sha": "deadbeef",
                    "updated_at": "2026-04-16T00:00:00+00:00",
                },
                "nodes": nodes,
                "edges": edges,
            }
        ),
        encoding="utf-8",
    )


def _write_rules(root: Path, text: str) -> None:
    (root / ".weld" / "lint-rules.yaml").write_text(
        textwrap.dedent(text).lstrip(),
        encoding="utf-8",
    )


class CustomLintRulesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _graph(self):
        from weld.graph import Graph

        graph = Graph(self.root)
        graph.load()
        return graph

    def test_custom_deny_rule_reports_matching_edge(self) -> None:
        from weld.arch_lint import lint

        _write_graph(
            self.root,
            {
                "file:api/routes.py": _node("api/routes.py"),
                "file:internal/db.py": _node("internal/db.py"),
            },
            [_edge("file:api/routes.py", "file:internal/db.py")],
        )
        _write_rules(
            self.root,
            """
            rules:
              - name: no-api-to-internal
                deny:
                  from: { type: file, path_match: 'api/**' }
                  to: { type: file, path_match: 'internal/**' }
            """,
        )

        result = lint(self._graph(), rule_ids=["no-api-to-internal"])

        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["rules_run"], ["no-api-to-internal"])
        self.assertEqual(result["violation_count"], 1)
        violation = result["violations"][0]
        self.assertEqual(violation["rule"], "no-api-to-internal")
        self.assertEqual(violation["node_id"], "file:api/routes.py")

    def test_allow_matcher_suppresses_matching_deny(self) -> None:
        from weld.arch_lint import lint

        _write_graph(
            self.root,
            {
                "file:api/public.py": _node("api/public.py"),
                "file:internal/contracts.py": _node("internal/contracts.py"),
            },
            [_edge("file:api/public.py", "file:internal/contracts.py")],
        )
        _write_rules(
            self.root,
            """
            rules:
              - name: no-api-to-internal
                deny:
                  from: { type: file, path_match: 'api/**' }
                  to: { type: file, path_match: 'internal/**' }
                allow:
                  from: { type: file, path_match: 'api/public.py' }
                  to: { type: file, path_match: 'internal/contracts.py' }
            """,
        )

        result = lint(self._graph(), rule_ids=["no-api-to-internal"])

        self.assertEqual(result["violation_count"], 0)

    def test_cli_rule_filter_can_select_custom_rule(self) -> None:
        from weld.arch_lint import main

        _write_graph(
            self.root,
            {
                "file:api/routes.py": _node("api/routes.py"),
                "file:internal/db.py": _node("internal/db.py"),
            },
            [_edge("file:api/routes.py", "file:internal/db.py")],
        )
        _write_rules(
            self.root,
            """
            rules:
              - name: no-api-to-internal
                deny:
                  from: { type: file, path_match: 'api/**' }
                  to: { type: file, path_match: 'internal/**' }
            """,
        )

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(
                ["--root", str(self.root), "--rule", "no-api-to-internal", "--json"]
            )

        self.assertEqual(code, 1)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["rules_run"], ["no-api-to-internal"])
        self.assertEqual(payload["violation_count"], 1)


def _node(path: str) -> dict:
    return {"type": "file", "label": path, "props": {"file": path}}


def _edge(from_id: str, to_id: str) -> dict:
    return {"from": from_id, "to": to_id, "type": "imports", "props": {}}


if __name__ == "__main__":
    unittest.main()
