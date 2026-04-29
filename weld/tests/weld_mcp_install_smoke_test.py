"""WELD-P1-006 -- installable MCP server smoke test.

Pins three invariants the existing :mod:`weld_mcp_smoke_test` does not
cover:

1. The MCP server's missing-graph contract is **actionable** -- every
   graph-backed tool returns a structured error payload that mirrors the
   CLI's :func:`weld._graph_cli.missing_graph_message` (stable
   ``error_code='graph_missing'`` plus a hint and retry string).
2. The stale-graph response from :func:`weld.mcp_server.weld_stale` is
   **documented** -- the test asserts the canonical key set and value
   types so consumers (Claude Code, Codex, MCP-aware tools) can rely on
   it as a stable wire shape.
3. The MCP server boots from an **installed** package, not just from the
   repo source tree. This is the regression bar for the v0.8.0 wheel
   miss (memory: ``v0-8-0-broken-shipped-0-8-1-hotfix``): build a wheel,
   install it into an isolated prefix, and confirm
   ``python -m weld.mcp_server --help`` runs from the installed copy.

Layered for the local gate:

* The contract assertions (1) and (2) run in-process under Bazel: fast,
  deterministic, and hermetic (no network, no subprocess).
* The wheel-install smoke (3) requires :mod:`ensurepip` and ``pip wheel``
  / ``pip install``. The default devcontainer ships without ensurepip,
  so the test ``skipTest``-s when those toolchains are unavailable. CI
  picks the path up because the Ubuntu runner has both.

This file complements -- does not replace -- :mod:`weld_mcp_smoke_test`,
which keeps the JSON-RPC handshake and tool-name pinning.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Resolve repo root for source-tree imports when running outside Bazel.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from weld import mcp_server  # noqa: E402
from weld._mcp_guard import (  # noqa: E402
    graph_present as _graph_present,
    missing_graph_payload as _missing_graph_payload,
)


# ---------------------------------------------------------------------------
# 1. Missing-graph contract -- one assertion per graph-backed tool.
# ---------------------------------------------------------------------------

# Tools that the guard MUST cover. ``weld_find`` (file index, not graph)
# and ``weld_stale`` (already exposes graph_sha=null + reason) are exempt
# by design and are pinned separately below.
_GUARDED_TOOLS: dict[str, dict] = {
    "weld_query": {"term": "anything"},
    "weld_context": {"node_id": "node:nope"},
    "weld_path": {"from_id": "a", "to_id": "b"},
    "weld_brief": {"area": "anywhere"},
    "weld_callers": {"symbol_id": "anything"},
    "weld_export": {"format": "mermaid"},
    "weld_references": {"symbol_name": "anything"},
    "weld_diff": {},
    "weld_impact": {"target": "anything"},
    "weld_trace": {"term": "anything"},
    "weld_enrich": {},
}

_EXEMPT_TOOLS: frozenset[str] = frozenset({"weld_find", "weld_stale"})


class MissingGraphContractTest(unittest.TestCase):
    """Every graph-backed MCP tool must return the actionable error
    payload when ``.weld/graph.json`` is absent. Mirrors the CLI's
    :func:`weld._graph_cli.missing_graph_message`."""

    def test_payload_shape_is_stable(self) -> None:
        payload = _missing_graph_payload("weld_query")
        # Stable, machine-parseable error_code is the contract --
        # never let the human-readable text be the only signal.
        self.assertEqual(payload.get("error_code"), "graph_missing")
        for key in ("error", "hint", "retry"):
            self.assertIn(key, payload, f"missing key {key!r}")
            self.assertIsInstance(payload[key], str)
            self.assertTrue(payload[key].strip(), f"empty value for {key!r}")
        # Wording mirrors weld._graph_cli.missing_graph_message so docs
        # and onboarding guidance stay in sync.
        self.assertIn("No Weld graph found", payload["error"])
        self.assertIn("wd discover", payload["hint"])

    def test_graph_present_says_no_for_empty_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(_graph_present(Path(tmp)))

    def test_graph_present_says_yes_when_graph_json_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir()
            (root / ".weld" / "graph.json").write_text(
                json.dumps({"meta": {}, "nodes": {}, "edges": []}),
                encoding="utf-8",
            )
            self.assertTrue(_graph_present(root))

    def test_graph_present_says_yes_for_federated_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir()
            # workspaces.yaml at the root signals federation -- the
            # FederatedGraph layer reports per-child status separately,
            # so the boundary guard must not block.
            (root / ".weld" / "workspaces.yaml").write_text(
                "workspaces: []\n", encoding="utf-8",
            )
            self.assertTrue(_graph_present(root))

    def test_every_guarded_tool_returns_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for tool_name, args in _GUARDED_TOOLS.items():
                with self.subTest(tool=tool_name):
                    handler = getattr(mcp_server, tool_name)
                    result = handler(**args, root=root)
                    self.assertIsInstance(result, dict)
                    self.assertEqual(
                        result.get("error_code"),
                        "graph_missing",
                        f"{tool_name} did not return graph_missing payload "
                        f"on a missing graph; got keys={sorted(result)}",
                    )

    def test_dispatch_routes_through_guarded_handlers(self) -> None:
        # Regression guard for ADR 0015 wiring: ``dispatch`` builds the
        # registry from ``mcp_server.build_tools``, which must register
        # the wrapped (guarded) handlers, not the raw helpers.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for tool_name, args in _GUARDED_TOOLS.items():
                with self.subTest(tool=tool_name):
                    result = mcp_server.dispatch(tool_name, args, root=root)
                    self.assertEqual(
                        result.get("error_code"),
                        "graph_missing",
                        f"dispatch({tool_name!r}) bypassed the guard "
                        f"(handler in registry is not the wrapped one)",
                    )

    def test_exempt_tools_do_not_error_on_missing_graph(self) -> None:
        # weld_find reads the file index, not the graph. weld_stale
        # already exposes ``graph_sha=null`` + a reason string. Both
        # must remain side-effect-free and non-erroring on missing
        # graphs (legacy contract).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            find_result = mcp_server.weld_find("anything", root=root)
            self.assertNotIn("error_code", find_result)
            self.assertIn("files", find_result)
            stale_result = mcp_server.weld_stale(root=root)
            self.assertNotIn("error_code", stale_result)


# ---------------------------------------------------------------------------
# 2. Stale-graph response is documented -- pin the wire shape.
# ---------------------------------------------------------------------------

class StaleGraphContractTest(unittest.TestCase):
    """Document and pin the :func:`weld.mcp_server.weld_stale` response.

    Acceptance criterion from WELD-P1-006: "Stale graph response is
    documented." The test serves as the executable documentation -- if
    the schema drifts, the test breaks and the consumer contract has to
    be re-evaluated explicitly.
    """

    _REQUIRED_KEYS: frozenset[str] = frozenset({
        "stale", "source_stale", "sha_behind",
        "graph_sha", "current_sha", "commits_behind",
    })

    def test_stale_keys_for_non_git_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = mcp_server.weld_stale(root=Path(tmp))
            self.assertTrue(self._REQUIRED_KEYS.issubset(result.keys()))
            # Non-git roots carry an explanatory ``reason`` string.
            self.assertIn("reason", result)
            self.assertIsInstance(result["reason"], str)
            self.assertFalse(result["stale"])
            self.assertIsNone(result["graph_sha"])

    def test_stale_keys_for_git_root_without_graph(self) -> None:
        # In a git repo with no .weld/graph.json yet, the documented
        # signal is graph_sha=None + source_stale=True -- agents key
        # off ``source_stale`` (or the ``error_code`` from any other
        # tool) to know they should run ``wd discover``.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(
                ["git", "init", "-q", str(root)],
                check=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            # Need a HEAD for current_sha -- empty commit suffices.
            subprocess.run(
                ["git", "-C", str(root), "commit", "-q",
                 "--allow-empty", "-m", "init",
                 "--author=Test <test@example.com>"],
                check=True,
                env={
                    **os.environ,
                    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@e",
                    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@e",
                },
            )
            result = mcp_server.weld_stale(root=root)
            self.assertTrue(self._REQUIRED_KEYS.issubset(result.keys()))
            self.assertIsNone(result["graph_sha"])
            # current_sha is set to HEAD on a real git repo.
            self.assertIsInstance(result["current_sha"], str)
            self.assertTrue(result["source_stale"])


# ---------------------------------------------------------------------------
# 3. Wheel-install smoke -- catches v0.8.0-style packaging regressions.
# ---------------------------------------------------------------------------

def _ensurepip_available() -> bool:
    """Whether the runtime has :mod:`ensurepip` (i.e., can build venvs / wheels).

    Returns False on the default devcontainer (no python3-venv installed)
    and True on the public-readiness CI runners.
    """
    try:
        import ensurepip  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        return False
    return True


def _weld_source_root() -> Path:
    return _REPO_ROOT / "weld"


class WheelInstallSmokeTest(unittest.TestCase):
    """Build a wheel from ``weld/`` and import the MCP server from it.

    The check is intentionally minimal:

    * Build wheel via ``pip wheel --no-deps`` (deterministic; no network
      because we pass ``--no-deps`` and do not pull from PyPI).
    * Install the wheel into an isolated ``--prefix`` so other tests in
      the same Bazel runner are unaffected.
    * Run ``python -m weld.mcp_server --help`` against that prefix and
      confirm exit 0 and the ``configflux-weld[mcp]`` install hint
      string -- the same string :mod:`weld_mcp_smoke_test` checks for
      the source path. If this drifts vs. the source-tree behavior, the
      wheel does not match what users actually install.

    Skipped when ``ensurepip`` is missing (devcontainer / sandboxed Bazel
    on machines without pip), so local verification stays green while CI
    exercises the full path.
    """

    def setUp(self) -> None:
        if not _ensurepip_available():
            self.skipTest(
                "ensurepip not available; wheel-install smoke is CI-only"
            )
        if shutil.which("pip") is None and shutil.which("pip3") is None:
            # ``python -m pip`` works once ensurepip exists, but be
            # explicit so the skip message reads cleanly.
            try:
                import pip  # noqa: F401  # type: ignore[import-not-found]
            except ImportError:
                self.skipTest("pip is not importable; wheel-install smoke is CI-only")

    def test_wheel_install_exposes_python_m_weld_mcp_server(self) -> None:
        weld_root = _weld_source_root()
        self.assertTrue(
            (weld_root / "pyproject.toml").is_file(),
            f"weld pyproject.toml not found at {weld_root}",
        )

        with tempfile.TemporaryDirectory(prefix="weld-mcp-wheel-") as tmp:
            tmp_path = Path(tmp)
            dist_dir = tmp_path / "dist"
            dist_dir.mkdir()

            # 1. Build a wheel from weld/ source. ``--no-deps`` keeps it
            # offline; ``--no-build-isolation`` is intentionally NOT
            # passed because the smoke must mirror what users actually
            # do (``pip install configflux-weld``).
            self._run([
                sys.executable, "-m", "pip", "wheel",
                "--quiet", "--no-deps", str(weld_root),
                "-w", str(dist_dir),
            ], "build wheel")
            wheels = sorted(dist_dir.glob("*.whl"))
            self.assertEqual(
                len(wheels), 1,
                f"expected exactly one wheel, got {[w.name for w in wheels]}",
            )

            # 2. Install into an isolated prefix.
            prefix = tmp_path / "prefix"
            self._run([
                sys.executable, "-m", "pip", "install",
                "--quiet", "--no-deps", "--target", str(prefix),
                str(wheels[0]),
            ], "install wheel")
            self.assertTrue(
                (prefix / "weld" / "mcp_server.py").is_file(),
                "installed wheel does not expose weld/mcp_server.py "
                "(packaging regression -- compare with v0.8.0 miss)",
            )

            # 3. Run ``python -m weld.mcp_server --help`` from the
            # installed prefix, NOT the source tree. PYTHONPATH points
            # only at the prefix to prove the import resolves there.
            env = os.environ.copy()
            env["PYTHONPATH"] = str(prefix)
            # Drop any inherited PWD-based path leakage by running from
            # tmp_path (no source tree alongside).
            proc = subprocess.run(
                [sys.executable, "-m", "weld.mcp_server", "--help"],
                cwd=tmp_path, env=env, capture_output=True,
                text=True, timeout=30, check=False,
            )
            self.assertEqual(
                proc.returncode, 0,
                f"python -m weld.mcp_server --help failed from wheel "
                f"install: rc={proc.returncode} "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
            )
            self.assertIn(
                "Usage: python -m weld.mcp_server", proc.stdout,
                f"wheel-installed --help did not print the usage banner; "
                f"stdout={proc.stdout!r}",
            )
            self.assertIn(
                "configflux-weld[mcp]", proc.stdout,
                "wheel-installed --help is missing the install hint that "
                "tells users how to add the optional MCP SDK extra",
            )

    @staticmethod
    def _run(cmd: list[str], label: str) -> None:
        """Run *cmd* and surface stderr on failure (no silent skips)."""
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise AssertionError(
                f"{label} failed (rc={proc.returncode})\n"
                f"  cmd:    {cmd}\n"
                f"  stdout: {proc.stdout}\n"
                f"  stderr: {proc.stderr}\n"
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
