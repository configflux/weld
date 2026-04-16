"""Tests for the external_json adapter strategy in weld.discover."""
from __future__ import annotations

import json
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import validate_fragment  # noqa: E402
from weld.discover import _run_external_json  # noqa: E402

def _write_script(tmpdir: Path, name: str, output: dict) -> str:
    """Write a helper script that emits JSON to stdout."""
    script = tmpdir / name
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, sys
        json.dump({json.dumps(output)}, sys.stdout)
    """), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)

def _write_failing_script(tmpdir: Path, name: str, msg: str = "boom") -> str:
    """Write a helper script that exits non-zero."""
    script = tmpdir / name
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        print("{msg}", file=sys.stderr)
        sys.exit(1)
    """), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)

def _write_bad_json_script(tmpdir: Path, name: str) -> str:
    """Write a script that emits invalid JSON."""
    script = tmpdir / name
    script.write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        print("not json {{{")
    """), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)

# -- Valid fragment for reuse -----------------------------------------------

_VALID_FRAGMENT = {
    "nodes": {
        "tool:custom-lint": {
            "type": "tool",
            "label": "Custom Lint",
            "props": {
                "source_strategy": "external_json",
                "authority": "external",
            },
        },
    },
    "edges": [],
    "discovered_from": ["tools/custom-lint"],
}

class ExternalJsonHappyPathTest(unittest.TestCase):
    """The adapter runs a command and returns a valid StrategyResult."""

    def test_valid_output_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            cmd = _write_script(tmpdir, "adapter.py", _VALID_FRAGMENT)
            source = {"strategy": "external_json", "command": cmd}
            result = _run_external_json(tmpdir, source)
        self.assertIn("tool:custom-lint", result.nodes)
        self.assertEqual(result.edges, [])
        self.assertEqual(result.discovered_from, ["tools/custom-lint"])

    def test_output_validates_through_fragment_contract(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            cmd = _write_script(tmpdir, "adapter.py", _VALID_FRAGMENT)
            source = {"strategy": "external_json", "command": cmd}
            result = _run_external_json(tmpdir, source)
        fragment = {
            "nodes": result.nodes,
            "edges": result.edges,
            "discovered_from": result.discovered_from,
        }
        errs = validate_fragment(fragment, source_label="adapter:test")
        self.assertEqual(errs, [], f"Validation errors: {errs}")

    def test_command_with_args(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            script = tmpdir / "adapter.py"
            script.write_text(textwrap.dedent("""\
                #!/usr/bin/env python3
                import json, sys
                # Echo args back as discovered_from for test verification
                json.dump({
                    "nodes": {},
                    "edges": [],
                    "discovered_from": sys.argv[1:]
                }, sys.stdout)
            """), encoding="utf-8")
            script.chmod(script.stat().st_mode | stat.S_IEXEC)
            source = {
                "strategy": "external_json",
                "command": f"{script} --flag value",
            }
            result = _run_external_json(tmpdir, source)
        self.assertEqual(result.discovered_from, ["--flag", "value"])

    def test_missing_discovered_from_defaults_empty(self) -> None:
        frag = {"nodes": {}, "edges": []}
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            cmd = _write_script(tmpdir, "adapter.py", frag)
            source = {"strategy": "external_json", "command": cmd}
            result = _run_external_json(tmpdir, source)
        self.assertEqual(result.discovered_from, [])

class ExternalJsonValidationTest(unittest.TestCase):
    """The adapter rejects malformed output before returning."""

    def test_invalid_node_type_rejected(self) -> None:
        bad = {
            "nodes": {"spaceship:x": {
                "type": "spaceship", "label": "X", "props": {},
            }},
            "edges": [],
        }
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            cmd = _write_script(tmpdir, "adapter.py", bad)
            source = {"strategy": "external_json", "command": cmd}
            result = _run_external_json(tmpdir, source)
        # Invalid fragments produce empty results
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])

    def test_invalid_edge_type_rejected(self) -> None:
        bad = {
            "nodes": {
                "service:a": {"type": "service", "label": "A", "props": {}},
                "service:b": {"type": "service", "label": "B", "props": {}},
            },
            "edges": [{"from": "service:a", "to": "service:b",
                        "type": "teleports_to", "props": {}}],
        }
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            cmd = _write_script(tmpdir, "adapter.py", bad)
            source = {"strategy": "external_json", "command": cmd}
            result = _run_external_json(tmpdir, source)
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])

class ExternalJsonErrorHandlingTest(unittest.TestCase):
    """Adapter failures are handled gracefully — no crash, empty result."""

    def test_command_exit_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            cmd = _write_failing_script(tmpdir, "bad.py")
            source = {"strategy": "external_json", "command": cmd}
            result = _run_external_json(tmpdir, source)
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])

    def test_command_emits_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            cmd = _write_bad_json_script(tmpdir, "bad.py")
            source = {"strategy": "external_json", "command": cmd}
            result = _run_external_json(tmpdir, source)
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])

    def test_missing_command_key(self) -> None:
        source = {"strategy": "external_json"}
        result = _run_external_json(Path("/tmp"), source)
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])

    def test_nonexistent_command(self) -> None:
        source = {"strategy": "external_json",
                  "command": "/nonexistent/path/to/cmd"}
        result = _run_external_json(Path("/tmp"), source)
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])

class ExternalJsonTimeoutTest(unittest.TestCase):
    """The adapter enforces a timeout on external commands."""

    def test_timeout_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            script = tmpdir / "slow.py"
            script.write_text(textwrap.dedent("""\
                #!/usr/bin/env python3
                import time
                time.sleep(60)
            """), encoding="utf-8")
            script.chmod(script.stat().st_mode | stat.S_IEXEC)
            source = {
                "strategy": "external_json",
                "command": str(script),
                "timeout": 1,
            }
            result = _run_external_json(tmpdir, source)
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])

class ExternalJsonCwdTest(unittest.TestCase):
    """The adapter runs commands with cwd set to the project root."""

    def test_cwd_is_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            script = tmpdir / "cwd.py"
            script.write_text(textwrap.dedent("""\
                #!/usr/bin/env python3
                import json, os, sys
                json.dump({
                    "nodes": {},
                    "edges": [],
                    "discovered_from": [os.getcwd()]
                }, sys.stdout)
            """), encoding="utf-8")
            script.chmod(script.stat().st_mode | stat.S_IEXEC)
            source = {"strategy": "external_json", "command": str(script)}
            result = _run_external_json(tmpdir, source)
        self.assertEqual(result.discovered_from, [str(tmpdir)])

class ExternalJsonIntegrationTest(unittest.TestCase):
    """End-to-end: adapter output merges into a graph via discover()."""

    def test_discover_dispatches_external_json(self) -> None:
        from weld.discover import discover

        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            # Write adapter script
            cmd = _write_script(tmpdir, "my_adapter.py", _VALID_FRAGMENT)
            # Write discover.yaml
            weld_dir = tmpdir / ".weld"
            weld_dir.mkdir()
            (weld_dir / "discover.yaml").write_text(
                f"sources:\n"
                f"  - strategy: external_json\n"
                f'    command: "{cmd}"\n'
                f"topology:\n"
                f"  nodes: []\n"
                f"  edges: []\n",
                encoding="utf-8",
            )
            result = discover(tmpdir)
        self.assertIn("tool:custom-lint", result["nodes"])

if __name__ == "__main__":
    unittest.main()
