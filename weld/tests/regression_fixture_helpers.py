"""Shared synthetic-fixture helpers for the regression test suites.

The two whole-codebase regression tests
(``weld_artifact_class_regression_test`` and
``weld_graph_integrity_regression_test``) used to be gated on the host
repository having a populated ``.weld/discover.yaml``. In standalone
environments without that file the tests silently skipped — letting
discovery breakage ship undetected.

This module provides a canonical synthetic fixture (a tiny
``discover.yaml`` plus two Python modules and one markdown document) so
the regression suites can run unconditionally against a known graph.
The fixture is intentionally minimal: just enough to exercise two
strategies producing two declared node types.

Both regression test files import :func:`build_synthetic_fixture` and
:class:`SyntheticGraphMixin`; concrete assertions live in the test
files so the helper module stays a pure fixture provider. Health of the
helper itself is covered transitively: any breakage in
``build_synthetic_fixture`` or :class:`SyntheticGraphMixin` causes every
``Synthetic*Test`` in the regression suites to fail at ``setUpClass``,
which surfaces as a hard test failure rather than a silent skip.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from weld.discover import discover
from weld.strategies.concept_from_bd import DOGFOOD_GAP_LABEL

# Two configured strategies, each producing one declared node type.
# The markdown strategy globs against ``Path(pattern).parent`` so the
# glob must use a literal directory followed by ``*.md`` (not ``**``).
# The python_module strategy walks recursively so ``src/**/*.py`` is
# fine. Fixture files are placed accordingly.
SYNTH_DISCOVER_YAML = (
    "sources:\n"
    "  - strategy: python_module\n"
    "    glob: src/**/*.py\n"
    "    type: file\n"
    "  - strategy: markdown\n"
    "    glob: docs/*.md\n"
    "    type: doc\n"
    "    id_prefix: doc:docs\n"
)

# Two python modules (one importing the other so the file graph has
# real shape if a callgraph strategy is ever layered in) plus one md.
SYNTH_FILES = {
    "src/mod_a.py": "def alpha():\n    return 1\n",
    "src/mod_b.py": (
        "from src.mod_a import alpha\n\n"
        "def beta():\n    return alpha() + 1\n"
    ),
    "docs/intro.md": "# Intro\n\nA fixture document.\n",
}

SYNTH_NODE_TYPES = frozenset({"file", "doc"})
SYNTH_STRATEGIES = frozenset({"python_module", "markdown"})
_ACTIVE_ISSUE_STATUSES = frozenset({"open", "in_progress", "ready", "blocked"})


def build_synthetic_fixture(root: Path) -> None:
    """Materialise the canonical synthetic fixture under ``root``.

    Creates ``root/.weld/discover.yaml`` and the small file tree the
    fixture's globs expect. Idempotent: re-running over an existing
    fixture root simply rewrites the files.
    """
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(
        SYNTH_DISCOVER_YAML, encoding="utf-8"
    )
    for rel, content in SYNTH_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def source_should_require_output(root: Path, source: dict) -> bool:
    """Return whether a host configured source must produce nodes."""
    if source.get("strategy") != "concept_from_bd":
        return True
    rel = source.get("path")
    if not isinstance(rel, str) or not rel:
        return False
    return _has_active_dogfood_gap(root / rel)


def _has_active_dogfood_gap(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            issue = json.loads(line)
        except json.JSONDecodeError:
            continue
        labels = issue.get("labels") or []
        status = (issue.get("status") or "").lower()
        if DOGFOOD_GAP_LABEL in labels and status in _ACTIVE_ISSUE_STATUSES:
            return True
    return False


class SyntheticGraphMixin:
    """Mixin: provides ``cls.graph`` discovered from a fresh synthetic root.

    ``setUpClass`` materialises the fixture in a temp directory and runs
    discovery once; the resulting graph is cached on the class so each
    test method asserts against the same object. ``tearDownClass``
    removes the temp directory. The fixture is small enough that
    discovery is sub-second on any host.
    """

    _SYNTH_ROOT: Path
    graph: dict

    # Subclasses can override to scope the temp prefix.
    SYNTH_PREFIX = "weld-regression-synth-"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()  # type: ignore[misc]
        cls._SYNTH_ROOT = Path(tempfile.mkdtemp(prefix=cls.SYNTH_PREFIX))
        build_synthetic_fixture(cls._SYNTH_ROOT)
        cls.graph = discover(cls._SYNTH_ROOT, incremental=False)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._SYNTH_ROOT, ignore_errors=True)
        super().tearDownClass()  # type: ignore[misc]

