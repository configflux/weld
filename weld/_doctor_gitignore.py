"""Doctor checks for what the repository's ignore policy does to `.weld/`.

Two questions, both answered read-only, both about a file weld manages
but git owns the consequences of:

- is `.weld/.gitignore` itself behind the template weld would write
  today (:func:`check_gitignore_resync`, ADR 0131);
- does the ignore policy keep `.weld/discover.yaml` out of git, which
  disables worktree seeding repository-wide
  (:func:`check_seeding_config`, ADR 0096 §2 gate 5).

:func:`check_gitignore` runs both; it is what :mod:`weld.doctor` calls.

Carved out of :mod:`weld.doctor` so the dispatcher stays under the
400-line CLAUDE.md cap, matching every other doctor concern already
split into its own file.

ADR 0131 gave `resync_weld_gitignore` (:mod:`weld._gitignore_writer`) a
write path that self-heals a `.weld/.gitignore` initialised before a
template line existed, but only `wd init` / `wd workspace bootstrap` run
it -- a checkout that runs `wd discover` constantly and never re-runs
either command gets no signal that its ignore file has fallen behind.
:func:`check_gitignore_resync` closes that gap on the read-only side: it
reuses the exact same recognition computation
(:func:`weld._gitignore_writer.missing_gitignore_lines`) to ask "what
would resync append here" without writing anything, and reports it.

It mirrors ADR 0131's own leave-alone posture instead of inventing a
second one:

- recognized template, missing lines: ``warn``, naming the lines and the
  fix (``wd init``).
- recognized and already current, or content resync cannot fully account
  for (hand-edited, foreign, near-empty): silent. A file weld would not
  touch on the write side is not weld's place to nag about on the read
  side either.
- `.weld/.gitignore` absent: silent, matching every other doctor check
  for an optional/absent managed file (e.g. :mod:`weld._doctor_sqlite`,
  :mod:`weld._doctor_staleness`).
- unreadable or undecodable: silent, reusing the same fail-closed idiom
  `resync_weld_gitignore` itself uses (``except (OSError,
  UnicodeDecodeError)``) -- a doctor check must never crash the whole
  `wd doctor` run over one sidecar file it cannot even open.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def check_gitignore_resync(weld_dir: Path, result_cls: type[Any]) -> list[Any]:
    """Return a `[warn]` when `.weld/.gitignore` is missing known lines."""
    target = weld_dir / ".gitignore"
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    from weld._gitignore_writer import missing_gitignore_lines

    missing = missing_gitignore_lines(text)
    if not missing:
        return []
    count = len(missing)
    suffix = "" if count == 1 else "s"
    return [
        result_cls(
            "warn",
            f".weld/.gitignore is missing {count} known bookkeeping "
            f"line{suffix} ({', '.join(missing)}) -- run `wd init` to update it",
            "Config",
        ),
    ]


def check_seeding_config(weld_dir: Path, result_cls: type[Any]) -> list[Any]:
    """Return a `[note]` when git will not carry `discover.yaml` to a worktree.

    Worktree seeding (ADR 0096 §2 gate 5) reads the *worktree's own*
    ``discover.yaml``, and git only puts that file in a new checkout when
    the repository tracks it. A repository that ignores it -- the shape
    ``wd init --ignore-all`` writes, or a repo-level ``.gitignore``
    naming ``.weld/`` -- has therefore turned seeding off for every
    worktree it will ever have, and nothing says so: `wd doctor` reports
    a healthy project and the first read in each new worktree falls back
    to the ordinary first-run guidance. Field eval 0.23.1 finding 09 is
    that silence, reported from the other end.

    A ``note`` rather than a ``warn``, and ack-able: ignoring all of
    ``.weld/`` is a legitimate policy with a real tradeoff attached, not
    a mistake. The complementary half of the fix states the same cause on
    the read that hits it (:func:`weld._worktree_seed.seed_blocked_reason`).

    *weld_dir*'s parent is the repository root git is asked about, which
    holds by construction: :func:`weld.doctor.doctor` derives ``weld_dir``
    as ``root / ".weld"``, the same way every check here takes it.

    Silent when there is no config to carry (``_check_discover_yaml``
    already fails on that, and two findings about one absent file is
    noise), and silent whenever git cannot answer -- outside a
    repository, or with no ``git`` binary -- on the same fail-closed rule
    as every check above.
    """
    if not (weld_dir / "discover.yaml").is_file():
        return []
    from weld._git_worktree import discover_config_is_ignored

    if not discover_config_is_ignored(weld_dir.parent):
        return []
    return [
        result_cls(
            "note",
            ".weld/discover.yaml is not tracked by git (the ignore rules "
            "cover it), so linked worktrees of this repository start "
            "without it and cannot seed a graph from another checkout -- "
            "`git add -f .weld/discover.yaml` to enable worktree seeding",
            "Config",
            note_id="worktree-seeding-config-ignored",
        ),
    ]


def check_gitignore(weld_dir: Path, result_cls: type[Any]) -> list[Any]:
    """Run both ignore-policy checks; the entry point :mod:`weld.doctor` calls."""
    return (
        check_gitignore_resync(weld_dir, result_cls)
        + check_seeding_config(weld_dir, result_cls)
    )


__all__ = ["check_gitignore", "check_gitignore_resync", "check_seeding_config"]
