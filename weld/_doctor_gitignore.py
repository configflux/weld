"""Doctor check for a stale-but-recognized `.weld/.gitignore` (ADR 0131).

Carved out of :mod:`weld.doctor` so the dispatcher stays under the
400-line CLAUDE.md cap, matching every other doctor concern already
split into its own file.

ADR 0131 gave `resync_weld_gitignore` (:mod:`weld._gitignore_writer`) a
write path that self-heals a `.weld/.gitignore` initialised before a
template line existed, but only `wd init` / `wd workspace bootstrap` run
it -- a checkout that runs `wd discover` constantly and never re-runs
either command gets no signal that its ignore file has fallen behind.
This check closes that gap on the read-only side: it reuses the exact
same recognition computation
(:func:`weld._gitignore_writer.missing_gitignore_lines`) to ask "what
would resync append here" without writing anything, and reports it.

Mirrors ADR 0131's own leave-alone posture instead of inventing a second
one:

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


__all__ = ["check_gitignore_resync"]
