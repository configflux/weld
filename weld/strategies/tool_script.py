"""Strategy: Tool scripts with language detection from shebang.

Emits one ``tool:<stem>`` node per matched script. The language is read from
the file suffix where there is one and from the shebang line where there is
not, so a repo's extensionless entry points -- ``gradlew``, ``configure``,
a top-level task runner -- are classified rather than skipped.

``discovered_from`` records each matched *file* rather than the directory
they share. The directory form is wrong for any pattern anchored at the repo
root: ``(root / "*.sh").parent`` is the root itself, which this strategy
recorded as ``"./"`` -- and ``"./"`` is the root marker that makes
:func:`weld._git._path_is_tracked` report *every* path in the repository as
tracked source. A single root-level entry would therefore widen
``source_stale`` from "the files discovery read" to "the whole tree", which
is the failure mode ``WELD_BOOKKEEPING_PATHS`` has had to be extended after
five separate incidents to contain. Per-file provenance is also what the
sibling file-oriented strategies (``bazel``, ``manifest``, ``dockerfile``,
``gh_workflow``, ``runbook``) already record, and it drops the old
``parent.is_dir()`` guard that silently returned nothing for a ``**``
pattern, since ``root/tools/**`` is not a directory (bd 0edz).

Two things changed once the scope widened past the repo root (bd x5ec):

**IDs are path-qualified.** ``tool:<stem>`` was safe only while at most one
script could carry a given name; a directory tree makes ``tools/x.sh`` and
``tools/sub/x.sh`` the same node. :func:`weld._node_ids.tool_id` mints the
``file_id`` form under the ``tool:`` prefix instead, so the collision is
removed by construction. Root scripts -- every ``tool:`` node this repo had
-- mint the identical string either way.

**Scripts declare what they run.** Each script's body is scanned for
repo-relative ``.py``/``.sh`` paths (see :mod:`weld.strategies._shell_refs`
for what that can and cannot honestly read) and each one becomes an
``invokes`` edge at ``confidence: inferred``. The target spelling comes from
the shared :mod:`weld.strategies._target_ids` rule -- every plausible ID
class is offered and the post-processor's dangling-edge sweep keeps whichever
resolved -- because a Python tool reaches the graph as ``file:`` while a
shell one reaches it as ``tool:``, and this strategy cannot know which entry
claimed the path it just read.
"""

from __future__ import annotations

import codecs
from pathlib import Path

from weld._node_ids import tool_id
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult
from weld.strategies._shell_refs import script_references
from weld.strategies._target_ids import target_ids

#: Bytes read to answer "what does the first line say". An interpreter
#: directive is the first thing in the file and is short; 256 bytes holds a
#: generous one (``#!/usr/bin/env -S python3 -X dev -W error`` and then
#: some) with room to spare, and bounds the read whatever the file turns
#: out to be.
_HEAD_BYTES = 256


