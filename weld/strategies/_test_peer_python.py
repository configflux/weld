"""Python ``*_test.py`` / ``test_*.py`` resolver for the test_peer strategy.

Encapsulates the Python heuristic so the public dispatcher in
:mod:`weld.strategies.test_peer` can delegate by file extension. Two
conventions are recognised:

* ``<area>_test.py`` -- the Bazel / Go-style trailing suffix used
  throughout this repository.
* ``test_<area>.py`` -- pytest's default ``python_files = test_*.py``
  configuration, used by the majority of pinned Python Tier-1 corpora
  (black / flask / httpx / poetry). Without this branch the
  framework_strategies criterion check reports ``fail`` on every
  pytest-configured corpus even though every other binding criterion
  passes.

Per ADR 0046 (multi-language test-peer edges), each language helper is a
private module so it does not register as its own discovery strategy.
"""

from __future__ import annotations

from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id

#: ``_test.py`` is the Bazel / Go-style trailing-suffix convention used
#: throughout this repository. Helper modules drop the suffix so they
#: are never mistaken for runnable tests.
_TEST_SUFFIX = "_test"

#: ``test_*.py`` is pytest's default ``python_files`` pattern. We treat
#: it as a leading-prefix mirror of ``_TEST_SUFFIX`` and apply the same
#: "base must be non-empty" guard so a stray ``test_.py`` (or bare
#: ``test.py``) does not produce a spurious test node.
_TEST_PREFIX = "test_"

#: Many modules in this repository follow ``<area>_test.py`` while
#: their production peer lives at ``<area>.py``. A smaller subset uses
#: the ``weld_<area>_test.py`` shape against ``<area>.py`` (or
#: ``_<area>.py`` when the production module is private).
_PEER_PREFIX_CANDIDATES: tuple[str, ...] = ("", "weld_")
_PEER_FILENAME_PREFIXES: tuple[str, ...] = ("", "_")


def is_test_file(rel: Path) -> bool:
    """Return True iff *rel* matches a recognised Python test pattern.

    Two conventions are accepted:

    * Trailing suffix ``<area>_test.py`` (Bazel / Go-style).
    * Leading prefix ``test_<area>.py`` (pytest default).

    The bare-stem cases (``_test.py``, ``test_.py``, ``test.py``) are
    rejected so a stray ``test.py`` helper does not produce a spurious
    test node and so the pytest prefix branch cannot match the bare
    ``test_`` token on its own.
    """
    if rel.suffix != ".py":
        return False
    stem = rel.stem
    if stem.endswith(_TEST_SUFFIX) and stem != _TEST_SUFFIX:
        return True
    if (
        stem.startswith(_TEST_PREFIX)
        and len(stem) > len(_TEST_PREFIX)
    ):
        return True
    return False


def candidate_peer_stems(test_stem: str) -> list[str]:
    """Yield candidate production-module stems for a test-file stem.

    Order matches ``_PEER_PREFIX_CANDIDATES``: first the literal
    ``stem_without_suffix_or_prefix``, then variants with leading
    repo-style prefixes stripped. Both ``*_test`` (trailing) and
    ``test_*`` (leading, pytest default) conventions are handled.
    Returns an empty list when the stem does not look like a test
    module under either convention.
    """
    if test_stem.endswith(_TEST_SUFFIX) and test_stem != _TEST_SUFFIX:
        base = test_stem[: -len(_TEST_SUFFIX)]
    elif (
        test_stem.startswith(_TEST_PREFIX)
        and len(test_stem) > len(_TEST_PREFIX)
    ):
        base = test_stem[len(_TEST_PREFIX):]
    else:
        return []
    if not base:
        return []
    candidates: list[str] = [base]
    for prefix in _PEER_PREFIX_CANDIDATES:
        if prefix and base.startswith(prefix):
            stripped = base[len(prefix):]
            if stripped and stripped not in candidates:
                candidates.append(stripped)
    return candidates


#: Directory names that hold non-test production code in idiomatic
#: Python project layouts. The peer-lookup pass scans these next to
#: ``parent.parent`` so that pytest's typical project shapes
#: (``tests/test_x.py`` -> ``src/<pkg>/x.py`` or
#: ``tests/test_x.py`` -> ``<pkg>/x.py``) resolve to a real peer file
#: instead of dropping the edge. The list is intentionally short and
#: deterministic: every name here must be a *production* code root that
#: pinned Python Tier-1 corpora actually use (src layout: black, flask,
#: poetry; package-flat layout: httpx). Adding speculative names here
#: would cost discovery time without raising peer-resolution accuracy.
_PEER_SEARCH_ROOTS: tuple[str, ...] = ("src",)


