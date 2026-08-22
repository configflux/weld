"""Doc -> code citation edges: explicit textual references to real files.

ADR 0128 closes bd ziv1 ("ADRs that govern a code module are not reachable
from that module"): an ADR body routinely names the module it pins,
verbatim and in backticks (``weld.repo_boundary.path_within_repo_boundary``,
``weld/repo_boundary.py:iter_repo_files``), and nothing joined that
citation to the graph node it names. This module is the generic mechanism
-- it runs for every document :mod:`weld.strategies.markdown` processes,
not only ADRs, alongside the existing inter-doc ``[text](x.md)`` link pass.

Extraction honesty (ADR 0113's discipline, continued by ADR 0127 for
same-module references): an edge is minted only when a citation resolves,
concretely, against the real filesystem -- never from a title match or a
thematic association. Two citation shapes resolve:

* A path reference: a backtick span ending in ``.py``, optionally
  ``:qualname``-suffixed. The suffix is recognized and discarded, not
  resolved to a ``symbol:`` node (out of scope this iteration -- see ADR
  0128 Non-goals).
* A dotted-module reference: two or more dot-separated identifier
  segments. Resolved by trying the longest prefix first, down to a floor
  of two segments -- never one. A one-segment floor would let any string
  merely starting with a real top-level package name
  (``pkg.anything_made_up``) falsely resolve to that package's
  ``__init__.py``, since almost every Python project has one; requiring
  two real segments keeps every accepted match independently specific.

Resolution is a direct filesystem check against ``root`` (mirroring
:mod:`weld.strategies.validator_targets`), not a lookup against nodes
another strategy will mint later in the same pass -- this keeps the
mechanism independent of which ``.weld/discover.yaml`` entry happens to
own a given path today, and any edge that still can't resolve at merge
time is dropped by the discovery orchestrator's dangling-edge sweep.

Bounded like ``validator_targets.py`` bounds its own literal scan:
discovery runs against arbitrary repositories, so an over-long span is
skipped and the distinct targets kept per document is capped.
"""

from __future__ import annotations

import re
from pathlib import Path

from weld._node_ids import file_id
from weld.strategies._markdown_fence import content_text

#: Backtick-quoted inline code spans. Single line only: a real citation is
#: a short identifier or path, and CommonMark inline code spanning a blank
#: line is prose that happens to contain a backtick, not a reference.
_BACKTICK_SPAN_RE = re.compile(r"`([^`\n]+)`")

#: A ``.py`` path, optionally ``:qualname``-suffixed
#: (``weld/repo_boundary.py:iter_repo_files``). The suffix is captured so
#: it can be stripped rather than misread as part of the path.
_PATH_CITATION_RE = re.compile(
    r"^([A-Za-z0-9_][A-Za-z0-9_./-]{0,200}\.py)(:[A-Za-z_][A-Za-z0-9_.]*)?$"
)

#: Two or more dot-separated identifier segments
#: (``weld.repo_boundary.path_within_repo_boundary``). A single bare word
#: is never a module citation on its own.
_DOTTED_CITATION_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$"
)

#: Spans longer than this are not scanned: real citations are short, and
#: anything past this length is prose that happens to contain a backtick.
_MAX_SPAN_LEN = 200

#: Distinct resolved targets kept per document -- generous relative to
#: every real citation measured in this repository, bounds a pathological
#: one.
_MAX_TARGETS_PER_DOC = 100

#: Minimum accepted dotted-module prefix depth. See module docstring for
#: why one segment is unsafe.
_MIN_MODULE_PREFIX_SEGMENTS = 2


def _safe_candidate_path(root: Path, rel_candidate: str) -> bool:
    """Return True when *rel_candidate* names a real file safely under root.

    Mirrors ``weld.strategies.validator_targets._safe_direct_path``:
    rejects absolute paths, ``..`` traversal, symlinks, and anything that
    resolves outside *root*.
    """
    if rel_candidate.startswith(("/", "\\")):
        return False
    if ".." in Path(rel_candidate).parts:
        return False
    full = root / rel_candidate
    try:
        if full.is_symlink() or not full.is_file():
            return False
        resolved = full.resolve()
        root_resolved = root.resolve()
    except OSError:
        return False
    return resolved.is_relative_to(root_resolved)


def _resolve_path_citation(root: Path, span: str) -> str | None:
    """Return the repo-relative ``.py`` path *span* cites, or None."""
    match = _PATH_CITATION_RE.match(span)
    if match is None:
        return None
    candidate = match.group(1)
    return candidate if _safe_candidate_path(root, candidate) else None


def _resolve_dotted_citation(root: Path, span: str) -> str | None:
    """Return the ``.py`` path the longest resolving prefix of *span* names.

    Tries the full dotted string first, then drops trailing segments one
    at a time down to :data:`_MIN_MODULE_PREFIX_SEGMENTS`. Each candidate
    prefix is tried both as a plain module (``a/b.py``) and a package
    (``a/b/__init__.py``).
    """
    if _DOTTED_CITATION_RE.match(span) is None:
        return None
    parts = span.split(".")
    floor = _MIN_MODULE_PREFIX_SEGMENTS - 1
    for depth in range(len(parts), floor, -1):
        base = "/".join(parts[:depth])
        for candidate in (f"{base}.py", f"{base}/__init__.py"):
            if _safe_candidate_path(root, candidate):
                return candidate
    return None


def _resolve_citation(root: Path, span: str) -> str | None:
    if len(span) > _MAX_SPAN_LEN:
        return None
    target = _resolve_path_citation(root, span)
    if target is not None:
        return target
    return _resolve_dotted_citation(root, span)


def code_reference_edges(
    root: Path, doc_nid: str, doc_rel_path: str, text: str,
) -> list[dict]:
    """Return ``documents`` edges from *doc_nid* to every code citation in *text*.

    *doc_rel_path* is the citing document's own repo-relative path, stamped
    as ``props.provenance.file`` (ADR 0074): these edges cross
    ``.weld/discover.yaml`` source entries by construction (the doc family
    and the code family are different entries), so provenance is required,
    not optional polish. Output is sorted and deduplicated by target for
    determinism (ADR 0012 §3).
    """
    targets: set[str] = set()
    for span in _BACKTICK_SPAN_RE.findall(content_text(text)):
        target = _resolve_citation(root, span)
        if target is None:
            continue
        targets.add(target)
        if len(targets) >= _MAX_TARGETS_PER_DOC:
            break

    return [
        {
            "from": doc_nid,
            "to": file_id(rel_target),
            "type": "documents",
            "props": {
                "source_strategy": "markdown",
                "authority": "derived",
                "confidence": "inferred",
                "provenance": {"file": doc_rel_path},
            },
        }
        for rel_target in sorted(targets)
    ]


__all__ = ["code_reference_edges"]
