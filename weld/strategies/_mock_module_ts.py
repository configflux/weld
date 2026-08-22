"""Harvest ``jest.mock`` / ``vi.mock`` module targets from a TS/JS test file.

The TS/JS half of the blind spot :mod:`weld.strategies._mock_patch_python`
closes for Python (bd ymso, bd gyve). ``jest.mock("./payment-gateway")`` names
a module by *string*, so the test imports nothing from it and the import graph
records no dependency -- "who touches this module" omits every mock-based test,
exactly as it did on the Python side.

What differs from the Python resolver is the resolution rule, and it differs in
kind rather than degree: a patch target is a dotted *absolute* name resolved
against the project root, while a mock target is a module specifier resolved
against the **importing file's own directory**. Python's resolver cannot be
pointed at it, which is why this is a second module rather than a parameter on
the first. The edge they emit is deliberately identical -- same ``depends_on``
type, same :data:`MOCK_PATCH_RESOLUTION` tag imported from the Python module
rather than restated -- so ``props.resolution == "mock_patch"`` keeps answering
"which dependents are mocks?" across both languages.

Regex, not tree-sitter, and that is a capability decision rather than a
shortcut. The TS grammar is an optional dependency (ADR 0002) that the
CI-mirror smoke deliberately runs *without*, so a tree-sitter-only harvest
would silently emit nothing in the one configuration the gate checks hardest,
and nothing at all for a user who installed weld without the extra. The shape
being matched is a literal string in the first argument of a named call, which
is inside what a bounded, anchored regex can prove.

Its one imprecision is commented-out code, handled directly: a match preceded
on its own line by ``//`` or ``*`` is skipped, which covers the ``// jest.mock(
"./x")`` and JSDoc cases. A mock inside a template literal or a multi-line
comment without leading markers can still be seen. That costs a spurious edge
to a module the test file does name, which is the failure the on-disk
resolution bar below already bounds -- and a far smaller cost than the feature
being absent whenever the grammar is.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id
from weld.strategies._mock_patch_python import MOCK_PATCH_RESOLUTION

#: Module specifier extensions weld can resolve, in deterministic search
#: order. Shared with :mod:`weld.strategies._test_peer_ts`, which resolves a
#: test's production peer with the same first-existing-wins rule.
_SOURCE_EXTS: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx")

#: Extension a specifier may carry that does *not* name the file on disk.
#: TypeScript's ESM output convention has source ``a.ts`` import ``"./a.js"``,
#: so the specifier's own extension is a lead rather than an answer.
_ESM_REWRITES: dict[str, tuple[str, ...]] = {
    ".js": (".ts", ".js"),
    ".jsx": (".tsx", ".jsx"),
}

#: ``jest.mock(...)`` / ``vi.mock(...)`` and their deferred ``doMock`` form,
#: whose first argument carries the same module-specifier contract.
#: ``unmock`` is excluded on purpose: it *removes* a mock, so recording it as
#: a dependency would invert the fact. The pattern is linear and anchored with
#: no nested quantifiers, and the specifier class excludes newlines, so it is
#: ReDoS-free and cannot run past the end of a string literal.
_MOCK_CALL_RE = re.compile(
    r"\b(?:jest|vi)\s*\.\s*(?:mock|doMock)\s*\(\s*(['\"])([^'\"\n]+)\1"
)

#: Cheap pre-check: every recognised spelling contains this substring, so a
#: module without it provably has no mock call and is never scanned. Mirrors
#: the ``patch`` pre-check in :mod:`weld.strategies._mock_patch_python`.
_MOCK_HINT = "mock"

#: Line-comment marker. Treated as a substring of the prefix, so a trailing
#: ``// jest.mock("./x")`` is caught. It can also fire on a URL earlier in the
#: same line, which costs a dropped edge -- the safe direction for a module
#: whose stated bar is "emit only what is proven".
_LINE_COMMENT = "//"


def new_cache() -> dict:
    """Return an empty per-run cache for :func:`mock_target_edges`.

    Memoises ``candidate path -> exists`` across every test file in one
    ``extract()`` call. A shared module mocked by fifty specs is stat-ed once
    per candidate extension instead of fifty times, and the same signature as
    :func:`weld.strategies._mock_patch_python.new_cache` is what lets
    ``test_peer`` treat both languages through one hook.
    """
    return {}


def _is_commented(source_text: str, start: int) -> bool:
    """Return whether the match at *start* sits after a comment marker.

    Looks only at the text between the start of the match's own line and the
    match itself, so a ``//`` on an earlier line cannot suppress a live call.

    The block-comment form is matched as a *leading* ``*`` rather than as a
    substring, because ``*`` is also multiplication: ``const n = a * b;
    jest.mock("./x")`` is live code, while a JSDoc or ``/* ... */``
    continuation line always opens with the marker.
    """
    line_start = source_text.rfind("\n", 0, start) + 1
    prefix = source_text[line_start:start]
    return _LINE_COMMENT in prefix or prefix.lstrip().startswith("*")


def mock_targets(source_text: str) -> list[tuple[str, int]]:
    """Return ``(specifier, line)`` for every mock call in *source_text*.

    Order follows the scan, which is deterministic for given source. A
    specifier is returned exactly as written; interpreting it is
    :func:`resolve_mock_target`'s job.
    """
    if _MOCK_HINT not in source_text:
        return []
    found: list[tuple[str, int]] = []
    for match in _MOCK_CALL_RE.finditer(source_text):
        if _is_commented(source_text, match.start()):
            continue
        line = source_text.count("\n", 0, match.start()) + 1
        found.append((match.group(2), line))
    return found


def _candidate_rel_paths(base: str) -> list[str]:
    """Return the on-disk paths *base* could name, in resolution order.

    ``base`` is the specifier already joined to the test file's directory and
    normalised. The order is the module resolver's own: the exact path first
    (so an explicit extension wins), then each source extension appended, then
    the directory-index form. ``_ESM_REWRITES`` is consulted first for a
    specifier whose extension is a compiled-output name.
    """
    suffix = Path(base).suffix
    if suffix in _ESM_REWRITES:
        stem = base[: -len(suffix)]
        return [stem + ext for ext in _ESM_REWRITES[suffix]]
    if suffix in _SOURCE_EXTS:
        return [base]
    return (
        [base + ext for ext in _SOURCE_EXTS]
        + [f"{base}/index{ext}" for ext in _SOURCE_EXTS]
    )


def _normalized_join(rel: Path, specifier: str) -> str | None:
    """Return *specifier* resolved against *rel*'s directory, or None.

    ``None`` when the result climbs out of the project root. ``..`` segments
    are legitimate in a specifier (``../lib/thing``), so they are normalised
    rather than rejected, and the escape check is applied to the *result* --
    the only place it can be decided.
    """
    joined = posixpath.normpath(
        posixpath.join(rel.parent.as_posix(), specifier)
    )
    if joined == ".." or joined.startswith("../") or joined.startswith("/"):
        return None
    return joined


def resolve_mock_target(
    root: Path, rel: Path, specifier: str, cache: dict
) -> tuple[str, bool] | None:
    """Resolve *specifier* to ``(file node id, extension_was_explicit)``.

    Returns None -- emit nothing -- for everything this cannot prove, which is
    the same bar bd ymso set for the Python resolver: an absent edge costs
    nothing, while one pointing at the wrong real file is a lie the graph then
    repeats to every consumer.

    The dropped cases:

    * **Bare specifiers** (``jest.mock("axios")``, ``vi.mock("@app/db")``).
      They resolve through ``node_modules``, a ``moduleNameMapper`` entry or a
      ``tsconfig`` path alias -- three project-level configs weld does not read
      here. Guessing a repo file with a matching name is precisely how a mock
      of the npm ``crypto`` package would be attributed to a local
      ``crypto.ts``.
    * **Anything that escapes the root** -- see :func:`_normalized_join`.
    * **Specifiers naming no file on disk**, including a deleted module a
      stale test still mocks.

    The second tuple element reports whether the specifier named its file
    outright, which the caller turns into edge confidence: appending an
    extension or resolving a directory index is an inference about which of
    several possible files was meant, and is labelled as one.
    """
    if not specifier.startswith("./") and not specifier.startswith("../"):
        return None
    base = _normalized_join(rel, specifier)
    if base is None:
        return None
    explicit = Path(base).suffix in _SOURCE_EXTS
    for candidate in _candidate_rel_paths(base):
        exists = cache.get(candidate)
        if exists is None:
            exists = (root / candidate).is_file()
            cache[candidate] = exists
        if exists:
            return _canonical_file_id(candidate), explicit and candidate == base
    return None


def mock_target_edges(
    root: Path, rel: Path, from_id: str, *, cache: dict
) -> list[dict]:
    """Return ``depends_on`` edges from *from_id* to each mocked module.

    Signature, edge type and provenance direction all match
    :func:`weld.strategies._mock_patch_python.patch_target_edges`, so
    ``test_peer`` dispatches to either through one hook and the impact engine
    sees one edge shape for both languages. Per ADR 0074 the provenance stamp
    is the *test* file, never the mocked module: the incremental purge retains
    an edge only when it can attribute it to a file, and the target is the file
    that is stale in exactly the case that must not lose the edge.

    Confidence is ``definite`` when the specifier named its file outright and
    ``inferred`` when an extension or directory index had to be resolved --
    the honest split, because the latter picks the first of several candidate
    files that could have been meant.

    The read is guarded because a globbed file can vanish before a strategy
    reads it (bd pt38); an unreadable test costs its mock edges, not the run.
    """
    try:
        source_text = (root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    edges: list[dict] = []
    seen: set[str] = set()
    for specifier, line in mock_targets(source_text):
        resolved = resolve_mock_target(root, rel, specifier, cache)
        if resolved is None:
            continue
        target_id, explicit = resolved
        if target_id in seen or target_id == from_id:
            continue
        seen.add(target_id)
        edges.append(
            {
                "from": from_id,
                "to": target_id,
                "type": "depends_on",
                "props": {
                    "source_strategy": "test_peer",
                    "confidence": "definite" if explicit else "inferred",
                    "resolved": True,
                    "raw": specifier,
                    "resolution": MOCK_PATCH_RESOLUTION,
                    "provenance": {"file": rel.as_posix(), "line": line},
                },
            }
        )
    return edges


__all__ = [
    "mock_target_edges",
    "mock_targets",
    "new_cache",
    "resolve_mock_target",
]
