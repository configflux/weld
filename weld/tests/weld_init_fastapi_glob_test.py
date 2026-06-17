"""Regression test for the ``wd init`` fastapi-glob heuristic.

Surfaced 2026-06-12 by a polyrepo dogfood gap: after a federated refresh
``wd discover --recurse`` rebuilt a child's graph *without* its FastAPI
``route:`` nodes, while a standalone ``wd discover --full <child>``
extracted them correctly.

Root cause was ``weld.init._add_framework_sources`` choosing the FastAPI
source glob from ``detect_frameworks``'s per-framework *detection path*.
For a conventional ``services/<name>/{src/app.py, src/routers/*.py}``
layout the FastAPI app is detected in the *app-instantiation* file
(``src/app.py`` — the module that runs ``from fastapi import FastAPI``
and ``app = FastAPI()``), which falls under the ``src/*.py`` python_glob.
But ``APIRouter`` declarations live one level down in ``src/routers/``,
which ``src/*.py`` does not cover, so the ``fastapi`` strategy globbed a
directory with no router and emitted zero routes.

The detection-path preference (added for pallets/flask) is the *wrong*
signal for FastAPI here because the app file and the router files are in
different directories. Fix: ``_add_framework_sources`` prefers, for the
``fastapi`` strategy only, a python_glob whose directory segment names a
FastAPI router location (``routers``/``router``) when one exists; the
detection-path / keyword heuristics still apply when no such glob exists.

The test mimics that polyrepo child layout (``src/*.py`` and
``src/routers/*.py`` python_globs, FastAPI detected at ``src/app.py``),
runs ``_add_framework_sources``, and asserts the chosen glob covers the
``routers`` directory — *not* the app-file ``src/*.py`` glob.
"""

from __future__ import annotations

import unittest


from weld.init import _add_framework_sources  # noqa: E402


def _fastapi_entry_glob(sources: list[str]) -> str:
    """Pull the ``glob:`` value from the (single) fastapi strategy entry."""
    entries = [s for s in sources if "strategy: fastapi" in s]
    assert len(entries) == 1, (
        f"Expected exactly one fastapi strategy entry, got {len(entries)}:"
        f" {entries}"
    )
    for line in entries[0].splitlines():
        stripped = line.strip()
        if stripped.startswith("- glob:"):
            return stripped.split('"', 2)[1]
    raise AssertionError(f"fastapi entry has no glob: line: {entries[0]}")


class FastapiGlobHeuristicTest(unittest.TestCase):
    """Pinned coverage for ``_add_framework_sources`` FastAPI glob choice."""

    # Mirrors ``find_python_glob_roots`` output for the polyrepo demo's
    # ``services/auth`` child: ``src/*.py`` (app.py + package) and
    # ``src/routers/*.py`` (the actual APIRouter declarations).
    _SERVICE_PYTHON_GLOBS: list[str] = ["src/*.py", "src/routers/*.py"]

    def test_fastapi_strategy_lands_on_routers_glob(self) -> None:
        """FastAPI app detected in ``src/app.py`` but routers in
        ``src/routers/`` -> the fastapi strategy must use the
        ``src/routers/*.py`` glob, not the app-file ``src/*.py`` glob.

        ``src/*.py`` only matches ``app.py`` (which has no ``APIRouter``),
        so wiring the fastapi strategy there yields zero routes and the
        cross-repo ``service_graph`` resolver finds no endpoint to match.
        This is the polyrepo dogfood-gap regression.
        """
        sources: list[str] = []
        frameworks = [("FastAPI", "fastapi", "src/app.py")]
        _add_framework_sources(
            sources, frameworks, self._SERVICE_PYTHON_GLOBS,
        )
        chosen = _fastapi_entry_glob(sources)
        self.assertNotEqual(
            chosen, "src/*.py",
            f"fastapi strategy must not land on the app-file glob src/*.py "
            f"when a routers directory exists; got entry: {sources}",
        )
        self.assertEqual(
            chosen, "src/routers/*.py",
            f"fastapi strategy must land on the routers-directory glob; "
            f"got {chosen!r}, entry: {sources}",
        )

    def test_fastapi_strategy_falls_back_without_routers_dir(self) -> None:
        """When no routers-directory glob exists, the existing
        detection-path / keyword heuristic still applies.

        A flat ``app/`` layout where routes are declared in the same
        directory as the app keeps the detection-path glob (``app/*.py``),
        so this fix does not perturb the common single-directory case.
        """
        sources: list[str] = []
        python_globs = ["app/*.py", "tests/*.py"]
        frameworks = [("FastAPI", "fastapi", "app/main.py")]
        _add_framework_sources(sources, frameworks, python_globs)
        chosen = _fastapi_entry_glob(sources)
        self.assertEqual(
            chosen, "app/*.py",
            f"without a routers glob the detection-path glob must win; "
            f"got {chosen!r}, entry: {sources}",
        )

    def test_fastapi_routers_glob_beats_detection_path_glob(self) -> None:
        """When both a routers-directory glob and an app-file
        detection-path glob are present, the routers glob wins.

        The ``fastapi`` strategy extracts ``APIRouter`` declarations; its
        correct target is always the router location. Pin the priority so
        a future change cannot quietly route it back to the app-file glob.
        The app is detected at ``api/server.py`` (covered by ``api/*.py``)
        but the routers live under ``api/routers/``.
        """
        sources: list[str] = []
        python_globs = ["api/*.py", "api/routers/*.py"]
        frameworks = [("FastAPI", "fastapi", "api/server.py")]
        _add_framework_sources(sources, frameworks, python_globs)
        chosen = _fastapi_entry_glob(sources)
        self.assertEqual(
            chosen, "api/routers/*.py",
            f"the routers-directory glob must outrank the app-file glob "
            f"for fastapi; got {chosen!r}, entry: {sources}",
        )


if __name__ == "__main__":
    unittest.main()
