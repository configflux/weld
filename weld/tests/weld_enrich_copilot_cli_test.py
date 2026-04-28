"""Unit tests for :class:`weld.providers.copilot_cli.CopilotCliProvider`.

The provider wraps the standalone GitHub Copilot CLI via subprocess. These
tests inject a stub runner so they never touch a real binary, and cover
the failure surfaces a subprocess-based provider introduces (missing
binary, non-zero exit, timeout, non-JSON output) plus binary-path
resolution precedence (explicit arg > env > default).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.providers.copilot_cli import (  # noqa: E402
    _BINARY_ENV,
    _DEFAULT_BINARY,
    _INSTALL_HINT,
    CopilotCliProvider,
)


class _FakeProc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_runner(*, returncode: int = 0, stdout: str = "", stderr: str = ""):
    calls: list[dict] = []

    def runner(cmd, **kwargs):
        calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
        return _FakeProc(returncode=returncode, stdout=stdout, stderr=stderr)

    return runner, calls


_NODE = {"id": "n:1", "type": "module", "label": "L", "props": {}}
_NEIGHBORS: list[dict] = []
_VALID_PAYLOAD = json.dumps(
    {
        "description": "A module that does X.",
        "purpose": "Bridges A and B.",
        "complexity_hint": "low",
        "suggested_tags": ["graph", "core"],
    }
)


class CopilotCliProviderTest(unittest.TestCase):
    def test_happy_path_returns_validated_result(self) -> None:
        runner, calls = _make_runner(stdout=_VALID_PAYLOAD)
        provider = CopilotCliProvider(runner=runner, binary="copilot")

        result = provider.enrich(_NODE, _NEIGHBORS, model="default")

        self.assertEqual(result.description, "A module that does X.")
        self.assertEqual(result.purpose, "Bridges A and B.")
        self.assertEqual(result.complexity_hint, "low")
        self.assertEqual(result.suggested_tags, ("graph", "core"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["cmd"][0], "copilot")
        self.assertEqual(calls[0]["cmd"][1], "-p")
        # The third arg is the prompt; just confirm it was forwarded.
        self.assertIn("knowledge-graph node", calls[0]["cmd"][2])
        self.assertEqual(calls[0]["kwargs"].get("timeout"), 120)

    def test_strips_markdown_envelope_around_json(self) -> None:
        wrapped = (
            "Sure, here is the JSON you asked for:\n"
            "```json\n"
            f"{_VALID_PAYLOAD}\n"
            "```\n"
            "Let me know if you need anything else.\n"
        )
        runner, _ = _make_runner(stdout=wrapped)
        provider = CopilotCliProvider(runner=runner, binary="copilot")

        result = provider.enrich(_NODE, _NEIGHBORS, model="default")

        self.assertEqual(result.description, "A module that does X.")

    def test_non_zero_exit_raises_runtime_error_with_stderr_tail(self) -> None:
        runner, _ = _make_runner(returncode=2, stderr="auth required")
        provider = CopilotCliProvider(runner=runner, binary="copilot")

        with self.assertRaises(RuntimeError) as cm:
            provider.enrich(_NODE, _NEIGHBORS, model="default")

        msg = str(cm.exception)
        self.assertIn("status 2", msg)
        self.assertIn("auth required", msg)

    def test_missing_binary_raises_install_hint(self) -> None:
        def runner(cmd, **kwargs):
            raise FileNotFoundError(cmd[0])

        provider = CopilotCliProvider(runner=runner, binary="copilot")

        with self.assertRaises(RuntimeError) as cm:
            provider.enrich(_NODE, _NEIGHBORS, model="default")

        self.assertEqual(str(cm.exception), _INSTALL_HINT)

    def test_timeout_raises_runtime_error(self) -> None:
        def runner(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

        provider = CopilotCliProvider(runner=runner, binary="copilot", timeout_sec=5)

        with self.assertRaises(RuntimeError) as cm:
            provider.enrich(_NODE, _NEIGHBORS, model="default")

        msg = str(cm.exception)
        self.assertIn("timed out", msg)
        self.assertIn("5", msg)

    def test_env_var_overrides_default_binary(self) -> None:
        runner, calls = _make_runner(stdout=_VALID_PAYLOAD)
        with mock.patch.dict(
            os.environ, {_BINARY_ENV: "/opt/custom/copilot"}, clear=False
        ):
            provider = CopilotCliProvider(runner=runner)
        provider.enrich(_NODE, _NEIGHBORS, model="default")

        self.assertEqual(calls[0]["cmd"][0], "/opt/custom/copilot")

    def test_explicit_binary_overrides_env(self) -> None:
        runner, calls = _make_runner(stdout=_VALID_PAYLOAD)
        with mock.patch.dict(os.environ, {_BINARY_ENV: "/opt/copilot"}, clear=False):
            provider = CopilotCliProvider(runner=runner, binary="/explicit/copilot")
        provider.enrich(_NODE, _NEIGHBORS, model="default")

        self.assertEqual(calls[0]["cmd"][0], "/explicit/copilot")

    def test_default_binary_used_when_no_env_no_arg(self) -> None:
        runner, calls = _make_runner(stdout=_VALID_PAYLOAD)
        env_without = {k: v for k, v in os.environ.items() if k != _BINARY_ENV}
        with mock.patch.dict(os.environ, env_without, clear=True):
            provider = CopilotCliProvider(runner=runner)
        provider.enrich(_NODE, _NEIGHBORS, model="default")

        self.assertEqual(calls[0]["cmd"][0], _DEFAULT_BINARY)

    def test_non_json_stdout_surfaces_parse_error(self) -> None:
        runner, _ = _make_runner(stdout="this is not json at all")
        provider = CopilotCliProvider(runner=runner, binary="copilot")

        with self.assertRaises(Exception):
            provider.enrich(_NODE, _NEIGHBORS, model="default")


if __name__ == "__main__":
    unittest.main()
