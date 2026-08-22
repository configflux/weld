"""Write `.weld/.gitattributes` for Mode B, and register its merge driver.

Mode B (``wd init --track-graphs``) commits artifacts that are *derived
from source*. Two branches that both re-discover therefore both rewrite
them, and git has no way to know the two versions are not really in
disagreement -- they are two renderings of two different source trees,
and the merge of those source trees has exactly one correct rendering,
which is neither of them. Resolving such a conflict by choosing hunks is
never right; regenerating is (ADR 0110).

So the rule this module encodes is: **keep one side, and let freshness
rebuild.** ``merge=weld-regenerable`` maps the tracked artifacts to a
driver whose command is ``true`` -- it succeeds without writing, which
leaves git's ``%A`` (the current branch's copy) as the result. The merge
commit then has a new ``HEAD``, so the next read sees the graph behind it
and the ordinary auto-refresh path rebuilds from the merged sources. The
artifacts stay mutually consistent because they all resolve the same way:
``graph.json`` and its ``discovery-state.json`` both come from *our*
side, so the inventory still vouches for the body beside it.

**Why not regenerate inside the driver.** Git's merge machinery computes
every path's content merge before it updates the working tree, so a
driver that shelled out to ``wd discover`` would read the *pre-merge*
sources -- producing our graph, at the cost of a full discovery, while
looking like it had produced the merged one. Deferring the rebuild to the
read path is both cheaper and the only sound ordering.

**Why the driver has to be registered per clone.** ``ours`` is not a
built-in git merge driver -- only ``text``, ``binary`` and ``union`` are
-- and git deliberately does not clone ``merge.*.driver`` config, since
that would let a repository run commands on the machine of anyone who
clones it. The ``.gitattributes`` file is tracked, so every clone
inherits the *intent*; the one-line registration is named in its own
header and performed by :func:`register_merge_driver` at
``wd init --track-graphs``. A clone that never runs it gets ordinary
conflict markers -- the behaviour it has today -- not a wrong merge.

Mode A writes no ``.gitattributes`` at all: it commits none of these
files, so there is nothing to merge.

:func:`write_repo_git_policy` is the entry point every caller uses. It
lives here rather than beside the ignore writer because the ignore
policy is mode-agnostic while the merge policy exists only for Mode B --
so this module is the one that knows both halves, and the direction of
the import follows.

Idempotent like :mod:`weld._gitignore_writer`: an existing
``.weld/.gitattributes`` is left alone.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from weld._gitignore_probe import check_ignore
from weld._gitignore_writer import (
    ignore_expresses_mode, resync_weld_gitignore, write_weld_gitignore,
)

__all__ = [
    "MERGE_DRIVER_NAME",
    "TRACK_GRAPHS_GITATTRIBUTES",
    "ManagedPolicy",
    "register_merge_driver",
    "write_repo_git_policy",
    "write_weld_gitattributes",
]


class ManagedPolicy(NamedTuple):
    """Whether the requested ignore mode is in effect, and what blocks it.

    *blocking_rule* is git's ``<source>:<line>:<pattern>`` for the rule that
    hides the artifacts, or ``None`` -- either because nothing blocks the
    mode, or because git could not be asked and the verdict came from the
    managed file alone. The caller turns it into the diagnostic
    (:mod:`weld._init_exit`); the two fields travel together so the verdict
    and its reason can never be computed from two different probes.
    """

    in_effect: bool
    blocking_rule: str | None = None


#: Attribute value, git config key stem, and the name a reader sees in a
#: conflict. Deliberately weld-specific rather than the conventional
#: ``ours``: a repo may already define ``merge.ours`` for something else,
#: and the name should say *why* discarding a side is safe here.
MERGE_DRIVER_NAME = "weld-regenerable"

#: Written verbatim into ``.weld/.gitattributes``. Patterns in a
#: ``.gitattributes`` are relative to its own directory, so these bare
#: basenames match ``.weld/graph.json`` and friends and nothing outside
#: ``.weld/`` -- which is why this lives here rather than in the repo-root
#: ``.gitattributes`` weld has no business rewriting.
TRACK_GRAPHS_GITATTRIBUTES = f"""\
# Managed by weld (--track-graphs mode). Everything below is derived
# from your source, so a merge conflict in one is resolved by
# regenerating, never by editing: `merge={MERGE_DRIVER_NAME}` keeps the
# current branch's copy and the next weld read rebuilds it from the
# merged sources.
#
# Git does not clone merge-driver config (it would let a repository run
# commands on your machine), so each clone registers it once:
#
#     git config merge.{MERGE_DRIVER_NAME}.driver true
#
# `wd init --track-graphs` runs that for you in the checkout it is run
# in. Without it you get ordinary conflict markers; resolve them with
# `git checkout --ours .weld/graph.json && wd discover`.
graph.json merge={MERGE_DRIVER_NAME}
agent-graph.json merge={MERGE_DRIVER_NAME}
discovery-state.json merge={MERGE_DRIVER_NAME}
file-index.json merge={MERGE_DRIVER_NAME}
file-index-state.json merge={MERGE_DRIVER_NAME}
"""


def write_weld_gitattributes(weld_dir: Path) -> bool:
    """Write ``<weld_dir>/.gitattributes`` if missing. Skip-if-exists.

    Returns ``True`` when the file was created, ``False`` when it already
    existed and was left untouched. Creates *weld_dir* if necessary.

    Only Mode B calls this. The caller decides; this helper has no mode
    switch of its own, because there is no Mode A content for it to write.
    """
    target = Path(weld_dir) / ".gitattributes"
    if target.exists():
        return False
    Path(weld_dir).mkdir(parents=True, exist_ok=True)
    target.write_text(TRACK_GRAPHS_GITATTRIBUTES, encoding="utf-8")
    return True


def register_merge_driver(root: Path) -> bool:
    """Register ``merge.weld-regenerable.driver`` in *root*'s local git config.

    Returns ``True`` when the driver is registered in *root* after this
    call (including when it already was), ``False`` when it could not be
    -- *root* is not a git checkout, ``git`` is not installed, or the
    config write failed. Never raises: failing to register costs conflict
    markers on a future merge, which is strictly better than failing an
    ``init``.

    ``--local`` scopes the key to this repository, so nothing this writes
    can affect another checkout on the machine. The value ``true`` is the
    coreutils no-op: git invokes it with the three temp files, it exits 0
    without writing any of them, and git takes that as "the driver
    resolved it", leaving ``%A`` -- our side -- as the merged content.

    Re-running is safe and is the intended way to fix a fresh clone: the
    write is unconditional rather than checked-then-set, so a key some
    other tool left pointing elsewhere is corrected rather than trusted.
    """
    key = f"merge.{MERGE_DRIVER_NAME}.driver"
    try:
        completed = subprocess.run(
            ["git", "config", "--local", key, "true"],
            cwd=str(root),
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def write_repo_git_policy(
    root: Path,
    weld_dir: Path,
    *,
    ignore_all: bool = False,
    track_graphs: bool = False,
    announce: bool = True,
) -> ManagedPolicy:
    """Seed *root*'s whole managed ``.weld/`` git policy. Idempotent.

    One entry point because the policy is two questions, not one: the
    ``.gitignore`` decides *what is committed*, and -- in Mode B only --
    the ``.gitattributes`` plus its registered driver decide *what happens
    when two branches commit different renderings of it*. Splitting them
    across call sites is how one of them gets forgotten; ``wd init`` and
    ``wd workspace bootstrap`` both come through here.

    Also the one entry point for keeping an *existing* ``.gitignore`` in
    sync: right after the skip-if-exists write,
    :func:`weld._gitignore_writer.resync_weld_gitignore` appends whatever
    pattern lines a recognized file's template gained since the checkout
    was initialised. That call is unconditional and mode-agnostic -- it
    runs for every mode, not only Mode B -- because it never switches which
    mode is in effect, only completes the one already there.

    A polyrepo child is its own git repository, so *root* must be the
    repository that owns *weld_dir* -- registering the driver once at a
    workspace root would leave every child's merges unresolved.

    *announce* prints to stderr what was done and what a fresh clone still
    owes (stderr, so a piped ``--json`` reader is unaffected). Bootstrap
    passes ``False``: one such block per child would drown the run.

    Returns a :class:`ManagedPolicy`: whether the mode this call asked for is
    in effect, and the rule that blocks it when it is not. It answers for the
    mode in the arguments, so a default call answers about the config-only
    policy; a caller that passed no mode flag has asked for nothing in
    particular and should not consult it. ``in_effect=False`` means the mode
    did not happen -- an ignore file from an earlier init survived (the writer
    never rewrites one), or a rule outside weld's managed file hides the
    artifacts -- and only the caller knows what to do about that
    (:mod:`weld._init_exit`).
    """
    write_weld_gitignore(
        weld_dir, ignore_all=ignore_all, track_graphs=track_graphs,
    )
    resynced = resync_weld_gitignore(weld_dir)
    if resynced and announce:
        count = len(resynced)
        suffix = "" if count == 1 else "s"
        print(
            f"Resynced {weld_dir / '.gitignore'}: added {count} "
            f"line{suffix} it predated.",
            file=sys.stderr,
        )
    policy = _ignore_in_effect(
        root, weld_dir, ignore_all=ignore_all, track_graphs=track_graphs,
    )
    if not track_graphs:
        # Mode A and --ignore-all commit none of it; nothing to merge.
        return policy
    wrote = write_weld_gitattributes(weld_dir)
    registered = register_merge_driver(root)
    if not announce:
        return policy
    if wrote:
        print(f"Wrote {weld_dir / '.gitattributes'}", file=sys.stderr)
    if registered:
        print(
            f"Registered merge.{MERGE_DRIVER_NAME}.driver in this checkout. "
            "Git does not clone that, so each clone runs "
            f"`git config merge.{MERGE_DRIVER_NAME}.driver true` once.",
            file=sys.stderr,
        )
    else:
        print(
            f"note: could not register merge.{MERGE_DRIVER_NAME}.driver in "
            f"{root} -- run `git config merge.{MERGE_DRIVER_NAME}.driver true` "
            "there once, or tracked-graph merges will need manual resolution.",
            file=sys.stderr,
        )
    return policy


def _ignore_in_effect(
    root: Path, weld_dir: Path, *, ignore_all: bool, track_graphs: bool,
) -> ManagedPolicy:
    """Is the mode asked for actually in effect for the repository at *root*?

    Mode B asks one question -- **will git commit the graph** -- and only git
    can answer it, because weld manages one layer of an ignore stack with
    several. So ``--track-graphs`` is judged by
    :func:`weld._gitignore_probe.check_ignore` on ``graph.json``, which
    subsumes the managed-file predicate (a config-only ``.weld/.gitignore``
    lists ``graph.json``, so git names *it* as the culprit, with a line
    number the parser could not supply) and additionally catches the rules
    weld does not manage -- a root ``.gitignore`` carrying ``.weld/``,
    ``.git/info/exclude``, a global ``core.excludesFile`` (bd jya6).

    The parsed managed file remains the fallback for the one case git cannot
    answer: ``wd init`` outside a checkout, which is supported and must stay
    supported.

    The other two modes are deliberately *not* re-judged by git. Their
    request is "hide what weld writes", and a rule outside the managed file
    that hides more of ``.weld/`` delivers that request rather than defeating
    it -- there is nothing for the probe to catch. ``--ignore-all`` also
    wants the blanket, which is a property of the managed file's text, not of
    any one path's ignore status.

    Unreadable counts as "not in effect": the caller turns this into a
    diagnostic naming the file, and a ``.weld/.gitignore`` that cannot be read
    -- including one that cannot even be decoded -- is a thing the user needs
    to look at either way.
    """
    if track_graphs:
        verdict = check_ignore(Path(root), Path(weld_dir) / "graph.json")
        if verdict.answered:
            return ManagedPolicy(not verdict.ignored, verdict.rule)
    try:
        text = (Path(weld_dir) / ".gitignore").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ManagedPolicy(False, None)
    return ManagedPolicy(
        ignore_expresses_mode(
            text, ignore_all=ignore_all, track_graphs=track_graphs,
        ),
        None,
    )