def _shebang_lang(path: Path) -> str | None:
    """Classify *path* from its first line, or ``None`` to emit no node.

    The question is what the *first line* says, so the first line is what
    this reads. It previously read the whole file and threw all but one
    line away -- an unbounded read of a file weld did not write, for a
    strategy whose whole purpose is extensionless files matched by a broad
    glob (bd 2fe4). :func:`weld.file_index._opens_with_shebang` answers the
    neighbouring question in two bytes; this is the same discipline one
    line wider.

    ``None`` on ``OSError`` (vanished, unreadable, a directory) and on
    undecodable bytes, which is the pre-existing contract: a binary that a
    glob happened to match yields no node rather than an ``unknown``-lang
    one.

    Truncation is not corruption. A bounded read can cut a multi-byte
    character in half, and a strict ``bytes.decode`` cannot tell that from
    a genuine binary -- it raises for both, which would drop a legitimate
    script whose first line is long and non-ASCII. An incremental decoder
    buffers an incomplete trailing sequence and raises only on bytes that
    are invalid *anywhere*, which is exactly the distinction wanted.

    One behaviour does shift with the bound: a file that is text for its
    first line and binary further down now yields a node, where the
    whole-file read rejected it. That is the honest reading -- the
    classification is a statement about the first line -- and it is the
    same trade ``_opens_with_shebang`` made when it moved from a decode
    error to a magic number.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(_HEAD_BYTES)
    except OSError:
        return None
    try:
        first_line = codecs.getincrementaldecoder("utf-8")().decode(
            head.split(b"\n", 1)[0]
        )
    except UnicodeDecodeError:
        return None
    if "python" in first_line:
        return "python"
    if "bash" in first_line or "sh" in first_line:
        return "bash"
    return "unknown"


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract tool scripts with language detection from shebang."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob")
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)
    excludes = source.get("exclude", [])

    # ``resolve_glob`` sorts, so node emission and ``discovered_from`` order
    # are properties of the tree rather than of filesystem enumeration
    # (ADR 0012 §3, ADR 0112).
    for path in resolve_glob(root, pattern, excludes):
        # Belt-and-braces since bd 0d73: neither branch of the shared walker
        # yields a directory any more.
        if not path.is_file():
            continue
        rel_path = path.relative_to(root).as_posix()
        if path.suffix == ".py":
            lang = "python"
        elif path.suffix == ".sh":
            lang = "bash"
        else:
            detected = _shebang_lang(path)
            if detected is None:
                continue
            lang = detected

        nid = tool_id(rel_path)
        discovered_from.append(rel_path)
        existing = nodes.get(nid)
        if existing is not None:
            _record_shadowed(existing, rel_path)
            continue
        nodes[nid] = {
            "type": "tool",
            "label": path.name,
            "props": {
                "file": rel_path,
                "lang": lang,
                "source_strategy": "tool_script",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["script"],
            },
        }
        edges.extend(_invocation_edges(root, rel_path))

    return StrategyResult(nodes, edges, discovered_from)


def _record_shadowed(node: dict, rel_path: str) -> None:
    """Name a file that lost the ID race, on the node that won it.

    :func:`weld._node_ids.tool_id` removes the collision this strategy was
    scoped away from ``tools/`` to avoid -- two same-named scripts in
    different directories. One residual survives it, because the rule strips
    the final extension: ``tools/run.sh`` and ``tools/run.py`` are two files
    and one ``tool:tools/run``. Keeping the extension would fix that and cost
    a graph migration for every ``tool:`` node in existence; ``file_id`` made
    the same trade, and ``_target_ids.FILE_NODE_EXTENSIONS`` is the guard it
    grew afterwards.

    So the residual is guarded rather than removed. The walk is sorted, so
    the winner is a property of the tree; the loser is *named on the winner*
    rather than dropped in silence, because a node that quietly stands for
    two files is the wrong-but-real node this whole change exists to refuse
    -- indistinguishable from a correct one at the point of use. Both files
    still enter ``discovered_from``: they were read, and an edit to either
    must re-run this strategy.
    """
    props = node.setdefault("props", {})
    shadowed = props.setdefault("shadowed", [])
    if rel_path not in shadowed:
        shadowed.append(rel_path)


def _invocation_edges(root: Path, rel_path: str) -> list[dict]:
    """Return ``invokes`` edges for every script *rel_path* names in its body.

    One edge per plausible spelling of each referent, which is the referrer
    contract: the strategy that minted the target chose its ID class, and
    ``_clean_and_dedup_edges`` drops the spellings that resolve to nothing.
    Emitting a single guessed spelling instead is how ``concept_from_bd``
    spent months emitting only dangling edges without one test failing.
    """
    out: list[dict] = []
    for referent in script_references(root, rel_path):
        for target in target_ids(referent):
            out.append({
                "from": tool_id(rel_path),
                "to": target,
                "type": "invokes",
                "props": {
                    "source_strategy": "tool_script",
                    "confidence": "inferred",
                    # ADR 0074: the referring script this edge was scanned
                    # from, never the invoked target -- so a clean-provenance
                    # edge into a dirtied target survives the incremental
                    # purge instead of falling to the endpoint-membership
                    # floor and never being re-minted (the referring script
                    # is usually clean when only the target it invokes
                    # changes; bd 57lra).
                    "provenance": {"file": rel_path},
                },
            })
    return out
