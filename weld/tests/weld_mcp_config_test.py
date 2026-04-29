"""Tests for ``weld.mcp_config`` and the ``wd mcp config`` CLI surface.

This file is intentionally distinct from ``weld_mcp_smoke_test``,
``weld_mcp_interaction_test``, and ``weld_mcp_server_test`` to avoid
collision with concurrent MCP fixture-consolidation work; those files cover
the running stdio server, while this one covers the per-client config
generator described in ADR 0023.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import cli as cli_mod  # noqa: E402
from weld import mcp_config  # noqa: E402


class RenderTests(unittest.TestCase):
    """Each client must render a JSON snippet that parses and matches its
    documented shape: Claude Code and Cursor use the ``mcpServers`` key,
    VS Code uses ``servers``."""

    def test_claude_render_shape(self) -> None:
        rendered = mcp_config.render("claude")
        parsed = json.loads(rendered)
        self.assertIn("mcpServers", parsed)
        self.assertIn("weld", parsed["mcpServers"])
        entry = parsed["mcpServers"]["weld"]
        self.assertEqual(entry["command"], "python")
        self.assertEqual(entry["args"], ["-m", "weld.mcp_server"])

    def test_cursor_render_shape(self) -> None:
        rendered = mcp_config.render("cursor")
        parsed = json.loads(rendered)
        self.assertIn("mcpServers", parsed)
        self.assertIn("weld", parsed["mcpServers"])

    def test_vscode_render_shape(self) -> None:
        rendered = mcp_config.render("vscode")
        parsed = json.loads(rendered)
        # VS Code's MCP integration uses the ``servers`` key, not ``mcpServers``
        self.assertIn("servers", parsed)
        self.assertIn("weld", parsed["servers"])
        entry = parsed["servers"]["weld"]
        self.assertEqual(entry["command"], "python")
        self.assertEqual(entry["args"], ["-m", "weld.mcp_server"])

    def test_render_unknown_client_raises_with_list(self) -> None:
        with self.assertRaises(mcp_config.UnknownClientError) as cm:
            mcp_config.render("emacs")
        # Diagnostic must list every supported client so users can self-correct
        message = str(cm.exception)
        for name in ("claude", "vscode", "cursor"):
            self.assertIn(name, message)


class TargetPathTests(unittest.TestCase):
    """Per-client output paths follow the conventions documented in
    docs/mcp.md and ADR 0023; pinning here keeps the generator from drifting."""

    def test_target_paths(self) -> None:
        self.assertEqual(mcp_config.target_path("claude"), Path(".mcp.json"))
        self.assertEqual(
            mcp_config.target_path("cursor"), Path(".cursor/mcp.json")
        )
        self.assertEqual(
            mcp_config.target_path("vscode"), Path(".vscode/mcp.json")
        )


class CliPrintTests(unittest.TestCase):
    """The default invocation prints valid JSON to stdout (the behaviour the
    tracked issue and ADR 0023 require). Errors must surface as non-zero exits."""

    def _run(self, *args: str) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_mod.main(["mcp", "config", *args])
        return rc, out.getvalue(), err.getvalue()

    def test_print_claude_config_is_valid_json(self) -> None:
        rc, stdout, _ = self._run("--client=claude")
        self.assertEqual(rc, 0)
        parsed = json.loads(stdout)
        self.assertIn("mcpServers", parsed)

    def test_unknown_client_exits_nonzero(self) -> None:
        rc, _, stderr = self._run("--client=emacs")
        self.assertNotEqual(rc, 0)
        # Diagnostic on stderr must list supported clients
        for name in ("claude", "vscode", "cursor"):
            self.assertIn(name, stderr)

    def test_missing_client_flag_errors(self) -> None:
        # argparse on a missing required flag raises SystemExit; we just want
        # to confirm it's a non-zero exit, not necessarily a clean return.
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                cli_mod.main(["mcp", "config"])
        self.assertNotEqual(cm.exception.code, 0)


class WriteAndMergeTests(unittest.TestCase):
    """File-writing modes must be safe by default: refuse to clobber without
    ``--force``, atomically write a ``.bak`` sibling when replacing a file,
    and never touch disk under ``--dry-run``."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="weld_mcp_config_")
        self.tmpdir = Path(self._tmp)
        self._cwd = os.getcwd()
        os.chdir(self.tmpdir)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        # Best-effort cleanup; tempfiles aren't load-bearing
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_write_creates_file_for_claude(self) -> None:
        result = mcp_config.write_config("claude", root=self.tmpdir)
        self.assertTrue(result.wrote)
        target = self.tmpdir / ".mcp.json"
        self.assertTrue(target.is_file())
        parsed = json.loads(target.read_text(encoding="utf-8"))
        self.assertIn("mcpServers", parsed)

    def test_write_creates_parent_dirs_for_cursor(self) -> None:
        mcp_config.write_config("cursor", root=self.tmpdir)
        self.assertTrue((self.tmpdir / ".cursor" / "mcp.json").is_file())

    def test_refuses_clobber_without_force(self) -> None:
        target = self.tmpdir / ".mcp.json"
        target.write_text('{"mcpServers": {"foreign": {}}}', encoding="utf-8")
        result = mcp_config.write_config("claude", root=self.tmpdir)
        # Differing existing content -> not written and not merged
        self.assertFalse(result.wrote)
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            '{"mcpServers": {"foreign": {}}}',
        )

    def test_force_overwrites_existing(self) -> None:
        target = self.tmpdir / ".mcp.json"
        target.write_text('{"mcpServers": {"foreign": {}}}', encoding="utf-8")
        result = mcp_config.write_config("claude", root=self.tmpdir, force=True)
        self.assertTrue(result.wrote)
        # Force overwrite drops siblings entirely; that's why ``--merge`` exists.
        parsed = json.loads(target.read_text(encoding="utf-8"))
        self.assertNotIn("foreign", parsed["mcpServers"])
        self.assertIn("weld", parsed["mcpServers"])
        # A backup of the previous content must exist
        self.assertTrue((self.tmpdir / ".mcp.json.bak").is_file())

    def test_merge_preserves_siblings(self) -> None:
        target = self.tmpdir / ".mcp.json"
        target.write_text(
            json.dumps(
                {"mcpServers": {"context7": {"command": "npx", "args": ["x"]}}}
            ),
            encoding="utf-8",
        )
        result = mcp_config.write_config("claude", root=self.tmpdir, merge=True)
        self.assertTrue(result.wrote)
        parsed = json.loads(target.read_text(encoding="utf-8"))
        self.assertIn("context7", parsed["mcpServers"])
        self.assertIn("weld", parsed["mcpServers"])

    def test_merge_vscode_uses_servers_key(self) -> None:
        target = self.tmpdir / ".vscode" / "mcp.json"
        target.parent.mkdir(parents=True)
        target.write_text(
            json.dumps({"servers": {"existing": {"command": "x", "args": []}}}),
            encoding="utf-8",
        )
        mcp_config.write_config("vscode", root=self.tmpdir, merge=True)
        parsed = json.loads(target.read_text(encoding="utf-8"))
        self.assertIn("existing", parsed["servers"])
        self.assertIn("weld", parsed["servers"])

    def test_merge_idempotent_when_already_present(self) -> None:
        target = self.tmpdir / ".mcp.json"
        # Pre-write a file that already matches what merge would produce
        mcp_config.write_config("claude", root=self.tmpdir)
        before = target.read_text(encoding="utf-8")
        result = mcp_config.write_config("claude", root=self.tmpdir, merge=True)
        # No diff -> no write, no .bak side-effect
        self.assertFalse(result.wrote)
        self.assertEqual(target.read_text(encoding="utf-8"), before)
        self.assertFalse((self.tmpdir / ".mcp.json.bak").exists())

    def test_dry_run_never_writes(self) -> None:
        # Even when --write is requested, --dry-run must short-circuit before
        # any filesystem mutation, including .bak file creation.
        target = self.tmpdir / ".mcp.json"
        target.write_text('{"mcpServers": {"x": {}}}', encoding="utf-8")
        before = target.read_text(encoding="utf-8")
        result = mcp_config.write_config(
            "claude", root=self.tmpdir, force=True, dry_run=True
        )
        self.assertFalse(result.wrote)
        # File still untouched, no .bak created
        self.assertEqual(target.read_text(encoding="utf-8"), before)
        self.assertFalse((self.tmpdir / ".mcp.json.bak").exists())

    def test_dry_run_does_not_create_parent_dirs(self) -> None:
        # A dry run on a path whose parent directory does not yet exist must
        # not create the parent. Otherwise we'd be partially mutating disk.
        result = mcp_config.write_config(
            "cursor", root=self.tmpdir, dry_run=True
        )
        self.assertFalse(result.wrote)
        self.assertFalse((self.tmpdir / ".cursor").exists())

    def test_merge_malformed_existing_marks_error(self) -> None:
        # Merging into an existing file that is not valid JSON must surface as
        # an error on the WriteResult so callers (including the CLI) can fail
        # loudly instead of silently no-oping.
        target = self.tmpdir / ".mcp.json"
        target.write_text("{not json", encoding="utf-8")
        before = target.read_text(encoding="utf-8")
        result = mcp_config.write_config("claude", root=self.tmpdir, merge=True)
        self.assertFalse(result.wrote)
        self.assertTrue(result.error)
        self.assertIn("not valid JSON", result.reason)
        # File must remain untouched
        self.assertEqual(target.read_text(encoding="utf-8"), before)
        # No .bak side-effect either
        self.assertFalse((self.tmpdir / ".mcp.json.bak").exists())


