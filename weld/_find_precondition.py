"""The ADR 0134 precondition for ``wd find`` / MCP ``weld_find``.

``find`` is the one read command that never needed a graph: it answers from
``.weld/file-index.json``, which a user may legitimately build with
``wd build-index`` and no discovery at all. That is why it was exempted from
the missing-*graph* guard -- correctly. What the exemption then did, on both
surfaces, was let it answer ``no matches`` at exit 0 from an index **that did
not exist**, which is the same collapse ADR 0134 forbids: "cannot answer"
presented as "answered, empty". A fresh worktree of a federation root
reproduced it in one command -- ``wd query`` and ``wd brief`` refused with
guidance while ``wd find`` reported a clean negative about a file plainly on
disk.

The rule this module states is narrow and is the ADR's, not a new one:
**apply the precondition to the artifact the command reads.** For ``query``
that artifact is the graph; for ``find`` it is the index. Nothing here gives
``find`` a graph requirement, and nothing here builds an index -- discovery
stays a command the user runs deliberately (ADR 0051 governs what a read may
write, and a full index build is not on that list).

What counts as absent
---------------------
Every index the search would actually read is missing:

* single-repo root -- its own ``.weld/file-index.json``;
* federation root -- its own **and** every registered child's, since
  :func:`weld._federation_find.federated_find` fans out across all of them
  (ADR 0089). A root with no index of its own but one live child index can
  still answer, so it is not a cannot-answer state.

An index that exists and yields no hit is untouched: that is a real negative
answer and stays exit 0.

One payload, two surfaces
-------------------------
:func:`missing_file_index_payload` is the single source, and the CLI's stderr
block is *rendered from it* (:func:`cannot_answer_block`) rather than written
alongside it. ``graph_missing`` builds its two spellings in two places and
needs a test to prove they still agree; here they cannot disagree, because
there is only one.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Mapping

from weld._errors import FILE_INDEX_MISSING, format_error_line, structured_payload
from weld._safe_text import sanitize_terminal_text

__all__ = [
    "cannot_answer_block",
    "ensure_file_index_exists",
    "file_index_available",
    "find_or_cannot_answer",
    "missing_file_index_payload",
]

#: The index file, relative to a repository root.
_INDEX_REL = Path(".weld") / "file-index.json"


def file_index_available(root: Path | str) -> bool:
    """Whether a ``find`` at *root* has any index to read.

    Cheap by construction -- one ``stat`` per candidate root, no parse, and
    the common case (the root has its own index) settles on the first. The
    child walk runs only at a federation root that lacks one, and a child
    directory that is not checked out simply fails its ``stat`` like any
    other absent index.
    """
    root = Path(root)
    if (root / _INDEX_REL).is_file():
        return True
    from weld.workspace_state import load_workspace_config

    config = load_workspace_config(root)
    if config is None:
        return False
    return any(
        (root / child.path / _INDEX_REL).is_file() for child in config.children
    )


def missing_file_index_payload(
    root: Path | str, retry_cmd: str | None = None,
) -> dict | None:
    """The cannot-answer payload for *root*, or ``None`` when it can answer.

    ``None`` is the answer for the overwhelmingly common case, so callers
    read as "refuse if there is something to refuse" rather than having to
    ask two questions.

    A checkout that could never have had an index gets told why, from
    :func:`weld._worktree_seed.seed_blocked_reason` -- the graph route's own
    function, reused rather than restated. It is the right explanation here
    too and not a borrowed one: seeding copies ``file-index.json`` alongside
    ``graph.json`` (``weld._worktree_seed_copy.SEED_STATE_FILES``), so the
    precondition it names withheld *both* artifacts from this worktree. The
    cause extends the standing summary instead of replacing it, so
    ``No Weld file index found.`` still leads ``error`` for anything matching
    on it.

    The probe is read-only and every component is a module constant: no path
    is echoed, so a refusal says nothing about what exists on disk. The one
    caller-derived string is *retry_cmd*, which carries the search term the
    caller just typed -- exactly what :func:`weld._graph_cli.\
missing_graph_message` already does with it.
    """
    if file_index_available(root):
        return None
    from weld._errors import default_summary
    from weld._worktree_seed import seed_blocked_reason

    cause = seed_blocked_reason(root)
    summary = default_summary(FILE_INDEX_MISSING)
    return structured_payload(
        FILE_INDEX_MISSING,
        detail=f"{summary}\n{cause}" if cause else None,
        retry_cmd=retry_cmd,
    )


def cannot_answer_block(payload: Mapping[str, object]) -> str:
    """Render *payload* as the CLI's stderr block, newline-terminated.

    The MCP payload splits what a terminal reader wants as one block across
    ``error`` / ``hint`` / ``retry``; this puts it back together in the
    shape the rest of the CLI already emits -- the shared
    ``error[<code>]: <summary> | hint: <hint>`` line first, so an agent
    scraping stderr finds the code where it finds every other one. The
    cause, when there is one, follows on its own lines rather than being
    folded into that line, where it would render as escaped ``\\x0a`` noise.

    A **pure** formatter: the terminal escape belongs at the write boundary
    (:mod:`weld._safe_text`), which is where :func:`ensure_file_index_exists`
    applies it -- the same shape ``ensure_graph_exists`` uses for the
    missing-graph block, and the same residual it accepts (a newline smuggled
    through the caller's own search term can forge a plausible extra line;
    that is output spoofing, not terminal control). The headline is the one
    exception, and not one this function makes: ``format_error_line`` escapes
    its own line because its contract *is* one line.
    """
    headline, _, cause = str(payload.get("error", "")).partition("\n")
    lines = [format_error_line(str(payload.get("error_code", "")), headline)]
    if cause:
        lines.append(cause)
    retry = payload.get("retry")
    if retry:
        lines.append(str(retry))
    return "\n".join(lines) + "\n"


def ensure_file_index_exists(root: Path | str, term: str) -> None:
    """Exit non-zero with guidance when ``find`` has no index to read.

    A no-op when an index is reachable, which is what keeps the cost of this
    guard a single ``stat`` on the served path. Call it *after* seeding (the
    CLI seeds ``find`` through ``_SEED_ONLY_COMMANDS``, MCP through
    :func:`weld._mcp_guard.resolve_dispatch_root`), so a worktree that could
    be repaired has been, and *before* the index is loaded, so the refusal
    replaces the empty answer rather than following it.

    The retry line is built by :func:`weld._graph_cli._build_retry_hint`, the
    one formatter every ``wd`` guidance block uses, so ``find``'s reads like
    its neighbours' rather than being quoted a second way here. Imported
    where it is used: both CLI dispatchers reach this module lazily, and the
    federated one is deliberately barred from importing its dispatcher back.
    """
    from weld._graph_cli import _build_retry_hint

    payload = missing_file_index_payload(root, _build_retry_hint("find", term))
    if payload is None:
        return
    sys.stderr.write(sanitize_terminal_text(cannot_answer_block(payload)))
    sys.exit(1)


def find_or_cannot_answer(
    root: Path | str, term: str, limit: int | None = None,
    *, retry_cmd: str = "weld_find",
) -> dict:
    """The whole ``find`` read path, guard included -- MCP's product half.

    Lives here rather than in :mod:`weld.mcp_server` because it is product
    behaviour, not server behaviour: the federated fan-out (ADR 0089), the
    index self-heal (bd yw4b) and now the precondition are what ``find``
    *is*, and a copy of any of them that only one surface ran would make the
    two answer differently about the same tree. The CLI reaches the same
    three through its own dispatcher, which has already split federated from
    single-repo by the time it gets here.

    A negative *limit* is ignored rather than refused -- the tolerance the
    MCP tool had before this moved.
    """
    root_path = Path(root)
    effective_limit = limit if limit is None or limit >= 0 else None
    unanswerable = missing_file_index_payload(root_path, retry_cmd)
    if unanswerable is not None:
        return unanswerable
    from weld.workspace_state import find_workspaces_yaml

    if find_workspaces_yaml(root_path) is not None:
        from weld._federation_find import federated_find

        return federated_find(root_path, term, limit=effective_limit)
    from weld._file_index_coverage import ensure_index_covers_surface
    from weld.file_index import load_file_index
    from weld.file_index_search import find_files

    ensure_index_covers_surface(root_path)
    return find_files(load_file_index(root_path), term, limit=effective_limit)
