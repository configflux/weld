"""Go ``*_test.go`` resolver for the test_peer strategy.

Per ADR 0046, the Go convention is ``foo_test.go`` paired with
``foo.go`` in the same directory. The heuristic is filename-only: the
trailing ``_test`` is dropped and ``<stem>.go`` is checked for
existence. No build-tag parsing, no package-name verification.

bd cw4f (ADR 0125 follow-up):
:func:`file_summary_for_test` gives a Go test file the same
``props.summary`` channel bd ikof gave Python test files -- its own leading
comment, read via the package-doc-comment convention (immediately preceding
``package``, the same convention ADR 0124's symbol-level reader already
verified against this environment's grammar). See
:mod:`weld.strategies._ts_file_doc_comments` for the extraction mechanism.
"""

from __future__ import annotations

from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id
from weld.strategies._ts_file_doc_comments import go_file_summary

_TEST_SUFFIX = "_test"


def is_test_file(rel: Path) -> bool:
    """Return True iff *rel* matches the Go ``*_test.go`` pattern."""
    if rel.suffix != ".go":
        return False
    stem = rel.stem
    return stem.endswith(_TEST_SUFFIX) and stem != _TEST_SUFFIX


def resolve_peer(root: Path, rel: Path) -> tuple[str, str] | None:
    """Return ``(peer_id, peer_rel)`` for the matching ``<stem>.go`` file.

    Drops the ``_test`` suffix and looks for ``<stem>.go`` in the same
    directory. Returns ``None`` if the peer file does not exist.
    """
    if not is_test_file(rel):
        return None
    base = rel.stem[: -len(_TEST_SUFFIX)]
    if not base:
        return None
    parent = rel.parent
    candidate = parent / f"{base}.go"
    if not (root / candidate).is_file():
        return None
    peer_rel = candidate.as_posix()
    return _canonical_file_id(peer_rel), peer_rel


def file_summary_for_test(root: Path, rel_path: Path) -> str:
    """Return the collapsed leading comment of *rel_path*'s own package.

    Delegates to :func:`weld.strategies._ts_file_doc_comments.
    go_file_summary`, which already degrades to ``""`` on any parse or
    grammar failure -- ``test_peer`` records a *test* node either way, the
    same "always present, empty when absent" contract
    :func:`weld.strategies._test_peer_python.module_summary_for_test`
    documents for Python.
    """
    return go_file_summary(root / rel_path)
