"""The one place a ``dockerfile:`` node id is minted (bd bz5w9).

The id used to be derived from the file *stem*, in three copies of one
expression: twice inside :mod:`weld.strategies.dockerfile` and once in
:mod:`weld.strategies.compose`, whose comment asked the reader to keep the
third aligned with the other two by hand.

A stem is not an identity. Every ``Dockerfile`` in a repository produced
``dockerfile:Dockerfile``, so a monorepo with one image per app got one node
for all of them -- and not merely the last one: node props are
first-writer-wins (:mod:`weld._discover_node_merge`) while edges accumulate,
so one image's ``FROM`` ended up wearing every image's ``COPY`` closure.
``Dockerfile`` beside ``Dockerfile.dev`` in a single directory collided too,
since ``Path.stem`` drops the ``.dev`` -- the case ADR 0045 recorded as
deferred.

The repo-relative path is the identity, matching what the sibling strategies
already key on (``python_module``'s ``file:services/x/src/main``,
``manifest``'s ``config:apps_a_package_json``) and what this strategy already
does for its own ``file:`` COPY-source nodes. A Dockerfile at the repo root
keeps ``dockerfile:Dockerfile`` exactly, because the repo-relative path of a
root file *is* its name -- so the single-Dockerfile repo, which is every
fixture and every documented example, does not move.

:func:`legacy_alias_by_path` is the compatibility half: a graph, a bookmark or
a transcript naming the old stem id still resolves through ``props.aliases``
(:mod:`weld._alias_index`) -- but only where that old id names one file
unambiguously. Where two Dockerfiles claimed it there is no single right
answer, and minting the alias on both would rebuild the same ambiguity one
layer up, since ``build_alias_index`` settles a duplicate claim
first-writer-wins, i.e. arbitrarily.

The same one-claimant rule is what keeps an alias from ever shadowing a real
node id. An alias is always a dot-free, slash-free stem, so the only new ids
it can collide with are root-level names without dots -- and such a file's own
legacy id equals its new id, which makes it a claimant of that id and pushes
the count past one. The ``Dockerfile`` + ``docker/Dockerfile`` pair is the
case: neither gets the alias, and the root node keeps the id outright.

That reasoning holds over the paths handed to :func:`legacy_alias_by_path`,
which is the match set of **one** ``discover.yaml`` source entry -- a strategy
is called per entry and cannot see its siblings. A config that wires this
strategy twice, one entry matching a root ``Dockerfile`` and another matching
``apps/*/Dockerfile``, can therefore record an alias on a subdirectory node
that the other entry's canonical node also claims. The backstop for exactly
that shape is already downstream and is why this is left as a bound rather
than plumbed through the orchestrator: ``build_alias_index`` refuses an alias
that shadows a real node id, warns, and keeps the canonical lookup. The graph
carries one misleading ``props.aliases`` entry; no lookup is answered wrongly.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterable

__all__ = [
    "dockerfile_node_id",
    "legacy_alias_by_path",
    "legacy_dockerfile_node_id",
]


def dockerfile_node_id(rel_path: str) -> str:
    """Return the node id for the Dockerfile at repo-relative *rel_path*.

    *rel_path* is a posix repo-relative path as
    :func:`weld._rel_path.rel_to_root` produces it. Slashes and dots are left
    alone: both already appear in ``file:`` ids minted by this same strategy,
    and collapsing them is what made the stem ambiguous in the first place.
    """
    return f"dockerfile:{rel_path}"


def legacy_dockerfile_node_id(name: str) -> str:
    """Return the pre-bz5w9 stem-derived id for a Dockerfile called *name*.

    *name* may be a file name or a path; only its stem is consulted, exactly
    as the replaced expression did, so the ids this produces are the ones an
    existing graph on disk actually carries.
    """
    return f"dockerfile:{Path(name).stem.replace('.', '_')}"


def legacy_alias_by_path(rel_paths: Iterable[str]) -> dict[str, str]:
    """Map each rel path to the legacy id it may claim, where unambiguous.

    A path is absent from the result when another discovered Dockerfile claims
    the same legacy id, or when that legacy id already equals the path's new
    id (a root ``Dockerfile``, where the alias would be the self-alias
    :func:`weld._alias_index.build_alias_index` drops anyway).

    Order-independent: the answer is a function of the *set* of paths, not of
    walk order, so two runs over one tree write the same bytes.
    """
    legacy = {rel: legacy_dockerfile_node_id(rel) for rel in rel_paths}
    claims = Counter(legacy.values())
    return {
        rel: legacy_id
        for rel, legacy_id in legacy.items()
        if claims[legacy_id] == 1
        and legacy_id != dockerfile_node_id(rel)
    }
