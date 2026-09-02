"""The entry-shaped half of ``wd init --refresh``: what a *language* is not.

``--refresh``'s unit of work was an unclaimed language (ADR 0135, ADR 0144).
That unit can never emit a root-config entry or a framework entry, because
neither is a language: when ``tsconfig.json`` joined
:data:`weld.init_detect_constants.ROOT_CONFIG_NAMES`, every existing
TypeScript project kept a graph with no ``config:tsconfig_json`` node, and the
only command that added the entry was ``wd init --force`` -- the one that
discards the hand edits ``--refresh`` exists to preserve.

This module is the second comparison, keyed on the **entry** rather than on
the language. A key is ``(strategy, target)``: one key per name for a
``files:`` entry, one per pattern for a ``glob:`` / ``path:`` entry -- the
granularity at which a config actually wires a thing, and the granularity at
which one can be missing. Per-name keying is what lets a config carrying
``files: ["package.json"]`` be offered ``tsconfig.json`` and nothing else,
rather than a second whole-list entry that repeats what is already wired.

Two detector families are wired here, the two the issue measured:

* **Root configs** -- one ``config_file`` entry over the well-known manifests
  present at the repository root.
* **Framework entries** -- express, Next.js, gin, axum and the Python set
  (SQLAlchemy, FastAPI, Flask, Pydantic, outbound HTTP). These *are* gated on
  a language, exactly as ``wd init`` gates them, but on the language being
  **present** rather than on it being unclaimed -- which is the gap: a repo
  whose TypeScript is fully claimed and which has just grown an express server
  has nothing for the language comparison to say and a missing entry all the
  same.

Both reuse the builders ``wd init`` emits from
(:mod:`weld._init_framework_sources`), so a refreshed config is
indistinguishable from a freshly generated one rather than a second spelling
of it -- the drift :mod:`weld._init_language_entries` ended for the language
half.

The remaining artifact-shaped entries a full init writes -- docs directories
and the markdown fallback, Dockerfiles, compose files, CI workflows, the
``.claude`` agent and command entries, the C++ build-system probe, and the
ROS2 / interface stacks when no language happens to be unclaimed -- are the
same class and are not wired here yet. The table is what to add a row to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from weld._init_framework_sources import (
    _add_framework_sources,
    _add_go_framework_sources,
    _add_rust_framework_sources,
    _add_ts_js_framework_sources,
    _files_entry,
)

#: ``(strategy, target)``. The target is a file name for a ``files:`` entry and
#: a pattern for a ``glob:`` / ``path:`` one.
EntryKey = tuple[str, str]

#: The node type and strategy the root-config entry is written with, and the
#: comment ``wd init`` puts above it -- so an appended block reads as the one a
#: full init writes.
_ROOT_CONFIG_STRATEGY = "config_file"
_ROOT_CONFIG_TYPE = "config"
_ROOT_CONFIG_COMMENT = "Root configuration files"

#: Matches the ``- glob: "..."`` line of a generated entry block, and the
#: ``strategy:`` line under it. Together they key a block against what a config
#: already wires. A block neither pattern matches is kept: the guard exists to
#: avoid duplicates, never to silently drop wiring.
_BLOCK_GLOB_RE = re.compile(r'^\s*-\s+glob:\s+"(?P<glob>.*)"\s*$', re.MULTILINE)
_BLOCK_STRATEGY_RE = re.compile(r"^\s*strategy:\s+(?P<strategy>\S+)\s*$", re.MULTILINE)


def block_glob_and_strategy(block: str) -> tuple[str, str] | None:
    """``(glob, strategy)`` for a generated entry block, or None if unreadable."""
    glob = _BLOCK_GLOB_RE.search(block)
    strategy = _BLOCK_STRATEGY_RE.search(block)
    if glob is None or strategy is None:
        return None
    return glob.group("glob"), strategy.group("strategy")


def block_entry_key(block: str) -> EntryKey | None:
    """``(strategy, glob)`` for a generated entry block, or None if unreadable."""
    pair = block_glob_and_strategy(block)
    return None if pair is None else (pair[1], pair[0])


@dataclass(frozen=True)
class EntryWiring:
    """What the entry-shaped detectors found, as the builders need to see it.

    Every field is a whole-repo detection artifact, computed exactly as
    :func:`weld.init.init` computes it. ``languages`` is the set *present on
    disk*, not the unclaimed subset: a framework entry is missing or not
    independently of whether its language is claimed, which is the whole
    reason this comparison exists beside the language one.
    """

    #: ``detect_root_configs`` output, in its documented order.
    root_configs: tuple[str, ...] = ()
    #: ``detect_frameworks`` output: ``(framework, strategy, detected_in_path)``.
    frameworks: tuple[tuple[str, str, str], ...] = ()
    #: ``find_python_glob_roots`` output, whole-repo.
    python_globs: tuple[str, ...] = ()
    #: Languages present on disk (``detect_languages`` keys).
    languages: frozenset[str] = field(default_factory=frozenset)


def _framework_blocks(wiring: EntryWiring) -> list[tuple[EntryKey, str]]:
    """Every framework entry block a full init would write, keyed.

    Mirrors :func:`weld._init_language_entries._add_language_framework_entries`
    gate for gate -- each family's adder runs only when its own language is
    present -- so the blocks are the ones ``wd init`` emits, not a second
    table that can drift from them. A block whose key cannot be read is
    dropped rather than offered under a wrong key: an unkeyable block would
    be re-appended on every refresh.
    """
    blocks: list[str] = []
    frameworks = list(wiring.frameworks)
    if "python" in wiring.languages:
        _add_framework_sources(blocks, frameworks, list(wiring.python_globs))
    if "go" in wiring.languages:
        _add_go_framework_sources(blocks, frameworks)
    if "rust" in wiring.languages:
        _add_rust_framework_sources(blocks, frameworks)
    if wiring.languages & {"javascript", "typescript"}:
        _add_ts_js_framework_sources(blocks, frameworks)
    keyed: list[tuple[EntryKey, str]] = []
    for block in blocks:
        key = block_entry_key(block)
        if key is not None:
            keyed.append((key, block))
    return keyed


def entry_keys(wiring: EntryWiring) -> list[EntryKey]:
    """Every key this table can produce for the repo *wiring* describes.

    ``wd init`` records exactly this set as having been wired, because a full
    init writes all of it. Order is the detectors' own, so the record a config
    carries is stable across runs.
    """
    keys: list[EntryKey] = [
        (_ROOT_CONFIG_STRATEGY, name) for name in wiring.root_configs
    ]
    keys.extend(key for key, _block in _framework_blocks(wiring))
    return keys


def entry_blocks(
    wiring: EntryWiring, known: frozenset[EntryKey],
) -> tuple[list[str], list[EntryKey]]:
    """``(blocks to append, the keys they carry)`` for keys not in *known*.

    *known* is the union of what the config already carries and what weld has
    recorded writing into it, so a key drops out of the offer either because
    it is wired now or because it was wired once and removed since. The
    surviving root configs are collapsed into one ``- files: [...]`` block --
    the shape a full init writes -- rather than one block per name.
    """
    blocks: list[str] = []
    keys: list[EntryKey] = []

    new_configs = [
        name for name in wiring.root_configs
        if (_ROOT_CONFIG_STRATEGY, name) not in known
    ]
    if new_configs:
        blocks.append(_files_entry(
            new_configs, _ROOT_CONFIG_TYPE, _ROOT_CONFIG_STRATEGY,
            comment=_ROOT_CONFIG_COMMENT,
        ))
        keys.extend((_ROOT_CONFIG_STRATEGY, name) for name in new_configs)

    for key, block in _framework_blocks(wiring):
        if key not in known:
            blocks.append(block)
            keys.append(key)
    return blocks, keys
