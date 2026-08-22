"""Java ``*Test.java`` / ``*Tests.java`` resolver for the test_peer strategy.

Per ADR 0046, JUnit's standard convention is ``FooTest.java`` paired
with ``Foo.java`` in the same package directory. Some repos use the
plural ``FooTests.java``; both are recognized.

Match is case-sensitive: ``footest.java`` is not treated as a test
file. This matches the JVM convention where class names (and therefore
file names) preserve case.

bd cw4f (ADR 0125 follow-up):
:func:`file_summary_for_test` gives a Java test file the same
``props.summary`` channel bd ikof gave Python test files -- its own leading
comment run (a license header or file-purpose block preceding ``package``;
Javadoc ``/** */`` is stripped the same as a plain ``/* */`` block, since
tree-sitter-java's grammar does not distinguish the two). See
:mod:`weld.strategies._ts_file_doc_comments` for the extraction mechanism.
"""

from __future__ import annotations

from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id
from weld.strategies._ts_file_doc_comments import java_file_summary

#: Recognized trailing tokens, longest-first so ``Tests`` is tried
#: before ``Test`` and we don't accidentally strip only the ``s`` from
#: ``FooTests``.
_TEST_SUFFIXES: tuple[str, ...] = ("Tests", "Test")


def is_test_file(rel: Path) -> bool:
    """Return True iff *rel* ends with ``Test`` or ``Tests`` before ``.java``."""
    if rel.suffix != ".java":
        return False
    stem = rel.stem
    for suffix in _TEST_SUFFIXES:
        if stem.endswith(suffix) and stem != suffix:
            return True
    return False


def _strip_test_suffix(stem: str) -> str | None:
    """Drop the trailing ``Test`` / ``Tests`` token, longest first.

    Returns the bare class name, or ``None`` when the stem is empty or
    matches the suffix exactly (which would yield an empty peer stem).
    """
    for suffix in _TEST_SUFFIXES:
        if stem.endswith(suffix) and stem != suffix:
            base = stem[: -len(suffix)]
            return base or None
    return None


def resolve_peer(root: Path, rel: Path) -> tuple[str, str] | None:
    """Return ``(peer_id, peer_rel)`` for the matching ``<base>.java`` file.

    Searches the same directory as the test file. Returns ``None`` if
    the peer file does not exist on disk.
    """
    if not is_test_file(rel):
        return None
    base = _strip_test_suffix(rel.stem)
    if not base:
        return None
    candidate = rel.parent / f"{base}.java"
    if not (root / candidate).is_file():
        return None
    peer_rel = candidate.as_posix()
    return _canonical_file_id(peer_rel), peer_rel


def file_summary_for_test(root: Path, rel_path: Path) -> str:
    """Return the collapsed leading comment run of *rel_path*.

    Delegates to :func:`weld.strategies._ts_file_doc_comments.
    java_file_summary`, which already degrades to ``""`` on any parse or
    grammar failure -- the same "always present, empty when absent"
    contract :func:`weld.strategies._test_peer_python.module_summary_for_test`
    documents for Python.
    """
    return java_file_summary(root / rel_path)
