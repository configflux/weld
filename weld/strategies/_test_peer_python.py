"""Python ``*_test.py`` resolver for the test_peer strategy.

Encapsulates the legacy Python heuristic so the public dispatcher in
:mod:`weld.strategies.test_peer` can delegate by file extension. The
behavior here is preserved verbatim from the pre-multi-language version
of ``test_peer.py``: the trailing ``_test`` suffix is dropped, the
``weld_`` prefix is optionally stripped, and a private peer
(``_<name>.py``) is tried as a filename fallback.

Per ADR 0046 (multi-language test-peer edges), each language helper is a
private module so it does not register as its own discovery strategy.
"""

from __future__ import annotations

from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id

#: ``_test.py`` is the canonical Bazel/pytest naming convention used
#: throughout this repository. Helper modules drop the suffix so they
#: are never mistaken for runnable tests.
_TEST_SUFFIX = "_test"

#: Many modules in this repository follow ``<area>_test.py`` while
#: their production peer lives at ``<area>.py``. A smaller subset uses
#: the ``weld_<area>_test.py`` shape against ``<area>.py`` (or
#: ``_<area>.py`` when the production module is private).
_PEER_PREFIX_CANDIDATES: tuple[str, ...] = ("", "weld_")
_PEER_FILENAME_PREFIXES: tuple[str, ...] = ("", "_")


def is_test_file(rel: Path) -> bool:
    """Return True iff *rel* matches the Python ``*_test.py`` pattern."""
    if rel.suffix != ".py":
        return False
    stem = rel.stem
    return stem.endswith(_TEST_SUFFIX) and stem != _TEST_SUFFIX


def candidate_peer_stems(test_stem: str) -> list[str]:
    """Yield candidate production-module stems for a ``*_test.py`` stem.

    Order matches ``_PEER_PREFIX_CANDIDATES``: first the literal
    ``stem_without_suffix``, then variants with leading repo-style
    prefixes stripped. Returns an empty list when the stem does not
    look like a test module.
    """
    if not test_stem.endswith(_TEST_SUFFIX) or test_stem == _TEST_SUFFIX:
        return []
    base = test_stem[: -len(_TEST_SUFFIX)]
    if not base:
        return []
    candidates: list[str] = [base]
    for prefix in _PEER_PREFIX_CANDIDATES:
        if prefix and base.startswith(prefix):
            stripped = base[len(prefix):]
            if stripped and stripped not in candidates:
                candidates.append(stripped)
    return candidates


def resolve_peer(
    root: Path,
    rel_path: Path,
) -> tuple[str, str] | None:
    """Resolve *rel_path* to ``(peer_id, peer_rel_posix)`` when possible.

    Walks each candidate stem and each filename-prefix variant
    (``foo.py`` then ``_foo.py``) under the test file's grandparent
    directory. Only the first existing file is returned; missing peers
    yield ``None`` so the caller skips edge emission instead of writing
    a dangling edge that ``_clean_and_dedup_edges`` would prune.
    """
    parent = rel_path.parent.parent
    for stem_guess in candidate_peer_stems(rel_path.stem):
        for fn_prefix in _PEER_FILENAME_PREFIXES:
            filename = f"{fn_prefix}{stem_guess}.py"
            candidate = root / parent / filename
            if candidate.is_file():
                peer_rel = (parent / filename).as_posix()
                peer_id = _canonical_file_id(peer_rel)
                return peer_id, peer_rel
    return None


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
