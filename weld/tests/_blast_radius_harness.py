"""Helpers for the blast-radius fixture harness (ADR 0047).

Split out of ``weld_blast_radius_fixtures_test.py`` so the test file
itself stays under the 400-line cap. The contract is intentionally
narrow: discover fixture directories, copy them into a writable
scratch tree (de-mangling ``.in`` shadow files), normalise the graph
output for the determinism contract, and produce diff snippets when a
golden mismatches.

Public surface (re-exported by the test module):

* :func:`fixtures_dir` - path to the on-disk fixture root.
* :func:`discover_fixture_names` - sorted list of fixture dirs.
* :func:`copy_fixture` - copy with ``.in`` rename.
* :func:`canonical_graph_text` - normalised + serialised graph bytes.
* :func:`impact_canonical_text` - canonical impact envelope bytes.
* :func:`impact_envelope` - run :func:`impact` on a graph dict.
* :func:`seed_pairs` - list of ``(slug, golden_path)`` for a fixture.
* :func:`seed_from_golden` - extract ``(seed, depth)`` from a golden.
* :func:`diff_snippet` - small unified-diff text for failure messages.
* :func:`is_regen_mode` - check the regen env var.
* :func:`regen_env_var` - the env var name (single source of truth).
"""

from __future__ import annotations

import difflib
import json
import os
import shutil
from pathlib import Path

from weld.discover import discover  # noqa: F401 -- re-export for callers
from weld.graph import Graph
from weld.impact_core import impact
from weld.serializer import dumps_graph

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_FIXTURES_DIR = (
    _REPO_ROOT / "weld" / "tests" / "fixtures" / "blast_radius"
)
_REGEN_ENV_VAR = "REGEN_BLAST_RADIUS_GOLDENS"
_DIFF_LINES = 30
# Files that exist in fixture trees under a ``.in`` suffix to keep them
# out of the parent Bazel build, but must be renamed back at copy time
# so weld discovery sees the real filename.
_RENAME_SUFFIX = ".in"
_RENAME_TARGETS = ("MODULE.bazel", "BUILD.bazel", "WORKSPACE")


def fixtures_dir() -> Path:
    return _FIXTURES_DIR


def regen_env_var() -> str:
    return _REGEN_ENV_VAR


def is_regen_mode() -> bool:
    """Return True when the harness should rewrite goldens."""
    return os.environ.get(_REGEN_ENV_VAR, "").strip() not in (
        "", "0", "false", "False",
    )


