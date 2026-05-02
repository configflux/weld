"""Unit tests for the ``wd enrich`` no-provider error message.

When a user runs ``wd enrich`` without ``--provider`` and without the
``WELD_ENRICH_PROVIDER`` environment variable, the error must:

* still mention ``WELD_ENRICH_PROVIDER`` so existing users see the
  familiar fallback;
* list which provider strings are valid (``Available: ...``), reusing
  the ``wd doctor`` optional-dep detection so the two surfaces never
  drift;
* point at the agent-direct workflow (``/enrich-weld``) so harnesses
  without an installed provider know what to run instead;
* mention ``--safe`` for users who specifically want to refuse network
  providers.

When ``--safe`` is set with no provider, the message must instead
acknowledge that safe-mode forbids every registered network provider
and direct the user at the agent-direct path. Recommending a
``pip install`` line under ``--safe`` would defeat safe-mode's promise.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._enrich_safe import resolve_provider_name  # noqa: E402
from weld.enrich import main as enrich_main  # noqa: E402
from weld.graph import Graph  # noqa: E402


def _write_graph(root: Path, nodes: dict[str, dict]) -> Graph:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    graph_path = weld_dir / "graph.json"
    graph_path.write_text(
        json.dumps(
            {
                "meta": {"version": 4, "updated_at": "2026-04-25T00:00:00+00:00"},
                "nodes": nodes,
                "edges": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    graph = Graph(root)
    graph.load()
    return graph


class BareNoProviderMessageTest(unittest.TestCase):
    """``wd enrich`` (no flag, no env) error must guide the user."""

    def test_error_preserves_env_var_mention(self) -> None:
        # Existing users grep for WELD_ENRICH_PROVIDER; keep it reachable.
        with mock.patch.dict(
            "os.environ", {"WELD_ENRICH_PROVIDER": ""}, clear=False
        ), mock.patch(
            "weld._enrich_safe._available_provider_names",
            return_value=("anthropic",),
        ):
            with self.assertRaises(ValueError) as cm:
                resolve_provider_name(None)
        self.assertIn("WELD_ENRICH_PROVIDER", str(cm.exception))

    def test_error_lists_available_providers(self) -> None:
        with mock.patch.dict(
            "os.environ", {"WELD_ENRICH_PROVIDER": ""}, clear=False
        ), mock.patch(
            "weld._enrich_safe._available_provider_names",
            return_value=("anthropic", "openai", "ollama", "copilot-cli"),
        ):
            with self.assertRaises(ValueError) as cm:
                resolve_provider_name(None)
        msg = str(cm.exception)
        self.assertIn("Available:", msg)
        # Every detected provider name must appear so the list is
        # actually copy-pastable.
        for name in ("anthropic", "openai", "ollama", "copilot-cli"):
            self.assertIn(name, msg)

    def test_error_mentions_agent_direct_path(self) -> None:
        with mock.patch.dict(
            "os.environ", {"WELD_ENRICH_PROVIDER": ""}, clear=False
        ), mock.patch(
            "weld._enrich_safe._available_provider_names",
            return_value=("anthropic",),
        ):
            with self.assertRaises(ValueError) as cm:
                resolve_provider_name(None)
        self.assertIn("/enrich-weld", str(cm.exception))

    def test_error_mentions_safe_flag(self) -> None:
        with mock.patch.dict(
            "os.environ", {"WELD_ENRICH_PROVIDER": ""}, clear=False
        ), mock.patch(
            "weld._enrich_safe._available_provider_names",
            return_value=("anthropic",),
        ):
            with self.assertRaises(ValueError) as cm:
                resolve_provider_name(None)
        self.assertIn("--safe", str(cm.exception))

    def test_error_when_no_providers_installed(self) -> None:
        # Even with zero detected providers, the error must still nudge
        # the user toward the agent-direct path rather than rendering an
        # empty "Available:" line.
        with mock.patch.dict(
            "os.environ", {"WELD_ENRICH_PROVIDER": ""}, clear=False
        ), mock.patch(
            "weld._enrich_safe._available_provider_names",
            return_value=(),
        ):
            with self.assertRaises(ValueError) as cm:
                resolve_provider_name(None)
        msg = str(cm.exception)
        self.assertIn("/enrich-weld", msg)
        self.assertNotIn("Available: \n", msg)
        self.assertNotIn("Available: .", msg)


class SafeModeNoProviderMessageTest(unittest.TestCase):
    """``wd enrich --safe`` (no provider) is a distinct terminal state."""

    def test_error_acknowledges_safe_mode(self) -> None:
        with mock.patch.dict(
            "os.environ", {"WELD_ENRICH_PROVIDER": ""}, clear=False
        ):
            with self.assertRaises(ValueError) as cm:
                resolve_provider_name(None, safe=True)
        self.assertIn("safe", str(cm.exception).lower())

    def test_error_points_at_agent_direct_path(self) -> None:
        with mock.patch.dict(
            "os.environ", {"WELD_ENRICH_PROVIDER": ""}, clear=False
        ):
            with self.assertRaises(ValueError) as cm:
                resolve_provider_name(None, safe=True)
        self.assertIn("/enrich-weld", str(cm.exception))

    def test_error_does_not_recommend_network_install(self) -> None:
        # Recommending `pip install` here would contradict --safe.
        with mock.patch.dict(
            "os.environ", {"WELD_ENRICH_PROVIDER": ""}, clear=False
        ):
            with self.assertRaises(ValueError) as cm:
                resolve_provider_name(None, safe=True)
        self.assertNotIn("pip install", str(cm.exception))


class CliErrorTextTest(unittest.TestCase):
    """The CLI surfaces the enriched message via stderr without truncation."""

    def test_main_no_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(
                root,
                {
                    "entity:Store": {
                        "type": "entity",
                        "label": "Store",
                        "props": {"file": "store.py"},
                    },
                },
            )
            err = io.StringIO()
            with mock.patch.dict(
                "os.environ", {"WELD_ENRICH_PROVIDER": ""}, clear=False
            ), mock.patch(
                "weld._enrich_safe._available_provider_names",
                return_value=("anthropic",),
            ), redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = enrich_main(["--root", str(root)])
            self.assertEqual(rc, 1)
            stderr = err.getvalue()
            self.assertIn("wd enrich:", stderr)
            self.assertIn("Available:", stderr)
            self.assertIn("/enrich-weld", stderr)

    def test_main_safe_no_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(
                root,
                {
                    "entity:Store": {
                        "type": "entity",
                        "label": "Store",
                        "props": {"file": "store.py"},
                    },
                },
            )
            err = io.StringIO()
            with mock.patch.dict(
                "os.environ", {"WELD_ENRICH_PROVIDER": ""}, clear=False
            ), redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = enrich_main(["--root", str(root), "--safe"])
            self.assertEqual(rc, 1)
            stderr = err.getvalue()
            self.assertIn("wd enrich:", stderr)
            self.assertIn("/enrich-weld", stderr)


class AvailableProviderDetectionTest(unittest.TestCase):
    """The detection helper must reuse the ``wd doctor`` probes."""

    def test_returns_subset_of_known_provider_names(self) -> None:
        # Whatever providers the helper reports must be valid strings
        # ``resolve_provider`` accepts -- otherwise the error message
        # would advertise unusable names.
        from weld._enrich_safe import _available_provider_names
        from weld.providers import _PROVIDER_LOADERS

        detected = _available_provider_names()
        self.assertIsInstance(detected, tuple)
        for name in detected:
            self.assertIn(
                name,
                _PROVIDER_LOADERS,
                f"detected provider {name!r} is not a registered loader",
            )

    def test_excludes_non_enrichment_optional_deps(self) -> None:
        # ``_doctor_optional`` also probes the ``mcp`` SDK, which is
        # unrelated to enrichment. The helper must not surface it.
        from weld._enrich_safe import _available_provider_names

        detected = _available_provider_names()
        self.assertNotIn("mcp", detected)
        self.assertNotIn("mcp SDK", detected)


if __name__ == "__main__":
    unittest.main()
