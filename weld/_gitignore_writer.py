"""Write `.weld/.gitignore` for `wd init` / `wd workspace bootstrap`.

Two policies, one helper. The default is *selective*: it tracks
source-of-truth files (``discover.yaml``, ``workspaces.yaml``,
``agents.yaml``, ``graph.json``, ``agent-graph.json``, ``strategies/``,
``adapters/``, ``README.md``) and only ignores per-machine state and
snapshots. The principle is "track the source-of-truth, ignore the
runtime" -- the same convention used by other git-tracked tooling
state directories.

The opt-in *ignore-all* policy writes ``*\\n!.gitignore`` instead.
For users still experimenting with weld who do not want any state
under version control yet. To switch back: delete the file; the next
init/bootstrap reseeds the selective default.

Both modes are idempotent: if ``.weld/.gitignore`` already exists, the
helper leaves it alone and returns ``False``. The user can replace or
delete the file at any time.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "IGNORE_ALL_GITIGNORE",
    "SELECTIVE_GITIGNORE",
    "write_weld_gitignore",
]

SELECTIVE_GITIGNORE = """\
# Managed by weld. Tracks config and the canonical graphs;
# ignores per-machine state, snapshots, and locks.
# Delete this file to opt out, or replace its contents to customise.
discovery-state.json
graph-previous.json
workspace-state.json
workspace.lock
query_state.bin
"""

IGNORE_ALL_GITIGNORE = """\
# Managed by weld (--ignore-all mode). Every weld file is ignored.
# Delete this file and re-run `wd init` to switch to the selective default.
*
!.gitignore
"""


def write_weld_gitignore(weld_dir: Path, *, ignore_all: bool = False) -> bool:
    """Write ``<weld_dir>/.gitignore`` if missing. Idempotent skip-if-exists.

    Returns ``True`` when the file was created, ``False`` when it already
    existed and was left untouched. Creates *weld_dir* if necessary.
    """
    target = weld_dir / ".gitignore"
    if target.exists():
        return False
    weld_dir.mkdir(parents=True, exist_ok=True)
    contents = IGNORE_ALL_GITIGNORE if ignore_all else SELECTIVE_GITIGNORE
    target.write_text(contents, encoding="utf-8")
    return True