class CliWriteTests(unittest.TestCase):
    """End-to-end check that the ``--write``/``--dry-run`` flags reach the
    writer and that ``--dry-run`` really doesn't write."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="weld_mcp_config_cli_")
        self.tmpdir = Path(self._tmp)
        self._cwd = os.getcwd()
        os.chdir(self.tmpdir)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run(self, *args: str) -> int:
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            return cli_mod.main(["mcp", "config", *args])

    def test_cli_write_creates_file(self) -> None:
        rc = self._run("--client=claude", "--write")
        self.assertEqual(rc, 0)
        self.assertTrue((self.tmpdir / ".mcp.json").is_file())

    def test_cli_dry_run_skips_write(self) -> None:
        rc = self._run("--client=claude", "--write", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertFalse((self.tmpdir / ".mcp.json").exists())

    def test_cli_merge_malformed_existing_exits_nonzero(self) -> None:
        # Scripted users need a non-zero exit and a clear stderr message when
        # ``wd mcp config --merge`` runs against an unparseable existing file;
        # otherwise the failure is invisible. The file must not be modified.
        target = self.tmpdir / ".mcp.json"
        target.write_text("{not json", encoding="utf-8")
        before = target.read_text(encoding="utf-8")
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_mod.main(["mcp", "config", "--client=claude", "--merge"])
        self.assertNotEqual(rc, 0)
        stderr = err.getvalue()
        # Error must mention the file path and the parse failure so users can
        # find the offending file without grepping.
        self.assertIn(".mcp.json", stderr)
        self.assertIn("not valid JSON", stderr)
        # File untouched
        self.assertEqual(target.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
