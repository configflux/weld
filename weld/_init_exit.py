"""What exit status a ``wd init`` run has actually earned.

``wd init`` does two separable things: it generates ``.weld/discover.yaml``,
and it seeds the managed git policy under ``.weld/``. Both are write-once --
an existing file is left alone rather than overwritten -- and the exit code
used to answer only about the first one. That made the documented Mode A ->
Mode B upgrade path (``rm .weld/.gitignore && wd init --track-graphs``, see
``docs/graph-tracking-policy.md``) write everything it was asked for, announce
success on stderr, and then exit 1 because ``discover.yaml`` already existed --
which reads as failure and aborts any ``set -e`` setup script (bd ilax).

The rule this module encodes is: **fail only when the run could not leave the
repository in the state it was asked for.**

- A ``discover.yaml`` left alone is a refusal only when the config is *all*
  that was asked for. Bare ``wd init`` on an initialised repo therefore still
  exits 1, with the "use --force" message it already printed: nothing else was
  requested, so nothing else happened.
- ``--track-graphs`` / ``--ignore-all`` is a second request. If the mode is in
  effect afterwards, that request succeeded and the run exits 0 -- saying, so
  the earlier "already exists" line is not mistaken for the verdict.
- If the mode is *not* in effect, the run exits 1 naming that, which is the
  case this file exists for. It was previously silent: passing
  ``--track-graphs`` to a repo whose config-only ignore file survives writes a
  ``.weld/.gitattributes`` declaring a merge policy for artifacts the ignore
  file still hides, leaving a half-Mode-B checkout and reporting the *config*
  as the problem.

What blocks Mode B need not be weld's own file, which is why the diagnostic
takes the culprit as an argument instead of assuming one. A repository whose
**root** ``.gitignore`` carries ``.weld/`` -- or whose ``.git/info/exclude``
or global ``core.excludesFile`` does -- hides the artifacts just as
completely, and the remedy is the opposite of the managed file's: that rule
is the user's, so it is edited, never deleted by weld or by advice from weld
(bd jya6). ``git check-ignore`` supplies the culprit as
``<file>:<line>:<pattern>``, so the message can point at the exact line.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from weld._safe_text import sanitize_terminal_line

if TYPE_CHECKING:  # pragma: no cover - annotation only, no runtime import
    from weld._gitattributes_writer import ManagedPolicy

__all__ = ["finish_init"]

#: What is wrong with the surviving ignore file, said in terms of the artifacts
#: rather than of the flag -- the flag is already in the sentence.
_MISMATCH = {
    "--track-graphs": "still ignores the artifacts --track-graphs asks to track",
    "--ignore-all": "does not blanket-ignore what --ignore-all asks to hide",
}


def _mode_flag(ignore_all: bool, track_graphs: bool) -> str | None:
    """The mode flag this run passed, or ``None`` for the default policy.

    The two are mutually exclusive at the CLI (argparse rejects both), so
    there is no third case to resolve.
    """
    if ignore_all:
        return "--ignore-all"
    if track_graphs:
        return "--track-graphs"
    return None


def _blocker(
    weld_dir: Path, blocking_rule: str | None, mode: str,
) -> tuple[str, str, str]:
    """``(what to name, why weld left it, what to do)`` for the blocking rule.

    Two cases, and the *remedy* is what separates them. When the culprit is
    weld's own ``.weld/.gitignore``, deleting the file and re-running is the
    documented switch procedure -- weld wrote it and will write it again.
    When the culprit is any other file, the rule belongs to the user: weld
    has no business advising them to delete their repository's
    ``.gitignore``, and the fix is to drop or narrow the one line that names
    ``.weld/``.

    A ``blocking_rule`` of ``None`` means git could not be asked (no
    checkout, no ``git`` on ``PATH``), so the verdict came from the managed
    file alone and only that file can be named.
    """
    managed = str(weld_dir / ".gitignore")
    if blocking_rule is None or blocking_rule.startswith(managed + ":"):
        return (
            blocking_rule or managed,
            "weld does not switch an existing ignore file's mode",
            f"Delete {managed} and re-run `wd init {mode}` to switch.",
        )
    return (
        blocking_rule,
        "that rule lives outside weld's managed .weld/.gitignore, so it is "
        "not weld's to rewrite",
        f"Remove or narrow that rule, then re-run `wd init {mode}`.",
    )


def finish_init(
    weld_dir: Path,
    *,
    config_written: bool,
    policy: ManagedPolicy,
    ignore_all: bool = False,
    track_graphs: bool = False,
) -> None:
    """End a ``wd init`` run: report, and exit non-zero only on a real failure.

    *config_written* is whether ``discover.yaml`` was generated (``False`` when
    an existing one was deliberately left alone). *policy* is
    :func:`weld._gitattributes_writer.write_repo_git_policy`'s whole answer,
    consulted only when a mode flag was passed -- without one the caller asked
    for no particular policy. Taken whole rather than destructured at the call
    site: the verdict and the rule that explains it come from one probe, and
    splitting them into two arguments is the seam through which a caller could
    pair a verdict with the wrong reason.

    Returns normally for success; raises ``SystemExit(1)`` otherwise.
    """
    mode = _mode_flag(ignore_all, track_graphs)
    if mode is not None and not policy.in_effect:
        subject, why, remedy = _blocker(weld_dir, policy.blocking_rule, mode)
        # Sanitized because *the pattern is file content*, not a weld-derived
        # path: it is whatever line somebody wrote in a ``.gitignore``, so it
        # can carry live control bytes straight into the operator's terminal
        # (:mod:`weld._safe_text`). ``_line`` rather than ``_text`` -- a
        # pattern containing a newline must not forge a second diagnostic
        # line under an escaped one.
        print(
            sanitize_terminal_line(
                f"{subject} {_MISMATCH[mode]}, and {why} -- so this "
                f"repository is not in {mode} mode."
            ),
            file=sys.stderr,
        )
        if track_graphs:
            print(
                f"({weld_dir / '.gitattributes'} is present either way: "
                "harmless while the artifacts stay ignored, and correct once "
                "they are not.)",
                file=sys.stderr,
            )
        print(
            sanitize_terminal_line(
                f"{remedy} The full procedure is under 'Switching' in "
                "docs/graph-tracking-policy.md."
            ),
            file=sys.stderr,
        )
        sys.exit(1)
    if config_written:
        return
    if mode is None:
        # init() already printed "already exists" / "use --force"; the config
        # was the whole request, and it was declined.
        sys.exit(1)
    print(
        f"Left discover.yaml as it is; the {mode} git policy is in effect.",
        file=sys.stderr,
    )
