"""C# ``*Test.cs`` / ``*Tests.cs`` resolver for the test_peer strategy.

Per ADR 0046, the recognized conventions are:

- ``FooTests.cs`` -- xUnit / NUnit standard.
- ``FooTest.cs`` -- MSTest standard.

Both pair with ``Foo.cs`` in the same directory. Match is
case-sensitive (matching .NET's case-preserving naming convention).
"""

from __future__ import annotations

from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id

#: Recognized trailing tokens, longest-first so ``Tests`` is tried
#: before ``Test`` and we don't accidentally strip only ``Test`` off a
#: ``FooTests`` stem.
_TEST_SUFFIXES: tuple[str, ...] = ("Tests", "Test")


def is_test_file(rel: Path) -> bool:
    """Return True iff *rel* ends with ``Test`` or ``Tests`` before ``.cs``."""
    if rel.suffix != ".cs":
        return False
    stem = rel.stem
    for suffix in _TEST_SUFFIXES:
        if stem.endswith(suffix) and stem != suffix:
            return True
    return False


def _strip_test_suffix(stem: str) -> str | None:
    """Drop the trailing ``Test`` / ``Tests`` token, longest first.

    Returns the bare class name, or ``None`` when the stem is empty or
    matches the suffix exactly.
    """
    for suffix in _TEST_SUFFIXES:
        if stem.endswith(suffix) and stem != suffix:
            base = stem[: -len(suffix)]
            return base or None
    return None


def resolve_peer(root: Path, rel: Path) -> tuple[str, str] | None:
    """Return ``(peer_id, peer_rel)`` for the matching ``<base>.cs`` file.

    Searches the same directory as the test file. Returns ``None`` if
    the peer file does not exist on disk.
    """
    if not is_test_file(rel):
        return None
    base = _strip_test_suffix(rel.stem)
    if not base:
        return None
    candidate = rel.parent / f"{base}.cs"
    if not (root / candidate).is_file():
        return None
    peer_rel = candidate.as_posix()
    return _canonical_file_id(peer_rel), peer_rel
