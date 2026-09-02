"""What ``wd init --refresh`` says it did, on stderr.

Split from :mod:`weld._init_refresh` at the 400-line cap, along the seam that
was already there: that module decides what to merge and writes the file, and
this one turns the :class:`weld._init_refresh.RefreshResult` into the lines a
maintainer reads. The reporting had moved out of :mod:`weld.init` for the same
reason once already, and it grew again when a refresh gained a second thing to
report -- entries, which are named rather than counted.
"""

from __future__ import annotations

import sys
from pathlib import Path

from weld._init_refresh import refresh
from weld._safe_text import sanitize_terminal_line


def run_refresh(root: Path, output: Path) -> None:
    """CLI entry for ``wd init --refresh``: run the merge and report on stderr.

    A missing config is an explicit cannot-answer (ADR 0134): refresh edits, it
    does not create, so it points at ``wd init`` rather than silently writing a
    fresh config.
    """
    result = refresh(root, output)
    if result is None:
        print(
            f"No discover.yaml at {output.name} to refresh: run `wd init` first "
            "to bootstrap a config, then `wd init --refresh` to merge in "
            "newly-detected strategies.",
            file=sys.stderr,
        )
        return
    if not result.wired and not result.entries:
        # A run that wires nothing can still write the file -- bumping the
        # stamp, or seeding the wired-entry record of a config that predates
        # it -- so the tail names what moved rather than claiming nothing did.
        moved = [
            label for label, changed in (
                ("version stamp", result.stamp_updated),
                ("wired-entry record", result.record_updated),
            ) if changed
        ]
        tail = (f"Refreshed the {' and the '.join(moved)}."
                if moved else "Nothing to change.")
        print(
            "discover.yaml is already current: every language present on disk "
            "has a wired strategy, and every detected entry is either wired "
            f"or was removed on purpose. {tail}",
            file=sys.stderr,
        )
        return
    print(
        f"Merged newly-detected sources into {output.name}, preserving your "
        "existing entries:",
        file=sys.stderr,
    )
    for item in result.wired:
        label = "C#" if item.language == "csharp" else item.language
        # A count of one is ordinary now that a claim is per file rather than
        # per language (ADR 0144) -- one unread ``.tsx`` beside a wired
        # ``**/*.ts`` is the common case -- so the plural is not assumed.
        plural = "file" if item.file_count == 1 else "files"
        print(
            sanitize_terminal_line(
                f"  wired {label} ({item.file_count} {plural})"),
            file=sys.stderr,
        )
    for strategy, target in result.entries:
        # Named by what was wired rather than counted: an entry is one thing,
        # and the target is the string a maintainer greps their config for --
        # and the one they delete from the record to re-offer it.
        print(
            sanitize_terminal_line(f"  wired {strategy} entry for {target}"),
            file=sys.stderr,
        )
    print("Run `wd discover` to rebuild the graph with the new sources.",
          file=sys.stderr)
