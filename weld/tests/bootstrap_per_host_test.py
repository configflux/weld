"""Tests for per-host bootstrap expansion (ADR 0054).

`wd bootstrap` adds four new hosts beyond claude/codex/copilot:

* ``cursor`` -> ``.cursor/rules/weld.mdc`` + ``.cursor/mcp.json``
* ``aider`` -> ``.aider.conf.yml`` + ``CONVENTIONS.md`` (no MCP; wiki fallback default)
* ``gemini-cli`` -> ``.gemini/skills/weld.md`` + ``.gemini/mcp.json``
* ``copilot-cli`` -> ``.copilot/skills/weld.md`` + ``.copilot/config.json``

The canonical "what is weld" stanza is shared; per-host overlays live under
``<!-- weld-host:NAME:start -->`` / ``<!-- weld-host:NAME:end -->`` markers.
Hosts without native MCP (``supports_mcp=False``) carry the wiki-fallback
stanza pointing at ``wd export --format=wiki`` (ADR 0053).
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weld._bootstrap_adapters import (
    aider_config_text,
    cursor_mcp_text,
    gemini_mcp_text,
    copilot_cli_config_text,
    host_registry,
)
from weld.bootstrap import bootstrap
from weld.cli import main as cli_main


_CANONICAL_FINGERPRINT = "wd brief"  # canonical body always mentions wd brief
_WIKI_FALLBACK_SUBSTRING = "wd export --format=wiki"


# --- Adapter unit tests (smallest layer first per TDD) --------------------

class HostRegistryShapeTest(unittest.TestCase):
    """The per-host registry declares the matrix in ADR 0054 §"Per-host integration matrix"."""

    def test_registry_contains_all_four_new_hosts(self) -> None:
        names = {h.name for h in host_registry()}
        self.assertIn("cursor", names)
        self.assertIn("aider", names)
        self.assertIn("gemini-cli", names)
        self.assertIn("copilot-cli", names)

    def test_aider_has_no_mcp_support(self) -> None:
        spec = next(h for h in host_registry() if h.name == "aider")
        self.assertFalse(spec.supports_mcp)

    def test_cursor_supports_mcp(self) -> None:
        spec = next(h for h in host_registry() if h.name == "cursor")
        self.assertTrue(spec.supports_mcp)

    def test_gemini_cli_supports_mcp(self) -> None:
        spec = next(h for h in host_registry() if h.name == "gemini-cli")
        self.assertTrue(spec.supports_mcp)

    def test_copilot_cli_supports_mcp(self) -> None:
        spec = next(h for h in host_registry() if h.name == "copilot-cli")
        self.assertTrue(spec.supports_mcp)

    def test_skill_paths_match_adr(self) -> None:
        by_name = {h.name: h for h in host_registry()}
        self.assertEqual(by_name["cursor"].skill_path,
                         Path(".cursor") / "rules" / "weld.mdc")
        self.assertEqual(by_name["aider"].skill_path,
                         Path("CONVENTIONS.md"))
        self.assertEqual(by_name["gemini-cli"].skill_path,
                         Path(".gemini") / "skills" / "weld.md")
        self.assertEqual(by_name["copilot-cli"].skill_path,
                         Path(".copilot") / "skills" / "weld.md")


class AdapterFunctionsTest(unittest.TestCase):
    """The per-host config adapters produce valid format-specific output."""

    def test_cursor_mcp_text_is_valid_json(self) -> None:
        text = cursor_mcp_text()
        payload = json.loads(text)
        # Cursor uses "mcpServers" key (per ADR 0023).
        self.assertIn("mcpServers", payload)
        self.assertIn("weld", payload["mcpServers"])

    def test_gemini_mcp_text_is_valid_json(self) -> None:
        text = gemini_mcp_text()
        payload = json.loads(text)
        # Gemini CLI follows the standard mcpServers shape.
        self.assertIn("mcpServers", payload)
        self.assertIn("weld", payload["mcpServers"])

    def test_copilot_cli_config_text_is_valid_json(self) -> None:
        text = copilot_cli_config_text()
        payload = json.loads(text)
        self.assertIn("mcpServers", payload)
        self.assertIn("weld", payload["mcpServers"])

    def test_aider_config_text_is_yaml_and_has_no_mcp(self) -> None:
        text = aider_config_text()
        # Aider config is yaml; assert the key markers and that it does not
        # carry an mcp stanza (aider does not run MCP).
        self.assertIn("read:", text)
        self.assertIn("CONVENTIONS.md", text)
        # Aider has no MCP support today (see ADR 0054 host matrix).
        self.assertNotIn("mcp", text.lower())


# --- Generation tests: canonical body + overlay + wiki fallback per host --

class GenerationCursorTest(unittest.TestCase):
    def test_writes_cursor_skill_and_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("cursor", root, force=True)
            skill = root / ".cursor" / "rules" / "weld.mdc"
            mcp = root / ".cursor" / "mcp.json"
            self.assertTrue(skill.is_file(), f"missing skill: {skill}")
            self.assertTrue(mcp.is_file(), f"missing mcp: {mcp}")
            content = skill.read_text(encoding="utf-8")
            # Canonical body must be present.
            self.assertIn(_CANONICAL_FINGERPRINT, content)
            # Per-host marker fragment must be present.
            self.assertIn("weld-host:cursor:start", content)
            self.assertIn("weld-host:cursor:end", content)
            # cursor supports MCP -> wiki-fallback stanza NOT required by
            # default, but the host can still mention wiki export. The
            # contract: only no-MCP hosts MUST carry the wiki-fallback stanza.

    def test_cursor_mcp_payload_carries_weld_server(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("cursor", root, force=True)
            mcp = root / ".cursor" / "mcp.json"
            payload = json.loads(mcp.read_text(encoding="utf-8"))
            entry = payload["mcpServers"]["weld"]
            # The console-script form, not `python -m`: a script's sys.path[0]
            # is its own directory, so a client launching this entry from a
            # repository never puts that repository on the import path.
            self.assertEqual(entry["command"], "wd")
            self.assertEqual(entry["args"], ["mcp", "serve"])


class GenerationAiderTest(unittest.TestCase):
    def test_writes_aider_conf_and_conventions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("aider", root, force=True)
            conf = root / ".aider.conf.yml"
            conventions = root / "CONVENTIONS.md"
            self.assertTrue(conf.is_file(), f"missing aider config: {conf}")
            self.assertTrue(conventions.is_file(),
                            f"missing conventions: {conventions}")

    def test_aider_conventions_has_canonical_body(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("aider", root, force=True)
            content = (root / "CONVENTIONS.md").read_text(encoding="utf-8")
            self.assertIn(_CANONICAL_FINGERPRINT, content)
            self.assertIn("weld-host:aider:start", content)
            self.assertIn("weld-host:aider:end", content)

    def test_aider_conventions_has_wiki_fallback(self) -> None:
        """Aider has no native MCP support -> wiki fallback is mandatory."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("aider", root, force=True)
            content = (root / "CONVENTIONS.md").read_text(encoding="utf-8")
            self.assertIn(_WIKI_FALLBACK_SUBSTRING, content)

    def test_aider_does_not_write_mcp_file(self) -> None:
        """Aider has no MCP support; no .aider/mcp.json should appear."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("aider", root, force=True)
            for path in root.rglob("*"):
                if path.is_file() and path.name.endswith("mcp.json"):
                    self.fail(f"unexpected MCP file under aider host: {path}")


class GenerationGeminiCliTest(unittest.TestCase):
    def test_writes_gemini_skill_and_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("gemini-cli", root, force=True)
            skill = root / ".gemini" / "skills" / "weld.md"
            mcp = root / ".gemini" / "mcp.json"
            self.assertTrue(skill.is_file(), f"missing skill: {skill}")
            self.assertTrue(mcp.is_file(), f"missing mcp: {mcp}")
            content = skill.read_text(encoding="utf-8")
            self.assertIn(_CANONICAL_FINGERPRINT, content)
            self.assertIn("weld-host:gemini-cli:start", content)
            self.assertIn("weld-host:gemini-cli:end", content)


class GenerationCopilotCliTest(unittest.TestCase):
    def test_writes_copilot_cli_skill_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot-cli", root, force=True)
            skill = root / ".copilot" / "skills" / "weld.md"
            config = root / ".copilot" / "config.json"
            self.assertTrue(skill.is_file(), f"missing skill: {skill}")
            self.assertTrue(config.is_file(), f"missing config: {config}")
            content = skill.read_text(encoding="utf-8")
            self.assertIn(_CANONICAL_FINGERPRINT, content)
            self.assertIn("weld-host:copilot-cli:start", content)
            self.assertIn("weld-host:copilot-cli:end", content)

    def test_copilot_cli_config_payload_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot-cli", root, force=True)
            config = root / ".copilot" / "config.json"
            payload = json.loads(config.read_text(encoding="utf-8"))
            self.assertIn("mcpServers", payload)


# --- CLI dispatch tests ---------------------------------------------------

class CliDispatchPerHostTest(unittest.TestCase):
    """The top-level CLI must dispatch all four new bootstrap names."""

    def test_cursor_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                cli_main(["bootstrap", "cursor", "--root", td, "--force"])
            self.assertTrue((Path(td) / ".cursor" / "rules" / "weld.mdc").is_file())

    def test_aider_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                cli_main(["bootstrap", "aider", "--root", td, "--force"])
            self.assertTrue((Path(td) / "CONVENTIONS.md").is_file())

    def test_gemini_cli_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                cli_main(["bootstrap", "gemini-cli", "--root", td, "--force"])
            self.assertTrue((Path(td) / ".gemini" / "skills" / "weld.md").is_file())

    def test_copilot_cli_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                cli_main(["bootstrap", "copilot-cli", "--root", td, "--force"])
            self.assertTrue(
                (Path(td) / ".copilot" / "skills" / "weld.md").is_file(),
            )

    def test_bootstrap_help_mentions_new_hosts(self) -> None:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            with self.assertRaises(SystemExit) as cm:
                cli_main(["bootstrap", "--help"])
        self.assertEqual(cm.exception.code, 0)
        output = buf.getvalue()
        for host in ("cursor", "aider", "gemini-cli", "copilot-cli"):
            self.assertIn(host, output, f"missing {host} in --help")


# --- Idempotence and dry-run (ADR 0026, ADR 0033) -------------------------

class IdempotenceTest(unittest.TestCase):
    """Re-running with the same template version is byte-identical."""

    def test_cursor_second_run_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("cursor", root, force=True)
            first = (root / ".cursor" / "rules" / "weld.mdc").read_text(
                encoding="utf-8")
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bootstrap("cursor", root, force=False)
            second = (root / ".cursor" / "rules" / "weld.mdc").read_text(
                encoding="utf-8")
            self.assertEqual(first, second)
            # The writer should report up-to-date, not pre-marker layout.
            output = buf.getvalue()
            self.assertNotIn("pre-marker", output)

    def test_aider_second_run_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("aider", root, force=True)
            first = (root / "CONVENTIONS.md").read_text(encoding="utf-8")
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bootstrap("aider", root, force=False)
            second = (root / "CONVENTIONS.md").read_text(encoding="utf-8")
            self.assertEqual(first, second)


class DryRunDiffTest(unittest.TestCase):
    """--diff for new hosts: signals missing files, exits 1 when changes pending."""

    def test_cursor_diff_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit) as cm:
                    cli_main([
                        "bootstrap", "cursor",
                        "--root", td, "--diff",
                    ])
            # Files missing -> exit 1, message printed.
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("would seed", buf.getvalue())

    def test_aider_diff_clean_after_force(self) -> None:
        """After --force seeds the files, --diff should exit 0."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("aider", root, force=True)
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit) as cm:
                    cli_main([
                        "bootstrap", "aider",
                        "--root", str(root), "--diff",
                    ])
            self.assertEqual(cm.exception.code, 0)


