"""Rust integration-test resolver for the test_peer strategy.

Per ADR 0046, the recognized convention is Cargo's integration tests
directory: ``tests/<name>.rs`` paired with ``src/<name>.rs`` in the
same crate root.

In-file ``#[cfg(test)]`` modules in the same source file are out of
scope for v1 -- those would require parsing source, not just file
names. They are documented as a follow-up in ADR 0046.

Detection rule: the test file's parent directory name must be exactly
``tests`` and a sibling ``src/`` directory must exist (this disambiguates
the Rust convention from generic ``tests/`` directories used by other
languages such as Python or TS).

bd cw4f (ADR 0125 follow-up):
:func:`file_summary_for_test` gives a Rust integration-test file the same
``props.summary`` channel bd ikof gave Python test files -- its own leading
``//!``/``/*!`` module-doc-comment run. See
:mod:`weld.strategies._ts_file_doc_comments` for the extraction mechanism.
"""

from __future__ import annotations

from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id
from weld.strategies._ts_file_doc_comments import rust_file_summary

_TESTS_DIR = "tests"
_SRC_DIR = "src"


def is_test_file(rel: Path) -> bool:
    """Return True iff *rel* matches the Cargo ``tests/<name>.rs`` shape.

    Does not require the sibling ``src/`` to exist at this stage --
    the existence check happens in :func:`resolve_peer` so a missing
    ``src/`` simply yields no edge instead of misclassifying the file.
    """
    if rel.suffix != ".rs":
        return False
    return rel.parent.name == _TESTS_DIR


def resolve_peer(root: Path, rel: Path) -> tuple[str, str] | None:
    """Return ``(peer_id, peer_rel)`` for the matching ``src/<name>.rs`` file.

    Resolves to ``<crate-root>/src/<name>.rs`` where ``<crate-root>``
    is the parent of the ``tests`` directory. Returns ``None`` when the
    peer file does not exist or when the test file's grandparent
    directory is invalid (e.g. the test lives at repo root with no
    ``src/`` sibling).
    """
    if not is_test_file(rel):
        return None
    crate_root = rel.parent.parent
    candidate = crate_root / _SRC_DIR / f"{rel.stem}.rs"
    if not (root / candidate).is_file():
        return None
    peer_rel = candidate.as_posix()
    return _canonical_file_id(peer_rel), peer_rel


def file_summary_for_test(root: Path, rel_path: Path) -> str:
    """Return the collapsed leading ``//!``/``/*!`` run of *rel_path*.

    Delegates to :func:`weld.strategies._ts_file_doc_comments.
    rust_file_summary`, which already degrades to ``""`` on any parse or
    grammar failure -- the same "always present, empty when absent"
    contract :func:`weld.strategies._test_peer_python.module_summary_for_test`
    documents for Python.
    """
    return rust_file_summary(root / rel_path)
