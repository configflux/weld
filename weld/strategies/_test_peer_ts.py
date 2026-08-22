"""TypeScript / JavaScript test-peer resolver.

Per ADR 0046 the conventions recognized are:

- ``*.test.{ts,tsx,js,jsx}`` -- Jest / Vitest / Mocha standard.
- ``*.spec.{ts,tsx,js,jsx}`` -- alternate spec convention.
- ``__tests__/<name>.{ts,tsx,js,jsx}`` -- Jest directory convention.
- ``__tests__/<name>.test.{ts,tsx,js,jsx}`` -- both combined.

Peer resolution looks for the same stem with one of the supported
source extensions in the parent directory (or the parent of the
``__tests__`` directory). The extension search order is fixed at
``.ts -> .tsx -> .js -> .jsx`` so the first hit is deterministic.

bd cw4f (ADR 0125 follow-up):
:func:`file_summary_for_test` gives a TS/JS test file the same
``props.summary`` channel bd ikof gave Python test files -- its own leading
comment run, read with the ``typescript`` grammar for all four extensions
(a strict-superset parse of plain JS, the same grammar choice
``weld/languages/typescript.yaml`` already makes). See
:mod:`weld.strategies._ts_file_doc_comments` for the extraction mechanism.
"""

from __future__ import annotations

from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id
from weld.strategies._ts_file_doc_comments import typescript_file_summary

#: Recognized source/test file extensions, in deterministic search
#: order. The order matters because the resolver returns the *first*
#: existing peer; placing ``.ts`` before ``.tsx`` keeps the more
#: common case earliest.
_SOURCE_EXTS: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx")

#: Mid-stem suffixes that mark a test file (``foo.test.ts``,
#: ``foo.spec.ts``).
_TEST_INFIXES: tuple[str, ...] = (".test", ".spec")

#: Directory name that, when it is the immediate parent of a source
#: file, marks the file as a test under the Jest ``__tests__/``
#: convention.
_TESTS_DIR = "__tests__"


def is_test_file(rel: Path) -> bool:
    """Return True iff *rel* matches a recognized TS/JS test pattern.

    Either the filename carries a ``.test`` / ``.spec`` mid-suffix, or
    the file lives in a ``__tests__/`` directory.
    """
    if rel.suffix not in _SOURCE_EXTS:
        return False
    if rel.parent.name == _TESTS_DIR:
        return True
    stem = rel.stem
    for infix in _TEST_INFIXES:
        if stem.endswith(infix) and stem != infix:
            return True
    return False


def _strip_test_infix(stem: str) -> str:
    """Drop ``.test`` / ``.spec`` from the trailing of *stem*.

    ``foo.test`` -> ``foo``, ``foo.spec`` -> ``foo``,
    ``foo`` -> ``foo`` (unchanged).
    """
    for infix in _TEST_INFIXES:
        if stem.endswith(infix) and stem != infix:
            return stem[: -len(infix)]
    return stem


def _find_first_existing(
    root: Path, parent: Path, stem: str,
) -> tuple[str, str] | None:
    """Return the first ``parent/<stem>.<ext>`` file that exists on disk.

    Walks ``_SOURCE_EXTS`` in declaration order so the result is
    deterministic.
    """
    for ext in _SOURCE_EXTS:
        candidate = parent / f"{stem}{ext}"
        if (root / candidate).is_file():
            peer_rel = candidate.as_posix()
            return _canonical_file_id(peer_rel), peer_rel
    return None


def resolve_peer(root: Path, rel: Path) -> tuple[str, str] | None:
    """Return ``(peer_id, peer_rel)`` for the matching source file.

    Two recognition paths -- both produce the same resolution shape:

    1. ``__tests__/<name>.<ext>`` -- search the parent of ``__tests__``.
       The mid-suffix is dropped first so ``__tests__/foo.test.ts``
       and ``__tests__/foo.ts`` both resolve to ``foo.<ext>``.
    2. ``<dir>/<name>.test.<ext>`` / ``<dir>/<name>.spec.<ext>`` --
       search ``<dir>``.
    """
    if not is_test_file(rel):
        return None
    stem = _strip_test_infix(rel.stem)
    if not stem:
        return None
    if rel.parent.name == _TESTS_DIR:
        parent = rel.parent.parent
    else:
        parent = rel.parent
    return _find_first_existing(root, parent, stem)


def file_summary_for_test(root: Path, rel_path: Path) -> str:
    """Return the collapsed leading comment run of *rel_path*.

    Delegates to :func:`weld.strategies._ts_file_doc_comments.
    typescript_file_summary`, which already degrades to ``""`` on any parse
    or grammar failure -- the same "always present, empty when absent"
    contract :func:`weld.strategies._test_peer_python.module_summary_for_test`
    documents for Python.
    """
    return typescript_file_summary(root / rel_path)
