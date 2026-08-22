"""How a strategy records the files it read (ADR 0017, bd 8ia5 / bd od2a).

``StrategyResult.discovered_from`` is provenance -- the repo-relative path of
every file a strategy read -- and it is never node *identity*. Identity lives
in the node's own props (``python_package`` records its directory in
``props["dir"]``), so "this node is a directory" is not a reason to record a
directory here.

Twenty-two strategies once derived the field from a directory instead, in two
shapes that were wrong for one reason:

* from the glob *pattern* -- ``(root / pattern).parent`` (bd 8ia5);
* from each matched file's *parent* -- ``f.parent.relative_to(root)``
  (bd jv5d, bd od2a).

Either degenerates at the repo root to ``"./"``, the marker
:func:`weld._git._path_is_tracked` reads as "every path here is tracked
source", which widens ``source_stale`` to the whole tree permanently. Both
are now expressed through the two functions here, so the degeneration is
handled once rather than filtered strategy by strategy -- and
``strategy_provenance_shape_test`` refuses the raw ``.parent`` form
structurally, so it cannot come back a twenty-third time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

__all__ = ["directory_provenance", "file_provenance"]


def file_provenance(root: Path, paths: Iterable[Path]) -> list[str]:
    """Repo-relative ``discovered_from`` entries for the files a strategy read.

    The per-file form every strategy owes ADR 0017. Sorted for determinism
    (the graph body must be byte-identical across runs) and deduplicated; a
    path outside *root* is dropped rather than recorded as an absolute path
    that no staleness prefix could match.

    Record the whole resolved match list, not the subset a run happened to
    parse: under ADR 0084 dirty-scoping a strategy parses only the dirty
    files, and narrowing provenance to those would drop every clean sibling's
    claim on the next incremental pass.
    """
    out: set[str] = set()
    for path in paths:
        try:
            out.add(path.relative_to(root).as_posix())
        except ValueError:
            continue
    return sorted(out)


def directory_provenance(
    root: Path, rel_dir: str, members: Iterable[Path],
) -> list[str]:
    """Provenance for a node that *is* a directory (a package).

    Returns the directory itself for a real subdirectory -- the one case
    where a directory entry is load-bearing rather than a stand-in for the
    files under it, because a package's membership changes when a file is
    added beside its siblings.

    At the repo root that entry would be ``"./"``, so *members* are recorded
    instead. Dropping the entry outright (the ``csharp_package`` one-off this
    replaces) is not the safe fallback it looks like: a package with no
    provenance at all is a package no source change can ever mark stale.
    """
    normalized = rel_dir.strip("/")
    if normalized and normalized != ".":
        return [normalized + "/"]
    return file_provenance(root, members)