def discover_fixture_names() -> list[str]:
    """List fixture directories that look complete enough to test.

    A directory qualifies if it has ``.weld/discover.yaml`` and an
    ``expected/`` folder. Regen mode is more forgiving and includes
    fixtures that are missing the ``expected/`` folder so a brand-new
    fixture can be brought up with one regen pass.
    """
    if not _FIXTURES_DIR.is_dir():
        return []
    names: list[str] = []
    for entry in sorted(_FIXTURES_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("_") or entry.name == "__pycache__":
            continue
        if not (entry / ".weld" / "discover.yaml").is_file():
            continue
        if not (entry / "expected").is_dir() and not is_regen_mode():
            continue
        names.append(entry.name)
    return names


def copy_fixture(src_root: Path, dst_root: Path) -> None:
    """Copy *src_root* to *dst_root* and de-mangle ``.in`` files.

    Bazel ``data`` deps are read-only inside the sandbox; weld
    discovery writes ``.weld/graph.json`` and other state alongside
    the source tree, so we always work from a writable scratch copy.
    The rename pass converts ``MODULE.bazel.in`` -> ``MODULE.bazel``
    (and analogous ``BUILD.bazel`` / ``WORKSPACE`` shapes) so those
    files exist on disk where weld expects them but never trip the
    host Bazel build during repo-wide globs.
    """
    shutil.copytree(src_root, dst_root)
    for path in dst_root.rglob("*" + _RENAME_SUFFIX):
        stem = path.name[: -len(_RENAME_SUFFIX)]
        if stem in _RENAME_TARGETS:
            path.rename(path.with_name(stem))


def normalise(graph: dict) -> dict:
    """Strip volatile ``meta`` fields per ADR 0012 §1 and ADR 0047.

    ``meta.updated_at`` is wall-clock-stamped per run.
    ``meta.git_sha`` reflects the host worktree, irrelevant to the
    fixture contract. ``meta.discovered_from`` may include
    sandbox-specific scratch path fragments.
    """
    clone = json.loads(json.dumps(graph))
    meta = clone.get("meta")
    if isinstance(meta, dict):
        meta.pop("updated_at", None)
        meta.pop("generated_at", None)
        meta.pop("git_sha", None)
        meta.pop("discovered_from", None)
    return clone


def canonical_graph_text(graph: dict) -> str:
    """Return canonical JSON text for *graph* with volatile fields stripped."""
    return dumps_graph(normalise(graph))


def impact_envelope(graph_dict: dict, seed: str, depth: int) -> dict:
    """Run :func:`impact` against *graph_dict* using a fresh :class:`Graph`.

    The seed string may be a node id (``file:foo``) or a repo-relative
    path the impact engine resolves itself.
    """
    g = Graph(_REPO_ROOT)
    # The Graph constructor stamps ``self._data`` to a default empty
    # graph; we replace the in-memory state so we exercise the same
    # ``impact()`` path the CLI / MCP wrapper takes without writing a
    # graph.json roundtrip.
    g._data = json.loads(json.dumps(graph_dict))  # noqa: SLF001 -- harness owns Graph
    return impact(g, target=seed, depth=depth, stale_graph=False)


def impact_canonical_text(envelope: dict) -> str:
    """Canonicalise an impact envelope so byte-identity is well-defined."""
    return json.dumps(
        envelope, sort_keys=True, indent=2, ensure_ascii=False,
    ) + "\n"


def seed_pairs(expected_dir: Path) -> list[tuple[str, Path]]:
    """Return ``(slug, golden_path)`` pairs for each ``impact_*.json`` golden.

    The slug is the filename stem with the ``impact_`` prefix removed.
    The actual seed string is recorded inside the golden under
    ``target.input``; the slug is for diagnostics only.
    """
    pairs: list[tuple[str, Path]] = []
    for golden in sorted(expected_dir.glob("impact_*.json")):
        slug = golden.stem[len("impact_"):]
        pairs.append((slug, golden))
    return pairs


def seed_from_golden(golden_path: Path) -> tuple[str, int]:
    """Extract ``(seed_string, depth)`` from an impact golden.

    The harness invokes :func:`impact` with the same target/depth pair
    that produced the golden so the comparison is well-defined.
    """
    payload = json.loads(golden_path.read_text(encoding="utf-8"))
    target_input = payload.get("target", {}).get("input", "")
    depth = int(payload.get("depth", 3))
    if isinstance(target_input, list):
        # impact() supports list-form targets via the ``seeds`` kwarg;
        # the v1 fixture goldens stick to single-string targets.
        raise ValueError(
            f"Fixture golden {golden_path} uses a list-form target.input; "
            "the harness only supports single-string targets in v1."
        )
    return str(target_input), depth


def diff_snippet(actual: str, expected: str, fixture: str, label: str) -> str:
    """Return a small unified-diff snippet for the failure message.

    The first ~30 diff lines are usually enough to identify which node
    or edge changed; we add a hint when more lines were suppressed.
    """
    diff = list(difflib.unified_diff(
        expected.splitlines(keepends=True),
        actual.splitlines(keepends=True),
        fromfile=f"expected/{label}",
        tofile=f"actual/{label}",
        n=2,
    ))
    if not diff:
        return f"<{fixture}/{label}: no textual diff -- check whitespace>"
    snippet = "".join(diff[:_DIFF_LINES])
    if len(diff) > _DIFF_LINES:
        snippet += (
            f"... ({len(diff) - _DIFF_LINES} more diff lines suppressed)\n"
        )
    return snippet
