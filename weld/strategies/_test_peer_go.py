"""Go ``*_test.go`` resolver for the test_peer strategy.

Per ADR 0046, the Go convention is ``foo_test.go`` paired with
``foo.go`` in the same directory. The heuristic is filename-only: the
trailing ``_test`` is dropped and ``<stem>.go`` is checked for
existence. No build-tag parsing, no package-name verification.
"""

from __future__ import annotations

from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id

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