# --- Managed-region overwrite semantics on new hosts ----------------------

class ForceOverwriteManagedRegionTest(unittest.TestCase):
    """--force must overwrite drifted managed regions; curated text stays."""

    def test_force_clobbers_managed_region_keeps_curated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("gemini-cli", root, force=True)
            skill = root / ".gemini" / "skills" / "weld.md"
            text = skill.read_text(encoding="utf-8")

            # Curated edit OUTSIDE any managed region: prepend a sentinel
            # paragraph at the very top of the file (before all markers).
            curated_sentinel = "# Curated by operator — must survive\n\n"
            text = curated_sentinel + text

            # Drift INSIDE a managed region: corrupt the retrieval block so
            # it differs byte-wise from the bundled template.
            text = text.replace(
                "`wd brief <term>`",
                "`wd brief <DRIFTED>`",
                1,
            )
            skill.write_text(text, encoding="utf-8")

            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bootstrap("gemini-cli", root, force=True)

            new_text = skill.read_text(encoding="utf-8")
            # Curated sentinel survives -> outside-marker text is preserved.
            self.assertIn(curated_sentinel.strip(), new_text)
            # Drift inside the managed region is restored.
            self.assertNotIn("DRIFTED", new_text)


# --- Wiki-fallback stanza is mandatory for supports_mcp=False -------------

class WikiFallbackContractTest(unittest.TestCase):
    """ADR 0054: hosts with supports_mcp=False must emit a wiki-fallback stanza."""

    def test_aider_has_wiki_stanza(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("aider", root, force=True)
            content = (root / "CONVENTIONS.md").read_text(encoding="utf-8")
            self.assertIn(_WIKI_FALLBACK_SUBSTRING, content,
                          "aider (no MCP) must mention wiki fallback")

    def test_supports_mcp_false_iff_aider(self) -> None:
        # Today aider is the only no-MCP host. If a future host flips this,
        # the registry-level assertion above also covers it.
        no_mcp = [h.name for h in host_registry() if not h.supports_mcp]
        self.assertEqual(no_mcp, ["aider"])


if __name__ == "__main__":
    unittest.main()
