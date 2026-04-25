"""Unit tests for ``wd discover --safe`` (ADR 0024).

The flag must:

* refuse project-local strategy overrides under
  ``<root>/.weld/strategies/<name>.py`` (preferring the bundled
  implementation when one exists, otherwise treating the strategy as
  missing);
* refuse the ``external_json`` subprocess adapter without spawning the
  configured command;
* be visible in ``wd discover --help``;
* emit a stderr ``[weld] safe mode: skipped ...`` line for each refused
  path so operators can see what was disabled.

These are unit tests against ``weld._discover_strategies`` and the
``weld.discover`` argparse, plus a black-box ``--help`` check via the
``main`` entrypoint.
"""

from __future__ import annotations

import io
import stat
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._discover_strategies import (  # noqa: E402
    load_strategy,
    run_external_json,
    run_source,
)
from weld.discover import main as discover_main  # noqa: E402


def _write_local_strategy(root: Path, name: str, body: str) -> Path:
    """Place a project-local strategy under ``<root>/.weld/strategies``."""
    strat_dir = root / ".weld" / "strategies"
    strat_dir.mkdir(parents=True, exist_ok=True)
    path = strat_dir / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    return path


# A project-local body that *must not* execute under safe mode. It writes
# a marker file and would be obvious if the loader ran it.
_MALICIOUS_LOCAL_BODY = textwrap.dedent(
    """\
    from pathlib import Path

    # If safe mode is broken, this file gets created at import time.
    Path(__file__).parent.joinpath("LOCAL_STRATEGY_RAN").write_text("ran")

    def extract(root, source, context):
        from weld.strategies._helpers import StrategyResult
        return StrategyResult(nodes={}, edges=[], discovered_from=[])
    """
)


class LoadStrategySafeModeTest(unittest.TestCase):
    """``load_strategy`` refuses project-local overrides when safe=True."""

    def test_safe_mode_refuses_project_local_only_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Use a name that does NOT exist as a bundled strategy so the
            # safe-mode refusal cannot fall back to a bundled version.
            name = "weld_safe_mode_unit_test_only_local"
            _write_local_strategy(root, name, _MALICIOUS_LOCAL_BODY)

            buf = io.StringIO()
            with redirect_stderr(buf):
                fn = load_strategy(name, root, safe=True)

            self.assertIsNone(fn)
            stderr = buf.getvalue()
            self.assertIn("safe mode: skipped project-local strategy", stderr)
            self.assertIn(name, stderr)
            # Confirm the project-local module was *not* executed.
            marker = (root / ".weld" / "strategies" / "LOCAL_STRATEGY_RAN")
            self.assertFalse(marker.exists())

    def test_safe_mode_falls_back_to_bundled_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # ``markdown`` is a real bundled strategy. A project-local
            # override should be refused but the bundled one should still
            # load.
            _write_local_strategy(root, "markdown", _MALICIOUS_LOCAL_BODY)

            buf = io.StringIO()
            with redirect_stderr(buf):
                fn = load_strategy("markdown", root, safe=True)

            self.assertIsNotNone(fn)
            self.assertTrue(callable(fn))
            stderr = buf.getvalue()
            self.assertIn("safe mode: skipped project-local strategy", stderr)
            # Bundled fallback ran instead -- the malicious local body did
            # not.
            marker = (root / ".weld" / "strategies" / "LOCAL_STRATEGY_RAN")
            self.assertFalse(marker.exists())

    def test_unsafe_mode_still_loads_project_local(self) -> None:
        """Default (safe=False) preserves the existing override behavior."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            name = "weld_safe_mode_unit_test_unsafe_local"
            body = textwrap.dedent(
                """\
                def extract(root, source, context):
                    from weld.strategies._helpers import StrategyResult
                    return StrategyResult(
                        nodes={"unit:local": {"type": "unit", "label": "local"}},
                        edges=[],
                        discovered_from=[],
                    )
                """
            )
            _write_local_strategy(root, name, body)

            fn = load_strategy(name, root)  # safe defaults to False
            self.assertIsNotNone(fn)
            self.assertTrue(callable(fn))


class ExternalJsonSafeModeTest(unittest.TestCase):
    """``run_external_json`` refuses to spawn subprocesses when safe=True."""

    def test_safe_mode_blocks_subprocess(self) -> None:
        source = {
            "strategy": "external_json",
            "command": "/bin/echo this should never run",
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            buf = io.StringIO()
            with mock.patch(
                "weld._discover_strategies.subprocess.run"
            ) as run_mock, redirect_stderr(buf):
                result = run_external_json(root, source, safe=True)

            run_mock.assert_not_called()
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])
            self.assertEqual(result.discovered_from, [])
            stderr = buf.getvalue()
            self.assertIn("safe mode: skipped external_json", stderr)
            self.assertIn("/bin/echo", stderr)

    def test_safe_mode_blocks_via_run_source(self) -> None:
        """``run_source`` plumbs safe=True down to the external_json adapter."""
        source = {
            "strategy": "external_json",
            "command": "/usr/bin/touch /tmp/should_never_be_touched",
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch(
                "weld._discover_strategies.subprocess.run"
            ) as run_mock, redirect_stderr(io.StringIO()):
                result = run_source(root, source, {}, safe=True)
            run_mock.assert_not_called()
            self.assertEqual(result.nodes, {})

    def test_unsafe_mode_still_spawns(self) -> None:
        """Default (safe=False) preserves existing subprocess behavior."""
        # Use a tiny inline adapter so we know the subprocess actually runs
        # without depending on the external_json acceptance fixtures.
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            script = tmpdir / "adapter.py"
            script.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json, sys
                    json.dump({"nodes": {}, "edges": [], "discovered_from": []}, sys.stdout)
                    """
                ),
                encoding="utf-8",
            )
            script.chmod(script.stat().st_mode | stat.S_IEXEC)
            source = {
                "strategy": "external_json",
                "command": str(script),
            }
            buf = io.StringIO()
            with redirect_stderr(buf):
                result = run_external_json(tmpdir, source)
            # The subprocess actually ran -- result is the empty fragment
            # produced by our inline adapter.
            self.assertEqual(result.nodes, {})
            self.assertNotIn("safe mode", buf.getvalue())


class DiscoverHelpTest(unittest.TestCase):
    """``wd discover --help`` advertises the ``--safe`` flag."""

    def test_help_mentions_safe_flag(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                discover_main(["--help"])
        # argparse exits 0 on --help.
        self.assertEqual(cm.exception.code, 0)
        help_text = out.getvalue()
        self.assertIn("--safe", help_text)
        # The help line should reference the trust boundary so operators
        # know what the flag does.
        self.assertTrue(
            "external_json" in help_text or "untrusted" in help_text,
            f"--safe help line missing trust-boundary context: {help_text!r}",
        )


if __name__ == "__main__":
    unittest.main()
