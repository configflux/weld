"""Strategy: Static config file nodes.

Emits one ``config:`` node per named file -- a static data file the program
*reads at runtime*, whose content decides behaviour. That is the vocabulary
slot for weld's own shipped data too: a language pack under
``weld/languages/`` decides what discovery and query do for that language,
and a scaffolding template under ``weld/templates/`` decides what
``wd init`` writes into a target repo. Both are read, never imported, so no
Python strategy anchors them; without a node here, ``wd impact
weld/languages/python.yaml`` answers "not found" for a file the Tier-1
harness, discovery and the query surface all read, and "which targets and
tests consume this" is unanswerable (bd q85a).

Two ways to name the files, and both go through the same emitter:

* ``files:`` -- an explicit list. The original shape, kept verbatim.
* ``glob:`` -- a pattern, resolved through
  :func:`weld.strategies._glob_resolve.resolve_glob` exactly as every other
  glob strategy resolves one, with the entry's ``exclude`` honoured.

``glob:`` exists because enumeration does not generalise: the recursive-glob
lesson this repo already paid for once (bd crau) is that a hand-listed set
leaves the *next* file invisible, and stays invisible until somebody trips
over it. ``.weld/discover.yaml``'s own resolver has always accepted
``glob``, ``path`` and ``files`` interchangeably
(:func:`weld._source_resolve.resolve_source_files`), so a ``glob`` entry was
already being resolved for incremental hashing and coverage while this
strategy quietly read none of it -- the config surface promised something
the strategy did not honour.
"""

from __future__ import annotations

from pathlib import Path

from weld._node_ids import config_id
from weld._rel_path import rel_to_root
from weld.repo_boundary import path_within_repo_boundary
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult


def _configured_paths(root: Path, source: dict) -> list[str]:
    """Repo-relative entries this source names, ``glob`` first then ``files``.

    Returned as *strings spelled the way the config named them*, because the
    node id is minted from the configured entry rather than from the resolved
    path (see :func:`extract`). A glob match has no "as configured" spelling
    of its own, so its repo-relative POSIX path is the configured entry.

    The glob half resolves through ``resolve_glob`` with the entry's excludes
    and nothing else -- deliberately the identical call
    :func:`weld._source_resolve.resolve_source_files` makes. A second,
    differently-spelled filter here (a bare basename ``should_skip``, say)
    would let the strategy emit a set the resolver did not record, which reads
    as permanently uncovered scope: ``coverage_stale`` never clears and every
    read re-runs discovery. One resolution rule, applied once.
    """
    excludes = [p for p in (source.get("exclude") or []) if p]
    out: list[str] = []
    pattern = source.get("glob")
    if pattern:
        out.extend(
            rel_to_root(match, root)
            for match in resolve_glob(root, pattern, excludes)
        )
    out.extend(source.get("files", []))
    return out


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Create config nodes for the entry's ``glob`` and/or ``files``."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    for filepath in _configured_paths(root, source):
        full = root / filepath
        # ``is_file``, not ``exists``: a single-directory glob resolves
        # through ``Path.glob``, which yields matching *directories* as well
        # as files (the ``**`` branch of ``walk_glob`` yields only files, so
        # the two disagree). A ``config:`` node for a directory names a path
        # no reader can open, and ``build_file_hashes`` silently drops it
        # anyway -- so it would be a node with no content and no incremental
        # basis. The tightening applies to ``files:`` too, where naming a
        # directory has always been a mistake rather than a feature.
        if not full.is_file():
            continue
        if not path_within_repo_boundary(root, full):
            continue
        rel_path = rel_to_root(full, root)
        discovered_from.append(rel_path)
        # bd hxsi: the spelling rule lives in weld._node_ids (ADR 0041), which
        # the referring strategies read through weld.strategies._target_ids.
        # It is applied to the *configured entry*, not ``rel_path``, which is
        # what it has always been applied to -- the two differ for an entry
        # written with a leading "./", and re-pointing it would silently
        # rename those nodes.
        nid = config_id(filepath)
        nodes[nid] = {
            "type": "config",
            "label": Path(filepath).name,
            "props": {
                "file": rel_path,
                "source_strategy": "config_file",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["config"],
            },
        }

    return StrategyResult(nodes, edges, discovered_from)
