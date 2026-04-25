"""Unit tests for ADR 0024 unsafe-mode warnings.

When ``wd discover`` runs WITHOUT ``--safe`` and the workspace contains
project-local strategies (``.weld/strategies/<name>.py``) or
``external_json`` adapter sources, the loader must emit a clear,
grep-friendly warning so operators see what local code is about to run.

These warnings:

* fire only when ``safe=False`` (suppressed under safe mode -- safe mode
  emits its own ``[weld] safe mode: skipped ...`` line instead);
* name the strategy (or command) so operators can grep for it;
* are visible (stderr) but do not block discovery.
"""

from __future__ import annotations

import io
import stat
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr
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


# A benign project-local body. The tests never want this file to do
# anything observable -- the warning fires before/around the import.
_LOCAL_BODY = textwrap.dedent(
    """\
    def extract(root, source, context):
        from weld.strategies._helpers import StrategyResult
        return StrategyResult(nodes={}, edges=[], discovered_from=[])
    """
)


def _write_local_strategy(root: Path, name: str, body: str) -> Path:
    strat_dir = root / ".weld" / "strategies"
    strat_dir.mkdir(parents=True, exist_ok=True)
    path = strat_dir / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    return path


class ProjectLocalStrategyWarnTest(unittest.TestCase):
    """``load_strategy`` warns when loading project-local code in unsafe mode."""

    def test_unsafe_mode_warns_on_project_local_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            name = "weld_safe_warn_unit_test_only_local"
            _write_local_strategy(root, name, _LOCAL_BODY)

            buf = io.StringIO()
            with redirect_stderr(buf):
                fn = load_strategy(name, root)  # safe defaults to False

            self.assertIsNotNone(fn)
            stderr = buf.getvalue()
            # Stable, grep-friendly prefix and key phrasing.
            self.assertIn("[weld] warning:", stderr)
            self.assertIn("project-local strategy", stderr)
            self.assertIn(name, stderr)
            self.assertIn("--safe", stderr)

    def test_unsafe_mode_warns_when_local_shadows_bundled(self) -> None:
        """Override of a bundled strategy still trips the warning."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # ``markdown`` is a real bundled strategy.
            _write_local_strategy(root, "markdown", _LOCAL_BODY)

            buf = io.StringIO()
            with redirect_stderr(buf):
                fn = load_strategy("markdown", root)

            self.assertIsNotNone(fn)
            stderr = buf.getvalue()
            self.assertIn("[weld] warning:", stderr)
            self.assertIn("project-local strategy", stderr)
            self.assertIn("markdown", stderr)
            self.assertIn("--safe", stderr)

    def test_safe_mode_suppresses_unsafe_warning(self) -> None:
        """Safe mode emits its own line and does NOT emit the unsafe warning."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            name = "weld_safe_warn_unit_test_safe_suppresses"
            _write_local_strategy(root, name, _LOCAL_BODY)

            buf = io.StringIO()
            with redirect_stderr(buf):
                load_strategy(name, root, safe=True)

            stderr = buf.getvalue()
            # Safe mode message present.
            self.assertIn("safe mode: skipped project-local strategy", stderr)
            # Unsafe-mode warning absent. The "will execute" phrasing is
            # unique to the unsafe-mode warning.
            self.assertNotIn("will execute local code", stderr)

    def test_bundled_only_strategy_does_not_warn(self) -> None:
        """No project-local file -> no warning, even in unsafe mode."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            buf = io.StringIO()
            with redirect_stderr(buf):
                fn = load_strategy("markdown", root)
            self.assertIsNotNone(fn)
            stderr = buf.getvalue()
            self.assertNotIn("project-local strategy", stderr)


class ExternalJsonWarnTest(unittest.TestCase):
    """``run_external_json`` warns before spawning the subprocess."""

    def test_unsafe_mode_warns_before_subprocess(self) -> None:
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
                run_external_json(tmpdir, source)

            stderr = buf.getvalue()
            self.assertIn("[weld] warning:", stderr)
            self.assertIn("external_json", stderr)
            self.assertIn("will execute local code", stderr)
            self.assertIn("--safe", stderr)
            self.assertIn(str(script), stderr)

    def test_safe_mode_suppresses_unsafe_warning(self) -> None:
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
                run_external_json(root, source, safe=True)

            run_mock.assert_not_called()
            stderr = buf.getvalue()
            # Safe mode line present, unsafe-mode warning suppressed.
            self.assertIn("safe mode: skipped external_json", stderr)
            self.assertNotIn("will execute local code", stderr)

    def test_warning_fires_via_run_source(self) -> None:
        """``run_source`` plumbs the unsafe path through to the warning."""
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            script = tmpdir / "adapter2.py"
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
            source = {"strategy": "external_json", "command": str(script)}

            buf = io.StringIO()
            with redirect_stderr(buf):
                run_source(tmpdir, source, {})

            stderr = buf.getvalue()
            self.assertIn("[weld] warning:", stderr)
            self.assertIn("external_json", stderr)
            self.assertIn("will execute local code", stderr)
            self.assertIn("--safe", stderr)

    def test_missing_command_does_not_warn(self) -> None:
        """An external_json source without a command short-circuits without warning."""
        source = {"strategy": "external_json"}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            buf = io.StringIO()
            with redirect_stderr(buf):
                run_external_json(root, source)
            stderr = buf.getvalue()
            # The existing 'missing command' notice is fine, but the
            # trust-boundary warning should not fire if there is nothing
            # to execute.
            self.assertNotIn("will execute local code", stderr)


if __name__ == "__main__":
    unittest.main()
