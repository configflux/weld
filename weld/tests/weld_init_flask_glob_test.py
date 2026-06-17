"""Regression test for the ``wd init`` flask-glob heuristic (bd et6o).

Surfaced 2026-05-17 by the Python Tier-1 re-measurement (bd 1w7m):
pallets/flask kept landing on tier=preview because the auto-generated
``.weld/discover.yaml`` declared the flask strategy on ``docs/*.py``
instead of the directory where actual Flask user-app code lives.

Root cause was ``weld.init._add_framework_sources`` calling
``_find_matching_glob(python_globs, ("app", "blueprints", "views",
"routes", "api"))``. None of those keywords match the pallets/flask
layout's top-level python source directories (``docs``, ``examples``,
``src/flask``, ``tests``); the fallback was ``python_globs[0]`` which
is alphabetically ``docs/*.py``. The flask strategy on ``docs/*.py``
emitted zero routes because no .py file under ``docs/`` imports flask,
keeping criterion 3 ``route_count == 0`` and the verdict at preview.

Fix: ``_add_framework_sources`` now consults ``detect_frameworks``'s
per-framework detection path first, finding the ``python_glob`` whose
pattern covers that path. The keyword heuristic and ``python_globs[0]``
fallback only apply when no detection path is available. For
``pallets/flask``, Flask is detected in
``examples/celery/src/task_app/__init__.py`` -- which falls under the
``examples/**/*.py`` python_glob -- so the flask strategy now lands
where actual ``from flask import`` user-app code lives.

The test mimics the flask repo layout (multiple python_globs incl.
``docs/*.py`` and ``examples/**/*.py``), runs ``_add_framework_sources``
with the detection path, and asserts the chosen glob covers the
detection path -- *not* ``docs/*.py``.
"""

from __future__ import annotations

import unittest


from weld.init import _add_framework_sources  # noqa: E402


def _flask_entry_glob(sources: list[str]) -> str:
    """Pull the ``glob:`` value from the (single) flask strategy entry."""
    flask_entries = [s for s in sources if "strategy: flask" in s]
    assert len(flask_entries) == 1, (
        f"Expected exactly one flask strategy entry, got {len(flask_entries)}:"
        f" {flask_entries}"
    )
    for line in flask_entries[0].splitlines():
        stripped = line.strip()
        if stripped.startswith("- glob:"):
            return stripped.split('"', 2)[1]
    raise AssertionError(
        f"flask entry has no glob: line: {flask_entries[0]}",
    )


class FlaskGlobHeuristicTest(unittest.TestCase):
    """Pinned coverage for ``_add_framework_sources`` Flask glob choice."""

    # Mimics ``find_python_glob_roots`` output for pallets/flask at
    # the pinned SHA: docs/, examples/ (deep), src/flask/ + subdirs,
    # tests/ (deep). Alphabetical, exactly what production emits.
    _FLASK_REPO_PYTHON_GLOBS: list[str] = [
        "docs/*.py",
        "examples/**/*.py",
        "src/flask/*.py",
        "src/flask/json/*.py",
        "src/flask/sansio/*.py",
        "tests/**/*.py",
    ]

    def test_flask_strategy_lands_on_detection_path_glob(self) -> None:
        """When ``detect_frameworks`` returned Flask at
        ``examples/celery/src/task_app/__init__.py``, the flask strategy
        entry must use the python_glob that covers that path
        (``examples/**/*.py``), not the alphabetical ``docs/*.py``
        fallback.

        This is the production case for pallets/flask 3.1.3.
        """
        sources: list[str] = []
        frameworks = [
            ("Flask", "flask", "examples/celery/src/task_app/__init__.py"),
        ]
        _add_framework_sources(
            sources, frameworks, self._FLASK_REPO_PYTHON_GLOBS,
        )
        chosen_glob = _flask_entry_glob(sources)
        self.assertNotEqual(
            chosen_glob, "docs/*.py",
            f"flask strategy must not land on docs/*.py for pallets/flask "
            f"layout (regression of bd et6o); got entry: {sources}",
        )
        self.assertEqual(
            chosen_glob, "examples/**/*.py",
            f"flask strategy must land on the python_glob that covers the "
            f"detection path examples/celery/src/task_app/__init__.py; "
            f"got {chosen_glob!r}, entry: {sources}",
        )

    def test_flask_strategy_falls_back_to_keyword_match_without_path(
        self,
    ) -> None:
        """A typical user Flask app (``app/``, ``app/blueprints/``)
        still matches via the keyword heuristic even when
        ``detect_frameworks`` did not record a path.

        Belt-and-suspenders: callers that build ``frameworks`` without
        a path should still get a sensible glob choice. The keyword
        ``app`` matches ``app/*.py`` first.
        """
        sources: list[str] = []
        python_globs = [
            "app/*.py", "app/blueprints/*.py", "tests/*.py",
        ]
        frameworks = [("Flask", "flask", "")]
        _add_framework_sources(sources, frameworks, python_globs)
        chosen_glob = _flask_entry_glob(sources)
        self.assertIn(
            chosen_glob, {"app/*.py", "app/blueprints/*.py"},
            f"keyword heuristic must still pick an app/blueprint glob "
            f"when no detection path is supplied; got {chosen_glob!r}",
        )

    def test_flask_strategy_path_match_beats_keyword_match(self) -> None:
        """When both signals are present the detection path wins.

        Detection paths are derived from actually-read source files;
        the keyword heuristic is a fuzzy directory-name match. The
        detection path is strictly more reliable, so it must win.
        Pin this here so a future change cannot quietly swap the
        priority order back.
        """
        sources: list[str] = []
        python_globs = ["app/*.py", "examples/**/*.py"]
        # Detection path falls under ``examples/**/*.py`` even though
        # the keyword ``app`` matches ``app/*.py`` first. The fix MUST
        # prefer the detection-path glob.
        frameworks = [("Flask", "flask", "examples/myapp/views.py")]
        _add_framework_sources(sources, frameworks, python_globs)
        chosen_glob = _flask_entry_glob(sources)
        self.assertEqual(
            chosen_glob, "examples/**/*.py",
            f"detection-path glob must outrank the keyword match; got "
            f"{chosen_glob!r}, entry: {sources}",
        )


if __name__ == "__main__":
    unittest.main()
