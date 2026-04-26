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
graphs (``graph.json``, ``agent-graph.json``) are tracked alongside
config. This is the warm-CI / warm-MCP workflow: teams that want
every contributor to share an up-to-date pre-built graph commit it
to the repo. Pass ``track_graphs=True`` (or ``--track-graphs`` on
the CLI).

The opt-in *ignore-all* policy writes ``*\\n!.gitignore`` instead.
For users still experimenting with weld who do not want any state
under version control yet. Pass ``ignore_all=True`` (or
``--ignore-all`` on the CLI). It is mutually exclusive with
``track_graphs``: passing both raises ``ValueError``.

All three modes are idempotent: if ``.weld/.gitignore`` already
exists, the helper leaves it alone and returns ``False``. The user
can replace or delete the file at any time. Pre-existing checkouts
that were seeded by an older `wd init` retain whatever policy was
active when they were first initialised; manual migration is
``rm .weld/.gitignore && wd init``.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "CONFIG_ONLY_GITIGNORE",
    "IGNORE_ALL_GITIGNORE",
    "TRACK_GRAPHS_GITIGNORE",
    "write_weld_gitignore",
]

CONFIG_ONLY_GITIGNORE = """\
# Managed by weld. Tracks config (discover.yaml, workspaces.yaml,
# agents.yaml, strategies/, adapters/, README.md) and ignores
# everything weld can rebuild -- including generated graphs.
# Pass --track-graphs at init to track graph.json + agent-graph.json,
# or delete this file to opt out and re-run `wd init`.
discovery-state.json
graph-previous.json
workspace-state.json
workspace.lock
query_state.bin
graph.json
agent-graph.json
"""

TRACK_GRAPHS_GITIGNORE = """\
# Managed by weld (--track-graphs mode). Tracks config and the
# canonical graphs; ignores per-machine state, snapshots, and locks.
# Use this when CI / MCP / contributors should share the pre-built
# graph (warm cold path). Delete this file to opt out, or replace
# its contents to customise.
discovery-state.json
graph-previous.json
workspace-state.json
workspace.lock
query_state.bin
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

    - default: config-only -- ignores generated graphs along with
      per-machine state. Tracks ``discover.yaml`` / ``workspaces.yaml``
      / ``agents.yaml`` / ``strategies/`` / ``adapters/``.
    - ``track_graphs=True``: as default but **also** tracks
      ``graph.json`` and ``agent-graph.json`` (warm-CI / warm-MCP
      workflow).
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