def _scan_layout_roots(root: Path) -> list[Path]:
    """Return immediate-child production-code directories under *root*.

    Returns directories named in :data:`_PEER_SEARCH_ROOTS` (e.g.
    ``src``) plus every other top-level directory that does not itself
    look like a test, doc, build, or hidden helper tree. The list is
    sorted so the same root produces the same iteration order, which
    keeps :func:`resolve_peer` deterministic across processes.

    Missing directories are silently skipped so a corpus that has only
    a ``src`` layout (no top-level package) and one that has only a
    top-level package (no ``src``) both work without per-layout config.
    """
    discovered: list[Path] = []
    seen: set[str] = set()
    for name in _PEER_SEARCH_ROOTS:
        d = root / name
        if d.is_dir():
            discovered.append(d)
            seen.add(name)
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return discovered
    for entry in entries:
        if not entry.is_dir():
            continue
        if entry.name in seen:
            continue
        if entry.name.startswith(".") or entry.name.startswith("_"):
            continue
        # Skip directories we know contain tests / fixtures / build
        # artefacts. These are the directories whose presence would
        # otherwise produce a spurious ``tests/test_x.py`` ->
        # ``tests/x.py`` match when ``x.py`` is itself a fixture.
        if entry.name in {
            "tests", "test", "docs", "examples", "scripts",
            "build", "dist", "node_modules", "venv", "env",
        }:
            continue
        discovered.append(entry)
        seen.add(entry.name)
    return discovered


def resolve_peer(
    root: Path,
    rel_path: Path,
) -> tuple[str, str] | None:
    """Resolve *rel_path* to ``(peer_id, peer_rel_posix)`` when possible.

    The resolver walks each candidate stem (``test_foo`` ->
    ``["foo"]``; ``weld_foo_test`` -> ``["weld_foo", "foo"]``) against
    three layouts in order so the most-specific shape wins:

    1. **Grandparent** of the test file (``weld/tests/foo_test.py`` ->
       ``weld/foo.py``). Matches Bazel / Go-style layouts and remains
       the cheapest probe.
    2. **src layout** (``tests/test_foo.py`` ->
       ``src/<pkg>/foo.py``). Iterates the immediate children of
       ``<root>/src`` so a pytest project with src layout (black,
       flask, poetry) resolves the peer.
    3. **Package-flat layout** (``tests/test_foo.py`` ->
       ``<pkg>/foo.py``). Iterates the immediate children of ``root``
       that are not test/dev/hidden trees. Covers httpx-style projects
       where the production package sits at the repo root.

    Returns ``None`` when no candidate exists on disk so the caller
    skips edge emission instead of writing a dangling edge that
    :func:`weld._discover_postprocess._clean_and_dedup_edges` would
    prune anyway.
    """
    stems = candidate_peer_stems(rel_path.stem)
    if not stems:
        return None

    # Layer 1 -- grandparent (legacy Bazel layout). Same probe as the
    # pre-pytest resolver so existing fixtures keep matching.
    parent = rel_path.parent.parent
    for stem_guess in stems:
        for fn_prefix in _PEER_FILENAME_PREFIXES:
            filename = f"{fn_prefix}{stem_guess}.py"
            candidate = root / parent / filename
            if candidate.is_file():
                peer_rel = (parent / filename).as_posix()
                return _canonical_file_id(peer_rel), peer_rel

    # Layers 2 / 3 -- production-code roots discovered next to the
    # test directory. The walk is shallow (one level per root) so the
    # cost stays bounded on large monorepos while still resolving the
    # pinned Tier-1 src-layout corpora (black, flask, poetry) and the
    # package-flat httpx layout.
    layout_roots = _scan_layout_roots(root)
    for stem_guess in stems:
        for fn_prefix in _PEER_FILENAME_PREFIXES:
            filename = f"{fn_prefix}{stem_guess}.py"
            for layout_root in layout_roots:
                for pkg_dir in _iter_layout_package_dirs(layout_root):
                    candidate = pkg_dir / filename
                    if candidate.is_file():
                        peer_rel = candidate.relative_to(root).as_posix()
                        return _canonical_file_id(peer_rel), peer_rel
    return None


def _iter_layout_package_dirs(layout_root: Path) -> list[Path]:
    """Return the directories under *layout_root* where peers may live.

    For ``<root>/src`` we yield every immediate child (``src/black``,
    ``src/flask``, ``src/poetry``). For a top-level package directory
    (``<root>/httpx``) we yield the directory itself so
    ``httpx/_api.py`` resolves. The list is sorted for deterministic
    iteration.

    Missing or unreadable directories silently yield ``[]`` -- this
    helper is on the resolve-peer hot path and any exception would
    surface as a spurious "no peer" rather than a discovery crash.
    """
    if layout_root.name == "src":
        try:
            return sorted(p for p in layout_root.iterdir() if p.is_dir())
        except OSError:
            return []
    return [layout_root]


def first_candidate_peer_id(rel_path: Path) -> str | None:
    """Return the *first* candidate peer node id without disk lookup.

    Provenance-only helper that mirrors the pre-multi-language
    ``_peer_node_id`` shape: it never inspects the filesystem and
    therefore only returns the leading candidate. The actual edge
    emission goes through :func:`resolve_peer` so a real on-disk match
    is required.
    """
    candidates = candidate_peer_stems(rel_path.stem)
    if not candidates:
        return None
    parent = rel_path.parent.parent.as_posix()
    if parent and parent != ".":
        return _canonical_file_id(f"{parent}/{candidates[0]}")
    return _canonical_file_id(candidates[0])
