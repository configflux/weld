"""Unit tests for ``wd enrich --safe`` (extends ADR 0024 to enrichment).

Safe mode for ``wd enrich`` refuses every provider registered in
:data:`weld.providers.NETWORK_PROVIDERS` before the provider is
instantiated and before any graph mutation happens. The flag must:

* refuse anthropic, openai, and ollama (all currently network-bound);
* never call ``weld.providers.resolve_provider`` when refusing;
* exit ``main`` non-zero and never persist to ``.weld/graph.json``;
* be visible in ``wd enrich --help``;
* emit a stable ``[weld] safe mode: refused enrichment provider '<X>'``
  stderr line so operators can see what was disabled;
* preserve the unsafe-mode default (no flag) behavior.
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

from weld._enrich_safe import (  # noqa: E402
    SafeModeRefusedError,
    refuse_if_network_provider,
    resolve_provider_name,
)
from weld.enrich import enrich, main as enrich_main  # noqa: E402
from weld.graph import Graph  # noqa: E402
from weld.providers import NETWORK_PROVIDERS  # noqa: E402


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


def _read_graph_payload(root: Path) -> dict:
    return json.loads((root / ".weld" / "graph.json").read_text(encoding="utf-8"))


class RefuseHelperTest(unittest.TestCase):
    """``refuse_if_network_provider`` is the contract everything sits on."""

    def test_safe_false_is_noop_for_network_providers(self) -> None:
        # No exception, no stderr.
        buf = io.StringIO()
        with redirect_stderr(buf):
            for name in sorted(NETWORK_PROVIDERS):
                refuse_if_network_provider(name, safe=False)
        self.assertEqual(buf.getvalue(), "")

    def test_safe_true_refuses_every_network_provider(self) -> None:
        for name in sorted(NETWORK_PROVIDERS):
            buf = io.StringIO()
            with redirect_stderr(buf):
                with self.assertRaises(SafeModeRefusedError) as cm:
                    refuse_if_network_provider(name, safe=True)
            stderr = buf.getvalue()
            self.assertIn(
                f"[weld] safe mode: refused enrichment provider '{name}'",
                stderr,
                f"expected refusal line for provider {name!r}, got: {stderr!r}",
            )
            self.assertIn(name, str(cm.exception))

    def test_safe_true_permits_unknown_provider_name(self) -> None:
        # Names not registered as network-bound (e.g. a hypothetical
        # deterministic provider) must pass through. The downstream
        # resolve_provider call is what would reject an unregistered name.
        buf = io.StringIO()
        with redirect_stderr(buf):
            refuse_if_network_provider("weld-deterministic-stub", safe=True)
        self.assertEqual(buf.getvalue(), "")

    def test_resolve_provider_name_uses_env_fallback(self) -> None:
        with mock.patch.dict(
            "os.environ", {"WELD_ENRICH_PROVIDER": "Anthropic"}, clear=False
        ):
            self.assertEqual(resolve_provider_name(None), "anthropic")

    def test_resolve_provider_name_rejects_empty(self) -> None:
        with mock.patch.dict("os.environ", {"WELD_ENRICH_PROVIDER": ""}, clear=False):
            with self.assertRaises(ValueError) as cm:
                resolve_provider_name(None)
        self.assertIn("WELD_ENRICH_PROVIDER", str(cm.exception))


class EnrichApiSafeModeTest(unittest.TestCase):
    """``weld.enrich.enrich`` refuses before instantiating any provider."""

    def test_safe_true_does_not_call_resolve_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph = _write_graph(
                root,
                {
                    "entity:Store": {
                        "type": "entity",
                        "label": "Store",
                        "props": {"file": "store.py"},
                    },
                },
            )
            with mock.patch(
                "weld.enrich.resolve_provider"
            ) as resolve_mock, redirect_stderr(io.StringIO()):
                with self.assertRaises(SafeModeRefusedError):
                    enrich(graph, provider_name="anthropic", safe=True)
            resolve_mock.assert_not_called()

    def test_safe_true_does_not_persist_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph = _write_graph(
                root,
                {
                    "entity:Store": {
                        "type": "entity",
                        "label": "Store",
                        "props": {"file": "store.py"},
                    },
                },
            )
            before = _read_graph_payload(root)
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SafeModeRefusedError):
                    enrich(graph, provider_name="openai", safe=True)
            after = _read_graph_payload(root)
            self.assertEqual(before, after)

    def test_safe_false_default_preserves_existing_dispatch(self) -> None:
        # Without --safe, enrich still resolves the provider as before.
        # We don't actually want to hit the network, so we patch
        # resolve_provider and make it return a stub that records the call.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph = _write_graph(
                root,
                {
                    "entity:Store": {
                        "type": "entity",
                        "label": "Store",
                        "props": {"file": "store.py"},
                    },
                },
            )

            class _StubProvider:
                DEFAULT_MODEL = "stub-model"

                def __init__(self) -> None:
                    self.calls: list[str] = []

                def enrich(self, node, neighbors, *, model):
                    self.calls.append(node["id"])
                    from weld.providers import EnrichmentResult

                    return EnrichmentResult(description="stubbed.")

            stub = _StubProvider()
            with mock.patch(
                "weld.enrich.resolve_provider", return_value=stub
            ) as resolve_mock:
                result = enrich(graph, provider_name="anthropic", safe=False)
            resolve_mock.assert_called_once_with("anthropic")
            self.assertEqual(result["provider"], "anthropic")
            self.assertEqual(stub.calls, ["entity:Store"])


class EnrichCliSafeModeTest(unittest.TestCase):
    """``wd enrich --safe`` exits non-zero and writes the refusal line."""

    def test_main_safe_returns_nonzero_for_anthropic(self) -> None:
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
            out = io.StringIO()
            with redirect_stderr(err), redirect_stdout(out):
                rc = enrich_main(
                    [
                        "--root",
                        str(root),
                        "--provider",
                        "anthropic",
                        "--safe",
                    ]
                )
            self.assertEqual(rc, 1)
            self.assertIn(
                "[weld] safe mode: refused enrichment provider 'anthropic'",
                err.getvalue(),
            )
            # Refusal must not echo the same message twice via the generic
            # "wd enrich: ..." wrapper.
            self.assertNotIn("wd enrich: safe mode refused", err.getvalue())

    def test_main_safe_refuses_openai_and_ollama(self) -> None:
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
            for name in ("openai", "ollama"):
                err = io.StringIO()
                with redirect_stderr(err), redirect_stdout(io.StringIO()):
                    rc = enrich_main(
                        [
                            "--root",
                            str(root),
                            "--provider",
                            name,
                            "--safe",
                        ]
                    )
                self.assertEqual(rc, 1, f"--safe should refuse provider {name!r}")
                self.assertIn(
                    f"[weld] safe mode: refused enrichment provider '{name}'",
                    err.getvalue(),
                )

    def test_main_safe_does_not_modify_graph(self) -> None:
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
            before = _read_graph_payload(root)
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                rc = enrich_main(
                    [
                        "--root",
                        str(root),
                        "--provider",
                        "anthropic",
                        "--safe",
                    ]
                )
            self.assertEqual(rc, 1)
            after = _read_graph_payload(root)
            self.assertEqual(before, after)

    def test_help_mentions_safe_flag(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                enrich_main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        help_text = out.getvalue()
        self.assertIn("--safe", help_text)
        self.assertTrue(
            "network" in help_text.lower() or "untrusted" in help_text.lower(),
            f"--safe help should reference trust boundary: {help_text!r}",
        )

    def test_main_safe_does_not_call_resolve_provider(self) -> None:
        # Defense in depth: even though the unit-level enrich() test already
        # verifies this, exercise it through the CLI path so a refactor that
        # accidentally moves the gate after instantiation gets caught.
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
            with mock.patch(
                "weld.enrich.resolve_provider"
            ) as resolve_mock, redirect_stderr(io.StringIO()), redirect_stdout(
                io.StringIO()
            ):
                rc = enrich_main(
                    [
                        "--root",
                        str(root),
                        "--provider",
                        "anthropic",
                        "--safe",
                    ]
                )
            self.assertEqual(rc, 1)
            resolve_mock.assert_not_called()


class NetworkProvidersRegistryTest(unittest.TestCase):
    """The registry must cover every shipped provider so safe mode is total."""

    def test_every_registered_loader_is_network_bound(self) -> None:
        # If a future deterministic provider is added, this assertion will
        # fire and the author must consciously decide whether to add it to
        # NETWORK_PROVIDERS or leave it permitted under safe mode.
        from weld.providers import _PROVIDER_LOADERS

        self.assertEqual(set(_PROVIDER_LOADERS), set(NETWORK_PROVIDERS))


if __name__ == "__main__":
    unittest.main()
