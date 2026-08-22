"""Write `.weld/.gitignore` for `wd init` / `wd workspace bootstrap`.

Three policies, one helper. The default is *config-only*: it tracks
source-of-truth files (``discover.yaml``, ``workspaces.yaml``,
``agents.yaml``, ``strategies/``, ``adapters/``, ``README.md``) and
ignores **everything else** that weld writes -- per-machine state,
snapshots, locks, and the generated graphs themselves
(``graph.json``, ``agent-graph.json``). The principle is "track the
source-of-truth config, ignore everything weld can rebuild" -- a
contributor cloning the repo gets a clean ``git status`` instead of
megabyte-scale generated graph noise.

The opt-in *track-graphs* policy widens the default so the canonical
artifacts are tracked alongside config. This is the warm-CI / warm-MCP
workflow: teams that want every contributor to share an up-to-date
pre-built graph commit it to the repo. Pass ``track_graphs=True`` (or
``--track-graphs`` on the CLI).

What "the artifacts" means there is a decision, not a list (ADR 0110):
**each tracked artifact ships with the claim that explains it.** So
``graph.json`` travels with ``discovery-state.json``, and
``file-index.json`` travels with ``file-index-state.json``. An artifact
shipped without its claim is either believed blindly -- the ADR 0101
hole, where a clone holds a graph and no account of what it read -- or
disbelieved entirely, which costs every clone the full pass the mode
exists to avoid. Both companions are content-addressed (file hashes, a
``published_graph`` digest, ``meta.index_sha256``), so they stay true in
a checkout that did not build them and fail closed in one whose sources
have moved on.

The opt-in *ignore-all* policy writes ``*\\n!.gitignore`` instead.
For users still experimenting with weld who do not want any state
under version control yet. Pass ``ignore_all=True`` (or
``--ignore-all`` on the CLI). It is mutually exclusive with
``track_graphs``: passing both raises ``ValueError``.

**Deliberately absent from every template** (bd lt96): ``.weld/viz-views.json``
(saved visualizer views, :mod:`weld.viz._views`) and
``.weld/telemetry.disabled`` (the opt-out sentinel, :mod:`weld._telemetry`).
Both hold *persisted user intent* -- a decision someone made -- rather than
output weld can regenerate from source, so all three policies leave them
tracked. This mirrors the inclusion rule in :mod:`weld._git_bookkeeping`'s
``WELD_BOOKKEEPING_PATHS``: a template line is weld declaring "my output, I
can rebuild it", and neither file qualifies. Record this here rather than
re-deriving it on the next sweep of ``.weld/``.

All three modes are idempotent: if ``.weld/.gitignore`` already
exists, the helper leaves it alone and returns ``False``. The user
can replace or delete the file at any time. Switching *modes* is
still manual -- ``rm .weld/.gitignore && wd init`` -- because that is
a decision only the operator can make.

Staying in the same mode is a different question, and
:func:`resync_weld_gitignore` answers it: a checkout initialised
before a template gained a line (five times over so far --
``file-index-state.json``, ``auto-refresh.jsonl``, ``graph.write.lock``,
``telemetry.jsonl``, ``.enrichment-prompted``) no longer carries the
omission forever. It recognizes an existing file's content as one of
the three templates above by strict subset -- every line already
there must be a line that template ships *today* -- and, only then,
appends whichever current lines are missing. A file with even one
line it cannot account for (a hand-added pattern, foreign content) is
left completely untouched: recognition is all-or-nothing, never
partial. :func:`write_repo_git_policy` calls it right after the
skip-if-exists writer above, so both ``wd init`` and ``wd workspace
bootstrap`` self-heal an existing file of the same mode without ever
switching that mode.

The recognition-plus-diff computation itself is
:func:`missing_gitignore_lines`, factored out so :mod:`weld._doctor_gitignore`
can ask the read-only question ("what would resync append here") for
``wd doctor`` without importing the write path -- a checkout that runs
``wd discover`` constantly and never re-runs ``wd init`` gets a warning
instead of no signal at all (ADR 0131).
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "CONFIG_ONLY_GITIGNORE",
    "IGNORE_ALL_GITIGNORE",
    "TRACK_GRAPHS_GITIGNORE",
    "ignore_expresses_mode",
    "missing_gitignore_lines",
    "resync_weld_gitignore",
    "write_weld_gitignore",
]

CONFIG_ONLY_GITIGNORE = """\
# Managed by weld. Tracks config (discover.yaml, workspaces.yaml,
# agents.yaml, strategies/, adapters/, README.md) and ignores
# everything weld can rebuild -- graphs, the file index, and the
# bookkeeping beside them.
# Pass --track-graphs at init to track the graph and the file index,
# or delete this file to opt out and re-run `wd init`.
discovery-state.json
graph-previous.json
workspace-state.json
workspace.lock
graph.write.lock
query_state.bin
graph.json
graph.db
graph-meta.json
file-index.json
file-index-state.json
agent-graph.json
graph-communities.json
graph-community-report.md
graph-community-index.md
telemetry.jsonl
auto-refresh.jsonl
review-state.json
.enrichment-prompted
"""

TRACK_GRAPHS_GITIGNORE = """\
# Managed by weld (--track-graphs mode). Tracks config plus the
# artifacts that make a fresh checkout warm, each with the record that
# explains it:
#
#   graph.json       + discovery-state.json   (what the graph read)
#   agent-graph.json
#   file-index.json  + file-index-state.json  (what the index covers)
#
# Everything below is per-machine or derivable and stays ignored.
# Use this mode when the graph must be readable where `wd discover`
# cannot run. Delete this file to opt out, or replace its contents to
# customise.
graph-previous.json
workspace-state.json
workspace.lock
graph.write.lock
query_state.bin
graph.db
graph-meta.json
graph-communities.json
graph-community-report.md
graph-community-index.md
telemetry.jsonl
auto-refresh.jsonl
review-state.json
.enrichment-prompted
"""

IGNORE_ALL_GITIGNORE = """\
# Managed by weld (--ignore-all mode). Every weld file is ignored.
# Delete this file and re-run `wd init` to switch to the default.
*
!.gitignore
"""


def write_weld_gitignore(
    weld_dir: Path,
    *,
    ignore_all: bool = False,
    track_graphs: bool = False,
) -> bool:
    """Write ``<weld_dir>/.gitignore`` if missing. Idempotent skip-if-exists.

    Returns ``True`` when the file was created, ``False`` when it already
    existed and was left untouched. Creates *weld_dir* if necessary.

    Modes:

    - default: config-only -- ignores generated graphs and the file
      index along with per-machine state. Tracks ``discover.yaml`` /
      ``workspaces.yaml`` / ``agents.yaml`` / ``strategies/`` /
      ``adapters/``.
    - ``track_graphs=True``: as default but **also** tracks
      ``graph.json`` + ``discovery-state.json``, ``agent-graph.json``,
      and ``file-index.json`` + ``file-index-state.json`` (warm-CI /
      warm-MCP workflow).
    - ``ignore_all=True``: blanket-ignores every weld file.

    Raises ``ValueError`` when both ``ignore_all`` and ``track_graphs``
    are true. The two policies are mutually exclusive at the CLI
    layer too (argparse mutually-exclusive group).
    """
    if ignore_all and track_graphs:
        raise ValueError(
            "ignore_all and track_graphs are mutually exclusive: pass at "
            "most one of --ignore-all / --track-graphs",
        )
    target = weld_dir / ".gitignore"
    if target.exists():
        return False
    weld_dir.mkdir(parents=True, exist_ok=True)
    if ignore_all:
        contents = IGNORE_ALL_GITIGNORE
    elif track_graphs:
        contents = TRACK_GRAPHS_GITIGNORE
    else:
        contents = CONFIG_ONLY_GITIGNORE
    target.write_text(contents, encoding="utf-8")
    return True


def ignore_expresses_mode(
    text: str,
    *,
    ignore_all: bool = False,
    track_graphs: bool = False,
) -> bool:
    """True when a ``.weld/.gitignore`` reading *text* implements that mode.

    Asked because the writer above is skip-if-exists: passing
    ``--track-graphs`` to a repository that already has a config-only ignore
    file leaves the file alone, and the mode silently does not happen. The
    caller needs to know that, so it can say so instead of leaving a half-Mode
    B checkout whose ``.gitattributes`` declares a merge policy for artifacts
    the ignore file still hides (bd ilax).

    Judged on the *behaviour* rather than by comparing against the constants
    above: the managed files invite editing ("replace its contents to
    customise"), and a customised Mode B file is still Mode B. What separates
    the three modes is two questions -- is everything blanket-ignored, and is
    the graph ignored:

    - ``ignore_all`` wants the blanket.
    - ``track_graphs`` wants the graph *not* ignored, blanket included.
    - the default wants the graph ignored and no blanket, since a blanket
      would take ``discover.yaml`` with it.

    Negations are read as what they are: ``!graph.json`` under a blanket is a
    file that ends up tracked, so it does not read as ignored.
    """
    patterns = _pattern_lines(text)
    blanket = "*" in patterns
    negated = "!graph.json" in patterns
    graph_ignored = (blanket or "graph.json" in patterns) and not negated
    if ignore_all:
        return blanket
    if track_graphs:
        return not graph_ignored
    return graph_ignored and not blanket


def _pattern_lines(text: str) -> list[str]:
    """Non-blank, non-comment lines of *text*, order preserved, whitespace trimmed.

    Shared by :func:`ignore_expresses_mode` (a behavioural check: does this
    text *act like* a given mode) and :func:`resync_weld_gitignore` (a
    content check: which literal lines does this text already have). Neither
    reads nor writes comments or blank lines -- they carry no ignore policy.
    """
    return [
        line.strip() for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


#: Every mode's canonical template, keyed by the name :func:`_recognized_mode`
#: and :func:`resync_weld_gitignore` use internally. Order is for readability
#: only -- recognition and diffing both work in set terms.
_CANONICAL_TEMPLATES = {
    "ignore_all": IGNORE_ALL_GITIGNORE,
    "track_graphs": TRACK_GRAPHS_GITIGNORE,
    "default": CONFIG_ONLY_GITIGNORE,
}


def _recognized_mode(text: str) -> str | None:
    """Which canonical template *text* is a sub-copy of, or ``None``.

    Two passes, not one -- subset containment alone cannot pick a mode,
    because ``track_graphs``'s whole pattern set is *itself* a proper subset
    of ``default``'s (default additionally ignores ``graph.json`` and its
    warm-checkout siblings; track-graphs deliberately does not). Searching
    all three templates for "which one contains every line here" would find
    the config-only template as a candidate for *every* track-graphs file,
    stale or not, and calling that ambiguous would mean resync could never
    recognize track-graphs at all.

    So a candidate mode is chosen first, the same way
    :func:`ignore_expresses_mode` already would: blanket wins as
    ``ignore_all``; otherwise "graph.json is not ignored" wins as
    ``track_graphs``; otherwise ``default``. *Then*, and only then, every
    pattern line in *text* must be one that candidate's template ships
    *today* -- the file may be missing lines (that is the staleness
    :func:`resync_weld_gitignore` fixes), but nothing in it may be
    unaccounted for. A text with no pattern lines at all is never a
    candidate for anything: without that guard a lone comment such as
    ``"# do not touch\\n"`` would vacuously read as track-graphs (nothing in
    it ignores ``graph.json``, so by the letter of the predicate the graph
    is "not ignored").
    """
    existing_lines = _pattern_lines(text)
    if not existing_lines:
        return None
    if ignore_expresses_mode(text, ignore_all=True):
        candidate = "ignore_all"
    elif ignore_expresses_mode(text, track_graphs=True):
        candidate = "track_graphs"
    else:
        candidate = "default"
    canonical = set(_pattern_lines(_CANONICAL_TEMPLATES[candidate]))
    return candidate if set(existing_lines) <= canonical else None


def missing_gitignore_lines(text: str) -> list[str]:
    """Pattern lines the template recognized in *text* ships that *text* lacks.

    The pure "what would resync append here" computation -- given only the
    file's own content, never touching disk. Extracted so
    :func:`resync_weld_gitignore` (which acts on the answer) and the
    read-only ``wd doctor`` check in :mod:`weld._doctor_gitignore` (which
    only reports it) share one implementation instead of two that could
    drift apart. Returns ``[]`` when *text* is not a clean subset of exactly
    one known template (see :func:`_recognized_mode`) or when nothing is
    missing; otherwise the missing lines, in the recognized template's own
    order.
    """
    mode = _recognized_mode(text)
    if mode is None:
        return []
    existing = set(_pattern_lines(text))
    return [
        line for line in _pattern_lines(_CANONICAL_TEMPLATES[mode])
        if line not in existing
    ]


def resync_weld_gitignore(weld_dir: Path) -> list[str]:
    """Append canonical lines an existing ``.weld/.gitignore`` predates.

    The counterpart to :func:`write_weld_gitignore`'s skip-if-exists: this
    runs when the file *does* exist, so a checkout initialised before a
    template gained a line is not stuck with the omission forever (see the
    module docstring for the recurrence history). Returns the list of
    pattern lines appended, in the canonical template's own order -- ``[]``
    when nothing was missing, ``.weld/.gitignore`` does not exist, or its
    content is not recognized (see :func:`missing_gitignore_lines`).

    Never removes, reorders, or rewrites a single existing line, and never
    changes which mode is in effect: appending pattern lines a mode's own
    template already ships cannot flip :func:`ignore_expresses_mode`'s
    verdict for that mode. When nothing is missing, the file is not written
    at all -- not even an identical-bytes rewrite -- so an already-current
    checkout's ``.gitignore`` never gets a new mtime for no reason.
    """
    target = weld_dir / ".gitignore"
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    missing = missing_gitignore_lines(text)
    if not missing:
        return []
    separator = "" if text.endswith("\n") else "\n"
    count = len(missing)
    suffix = "" if count == 1 else "s"
    comment = f"# weld: {count} line{suffix} added by resync\n"
    target.write_text(
        text + separator + comment + "\n".join(missing) + "\n",
        encoding="utf-8",
    )
    return missing
