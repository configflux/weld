"""Regression test for the ``wd init`` http_client source generation.

Surfaced 2026-06-12 (bd 0ssj): ``wd init``'s framework->source mapping
(``weld._init_framework_sources._add_framework_sources``) emitted sources
for FastAPI/Flask/SQLAlchemy/Pydantic but never an ``http_client`` source.
A generated child ``discover.yaml`` therefore omitted outbound-HTTP-call
extraction entirely, so a polyrepo ``caller`` child produced no
``rpc:http:out`` node and the cross-repo ``service_graph`` resolver had no
client call to match (0 ``cross_repo:calls`` edges). The shipped
hand-written ``examples/05-polyrepo/services/api/.weld/discover.yaml`` has
the ``http_client`` source; a generated one did not.

Fix: ``detect_frameworks`` reports a synthetic ``HTTPClient`` framework
(strategy ``http_client``) for files importing a known HTTP client library,
and ``_add_framework_sources`` emits one ``http_client`` source entry with
``type: file`` on the python_glob covering the detection path. The chosen
shape mirrors the hand-written reference: ``type: file`` /
``strategy: http_client``.

These tests pin the source-entry side (the detection side is covered in
``weld_init_detect_frameworks_bound_test.py``).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


from weld.init import _add_framework_sources  # noqa: E402
from weld.init_detect import detect_frameworks, scan_files  # noqa: E402


def _http_client_entries(sources: list[str]) -> list[str]:
    """Return the (expected single) http_client strategy entries."""
    return [s for s in sources if "strategy: http_client" in s]


def _entry_field(entry: str, prefix: str) -> str:
    """Pull the quoted/bare value of the first ``- <prefix>:`` line."""
    for line in entry.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            tail = stripped.split(":", 1)[1].strip()
            return tail.split('"', 2)[1] if '"' in tail else tail
    raise AssertionError(f"entry has no {prefix!r} line: {entry}")


class HttpClientSourceEntryTest(unittest.TestCase):
    """Pinned coverage for ``_add_framework_sources`` http_client emission."""

    def test_emits_http_client_source_on_detection_path_glob(self) -> None:
        """An HTTPClient framework detected under ``src/`` emits exactly one
        ``http_client`` source entry whose glob covers the call sites and
        whose ``type`` is ``file`` (mirroring the hand-written polyrepo
        example), not ``route``/``entity``."""
        sources: list[str] = []
        python_globs = ["src/**/*.py"]
        frameworks = [("HTTPClient", "http_client", "src/client.py")]
        _add_framework_sources(sources, frameworks, python_globs)

        entries = _http_client_entries(sources)
        self.assertEqual(
            len(entries), 1,
            f"expected exactly one http_client entry; got {entries}",
        )
        self.assertEqual(_entry_field(entries[0], "- glob:"), "src/**/*.py")
        self.assertEqual(_entry_field(entries[0], "type:"), "file")
        self.assertEqual(_entry_field(entries[0], "strategy:"), "http_client")

    def test_http_client_glob_prefers_detection_path(self) -> None:
        """With several python_globs, the http_client entry lands on the
        glob covering the file where the client import was detected -- not
        an unrelated glob."""
        sources: list[str] = []
        python_globs = ["app/*.py", "app/clients/*.py", "tests/*.py"]
        frameworks = [("HTTPClient", "http_client", "app/clients/gateway.py")]
        _add_framework_sources(sources, frameworks, python_globs)

        entries = _http_client_entries(sources)
        self.assertEqual(len(entries), 1)
        self.assertEqual(
            _entry_field(entries[0], "- glob:"), "app/clients/*.py",
            f"http_client glob must cover the detection path; entry: {entries}",
        )

    def test_no_http_client_entry_when_not_detected(self) -> None:
        """Without an HTTPClient framework, no http_client source is added.

        Guards against the entry being emitted unconditionally, which would
        wire the (cheap but pointless) http_client strategy onto projects
        with no outbound HTTP client at all.
        """
        sources: list[str] = []
        frameworks = [("FastAPI", "fastapi", "src/app.py")]
        _add_framework_sources(sources, frameworks, ["src/**/*.py"])
        self.assertEqual(_http_client_entries(sources), [])

    def test_http_client_falls_back_to_first_glob(self) -> None:
        """When no python_glob covers the detection path, the first glob is
        used as a (commented) fallback rather than crashing or skipping the
        entry -- consistent with how route strategies degrade."""
        sources: list[str] = []
        # Detection path is outside any of the supplied globs.
        python_globs = ["pkg/*.py"]
        frameworks = [("HTTPClient", "http_client", "scripts/oneoff.py")]
        _add_framework_sources(sources, frameworks, python_globs)

        entries = _http_client_entries(sources)
        self.assertEqual(len(entries), 1)
        self.assertEqual(_entry_field(entries[0], "- glob:"), "pkg/*.py")


class HttpClientDetectionTest(unittest.TestCase):
    """``detect_frameworks`` reports the synthetic HTTPClient framework."""

    def test_detects_http_client_via_requests_import(self) -> None:
        """A file importing ``requests`` registers ``HTTPClient`` mapped to
        the ``http_client`` strategy with the file's relative path, so
        ``wd init`` can wire outbound-HTTP-call extraction (bd 0ssj)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "gateway.py").write_text(
                "import requests\n"
                "def ping():\n"
                "    return requests.get('https://example.com/health')\n",
            )
            detected = detect_frameworks(root, scan_files(root))
            by_fw = {fw: (s, p) for fw, s, p in detected}

        self.assertIn("HTTPClient", by_fw)
        self.assertEqual(by_fw["HTTPClient"], ("http_client", "gateway.py"))

    def test_detects_http_client_via_httpx_import(self) -> None:
        """``httpx`` is the other strategy-supported root; ``from httpx``
        must also trigger detection. Pins both library roots so a future
        change to ``_HTTP_LIBRARY_ROOTS`` keeps init and extraction aligned.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "client.py").write_text("from httpx import Client\n")
            detected = detect_frameworks(root, scan_files(root))

        self.assertIn("HTTPClient", {fw for fw, _, _ in detected})

    def test_no_http_client_without_client_import(self) -> None:
        """A plain Python file with no HTTP client import must NOT register
        HTTPClient, so projects with no outbound client get no http_client
        source. Pins the negative against an unconditional emission."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pure.py").write_text("x = 1\n")
            detected = detect_frameworks(root, scan_files(root))

        self.assertNotIn("HTTPClient", {fw for fw, _, _ in detected})


if __name__ == "__main__":
    unittest.main()
